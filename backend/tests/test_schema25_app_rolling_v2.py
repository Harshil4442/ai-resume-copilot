"""Finite v2/source/alias fixtures. Synthetic transport never proves deployment."""
from __future__ import annotations

import copy
import json
import subprocess
from dataclasses import asdict, fields, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_schema25_app_rolling_release import ROOT, AppFixture
from test_schema25_app_rolling_release import app_release as app

from scripts import schema25_app_release_contract_v2 as contract

rolling = app.rolling


def test_final_polling_dependencies_and_tests_are_bound_into_the_source_fingerprint(monkeypatch):
    required = {"frontend/lib/boundedStatusPolling.ts", "frontend/lib/boundedStatusPolling.test.ts",
        "frontend/lib/readQueryPolicy.ts", "frontend/lib/readQueryPolicy.test.ts", "frontend/lib/api.ts",
        "frontend/lib/resumeUpload.ts", "frontend/lib/resumeUpload.test.ts",
        "frontend/components/ResumeUploadProgress.tsx", "frontend/app/billing/page.test.tsx"}
    assert required <= set(contract.BOOTSTRAP_PATHS)
    prior = app.initial.prior_app_files(ROOT, contract.PRIOR_RELEASE)
    before = contract.source_fingerprint(ROOT, prior)
    original = Path.read_bytes
    target = ROOT / "frontend/lib/boundedStatusPolling.ts"
    monkeypatch.setattr(Path, "read_bytes", lambda path: original(path) + b"\n// source drift\n"
        if path == target else original(path))
    assert contract.source_fingerprint(ROOT, prior) != before


@pytest.mark.parametrize("name", ["infra/gcp/rolling_release.py", "infra/gcp/fresh_monetary_release.py"])
def test_release_order_helpers_are_bound_into_v2_qualification(monkeypatch, name):
    assert name in contract.QUALIFICATION_PATHS
    prior = app.initial.prior_app_files(ROOT, contract.PRIOR_RELEASE)
    before = contract.source_fingerprint(ROOT, prior)
    original = Path.read_bytes
    target = ROOT / name
    monkeypatch.setattr(Path, "read_bytes", lambda path: original(path) + b"\n# source drift\n"
        if path == target else original(path))
    assert contract.source_fingerprint(ROOT, prior) != before


class V2Fixture(AppFixture):
    def __init__(self):
        super().__init__()
        pins = []
        for pin in self.plan.services:
            state = self.states[pin.service]
            template = state["template"]
            template["labels"]["hirewiz-source"] = app.PRIOR_V2.release
            template["containers"][0]["image"] = self.plan.image.split("@")[0] + "@" + app.PRIOR_V2.image_digest
            rolling.environment(template)["APP_RELEASE"]["value"] = app.PRIOR_V2.release
            revision = {**copy.deepcopy(template), "name": state["latestReadyRevision"],
                "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}], "uid": "a"*8+"-"+"b"*4+"-"+"c"*4+"-"+"d"*4+"-"+"e"*12}
            self.revisions[(pin.service, pin.prior_revision)] = revision
            pins.append(replace(pin, prior_release=app.PRIOR_V2.release,
                template_sha256=rolling.template_sha(template, pin.service),
                revision_sha256=rolling.sha({key: revision.get(key) for key in pin.revision_keys})))
        values = {f.name: getattr(self.plan, f.name) for f in fields(app.App25Plan)
                  if f.name not in {"schema_contract","prior_source","services","reachable_revisions"}}
        self.plan = app.App25V2Plan(**values, services=tuple(pins),
            reachable_revisions=tuple(app.reachable_pin(p.service,p.prior_revision,self.revisions[(p.service,p.prior_revision)],
                prior_source=app.PRIOR_V2) for p in pins))
        self.targets = {p.service: app.candidate_template(self.plan,p,self.service(p.service)) for p in pins}
        self.plan.validate()


@pytest.fixture
def context(tmp_path):
    tmp_path.chmod(0o700)
    boundary = V2Fixture()
    return boundary, rolling.Journal(tmp_path, boundary.plan)


def tagged_v2():
    boundary = V2Fixture()
    pins = []
    for pin in boundary.plan.services:
        rows = [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": pin.prior_revision,
                 "percent": 100, "tag": "prior-health"},
                {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": pin.prior_revision,
                 "tag": "retained-ab"}]
        boundary.states[pin.service]["traffic"] = copy.deepcopy(rows)
        boundary.states[pin.service]["trafficStatuses"] = copy.deepcopy(rows)
        pins.append(replace(pin, traffic=tuple(rolling.pinned_traffic(pin.service, boundary.service(pin.service)))))
    boundary.plan = replace(boundary.plan, services=tuple(pins))
    boundary.plan.validate()
    boundary.targets = {p.service: app.candidate_template(boundary.plan, p, boundary.service(p.service)) for p in pins}
    return boundary


