import copy
import hashlib
import io
import json
import sys
from dataclasses import asdict
from uuid import UUID

import pytest
from infra.gcp import schema27_api_identity_readback as readback


def native_service(name, index, account, *, candidate=False, tag=None):
    old_revision = f"{name}-current"
    revision = f"{name}-candidate" if candidate else old_revision
    resource = readback.rolling.PARENT + "/services/" + name
    template = {"serviceAccount": account, "containers": [{"image": readback.IMAGE + "@sha256:" + "a" * 64,
                 "env": [{"name": "APP_RELEASE", "value": "b" * 40},
                         {"name": "DATABASE_URL", "valueSource": {"secretKeyRef": {
                             "secret": "synthetic-db", "version": "3"}}},
                         {"name": "AUTO_DB_MIGRATE", "value": "false"},
                         {"name": "RAZORPAY_CHECKOUT_ENABLED", "value": "false"},
                         {"name": "SYNTHETIC_KEY", "value": "synthetic-value-must-not-appear"}]}]}
    traffic = [{"type": readback.REVISION_TRAFFIC, "revision": old_revision, "percent": 100}]
    if name == readback.API:
        traffic.extend({"type": readback.REVISION_TRAFFIC, "revision": old_revision, "tag": tag}
                       for tag in ("preview", "restore", "validation"))
    if tag is not None:
        traffic.append({"type": readback.REVISION_TRAFFIC, "revision": revision, "tag": tag})
    service = {"name": resource, "uid": readback.application.UIDS[readback.API] if name == readback.API else
               str(UUID(int=index)), "generation": "5" if candidate else "4",
               "observedGeneration": "5" if candidate else "4", "latestCreatedRevision": revision, "etag": "etag-4",
               "latestReadyRevision": revision, "terminalCondition": {"state": "CONDITION_SUCCEEDED"},
               "template": template | {"revision": revision}, "traffic": traffic,
               "trafficStatuses": copy.deepcopy(traffic), "ingress": "INGRESS_TRAFFIC_ALL"}
    revisions = {
        old_revision: template | {"serviceAccount": readback.rolling.RUNTIME if candidate else account},
    }
    if candidate:
        revisions[revision] = template
    for offset, (identifier, row) in enumerate(revisions.items()):
        row.update({"name": resource + "/revisions/" + identifier, "uid": str(UUID(int=index + 10 + offset)),
                    "service": resource, "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}],
                    "containerStatuses": [{"name": "app"}]})
    return service, revisions


@pytest.fixture
def review():
    old_api = readback.Identity(readback.rolling.RUNTIME, "1" * 21)
    new_api = readback.Identity(f"hirewiz-api-runtime@{readback.rolling.release.PROJECT}.iam.gserviceaccount.com",
                                "2" * 21)
    plan = readback.Transition(old_api, new_api, readback.application.UIDS[readback.API], f"{readback.API}-current",
                               f"{readback.API}-candidate", "identity-review")
    services = {name: readback.project_service(name, *native_service(
        name, index, readback.COORDINATOR_ACCOUNT if name == readback.COORDINATOR else old_api.email))
        for index, name in enumerate(readback.SERVICES, 1)}
    identities = {key: readback.project_identity({"email": item.email, "uniqueId": item.uid}, [])
                  for key, item in {"old_api": old_api, "new_api": new_api}.items()}
    callers = {name: {"version": 1, "etag": "old", "bindings": [{"role": "roles/run.invoker", "members": [
        "serviceAccount:" + old_api.email, "serviceAccount:" + readback.COORDINATOR_ACCOUNT,
    ]}]} for name in (*readback.SERVICES, *readback.DOCUMENTS)}
    before = {"services": services, "identities": identities, "callers": callers}
    after = copy.deepcopy(before)
    after["services"][readback.API] = readback.project_service(
        readback.API, *native_service(readback.API, 1, new_api.email, candidate=True, tag=plan.candidate_tag))
    for name in readback.DOCUMENTS:
        after["callers"][name]["etag"] = "new"
        after["callers"][name]["bindings"][0]["members"].append("serviceAccount:" + new_api.email)
    return {"kind": readback.KIND, "plan": asdict(plan), "before": before, "after": after}


def test_readback_and_cli_custody(review, tmp_path, monkeypatch, capsys):
    raw = json.dumps(review).encode()
    assert b"synthetic-value-must-not-appear" not in raw
    assert b"synthetic-db" not in raw
    fixture = tmp_path / "review.json"
    fixture.write_bytes(raw)
    fixture.chmod(0o600)
    monkeypatch.setattr(sys, "argv", ["readback", "--review", str(fixture), "--sha256", hashlib.sha256(raw).hexdigest()])
    assert readback.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "status": "api_identity_readback_validated", "native_approval": False, "native_mutations": 0,
    }
    fixture.chmod(0o644)
    assert readback.main() == 1
    assert "api_identity_readback_refused" in capsys.readouterr().out


def test_untagged_candidate_has_no_alternate_allocation(review):
    tagged = review["after"]["services"][readback.API]
    new_account = review["plan"]["new_api"]["email"]
    review["plan"].pop("candidate_tag")
    review["after"]["services"][readback.API] = readback.project_service(
        readback.API, *native_service(readback.API, 1, new_account, candidate=True))
    assert readback.validate(review)["status"] == "api_identity_readback_validated"
    review["after"]["services"][readback.API] = tagged
    with pytest.raises(readback.rolling.RollingDenied, match="api_traffic_changed"):
        readback.validate(review)


