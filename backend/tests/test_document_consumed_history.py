"""Consumed-history audit and homogeneous traffic: synthetic providers only."""
from __future__ import annotations

import copy
import importlib
import io
import shutil
import sys
import zipfile
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_document_refresh_transition as fixture
from scripts import document_refresh as refresh
from scripts import retrieve_consumed_document_history as retrieval

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
apps = importlib.import_module("document_application_context")
history = importlib.import_module("document_consumed_history")
native = importlib.import_module("document_consumed_native")


def private(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(history.canonical(value))
    path.chmod(0o600)


def test_fixed_historical_chain_and_restore_bytes():
    assert tuple(pin.run for pin in history.ARTIFACTS) == (
        40771705772, 40781707554, 40788512340, 40805300453, 40811330747)
    assert [pin.artifact for pin in history.ARTIFACTS[:4]] == [
        12852818465, 12858021573, 12860975525, 12868086986]
    plan, records = history.restore_evidence(ROOT)
    assert plan.nonce == "5c8888070c0e8ff8e3ca93bf7784fbe0"
    assert records["service.ai-resume-parser.restore.checked.json"]["generation"] == 345
    assert all(len(pin.zip_sha256) == len(pin.tree_sha256) == 64 for pin in history.ARTIFACTS)


def test_original_restore_marker_tamper_is_refused(tmp_path):
    source = ROOT / "infra/document-processing/consumed-history-api-restore"
    target = tmp_path / "infra/document-processing/consumed-history-api-restore"
    shutil.copytree(source, target)
    (target / "service.ai-resume-parser.restore.checked.json").write_text('{"generation":345}')
    with pytest.raises(history.HistoryDenied, match="original_api_restore_evidence_changed"):
        history.restore_evidence(tmp_path)


@pytest.mark.parametrize("tamper", ["bytes", "mode", "symlink", "hardlink"])
def test_artifact_changes_never_become_a_consumed_receipt(tmp_path, tamper):
    row = tmp_path / "original"
    row.mkdir(mode=0o700)
    private(row / "marker.json", {"unchanged": True})
    digest = history.tree(history.read_tree(row))
    proof = history.AuditedDisposition(digest, (), "a" * 64, "b" * 64, "c" * 64, "d" * 64)
    if tamper == "bytes":
        private(row / "marker.json", {"unchanged": False})
    elif tamper == "mode":
        (row / "marker.json").chmod(0o644)
    elif tamper == "symlink":
        (row / "extra.json").symlink_to(row / "marker.json")
    else:
        import os
        os.link(row / "marker.json", row / "extra.json")
    # Even a correctly shaped hash/report is inert without fresh native audit.
    with pytest.raises(history.HistoryDenied, match="fresh_native_audit_required"):
        proof.matches(row)


def test_audit_repeats_physical_app_observation_and_saved_report_is_inert(monkeypatch, tmp_path):
    complete = {"marker": b"{}"}
    monkeypatch.setattr(history, "load_bundle", lambda _: {40811330747: complete})
    calls = []
    class Boundary:
        changed = False
        def verify_claims(self, value):
            assert value is complete
            calls.append("claims")
            return {"spent": 3}
        def verify_original_api_restore(self):
            calls.append("restore")
            return {"generation": 345}
        def verify_current_applications(self):
            calls.append("apps")
            return {"generation": 2 if self.changed else 1}
        def verify_current_document(self, value):
            calls.append("document")
            return {"accepted": "current-native-proof"}
    boundary = Boundary()
    proof = history.audit(tmp_path, boundary)
    assert calls == ["claims", "restore", "apps", "document", "apps"]
    assert proof.report()["read_only_audit"] is True
    assert proof.report()["new_build_job_patch_claim_authorized"] is False
    assert "_authority" not in proof.report()
    boundary.verify_current_document = lambda _: setattr(boundary, "changed", True)
    with pytest.raises(history.HistoryDenied, match="current_application_changed"):
        history.audit(tmp_path, boundary)


@pytest.mark.parametrize(("method", "uri"), [
    ("PATCH", "https://run.googleapis.com/v2/" + apps.PARENT + "/services/ai-resume-parser"),
    ("POST", "https://cloudbuild.googleapis.com/v1/" + apps.PARENT + "/builds"),
    ("POST", "https://logging.googleapis.com/v2/entries:write"),
    ("GET", "https://example.invalid/native-proof"),
])
def test_audit_http_cannot_mutate_or_change_origin(method, uri):
    delegate = SimpleNamespace(request=lambda *a, **k: pytest.fail("delegate called"))
    with pytest.raises(history.HistoryDenied, match="audit_write_or_origin_refused"):
        native.ReadOnlyHttp(delegate).request(uri, method=method)


def serving_fixture(name=apps.CALLERS[0]):
    value = fixture.service(name)
    revision = value["latestReadyRevision"]
    short = revision.rsplit("/", 1)[-1]
    value["uid"] = apps.UIDS[name]
    value["traffic"] = [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": short,
                         "percent": 100, "tag": "reviewed-health"}]
    value["trafficStatuses"] = [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": short,
                                 "percent": 100, "tag": "reviewed-health"}]
    created = value["name"] + "/revisions/" + name + "-idle-created"
    value["latestCreatedRevision"] = created
    value["latestReadyRevision"] = value["name"] + "/revisions/" + name + "-stale-paused"
    revisions = {}
    for full in (revision, created):
        revisions[full] = {**copy.deepcopy(value["template"]), "name": full, "service": name,
            "uid": "01234567-0123-0123-0123-012345678901",
            "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}]}
    return value, revisions


@pytest.mark.parametrize("name", apps.CALLERS)
def test_actual_allocation_qualified_despite_stale_unserved_latest_ready(name):
    value, revisions = serving_fixture(name)
    clients = SimpleNamespace(revision=lambda name: revisions[name])
    actual = apps.serving(clients, value, fixture.APP, name, apps.UIDS[name])
    assert actual["name"].endswith(name + "-00001-owned")
    with pytest.raises(fixture.probe.NativeProofDenied):
        fixture.probe.validate_serving(clients, value, fixture.APP, name)


@pytest.mark.parametrize("tamper", ["alias_source", "alias_env", "created_image", "uid", "traffic_intent"])
def test_mixed_template_alias_or_wrong_caller_is_refused(tamper):
    name = apps.CALLERS[0]
    value, revisions = serving_fixture(name)
    if tamper == "uid":
        value["uid"] = apps.UIDS[apps.CALLERS[1]]
    elif tamper == "traffic_intent":
        value["traffic"][0]["tag"] = "not-the-physical-tag"
    elif tamper == "created_image":
        revisions[value["latestCreatedRevision"]]["containers"][0]["image"] = "another-image"
    else:
        alias = name + "-old-rollback"
        row = {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": alias, "tag": "old-rollback"}
        value["traffic"].append(copy.deepcopy(row))
        value["trafficStatuses"].append(copy.deepcopy(row))
        full = value["name"] + "/revisions/" + alias
        revisions[full] = copy.deepcopy(revisions[value["latestCreatedRevision"]])
        revisions[full]["name"] = full
        if tamper == "alias_source":
            revisions[full]["labels"] = {"hirewiz-source": "another-source"}
        else:
            revisions[full]["containers"][0]["env"].append({"name": "NEW_FLAG", "value": "true"})
    with pytest.raises(apps.ApplicationDenied):
        apps.serving(SimpleNamespace(revision=lambda full: revisions[full]), value, fixture.APP, name, apps.UIDS[name])


def test_homogeneous_aliases_keep_all_tag_type_percent_intents():
    name = apps.CALLERS[0]
    value, revisions = serving_fixture(name)
    alias = name + "-equivalent"
    full = value["name"] + "/revisions/" + alias
    revisions[full] = {**copy.deepcopy(revisions[value["latestCreatedRevision"]]), "name": full}
    row = {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": alias, "tag": "extra-health"}
    value["traffic"].append(copy.deepcopy(row))
    value["trafficStatuses"].append(copy.deepcopy(row))
    apps.serving(SimpleNamespace(revision=lambda full: revisions[full]), value, fixture.APP, name, apps.UIDS[name])
    moved = apps.phase_traffic(value, name, name + "-successor")
    assert {row["tag"] for row in moved} == {"extra-health", "reviewed-health"}
    assert {row["revision"] for row in moved} == {name + "-successor"}
    assert sorted(row.get("percent", 0) for row in moved) == [0, 100]
    assert all(row["type"] == "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION" for row in moved)
    latest = fixture.service(name)
    assert apps.phase_traffic(latest, name, name + "-successor") == latest["traffic"]


def provenance_fixture():
    pin = next(iter(apps.sources(ROOT).values()))
    tag = pin.image.split("@")[0] + ":" + pin.release
    build = {"id": pin.build_id, "projectId": apps.PROJECT, "status": "SUCCESS",
        "sourceProvenance": {"resolvedGitSource": {"revision": pin.release,
            "url": "https://github.com/" + apps.REPOSITORY + ".git"}},
        "steps": [{"name": "gcr.io/cloud-builders/docker", "args": ["build", "--tag", tag, "."]},
                  {"name": "gcr.io/cloud-builders/docker", "args": ["push", tag]}],
        "results": {"images": [{"name": tag, "digest": pin.image_digest}]}}
    run = {"headSha": pin.release, "workflowName": "CI", "status": "completed", "conclusion": "success",
           "jobs": [{"name": name, "status": "completed", "conclusion": "success"} for name in apps.CI_JOBS]}
    workflow = {"path": ".github/workflows/ci.yml", "head_sha": pin.release, "event": "push",
                "repository": {"full_name": apps.REPOSITORY}}
    return pin, build, run, workflow


@pytest.mark.parametrize("tamper", ["source", "digest", "build_id", "extra_step", "secret", "ci_job", "ci_source", "repo"])
def test_native_source_ci_build_attestation_is_independent_of_labels(tamper):
    pin, build, run, workflow = provenance_fixture()
    apps.verify_build(pin, build)
    apps.verify_ci(pin, run, workflow)
    if tamper == "source":
        build["sourceProvenance"]["resolvedGitSource"]["revision"] = "0" * 40
    elif tamper == "digest":
        build["results"]["images"][0]["digest"] = "sha256:" + "0" * 64
    elif tamper == "build_id":
        build["id"] = "0" * 36
    elif tamper == "extra_step":
        build["steps"].append(build["steps"][0])
    elif tamper == "secret":
        build["steps"][0]["secretEnv"] = ["not-permitted"]
    elif tamper == "ci_job":
        run["jobs"].pop()
    elif tamper == "ci_source":
        run["headSha"] = "0" * 40
    else:
        workflow["repository"]["full_name"] = "another/repository"
    with pytest.raises(apps.ApplicationDenied):
        apps.verify_build(pin, build)
        apps.verify_ci(pin, run, workflow)


def test_audit_failure_never_emits_heartbeat(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["document_refresh", "--mode", "audit-consumed-v2-history",
        "--consumed-history-bundle", str(tmp_path / "missing"), "--output-directory", str(tmp_path / "output")])
    monkeypatch.setattr(refresh, "emit_status", lambda _: pytest.fail("audit wrote a heartbeat"))
    monkeypatch.setattr(refresh, "infra", lambda _: (_ for _ in ()).throw(RuntimeError("synthetic-private-error")))
    assert refresh.run_cli() == 2
    assert "synthetic-private-error" not in capsys.readouterr().out


def test_mixed_context_fails_before_candidate_build(monkeypatch, tmp_path):
    closed = []
    clients = SimpleNamespace(close=lambda: closed.append("clients"))
    delegate = SimpleNamespace(close=lambda: closed.append("http"))
    class Boundary:
        def __init__(self, *args):
            pass
        def verify_current_applications(self):
            raise apps.ApplicationDenied("document_application_mixed_or_unknown_reachable_template")
    module = SimpleNamespace(history=SimpleNamespace(required=lambda _: False), NativeAudit=Boundary)
    monkeypatch.setattr(refresh, "infra", lambda name: module if name == "document_consumed_native"
                        else SimpleNamespace(Clients=lambda: clients))
    monkeypatch.setattr(refresh, "native_http", lambda: delegate)
    monkeypatch.setattr(refresh, "candidate", lambda *a, **k: pytest.fail("Build before current app proof"))
    with pytest.raises(apps.ApplicationDenied, match="mixed_or_unknown"):
        refresh.refresh(tmp_path, publish=True, now=fixture.probe.datetime.now(fixture.probe.UTC),
                        reviewed_application_context=True)
    assert closed == ["clients", "http"]


def qualified_controller_fixture(monkeypatch, *, lost_at=None):
    http = fixture.Http(lost_at=lost_at)
    accepted = fixture.fixture_probe(monkeypatch)
    for name in apps.CALLERS:
        row = http.services[name]
        short = row["latestReadyRevision"].rsplit("/", 1)[-1]
        row["traffic"] = [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": short,
                           "percent": 100, "tag": "reviewed-health"}]
        row["trafficStatuses"] = copy.deepcopy(row["traffic"])
    request = http.request
    def allocation_http(uri, **kwargs):
        result = request(uri, **kwargs)
        if kwargs["method"] == "PATCH":
            name = uri.split("/services/")[1].split("?", 1)[0]
            if name in apps.CALLERS:
                row = http.services[name]
                row["trafficStatuses"] = [{**entry, "revision": row["latestCreatedRevision"]}
                                          for entry in row["traffic"]]
                for operation in http.operations.values():
                    if operation["name"] == row["name"] and operation["generation"] == row["generation"]:
                        operation["trafficStatuses"] = copy.deepcopy(row["trafficStatuses"])
        return result
    http.request = allocation_http
    context = SimpleNamespace(serving=apps.serving, phase_traffic=apps.phase_traffic, observe=lambda: {})
    return http, accepted, context


def test_qualified_transition_keeps_tags_and_all_unrelated_environment(monkeypatch, tmp_path):
    http, _, context = qualified_controller_fixture(monkeypatch)
    before = copy.deepcopy(http.services)
    result = fixture.transition.transition(fixture.NEW, {"nonce": fixture.NONCE, "build_id": "01234567-0123-0123-0123-012345678901"},
        tmp_path / "native", tmp_path / "transition", http=http, probe=fixture.probe, application_context=context)
    assert result["admission_restored"] is True
    assert len(http.writes) == 10
    for name in apps.CALLERS:
        current = http.services[name]
        assert current["traffic"][0]["tag"] == "reviewed-health"
        assert current["traffic"][0]["type"] == "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION"
        assert current["traffic"][0]["percent"] == 100
        prior = fixture.transition.environment(before[name]["template"])
        after = fixture.transition.environment(current["template"])
        assert {k: v for k, v in prior.items() if k not in fixture.transition.PINS} == {
            k: v for k, v in after.items() if k not in fixture.transition.PINS}
    assert not (tmp_path / "transition" / "claim.started.json").exists()


def test_unknown_qualified_patch_stays_one_send_and_cannot_be_replayed(monkeypatch, tmp_path):
    http, _, context = qualified_controller_fixture(monkeypatch, lost_at=1)
    target = tmp_path / "transition"
    with pytest.raises(fixture.transition.TransitionDenied, match="outcome_unknown"):
        fixture.transition.transition(fixture.NEW, {"nonce": fixture.NONCE, "build_id": "01234567-0123-0123-0123-012345678901"},
            tmp_path / "native", target, http=http, probe=fixture.probe, application_context=context)
    assert len(http.writes) == 1
    assert (target / ("close." + apps.CALLERS[0] + ".started.json")).exists()
    assert not (target / ("close." + apps.CALLERS[0] + ".acknowledged.json")).exists()
    with pytest.raises(fixture.transition.TransitionDenied):
        fixture.transition.transition(fixture.NEW, {"nonce": fixture.NONCE, "build_id": "01234567-0123-0123-0123-012345678901"},
            tmp_path / "native", target, http=http, probe=fixture.probe, application_context=context)
    assert len(http.writes) == 1


@pytest.mark.parametrize("tamper", ["source", "branch", "digest", "run", "expired", "conclusion", "workflow"])
def test_retrieval_requires_native_artifact_and_source_identity(tamper):
    pin = history.ARTIFACTS[0]
    value = {"id": pin.artifact, "name": pin.name, "expired": False, "digest": "sha256:" + pin.zip_sha256,
             "size_in_bytes": 10000, "workflow_run": {"id": pin.run, "head_sha": pin.source, "head_branch": "main"}}
    run = {"id": pin.run, "head_sha": pin.source, "head_branch": "main", "status": "completed",
           "conclusion": "failure", "path": ".github/workflows/document-refresh.yml",
           "repository": {"full_name": retrieval.REPOSITORY}}
    retrieval.metadata(pin, value, run)
    if tamper == "source":
        value["workflow_run"]["head_sha"] = "0" * 40
    elif tamper == "branch":
        value["workflow_run"]["head_branch"] = "unreviewed"
    elif tamper == "digest":
        value["digest"] = "sha256:" + "0" * 64
    elif tamper == "run":
        run["id"] += 1
    elif tamper == "expired":
        value["expired"] = True
    elif tamper == "conclusion":
        run["conclusion"] = "success"
    else:
        run["path"] = ".github/workflows/another.yml"
    with pytest.raises(history.HistoryDenied, match="exact_artifact_metadata_required"):
        retrieval.metadata(pin, value, run)


@pytest.mark.parametrize("path", ["../escape.json", "/absolute.json", "nested\\escape.json", "not-json.txt"])
def test_archive_extraction_refuses_paths_even_with_matching_archive_hash(tmp_path, path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(path, b"{}")
    raw = stream.getvalue()
    pin = replace(history.ARTIFACTS[0], zip_sha256=history.sha(raw))
    with pytest.raises(history.HistoryDenied, match="archive_(path|file)_refused"):
        retrieval.unpack(raw, tmp_path / "artifact", pin)
    assert not (tmp_path / "artifact").exists()


def test_no_old_history_row_means_no_five_artifact_dependency(tmp_path):
    old = tmp_path / "prior"
    old.mkdir(mode=0o700)
    private(old / "new-complete/result.json", {"status": "document_refresh_serving", "admission_restored": True})
    assert retrieval.retrieve(tmp_path / "not-created", old,
                              get=lambda *a, **k: pytest.fail("unneeded historical download")) is False
    assert not (tmp_path / "not-created").exists()


def test_current_document_uses_fresh_current_acceptance_after_old_job_retirement(monkeypatch):
    worker = fixture.service(fixture.transition.WORKER)
    worker["template"]["containers"][0]["image"] = fixture.NEW
    accepted = SimpleNamespace()
    from dataclasses import dataclass
    @dataclass
    class Acceptance:
        image: str
        job_name: str
    accepted = Acceptance(fixture.NEW, "new-native-job")
    calls = []
    class Probe:
        @staticmethod
        def verify_current_successor(image, actual):
            assert image == fixture.NEW and image != history.IMAGE and actual == worker
            calls.append("fresh-current-native-proof")
            return accepted
        @staticmethod
        def policy_for_serving(*args):
            return fixture.probe.POLICY
        @staticmethod
        def verify_caller_pins(clients, image, **kwargs):
            assert image == fixture.NEW and "caller_serving_validator" in kwargs
            calls.append("current-physical-pins")
    boundary = native.NativeAudit.__new__(native.NativeAudit)
    boundary.clients = SimpleNamespace(preflight=lambda: {"service": worker}, service=lambda _: worker)
    boundary.applications = SimpleNamespace(serving=apps.serving)
    boundary.probe = Probe
    result = boundary.verify_current_document(None)
    assert result["current_native_acceptance"] == asdict(accepted)
    assert calls == ["fresh-current-native-proof", "current-physical-pins"]


@pytest.mark.parametrize("code", ["cloudbuild_metadata_read_denied", "run_metadata_read_denied"])
def test_read_denied_reports_fixed_scope_without_changing_permissions(code):
    boundary = native.NativeAudit.__new__(native.NativeAudit)
    boundary.http = SimpleNamespace(request=lambda *a, **k: (SimpleNamespace(status=403), b'{"untrusted":"body"}'))
    uri = ("https://cloudbuild.googleapis.com/v1/" + apps.PARENT + "/builds/owned"
           if code.startswith("cloudbuild") else "https://run.googleapis.com/v2/" + apps.PARENT + "/operations/owned")
    with pytest.raises(history.HistoryDenied, match=code):
        boundary.get_json(uri)


@pytest.mark.parametrize("status", [200, 404])
def test_missing_or_changed_spent_claim_is_never_recreated(tmp_path, status):
    original = tmp_path / "40811330747/source-transition"
    ack = {"name": native.transition.RESCUE_CLAIM_OBJECT,
           "bucket": native.transition.PROJECT + "_cloudbuild", "generation": "1793227251677263"}
    private(original / "rescue-claim.acknowledged.json", ack)
    private(original / "rescue-claim.verified.json", {"acknowledgment": ack,
        "body_sha256": "2f1cb2ee3ce7b66a229a8ce1b7b122077bfb4cab1a7797994bafa9a10f34b706"})
    calls = []
    def request(uri, **kwargs):
        calls.append((uri, kwargs["method"]))
        return SimpleNamespace(status=status), b"{}"
    boundary = native.NativeAudit.__new__(native.NativeAudit)
    boundary.bundle = tmp_path
    boundary.http = native.ReadOnlyHttp(SimpleNamespace(request=request))
    with pytest.raises(native.transition.TransitionDenied, match="original_claim_changed"):
        boundary.verify_claims({"native-proof/acceptance.json": b"{}"})
    assert len(calls) == 1 and calls[0][1] == "GET"


@pytest.mark.parametrize("tamper", ["none", "not_done", "request_etag", "response_template", "response_traffic", "wrong_revision"])
def test_original_restore_requires_completed_native_operation_and_immutable_revision(monkeypatch, tamper):
    name = apps.CALLERS[0]
    resource = apps.PARENT + "/services/" + name
    short = name + "-m25-synthetic-restore"
    full = resource + "/revisions/" + short
    template = {"revision": short, "serviceAccount": apps.ACCOUNTS[name],
                "containers": [{"image": fixture.APP, "env": [{"name": "PRESERVED", "value": "true"}]}]}
    traffic = [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": name + "-original", "percent": 100}]
    pin = SimpleNamespace(name=name, uid=apps.UIDS[name], template_keys=tuple(k for k in template if k != "revision"))
    plan = SimpleNamespace(services=[pin], revision=lambda *args: short)
    operation_name = apps.PARENT + "/operations/synthetic-restore"
    body = {"name": resource, "etag": "closed344", "template": template, "traffic": traffic}
    records = {"service.ai-resume-parser.restore.started.json": {"request_sha256": history.sha(history.canonical(body))},
               "service.ai-resume-parser.restore.acknowledged.json": {"operation": operation_name},
               "service.ai-resume-parser.restore.checked.json": {"revision": full}}
    closed = {"name": native.readback.partial.MAINTENANCE_CLOSE_OPERATION, "done": True,
              "response": {"name": resource, "uid": pin.uid, "generation": "344", "etag": "closed344"}}
    operation = {"name": operation_name, "done": True, "response": {"name": resource, "uid": pin.uid,
        "generation": "345", "latestCreatedRevision": full, "template": copy.deepcopy(template), "traffic": traffic}}
    revision = {**copy.deepcopy(template), "name": full, "service": name, "uid": pin.uid,
                "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}]}
    revision.pop("revision")
    monkeypatch.setattr(history, "restore_evidence", lambda _: (plan, records))
    monkeypatch.setattr(history.maintenance, "desired", lambda *args: (template, traffic))
    if tamper == "not_done":
        operation["done"] = False
    elif tamper == "request_etag":
        closed["response"]["etag"] = "different-closed-state"
    elif tamper == "response_template":
        operation["response"]["template"]["containers"][0]["env"][0]["value"] = "false"
    elif tamper == "response_traffic":
        operation["response"]["traffic"] = []
    elif tamper == "wrong_revision":
        revision["containers"][0]["image"] = "unknown-image"
    boundary = native.NativeAudit.__new__(native.NativeAudit)
    boundary.root = ROOT
    boundary.get_json = lambda uri: closed if uri.endswith(native.readback.partial.MAINTENANCE_CLOSE_OPERATION) else operation
    boundary.clients = SimpleNamespace(revision=lambda _: revision)
    if tamper == "none":
        assert boundary.verify_original_api_restore()["generation"] == 345
    else:
        with pytest.raises(history.HistoryDenied, match="historical_api_restore_(operation|revision)_changed"):
            boundary.verify_original_api_restore()