@pytest.mark.parametrize("kind", ["default", "v1"])
def test_default_and_v1_promotion_keep_exact_historical_alias_traffic(kind):
    from backend.tests.test_rolling_release import FixtureBoundary

    boundary = FixtureBoundary() if kind == "default" else AppFixture()
    plan = boundary.plan
    pin = plan.services[0]
    captured = ({"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": pin.prior_revision,
                 "percent": 100, "tag": "old-health"},)
    pin = replace(pin, traffic=captured)
    original = copy.deepcopy(captured)
    expected = [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": pin.prior_revision,
                 "tag": "old-health"},
                {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": plan.revision(pin.service),
                 "tag": plan.tag, "percent": 100}]
    assert rolling._traffic_for(plan, pin, True) == sorted(expected, key=rolling.canonical)
    assert pin.traffic == original


@pytest.mark.parametrize("tamper", ["add", "drop", "tag", "revision", "type", "percent"])
def test_v2_promotion_cannot_invent_or_mutate_captured_alias_intent(tamper):
    boundary = tagged_v2()
    pin = boundary.plan.services[0]
    rows = [{key: value for key, value in row.items() if key != "percent"} for row in pin.traffic]
    if tamper == "add":
        rows.append({**rows[0], "tag": "unreviewed"})
    elif tamper == "drop":
        rows.pop()
    elif tamper == "percent":
        rows[0]["percent"] = 100
    else:
        rows[0][tamper] = "unreviewed"
    with pytest.raises(rolling.RollingDenied, match="alias_intent_changed"):
        boundary.plan.promoted_aliases(pin, rows)
    assert not boundary.writes


def test_v2_promoted_tags_are_homogeneous_and_original_rollback_still_works(tmp_path):
    import document_application_context as applications

    tmp_path.chmod(0o700)
    boundary = tagged_v2()
    plan = boundary.plan
    original_plan = rolling.canonical(asdict(plan))
    originals = copy.deepcopy(boundary.revisions)
    journal = rolling.Journal(tmp_path, plan)
    app.execute("stage", plan, boundary, journal)
    for pin in plan.services:
        assert [row for row in boundary.service(pin.service)["traffic"] if row.get("percent")] == [
            row for row in pin.traffic if row.get("percent")]
    assert app.execute("advance", plan, boundary, journal)["rolling_release_complete"]
    assert len(boundary.writes) == 6
    for pin in plan.services:
        current = boundary.service(pin.service)
        rows = current["traffic"]
        assert {row["tag"] for row in rows} == {"prior-health", "retained-ab", plan.tag}
        assert {row["revision"] for row in rows} == {plan.revision(pin.service)}
        assert sum(row.get("percent", 0) for row in rows) == 100
        assert int(current["generation"]) == pin.generation + 2
        assert all(row["type"] == "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION" for row in rows)
        if pin.service in applications.CALLERS:
            def revision(full, service=pin.service):
                value = boundary.revision(service, full)
                return value | {"service": service, "uid": "12345678-1234-1234-1234-123456789abc"}
            accepted = applications.serving(SimpleNamespace(revision=revision), current, plan.image, pin.service, pin.uid)
            assert accepted["name"] == current["latestCreatedRevision"]
    assert rolling.canonical(asdict(plan)) == original_plan
    assert all(boundary.revisions[key] == value for key, value in originals.items())
    assert app.execute("rollback", plan, boundary, journal)["status"] == "schema25_app_prior_traffic_restored"
    for pin in plan.services:
        current = boundary.service(pin.service)
        assert current["traffic"] == rolling._traffic_for(plan, pin, False, rollback=True)
        assert [row for row in current["traffic"] if row.get("percent")] == [
            row for row in pin.traffic if row.get("percent")]
        assert int(current["generation"]) == pin.generation + 3
    assert len(boundary.writes) == 9
    assert rolling.canonical(asdict(plan)) == original_plan