@pytest.fixture
def preparation(review):
    plan = review["plan"]
    plan.pop("candidate_tag")
    service, revisions = native_service(readback.API, 1, plan["old_api"]["email"])
    native = {"service": service, "revisions": revisions, "candidate_lookup": {
        "name": service["name"] + "/revisions/" + plan["candidate_revision"], "status": 404,
    }}
    return {"kind": readback.PREPARE_KIND, "plan": plan, "before": review["before"]["services"][readback.API],
            "etag": service["etag"]}, native


def test_prepare_cli_emits_exact_request_digest_only(preparation, tmp_path, monkeypatch, capsys):
    review, native = preparation
    service = native["service"]
    plan = review["plan"]
    raw = json.dumps(review).encode()
    assert b"synthetic-value-must-not-appear" not in raw
    fixture = tmp_path / "review.json"
    fixture.write_bytes(raw)
    fixture.chmod(0o600)
    monkeypatch.setattr(sys, "argv", ["readback", "--prepare", "--review", str(fixture), "--sha256",
                                     hashlib.sha256(raw).hexdigest()])
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(native).encode())))
    assert readback.main() == 0
    request = {"name": service["name"], "etag": service["etag"], "traffic": review["before"]["traffic"],
               "template": service["template"] | {"revision": plan["candidate_revision"],
                                                  "serviceAccount": plan["new_api"]["email"]}}
    assert json.loads(capsys.readouterr().out) == {
        "status": "api_identity_request_prepared", "request_sha256": readback.rolling.sha(request),
        "native_approval": False, "native_mutations": 0,
    }
    assert service["template"]["serviceAccount"] == plan["old_api"]["email"]


def test_prepare_explicitly_pins_latest_to_old_revision(preparation):
    review, native = preparation
    service = native["service"]
    pinned_request = readback.prepare(review, native)
    service["traffic"][0] = {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "percent": 100}
    review["before"] = readback.project_service(readback.API, service, native["revisions"])
    assert readback.prepare(review, native) == pinned_request


@pytest.mark.parametrize(("drift", "reason"), [
    ("etag", "prepare_etag_changed"), ("alias", "prepare_preimage_changed"),
    ("candidate_exists", "prepare_candidate_not_absent"), ("unrelated_404", "prepare_candidate_not_absent"),
    ("start_migration", "prepare_startup_migration_not_closed"), ("tag", "prior_traffic"),
])
def test_prepare_rejects_changed_precondition(preparation, drift, reason):
    review, native = preparation
    service = native["service"]
    if drift == "etag":
        service["etag"] = "etag-5"
    elif drift == "alias":
        for key in ("traffic", "trafficStatuses"):
            service[key] = [row for row in service[key] if row.get("tag") != "restore"]
    elif drift == "candidate_exists":
        native["candidate_lookup"]["status"] = 200
    elif drift == "unrelated_404":
        native["candidate_lookup"]["name"] += "-other"
    elif drift == "start_migration":
        env = service["template"]["containers"][0]["env"]
        env.remove({"name": "AUTO_DB_MIGRATE", "value": "false"})
        review["before"] = readback.project_service(readback.API, service, native["revisions"])
    elif drift == "tag":
        review["plan"]["candidate_tag"] = "restore"
    with pytest.raises(readback.rolling.RollingDenied, match=reason):
        readback.prepare(review, native)


def test_prepare_only_adds_explicit_reviewed_zero_percent_tag(preparation):
    review, native = preparation
    review["plan"]["candidate_tag"] = "identity-review"
    body = readback.prepare_request(review, native)
    assert body["traffic"] == sorted([*review["before"]["traffic"], {
        "type": readback.REVISION_TRAFFIC, "revision": review["plan"]["candidate_revision"],
        "tag": "identity-review",
    }], key=readback.rolling.canonical)
    assert readback.prepare(review, native)["request_sha256"] == readback.rolling.sha(body)
    assert body["template"] == native["service"]["template"] | {
        "revision": review["plan"]["candidate_revision"], "serviceAccount": review["plan"]["new_api"]["email"],
    }


@pytest.mark.parametrize("drift", ["identity", "template", "traffic", "intent", "prior_tag", "worker", "coordinator",
                                   "caller", "worker_iam", "old_revision", "candidate_revision"])
def test_rejects_out_of_scope_change(review, drift):
    after = review["after"]
    api = after["services"][readback.API]
    if drift == "identity":
        after["identities"]["new_api"]["uid"] = "3" * 21
    elif drift == "template":
        api["template"]["common_sha256"] = "c" * 64
    elif drift in {"traffic", "intent"}:
        row = next(row for row in api["traffic"] if row.get("tag") == "identity-review")
        if drift == "traffic":
            row["percent"] = 100
        else:
            row["type"] = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    elif drift == "prior_tag":
        api["traffic"] = [row for row in api["traffic"] if row.get("tag") != "restore"]
    elif drift in {"worker", "coordinator"}:
        name = readback.WORKERS[0] if drift == "worker" else readback.COORDINATOR
        after["services"][name]["generation"] += 1
    elif drift in {"caller", "worker_iam"}:
        name = readback.DOCUMENTS[0] if drift == "caller" else readback.WORKERS[0]
        after["callers"][name]["bindings"][0]["members"].append("allUsers")
    elif drift in {"old_revision", "candidate_revision"}:
        identifier = review["plan"][drift]
        api["revisions"][identifier]["common_sha256"] = "d" * 64
    with pytest.raises(readback.rolling.RollingDenied, match="schema27_api_identity_"):
        readback.validate(review)