@pytest.mark.parametrize("failure", ["pending", "unknown"])
def test_v2_tag_retarget_pending_or_unknown_promotion_never_repeats_patch(tmp_path, failure):
    tmp_path.chmod(0o700)
    boundary = tagged_v2()
    plan = boundary.plan
    journal = rolling.Journal(tmp_path, plan)
    app.execute("stage", plan, boundary, journal)
    boundary.pause = failure == "pending"
    boundary.lose_ack = failure == "unknown"
    if failure == "unknown":
        with pytest.raises(RuntimeError):
            app.execute("advance", plan, boundary, journal)
    else:
        assert app.execute("advance", plan, boundary, journal)["status"] == "schema25_app_same_operation_pending"
    assert len(boundary.writes) == 4
    app.execute("reconcile", plan, boundary, journal)
    assert len(boundary.writes) == 4
    boundary.pause = False
    for operation in boundary.operations.values():
        operation["done"] = True
    app.execute("reconcile", plan, boundary, journal)
    assert len(boundary.writes) == 4
    assert app.execute("advance", plan, boundary, journal)["rolling_release_complete"]
    assert len(boundary.writes) == 6


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize("policy_name", ["old", "v3", "v4", "unknown"])
def test_only_qualified_v2_resource_checker_selects_actual_v4_policy(monkeypatch, version, policy_name):
    import document_refresh_probe as native_proof
    import document_source_candidate as source_profile
    import test_document_worker_deployment as worker_fixture

    documents = app.rolling.release.documents
    policy = {"old": documents.POLICY_SHA256, "v3": documents.CANDIDATE_POLICY_SHA256,
              "v4": source_profile.POLICY_SHA256, "unknown": "f" * 64}[policy_name]
    snapshot = worker_fixture.snapshots(documents)
    snapshot["service"]["uri"] = documents.ACCEPTED_ORIGIN
    snapshot["service_iam"] = documents.render(worker_fixture.IMAGE, worker_fixture.RELEASE)["after_isolation_service_iam"]
    for template in (snapshot["service"]["template"], snapshot["revision"]):
        for row in template["containers"][0]["env"]:
            if row["name"] == "DOCUMENT_WORKER_POLICY_SHA256":
                row["value"] = policy
    boundary = app.NativeApp25Boundary(ROOT) if version == "v1" else app.NativeApp25BoundaryV2(ROOT)
    calls = []
    def get(api, path):
        assert api == "run"
        calls.append("metadata")
        return copy.deepcopy(snapshot["service"] if path == documents.RESOURCE else snapshot["revision"])
    def cloud(*args):
        if args[:4] == ("iam", "service-accounts", "keys", "list"):
            return []
        if args[:3] == ("iam", "service-accounts", "describe"):
            return snapshot["service_account"]
        if args[:3] == ("run", "services", "get-iam-policy"):
            return snapshot["service_iam"]
        if args[:2] == ("projects", "get-iam-policy"):
            return snapshot["project_iam"]
        pytest.fail("Unexpected provider read in finite synthetic document proof")
    def accepted(image, service):
        assert image == worker_fixture.IMAGE and service == snapshot["service"]
        calls.append("native_acceptance")
        return SimpleNamespace(image=image)
    original = documents.check_snapshot
    def checked(*args, **kwargs):
        calls.append(("configuration", kwargs))
        original(*args, **kwargs)
        # Stop before unrelated TeX/network reads, after the REAL worker checker.
        raise app.rolling.release.ReleaseDenied("synthetic_worker_configuration_matched")
    monkeypatch.setattr(boundary.observer, "_get", get)
    monkeypatch.setattr(boundary.observer, "_cloud", cloud)
    monkeypatch.setattr(native_proof, "verify_current_successor_for_release", accepted)
    monkeypatch.setattr(documents, "check_snapshot", checked)
    supported = policy_name in {"old", "v3"} or version == "v2" and policy_name == "v4"
    expected = "synthetic_worker_configuration_matched" if supported else "fresh_document_native_configuration_unverified"
    with pytest.raises(app.rolling.release.ReleaseDenied, match=expected):
        boundary.observer.verify_document_configuration(SimpleNamespace(release=worker_fixture.RELEASE,
            runtime_service_account=documents.API_INVOKER))
    configuration = next(value[1] for value in calls if isinstance(value, tuple))
    assert configuration.get("source_revision", False) is (version == "v2" and policy_name == "v4")
    assert configuration["candidate_v3"] is (policy_name == "v3")
    assert calls.index("native_acceptance") < next(i for i, value in enumerate(calls) if isinstance(value, tuple))


@pytest.mark.parametrize("historical_base", [False, True])
def test_v2_v4_selector_cannot_replace_actual_native_acceptance(monkeypatch, historical_base):
    import document_refresh_probe as native_proof
    import document_source_candidate as source_profile
    import test_document_worker_deployment as worker_fixture

    documents = app.rolling.release.documents
    snapshot = worker_fixture.snapshots(documents)
    snapshot["service"]["uri"] = documents.ACCEPTED_ORIGIN
    for template in (snapshot["service"]["template"], snapshot["revision"]):
        if historical_base:
            template["containers"][0]["image"] = documents.ACCEPTED_IMAGE
        template["containers"][0]["env"][1]["value"] = source_profile.POLICY_SHA256
    boundary = app.NativeApp25BoundaryV2(ROOT)
    monkeypatch.setattr(boundary.observer, "_get", lambda api, path: copy.deepcopy(
        snapshot["service"] if path == documents.RESOURCE else snapshot["revision"]))
    monkeypatch.setattr(boundary.observer, "_cloud", lambda *args: [] if args[:4] == (
        "iam", "service-accounts", "keys", "list") else snapshot["service_account"] if args[:3] == (
        "iam", "service-accounts", "describe") else snapshot["service_iam"] if args[:3] == (
        "run", "services", "get-iam-policy") else snapshot["project_iam"])
    native_calls = []
    def denied(*args):
        native_calls.append(True)
        raise native_proof.NativeProofDenied("synthetic_missing_native_acceptance")
    monkeypatch.setattr(native_proof, "verify_current_successor_for_release", denied)
    monkeypatch.setattr(documents, "check_snapshot", lambda *a, **k: pytest.fail("Selector cannot authorize native acceptance"))
    reason = ("fresh_document_source_revision_base_image_refused" if historical_base
              else "fresh_document_native_configuration_unverified")
    with pytest.raises(app.rolling.release.ReleaseDenied, match=reason):
        boundary.observer.verify_document_configuration(SimpleNamespace(release=worker_fixture.RELEASE,
            runtime_service_account=documents.API_INVOKER))
    assert len(native_calls) == (0 if historical_base else 1)


def test_v1_v2_loaders_never_adopt_each_others_plan(context):
    current, _ = context
    raw = rolling.canonical(asdict(current.plan))
    assert app.load_v2(raw) == current.plan
    with pytest.raises(rolling.RollingDenied):
        app.load(raw)
    with pytest.raises(rolling.RollingDenied):
        app.load_v2(rolling.canonical(asdict(AppFixture().plan)))
    with pytest.raises(rolling.RollingDenied):
        replace(current.plan, prior_source=app.PRIOR).validate()


def test_v2_uses_same_one_send_stage_promote_and_prior_health_rollback(context):
    current, journal = context
    assert app.execute("stage",current.plan,current,journal)["status"] == "schema25_app_staged_zero_traffic"
    assert app.execute("advance",current.plan,current,journal)["rolling_release_complete"]
    result = app.execute("rollback",current.plan,current,journal)
    assert result["status"] == "schema25_app_prior_traffic_restored"
    assert journal.read(app.ROLLBACK_INTENT)["prior_release"] == app.PRIOR_V2.release
    assert all(mask in {"template,traffic","traffic"} for _,_,mask in current.writes)


@pytest.mark.parametrize("flag", [*contract.STAGE5_FLAGS, "OPTIONAL_AI_GENERATION_ENABLED","RAZORPAY_CHECKOUT_ENABLED"])
def test_v2_cannot_implicitly_open_old_or_new_flags(context,flag):
    current, journal = context
    pin = current.plan.services[0]
    template = current.states[pin.service]["template"]
    env = rolling.environment(template)
    env[flag] = {"name":flag,"value":"true"}
    template["containers"][0]["env"] = list(env.values())
    with pytest.raises(rolling.RollingDenied):
        app.candidate_template(current.plan,pin,current.service(pin.service))
    assert not current.writes and not list(journal.directory.glob("*.started.json"))


@pytest.mark.parametrize("change", ["required","existing","unknown"])
def test_additive_response_manifest_rejects_unreviewed_schema_change(change):
    prior = app.initial.prior_app_files(ROOT,contract.PRIOR_RELEASE)["backend/app/schemas.py"]
    current = (ROOT/"backend/app/schemas.py").read_bytes()
    contract.additive_schema(prior,current)
    if change=="required":
        current=current.replace(b"evidence_quote: str | None = None",b"evidence_quote: str")
    elif change=="existing":
        current=current.replace(b"match_id:                int",b"match_id:                str")
    else:
        current+=b"\nclass UnreviewedResponse(BaseModel):\n    value: int\n"
    with pytest.raises(Exception,match="schema|additions"):
        contract.additive_schema(prior,current)


def test_old_tag_source_is_refused_by_new_release_capture(context):
    current,_=context
    pin=current.plan.services[0]
    old=copy.deepcopy(current.revisions[(pin.service,pin.prior_revision)])
    rolling.environment(old)["APP_RELEASE"]["value"]=app.PRIOR.release
    old["containers"][0]["image"]=current.plan.image.split("@")[0]+"@"+app.PRIOR.image_digest
    with pytest.raises(rolling.RollingDenied,match="source_or_admission"):
        app.reachable_pin(pin.service,pin.prior_revision,old,prior_source=app.PRIOR_V2)


@pytest.mark.parametrize("flag", contract.STAGE5_FLAGS)
def test_retained_tag_cannot_hide_stage5_activation(context, monkeypatch, flag):
    fixture, _ = context
    adapter = app.NativeApp25BoundaryV2(ROOT)
    pin = fixture.plan.services[0]
    retained_name = pin.service + "-retained-ab2"
    retained = copy.deepcopy(fixture.revision(pin.service, pin.prior_revision))
    retained["name"] = rolling.PARENT + "/services/" + pin.service + "/revisions/" + retained_name
    retained["containers"][0]["env"].append({"name": flag, "value": "true"})
    fixture.revisions[(pin.service, retained_name)] = retained
    changed = replace(pin, traffic=(*pin.traffic, {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
        "revision": retained_name, "tag": "retained-ab2"}))
    monkeypatch.setattr(adapter, "revision", fixture.revision)
    with pytest.raises(rolling.RollingDenied, match="stage5_activation_outside_release"):
        adapter._reachable((changed, *fixture.plan.services[1:]))
    assert not fixture.writes


@pytest.mark.parametrize("change", ["continue", "optional", "environment", "postgres_missing", "postgres_foreign", "retained", "history"])
def test_ci_cannot_relabel_optional_or_missing_qualification_as_success(tmp_path, change):
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    contract.verify_ci(ROOT)
    steps = workflow["jobs"]["backend"]["steps"]
    step = next(row for row in steps if row.get("name") == contract.PROTOCOL_STEP)
    if change == "continue":
        step["continue-on-error"] = True
    elif change == "optional":
        step["if"] = "false"
    elif change == "environment":
        step["env"]["HIREWIZ_AUTHORITY_EMULATOR_REQUIRED"] = "false"
    elif change == "postgres_missing":
        step["env"].pop("HIREWIZ_TEST_POSTGRES_URL")
    elif change == "postgres_foreign":
        step["env"]["HIREWIZ_TEST_POSTGRES_URL"] = "postgresql+psycopg://hirewiz:hirewiz@192.0.2.1:55433/hirewiz_admission_test"
    elif change == "retained":
        steps[:] = [row for row in steps if row.get("name") != "Qualify retained actual-source schema25 app v1"]
    else:
        next(row for row in steps if row.get("uses", "").startswith("actions/checkout@"))["with"]["fetch-depth"] = 1
    path = tmp_path / ".github/workflows/ci.yml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(workflow))
    with pytest.raises(Exception, match="ci_step_changed|history_required"):
        contract.verify_ci(tmp_path)


@pytest.mark.parametrize("change", ["post", "anonymous", "field", "unrelated"])
def test_additive_http_manifest_rejects_new_authority_or_other_schema_change(tmp_path, monkeypatch, change):
    prior = subprocess.run(["git", "show", contract.PRIOR_RELEASE + ":backend/openapi.json"],
        cwd=ROOT, capture_output=True, check=True).stdout
    value = json.loads((ROOT / "backend/openapi.json").read_bytes())
    contract.additive_http(ROOT)
    if change == "post":
        value["paths"]["/api/auth/bootstrap"]["post"] = value["paths"]["/api/auth/bootstrap"].pop("get")
    elif change == "anonymous":
        value["paths"]["/api/auth/bootstrap"]["get"].pop("security")
    elif change == "field":
        value["components"]["schemas"]["OwnedBootstrapView"]["properties"]["owner_id"] = {"type": "integer"}
    else:
        value["components"]["schemas"]["JobMatchResponse"]["properties"]["match_id"]["type"] = "string"
    path = tmp_path / "backend/openapi.json"
    path.parent.mkdir()
    path.write_text(json.dumps(value))
    monkeypatch.setattr(contract.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=prior))
    with pytest.raises(Exception, match="bootstrap_|unreviewed_http_change"):
        contract.additive_http(tmp_path)

