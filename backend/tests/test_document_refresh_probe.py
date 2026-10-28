"""Synthetic native-shaped controls only: no credentials, SDK, SQL or Docker."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from infra.gcp import document_refresh_probe as p
from infra.gcp import document_renderer_profiles as profile
from infra.gcp import document_worker as worker

from scripts import document_refresh as refresh

parser = p.load_source("hirewiz_document_refresh_receipt_test", p.HERE / "document_refresh_receipt.txt")

IMAGE = p.REPOSITORY + "@sha256:" + "a" * 64
NEXT = p.REPOSITORY + "@sha256:" + "b" * 64
NONCE = "0123456789abcdef0123456789abcdef"
JOB_UID = "01234567-1234-1234-1234-0123456789ab"
EX_UID = "01234567-1234-1234-1234-0123456789ac"
BUILD_ID = "01234567-1234-1234-1234-0123456789ad"
NOW = datetime.now(UTC)


def build():
    return {"version": 1, "kind": "native_cloud_build", "nonce": NONCE, "build_id": BUILD_ID,
        "build_resource": p.PARENT + "/builds/" + BUILD_ID, "immutable_image": IMAGE,
        "source_archive_sha256": "c" * 64, "refresh_dockerfile_sha256": "d" * 64,
        "source_sha256": {"fixture": "e" * 64}, "local_native_proof_sha256": "f" * 64}


def diag(code=0, processor="clean", validation="complete"):
    return {"launcher": "returned", "cleanup": "returned", "processor": processor,
        "validation": validation, "failure_phase": None if code == 0 else "processor",
        "launcher_code": code, "cleanup_code": 0, "launcher_elapsed_ms": 100,
        "cleanup_elapsed_ms": 10}


def payload(image=IMAGE, execution=None):
    scan = {"engine": "ClamAV", "version": "1.4.3", "definitions": "28155/" + (NOW - timedelta(hours=1)).isoformat()}
    rows = []
    for name in parser.NAMES:
        kind = {"clean_tex": "tex", "clean_texzip": "texzip", "eicar_tex_source": "tex",
                "eicar_texzip_member": "texzip"}.get(name,
                    "docx" if name == "clean_docx" or name in parser.NAMES[-2:] else "pdf")
        binding = {"version": 1, "sha256": "b" * 64, "size_bytes": 100, "source_format": kind}
        if name == "eicar_direct_detection":
            binding = {"sha256": parser.EICAR_SHA, "size_bytes": 68}
        if name in {"eicar_exact_file", "eicar_tex_source"}:
            binding.update(sha256=parser.EICAR_SHA, size_bytes=68)
        if name in parser.NAMES[-2:]:
            binding.update(operation="docx_layout", candidate_sha256="c" * 64,
                           candidate_size_bytes=100, edits_sha256="d" * 64)
        row = {"case": name, "passed": True, "attempted": True, "binding": binding,
               "binding_match": True, "diagnostic": diag(), "cleanup_passed": True}
        if name in parser.NAMES[:2]:
            row.update(scan=copy.deepcopy(scan), python_skill_present=True)
        elif name in {"clean_tex", "clean_texzip"}:
            row.update(scan=copy.deepcopy(scan), native_source_empty=True)
        elif name in {"eicar_exact_file", "eicar_tex_source", "eicar_texzip_member"}:
            row.update(worker_refused=True, diagnostic=diag(1, "refused"))
        elif name == "eicar_direct_detection":
            row.update(exact_file_unchanged=True, coherent_eicar_detection=True,
                       diagnostic=diag(0, "not_validated", "not_started"))
        elif name == "styled_multipage_fit":
            row.update(renderer_binding_match=True, candidate_scan_equal=True, scan=copy.deepcopy(scan), layout={
                "validator": "hirewiz-docx-layout-v1", "pages": 2, "unchanged_links": 1,
                "outside_changed_regions_pixel_delta": 0, "before_pdf_sha256": "e" * 64,
                "after_pdf_sha256": "f" * 64, "rendered_fonts_sha256": "a" * 64,
                "changed_paragraphs": 1, "protected_glyphs": 10, "unchanged_graphics": 2,
                "max_protected_geometry_delta_points": 0.0})
        else:
            row.update(reason="hyperlink_changed", matching_unit_or_none=True, diagnostic=diag(1, "refused"))
        rows.append(row)
    return {"event": "hirewiz_document_native_required_v2", "version": 2,
        "execution_name": execution or "hirewiz-doc-six-" + NONCE[:12] + "-abcde",
        "worker_image": image, "policy_sha256": p.POLICY, "outer_runtime_user_verified": True,
        "activation_flags": {key: False for key in parser.ACTIVATION},
        "identity_preflight": {"passed": True, "cleanup_passed": True,
            "diagnostic": diag(0, "not_validated", "not_started"), "runtime_hashes": p.runtime_hashes(),
            "architecture": "x86_64", "runtime_uid": 0, "runtime_gid": 0, "readonly_font_manifest": True,
            "font_manifest_process_writable": False, "renderer_version": p.RENDERER,
            "font_manifest_sha256": p.FONT},
        "cases": rows, "all_required_passed": True, "failure_stage": None, "elapsed_ms": 100}


def historical_payload():
    value = payload()
    value.update(event="hirewiz_document_native_six_case_v1", version=1, policy_sha256=p.LEGACY_POLICY)
    value["all_six_passed"] = value.pop("all_required_passed")
    value["cases"] = [row for row in value["cases"] if row["case"] not in {
        "clean_tex", "clean_texzip", "eicar_tex_source", "eicar_texzip_member"}]
    value["identity_preflight"].update(runtime_hashes=p.runtime_hashes(historical_v1=True),
        renderer_version=p.LEGACY_RENDERER, font_manifest_sha256=p.LEGACY_FONT)
    return value


def job(image=IMAGE, nonce=NONCE):
    value = p.desired(image, nonce)
    return {**value, "uid": JOB_UID, "etag": "opaque-etag", "generation": "1", "observedGeneration": "1",
        "terminalCondition": {"state": "CONDITION_SUCCEEDED"}, "createTime": (NOW - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")}


def execution(j=None):
    j = j or job()
    return {"name": j["name"] + "/executions/" + j["name"].rsplit("/", 1)[-1] + "-abcde",
        "uid": EX_UID, "job": j["name"], "taskCount": 1, "succeededCount": 1,
        "completionTime": (NOW - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "template": copy.deepcopy(j["template"]["template"])}


def completed(j=None):
    j = j or job()
    ex = execution(j)
    j.update(executionCount="1", latestCreatedExecution={"name": ex["name"]})
    return j, ex


def event(j, ex, data):
    return {"resource": {"type": "cloud_run_job", "labels": {"project_id": p.PROJECT,
        "location": p.REGION, "job_name": j["name"].rsplit("/", 1)[-1]}},
        "labels": {"run.googleapis.com/execution_name": ex["name"].rsplit("/", 1)[-1]},
        "logName": "projects/" + p.PROJECT + "/logs/run.googleapis.com%2Fstdout",
        "timestamp": (NOW - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ"), "jsonPayload": data}


class HttpError(Exception):
    def __init__(self, status_code):
        self.status_code = status_code


class Fake:
    errors = SimpleNamespace(HttpError=HttpError)

    def __init__(self):
        self.j, self.ex, self.created, self.ran = job(), None, False, False
        self.calls = {key: 0 for key in ("create", "start", "delete", "close", "preflight")}
        self.fail_create = self.fail_run = self.fail_delete = False
        self.pages = None

    def close(self):
        self.calls["close"] += 1

    def preflight(self):
        self.calls["preflight"] += 1
        return snapshot()

    def job(self, name):
        assert name == self.j["name"]
        if not self.created:
            raise HttpError(404)
        return copy.deepcopy(self.j)

    def create(self, body):
        assert body == p.desired(IMAGE, NONCE)
        self.calls["create"] += 1
        if self.fail_create:
            raise RuntimeError("secret-shaped forbidden provider response")
        self.created = True
        return {"name": p.PARENT + "/operations/create"}

    def start(self, j):
        assert j["uid"] == JOB_UID
        self.calls["start"] += 1
        if self.fail_run:
            raise RuntimeError("secret-shaped forbidden provider response")
        self.ran = True
        self.j, self.ex = completed(self.j)
        return {"name": p.PARENT + "/operations/run"}

    def operation(self, name):
        if name.endswith("/create"):
            data = {"@type": "type.googleapis.com/google.cloud.run.v2.Job", "name": self.j["name"], "uid": JOB_UID}
        elif name.endswith("/run"):
            data = {"@type": "type.googleapis.com/google.cloud.run.v2.Execution", "name": self.ex["name"], "uid": EX_UID}
        else:
            assert name.endswith("/delete")
            data = {"@type": "type.googleapis.com/google.protobuf.Empty"}
        return {"name": name, "done": True, "response": data}

    def execution(self, name):
        assert name == self.ex["name"]
        if not self.created:
            raise HttpError(404)
        return copy.deepcopy(self.ex)

    def entries(self, query, token):
        assert 'jsonPayload.event="hirewiz_document_native_required_v2"' in query
        if self.pages is not None:
            return self.pages[token]
        return {"entries": [event(self.j, self.ex, payload(execution=self.ex["name"].rsplit("/", 1)[-1]))]}

    def delete(self, j):
        self.calls["delete"] += 1
        if self.fail_delete:
            raise RuntimeError("secret-shaped forbidden provider response")
        self.created = False
        return {"name": p.PARENT + "/operations/delete"}


@pytest.fixture
def native(monkeypatch):
    fake = Fake()
    monkeypatch.setattr(p, "Clients", lambda: fake)
    monkeypatch.setattr(p, "build_module", lambda: SimpleNamespace(verify_native_build=lambda b: dict(b),
        verify_current_build=lambda *_: build()))
    return fake


def test_payload_is_exact_accepted_source_with_only_image_literal_substitution():
    raw = (p.HERE / "document_refresh_payload.txt").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == p.PAYLOAD_SHA
    assert hashlib.sha256((p.HERE / "document_refresh_receipt.txt").read_bytes()).hexdigest() == p.PARSER_SHA
    tree = ast.parse(p.rendered_payload(IMAGE))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "IMMUTABLE_IMAGE" for t in node.targets):
            node.value = ast.Constant(value=p.BASE_IMAGE)
    assert ast.dump(tree, include_attributes=False) == ast.dump(ast.parse(raw), include_attributes=False)


@pytest.mark.parametrize("image", [p.BASE_IMAGE, "latest", IMAGE + "?token=bad", IMAGE.replace("us-central1", "europe-west1")])
def test_no_image_range_or_fixed_parent_acceptance(image):
    with pytest.raises(p.NativeProofDenied):
        p.rendered_payload(image)


def test_finite_parser_validates_all_required_isolation_and_definition_age():
    data = payload()
    facts = p.validate_payload(data, IMAGE, execution()["name"], NOW)
    assert facts["payload_sha256"] == hashlib.sha256(p.canonical(data)).hexdigest()
    assert datetime.fromisoformat(facts["definition_expires_at"]) == NOW - timedelta(hours=1) + timedelta(days=3)


@pytest.mark.parametrize("change", ["old_protocol", "six_cases", "native_facts", "native_format", "native_scan", "eicar_source_hash"])
def test_native_v2_requires_exact_native_admission_and_antivirus_evidence(change):
    data = payload()
    if change == "old_protocol":
        data.update(event="hirewiz_document_native_six_case_v1", version=1)
        data["all_six_passed"] = data.pop("all_required_passed")
    elif change == "six_cases":
        data["cases"] = [row for row in data["cases"] if row["case"] not in {
            "clean_tex", "clean_texzip", "eicar_tex_source", "eicar_texzip_member"}]
    else:
        row = next(row for row in data["cases"] if row["case"] == (
            "eicar_tex_source" if change == "eicar_source_hash" else "clean_tex"))
        if change == "native_facts":
            row["native_source_empty"] = False
        elif change == "native_format":
            row["binding"]["source_format"] = "pdf"
        elif change == "native_scan":
            row.pop("scan")
        else:
            row["binding"]["sha256"] = "0" * 64
    with pytest.raises(p.NativeProofDenied):
        p.validate_payload(data, IMAGE, execution()["name"], NOW)


def test_historical_v1_receipt_is_readable_only_when_explicitly_selected():
    data = historical_payload()
    assert p.validate_payload(data, IMAGE, execution()["name"], NOW, historical_v1=True)
    with pytest.raises(p.NativeProofDenied):
        p.validate_payload(data, IMAGE, execution()["name"], NOW)
    data["policy_sha256"] = p.POLICY
    with pytest.raises(p.NativeProofDenied):
        p.validate_payload(data, IMAGE, execution()["name"], NOW, historical_v1=True)


def test_historical_contract_cannot_create_a_new_journal(tmp_path):
    target = tmp_path / "legacy"
    with pytest.raises(p.NativeProofDenied, match="historical_create_refused"):
        p.Journal(target, IMAGE, build(), create=True, historical_v1=True)
    assert not target.exists()


@pytest.mark.parametrize("change", [None, "policy", "duplicate", "image"])
def test_serving_profile_requires_exact_observed_image_and_known_policy(change):
    value = service()
    row = next(row for row in value["template"]["containers"][0]["env"]
               if row["name"] == "DOCUMENT_WORKER_POLICY_SHA256")
    row["value"] = p.LEGACY_POLICY
    if change == "policy":
        row["value"] = "0" * 64
    elif change == "duplicate":
        value["template"]["containers"][0]["env"].append(copy.deepcopy(row))
    elif change == "image":
        value["template"]["containers"][0]["image"] = NEXT
    if change:
        with pytest.raises(p.NativeProofDenied, match="serving_policy_unverified"):
            p.historical_v1_for_serving(value, IMAGE)
    else:
        assert p.historical_v1_for_serving(value, IMAGE) is True


@pytest.mark.parametrize("change", ["case", "link", "pixels", "cleanup", "source", "font", "renderer", "writable", "execution", "flags", "expired", "future", "scan_disagreement", "boolean"])
def test_receipt_cannot_turn_missing_or_mismatched_evidence_into_acceptance(change):
    data = payload()
    if change == "case":
        data["cases"][4] = {"case": parser.NAMES[4], "passed": False, "attempted": False}
    elif change == "link":
        data["cases"][parser.NAMES.index("styled_multipage_fit")]["layout"]["unchanged_links"] = 0
    elif change == "pixels":
        data["cases"][parser.NAMES.index("styled_multipage_fit")]["layout"]["outside_changed_regions_pixel_delta"] = 1
    elif change == "cleanup":
        data["cases"][0]["diagnostic"]["cleanup_code"] = 1
    elif change == "source":
        data["identity_preflight"]["runtime_hashes"]["/opt/document/service.py"] = "0" * 64
    elif change in ("font", "renderer", "writable"):
        key, value = {"font": ("font_manifest_sha256", "0" * 64), "renderer": ("renderer_version", "LibreOffice 26.0.0"),
                      "writable": ("font_manifest_process_writable", True)}[change]
        data["identity_preflight"][key] = value
    elif change == "execution":
        data["execution_name"] = "hirewiz-doc-six-ffffffffffff-other"
    elif change == "flags":
        data["activation_flags"]["checkout_enabled"] = True
    elif change in ("expired", "future", "scan_disagreement"):
        issued = NOW - timedelta(days=3) if change == "expired" else NOW + timedelta(hours=1)
        for row in data["cases"]:
            if "scan" in row:
                row["scan"]["definitions"] = "28155/" + issued.isoformat()
                if change == "scan_disagreement":
                    break
    else:
        data["cases"][0]["binding"]["size_bytes"] = True
    with pytest.raises(p.NativeProofDenied):
        p.validate_payload(data, IMAGE, execution()["name"], NOW)


def test_readonly_verification_never_adopts_saved_acceptance(native, tmp_path):
    target = tmp_path / "native"
    result = p.run_successor(IMAGE, build(), target)
    assert result.job_uid == JOB_UID and result.execution_uid == EX_UID
    assert native.calls["create"] == native.calls["start"] == 1
    (target / "acceptance.json").write_text('{"all_required_passed":true}')
    assert p.verify_successor(IMAGE, build(), target) == result
    assert native.calls["create"] == native.calls["start"] == 1
    native.pages = {None: {"entries": []}}
    with pytest.raises(p.NativeProofDenied, match="event_missing"):
        p.verify_successor(IMAGE, build(), target)


@pytest.mark.parametrize("phase", ["create", "run"])
def test_unknown_external_mutation_is_once_only_and_redacted(native, tmp_path, phase):
    setattr(native, "fail_" + phase, True)
    target = tmp_path / "native"
    with pytest.raises(p.NativeProofDenied, match="outcome_unknown") as caught:
        p.run_successor(IMAGE, build(), target)
    assert "secret-shaped" not in str(caught.value)
    with pytest.raises(p.NativeProofDenied, match="existing_journal"):
        p.run_successor(IMAGE, build(), target)
    with pytest.raises(p.NativeProofDenied):
        p.verify_successor(IMAGE, build(), target)
    assert native.calls["create"] == 1
    assert native.calls["start"] == (1 if phase == "run" else 0)
    assert (target / ("run.started.json" if phase == "run" else "create.started.json")).exists()


def test_existing_native_name_prevents_any_new_mutation(native, tmp_path):
    native.created = True
    with pytest.raises(p.NativeProofDenied, match="job_exists"):
        p.run_successor(IMAGE, build(), tmp_path / "native")
    assert native.calls["create"] == native.calls["start"] == 0


def test_build_failure_prevents_private_ownership_or_native_client(monkeypatch, tmp_path):
    def reject(_):
        raise ValueError("private raw provider failure")
    monkeypatch.setattr(p, "build_module", lambda: SimpleNamespace(verify_native_build=reject))
    monkeypatch.setattr(p, "Clients", lambda: pytest.fail("native auth must not run"))
    with pytest.raises(p.NativeProofDenied, match="outcome_unknown"):
        p.run_successor(IMAGE, build(), tmp_path / "native")
    assert not (tmp_path / "native").exists()


@pytest.mark.parametrize("field", ["create", "run"])
def test_saved_once_marker_is_bound_to_original_job_and_body(native, tmp_path, field):
    target = tmp_path / "native"
    p.run_successor(IMAGE, build(), target)
    marker = target / (field + ".started.json")
    data = __import__("json").loads(marker.read_text())
    data["body_sha256" if field == "create" else "job_uid"] = "0" * 64
    marker.write_bytes(p.canonical(data))
    with pytest.raises(p.NativeProofDenied, match="binding_changed"):
        p.verify_successor(IMAGE, build(), target)
    assert native.calls["start"] == 1


@pytest.mark.parametrize("change", ["sandbox", "env", "retry", "uid", "bool_count", "execution_count", "task"])
def test_native_job_execution_template_uid_and_singleton_tampering_refused(change):
    j, ex = completed()
    if change == "sandbox":
        j["template"]["template"]["containers"][0]["sandboxLauncher"] = False
    elif change == "env":
        j["template"]["template"]["containers"][0]["env"].append({"name": "SECRET", "value": "bad"})
    elif change == "retry":
        ex["template"]["maxRetries"] = 1
    elif change == "uid":
        ex["uid"] = "not-uid"
    elif change == "bool_count":
        ex["succeededCount"] = True
    elif change == "execution_count":
        j["executionCount"] = 2
    else:
        ex["taskCount"] = 2
    with pytest.raises(p.NativeProofDenied):
        p.validate_job(j, IMAGE, NONCE)
        p.validate_execution(ex, j, IMAGE, NONCE)


def test_complete_log_pagination_and_duplicate_event_refusal(native):
    j, ex = completed()
    data = event(j, ex, payload())
    native.pages = {None: {"entries": [], "nextPageToken": "second"}, "second": {"entries": [data]}}
    assert p.native_payload(native, j, ex) == data["jsonPayload"]
    native.pages[None]["entries"] = [data]
    with pytest.raises(p.NativeProofDenied, match="uniqueness"):
        p.native_payload(native, j, ex)
    native.pages = {None: {"entries": [], "nextPageToken": "cycle"}, "cycle": {"entries": [], "nextPageToken": "cycle"}}
    with pytest.raises(p.NativeProofDenied, match="pagination"):
        p.native_payload(native, j, ex)


def runtime():
    return [{"projectId": p.PROJECT, "projectNumber": p.NUMBER},
        {"uid": p.SERVICE_UID, "template": {"serviceAccount": p.ACCOUNT}},
        {"etag": "opaque", "bindings": [{"role": "roles/run.invoker", "members": list(p.CALLERS)}]},
        {"projectId": p.PROJECT, "email": p.ACCOUNT, "uniqueId": p.ACCOUNT_UID}, {}, {}, {}]


@pytest.mark.parametrize("change", [None, "public", "extra", "conditional", "key", "project", "account"])
def test_native_runtime_requires_exact_current_invokers_and_no_broad_authority(change):
    rows = runtime()
    if change == "public":
        rows[2]["bindings"][0]["members"][0] = "allUsers"
    elif change == "extra":
        rows[2]["bindings"][0]["members"].append("serviceAccount:foreign@example.com")
    elif change == "conditional":
        rows[2]["bindings"][0]["condition"] = {"expression": "true"}
    elif change == "key":
        rows[4] = {"keys": [{"name": "key"}]}
    elif change == "project":
        rows[6] = {"bindings": [{"role": "roles/editor", "members": [p.CALLERS[0]]}]}
    elif change == "account":
        rows[3]["uniqueId"] = "1" * 21
    if change:
        with pytest.raises(p.NativeProofDenied):
            p.validate_runtime(*rows)
    else:
        p.validate_runtime(*rows)


def service(image=IMAGE, name="hirewiz-document-inspector", uid=p.SERVICE_UID):
    resource = p.PARENT + "/services/" + name
    revision = resource + "/revisions/" + name + "-00001-abc"
    expected = worker.render(image, "1" * 40)["service"] if name == "hirewiz-document-inspector" else {"template": {"containers": [{"image": image}]}}
    return {**expected, "name": resource, "uid": uid, "generation": "2", "observedGeneration": "2",
        "terminalCondition": {"state": "CONDITION_SUCCEEDED"}, "latestReadyRevision": revision,
        "latestCreatedRevision": revision,
        "trafficStatuses": [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "revision": revision, "percent": 100}],
        "labels": {"hirewiz-refresh-build": BUILD_ID, "hirewiz-refresh-nonce": NONCE,
                   "hirewiz-refresh-job": "hirewiz-doc-six-" + NONCE[:12]}}


def revision(s):
    return {**copy.deepcopy(s["template"]), "name": s["latestReadyRevision"], "service": s["name"], "uid": EX_UID,
        "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}],
        "containers": copy.deepcopy(s["template"]["containers"])}


@pytest.mark.parametrize("change", [None, "staged", "traffic", "ready", "revision", "image", "generation"])
def test_current_proof_requires_native_ready_serving_revision_not_merely_template(change):
    s = service()
    rev = revision(s)
    if change == "staged":
        s["latestCreatedRevision"] += "-new"
    elif change == "traffic":
        s["trafficStatuses"][0]["percent"] = 0
    elif change == "ready":
        rev["conditions"][0]["state"] = "CONDITION_FAILED"
    elif change == "revision":
        rev["name"] += "-other"
    elif change == "image":
        rev["containers"][0]["image"] = NEXT
    elif change == "generation":
        s["observedGeneration"] = "1"
    clients = SimpleNamespace(revision=lambda _: rev)
    if change:
        with pytest.raises(p.NativeProofDenied):
            p.validate_serving(clients, s, IMAGE, "hirewiz-document-inspector", p.SERVICE_UID)
    else:
        assert p.validate_serving(clients, s, IMAGE, "hirewiz-document-inspector", p.SERVICE_UID) == rev


def test_current_labels_cannot_adopt_different_actual_native_service(native):
    native.created, native.ran = True, True
    native.j, native.ex = completed()
    s = service()
    native.service = lambda _: copy.deepcopy(s)
    native.revision = lambda _: revision(s)
    assert p.verify_current_successor(IMAGE, s).image == IMAGE
    caller_metadata = copy.deepcopy(s)
    caller_metadata["labels"]["hirewiz-refresh-nonce"] = "f" * 32
    caller_metadata["labels"]["hirewiz-refresh-job"] = "hirewiz-doc-six-" + "f" * 12
    with pytest.raises(p.NativeProofDenied, match="service_changed"):
        p.verify_current_successor(IMAGE, caller_metadata)


def test_expired_v2_current_proof_is_retirement_only(native, monkeypatch):
    native.created, native.ran = True, True
    native.j, native.ex = completed()
    s = service()
    native.service = lambda _: copy.deepcopy(s)
    native.revision = lambda _: revision(s)
    old = payload(execution=native.ex["name"].rsplit("/", 1)[-1])
    for row in old["cases"]:
        if "scan" in row:
            row["scan"]["definitions"] = "28155/" + (NOW - timedelta(days=4)).isoformat()
    native.pages = {None: {"entries": [event(native.j, native.ex, old)]}}
    with pytest.raises(p.NativeProofDenied, match="definitions_expired"):
        p.verify_current_successor(IMAGE, s)
    monkeypatch.setattr(p, "acceptance", lambda *args, **kwargs:
                        pytest.fail("expired V2 must never construct serving acceptance"))
    assert p.verify_expired_current_v2_for_retirement(IMAGE, s) is None
    assert native.calls["create"] == native.calls["start"] == native.calls["delete"] == 0
    s["labels"]["hirewiz-refresh-job"] = "hirewiz-doc-six-foreign"
    with pytest.raises(p.NativeProofDenied):
        p.verify_expired_current_v2_for_retirement(IMAGE, s)


def test_retirement_only_refuses_fresh_v2(native):
    native.created, native.ran = True, True
    native.j, native.ex = completed()
    s = service()
    native.service = lambda _: copy.deepcopy(s)
    native.revision = lambda _: revision(s)
    with pytest.raises(p.NativeProofDenied, match="requires_expired_v2"):
        p.verify_expired_current_v2_for_retirement(IMAGE, s)


def test_readonly_current_v1_route_binds_frozen_job_receipt_and_build_profile(native, monkeypatch):
    native.created, native.ran = True, True
    old_job = job()
    old_job["template"] = p.desired(IMAGE, NONCE, historical_v1=True)["template"]
    native.j, native.ex = completed(old_job)
    s = service()
    next(row for row in s["template"]["containers"][0]["env"]
         if row["name"] == "DOCUMENT_WORKER_POLICY_SHA256")["value"] = p.LEGACY_POLICY
    native.service = lambda _: copy.deepcopy(s)
    native.revision = lambda _: revision(s)
    calls = []

    def old_build(*args, **kwargs):
        calls.append(kwargs)
        return build()

    def old_entries(query, token):
        assert 'jsonPayload.event="hirewiz_document_native_six_case_v1"' in query and token is None
        return {"entries": [event(native.j, native.ex, historical_payload())]}

    monkeypatch.setattr(p, "build_module", lambda: SimpleNamespace(verify_current_build=old_build))
    native.entries = old_entries
    result = p.verify_current_successor(IMAGE, s)
    assert calls == [{"historical_v1": True}]
    assert result.contract_version == 1 and result.required_case_count == 6
    assert result.policy_sha256 == p.LEGACY_POLICY
    assert native.calls["create"] == native.calls["start"] == native.calls["delete"] == 0


def test_sdk_discovery_is_fixed_nonsecret_and_import_is_inert(monkeypatch, tmp_path):
    root = tmp_path / "google-cloud-sdk"
    (root / "lib/googlecloudsdk").mkdir(parents=True)
    (root / "lib/third_party").mkdir()
    monkeypatch.setattr(p.shutil, "which", lambda _: "/usr/bin/gcloud")
    def invoke(args, **kwargs):
        assert args == ["/usr/bin/gcloud", "info", "--format=value(installation.sdk_root)"]
        assert kwargs["timeout"] == 5 and kwargs["stdin"] == p.subprocess.DEVNULL
        return SimpleNamespace(returncode=0, stdout=str(root).encode() + b"\n")
    monkeypatch.setattr(p.subprocess, "run", invoke)
    assert p.sdk_library() == root / "lib"


def caller_services(image=NEXT, *, font=profile.FONT):
    result = {}
    for name in ("ai-resume-parser", "hirewiz-analysis-worker", "hirewiz-resume-coordinator"):
        s = service(image="app@sha256:" + "c" * 64, name=name, uid=JOB_UID)
        s["template"]["containers"][0]["env"] = [
            {"name": "DOCUMENT_WORKER_IMAGE_DIGEST", "value": image},
            {"name": "DOCUMENT_WORKER_URL", "value": p.WORKER_URL},
            {"name": "DOCUMENT_WORKER_POLICY_SHA256", "value": p.POLICY},
            {"name": "DOCUMENT_WORKER_DOCX_RENDERER_VERSION", "value": p.RENDERER},
            {"name": "DOCUMENT_WORKER_DOCX_FONT_MANIFEST_SHA256", "value": font}]
        result[s["name"]] = s
    worker = service(image)
    result[worker["name"]] = worker
    return result


@pytest.fixture
def current_caller_build(monkeypatch):
    """Fresh native Build port for the current accepted daily renderer parent."""
    calls = []

    def verify(build_id, nonce, image):
        assert build_id == BUILD_ID and nonce == NONCE
        calls.append(image)
        return {**build(), "immutable_image": image, "fixed_renderer_base": profile.IMAGE,
                "source_sha256": copy.deepcopy(profile.SOURCES)}

    monkeypatch.setattr(p, "build_module", lambda: SimpleNamespace(
        RENDERER_BASE=profile.IMAGE, verify_current_build=verify))
    return calls


@pytest.mark.parametrize("name", [None, "ai-resume-parser", "hirewiz-analysis-worker", "hirewiz-resume-coordinator"])
def test_cleanup_requires_all_three_actual_serving_consumers(name, current_caller_build):
    services = caller_services()
    revs = {s["latestReadyRevision"]: revision(s) for s in services.values()}
    if name:
        changed = services[p.PARENT + "/services/" + name]
        revs[changed["latestReadyRevision"]]["containers"][0]["env"][0]["value"] = IMAGE
    clients = SimpleNamespace(service=lambda key: services[key], revision=lambda key: revs[key])
    if name:
        with pytest.raises(p.NativeProofDenied, match="caller_pin"):
            p.verify_caller_pins(clients, NEXT)
    else:
        p.verify_caller_pins(clients, NEXT)
    assert current_caller_build == [NEXT]


def cleanup_fixture(native, monkeypatch, tmp_path):
    target = tmp_path / "native"
    p.run_successor(IMAGE, build(), target)
    # This retained synthetic native fixture explicitly models the older renderer.
    services = caller_services(font=p.FONT)
    s = service(NEXT)
    services[s["name"]] = s
    revs = {row["latestReadyRevision"]: revision(row) for row in services.values()}
    native.service = lambda key: copy.deepcopy(services[key])
    native.revision = lambda key: copy.deepcopy(revs[key])
    verified = []
    def verify_new(image, metadata):
        assert image == NEXT and metadata == s
        verified.append(image)
        return SimpleNamespace(image=image, contract_version=2, required_case_count=10, policy_sha256=p.POLICY)
    monkeypatch.setattr(p, "verify_current_successor", verify_new)
    return target, services, verified


def test_current_acceptance_job_is_retained(native, monkeypatch, tmp_path):
    target = tmp_path / "native"
    p.run_successor(IMAGE, build(), target)
    native.service = lambda _: service(IMAGE)
    with pytest.raises(p.NativeProofDenied, match="must_be_retained"):
        p.cleanup_successor(IMAGE, build(), target)
    assert native.calls["delete"] == 0


def test_expired_old_receipt_may_retire_only_after_newer_acceptance_and_serving_pins(native, monkeypatch, tmp_path):
    target, _, verified = cleanup_fixture(native, monkeypatch, tmp_path)
    data = payload()
    for row in data["cases"]:
        if "scan" in row:
            row["scan"]["definitions"] = "28155/" + (NOW - timedelta(days=4)).isoformat()
    native.pages = {None: {"entries": [event(native.j, native.ex, data)]}}
    with pytest.raises(p.NativeProofDenied, match="definitions_expired"):
        p.verify_successor(IMAGE, build(), target)
    result = p.cleanup_successor(IMAGE, build(), target)
    assert result["owned_resources_absent"] is True and result["job_uid"] == JOB_UID
    assert verified == [NEXT] and native.calls["delete"] == 1


def test_rescue_preflights_actual_owned_prior_journal_before_new_build(native, monkeypatch, tmp_path):
    history = tmp_path / "history"
    original = history / "retained-main-artifact" / "native-proof"
    original.parent.mkdir(parents=True, mode=0o700)
    full = {**build(), "builder_service_account": refresh.BUILDER,
            "source_bucket": refresh.PROJECT + "_cloudbuild",
            "source_object": "hirewiz-document-refresh-source/synthetic.tar.gz",
            "source_generation": "1", "fixed_renderer_base": refresh.RENDERER_BASE,
            "source_sha256": profile.SOURCES}
    def profiled_entries(query, token):
        data = payload(execution=native.ex["name"].rsplit("/", 1)[-1])
        data["identity_preflight"]["font_manifest_sha256"] = profile.FONT
        return {"entries": [event(native.j, native.ex, data)]}
    native.entries = profiled_entries
    p.run_successor(IMAGE, full, original)
    s = service()
    verified = []
    def current_build(build_id, nonce, image):
        assert (build_id, nonce, image) == (BUILD_ID, NONCE, IMAGE)
        verified.append((build_id, nonce, image))
        return {key: copy.deepcopy(value) for key, value in full.items()
                if key != "local_native_proof_sha256"}
    monkeypatch.setattr(refresh, "verify_current_build", current_build)
    assert refresh.rescue_prior_journal(history, s, native, p) == original
    assert verified == [(BUILD_ID, NONCE, IMAGE)] and full["local_native_proof_sha256"] == "f" * 64
    assert native.calls["delete"] == 0
    original_binding = (original / "binding.json").read_bytes()
    changed = json.loads(original_binding)
    del changed["build_identity"]["local_native_proof_sha256"]
    (original / "binding.json").write_bytes(p.canonical(changed))
    with pytest.raises(refresh.RefreshDenied, match="native_build_identity_unverified"):
        refresh.rescue_prior_journal(history, s, native, p)
    (original / "binding.json").write_bytes(original_binding)
    duplicate = history / "another-artifact" / "native-proof"
    duplicate.mkdir(parents=True, mode=0o700)
    (duplicate / "binding.json").write_bytes((original / "binding.json").read_bytes())
    with pytest.raises(refresh.RefreshDenied, match="prior_owned_journal_required"):
        refresh.rescue_prior_journal(history, s, native, p)


def test_unknown_delete_is_once_only_without_touching_current_job(native, monkeypatch, tmp_path):
    target, _, _ = cleanup_fixture(native, monkeypatch, tmp_path)
    native.fail_delete = True
    for _ in range(2):
        with pytest.raises(p.NativeProofDenied, match="cleanup_unknown") as caught:
            p.cleanup_successor(IMAGE, build(), target)
        assert "secret-shaped" not in str(caught.value)
    assert native.calls["delete"] == 1
    assert (target / "delete.started.json").exists()
    assert not (target / "delete.acknowledged.json").exists()


def test_changed_current_consumer_prevents_any_old_job_delete(native, monkeypatch, tmp_path):
    target, services, _ = cleanup_fixture(native, monkeypatch, tmp_path)
    services[p.PARENT + "/services/hirewiz-resume-coordinator"]["template"]["containers"][0]["env"][0]["value"] = IMAGE
    with pytest.raises(p.NativeProofDenied, match="caller_pin"):
        p.cleanup_successor(IMAGE, build(), target)
    assert native.calls["delete"] == 0 and not (target / "delete.started.json").exists()


def test_v1_current_proof_cannot_authorize_retirement(native, monkeypatch, tmp_path):
    target, services, _ = cleanup_fixture(native, monkeypatch, tmp_path)
    monkeypatch.setattr(p, "verify_current_successor", lambda *args: SimpleNamespace(
        image=NEXT, contract_version=1, required_case_count=6, policy_sha256=p.LEGACY_POLICY))
    with pytest.raises(p.NativeProofDenied, match="v2_successor_required"):
        p.cleanup_successor(IMAGE, build(), target)
    assert native.calls["delete"] == 0 and not (target / "delete.started.json").exists()


@pytest.mark.parametrize("read_only_release", [False, True])
@pytest.mark.parametrize("preflight_drift", [None, "sandbox", "coordinator"])
@pytest.mark.parametrize("maintenance_binding", [False, True])
def test_official_generated_sdk_requests_use_only_concrete_service_adapter_routes(monkeypatch, read_only_release, preflight_drift, maintenance_binding):
    """Exercise actual SDK serialization with an in-memory HTTP envelope, never auth."""
    import json
    from urllib.parse import parse_qs, urlsplit

    from backend.tests.test_document_worker_deployment import maintenance_snapshot
    from infra.gcp import document_refresh_auth as auth

    sdk = Path("/opt/homebrew/share/google-cloud-sdk/lib")
    if not (sdk / "googlecloudsdk").is_dir():
        pytest.skip("optional actual installed SDK wiring control")
    # Clients prepends the SDK's vendored libraries; keep later tests on their
    # original import path instead of leaking SDK dependencies into the suite.
    monkeypatch.setattr(p.sys, "path", list(p.sys.path))
    seen = []
    j, ex = completed()
    runtime_rows = runtime()
    runtime_rows[2]["etag"] = "YWJj"
    runtime_rows[1] = service(p.BASE_IMAGE)
    metadata = maintenance_snapshot(worker)
    if maintenance_binding:
        runtime_rows[2]["bindings"].append({"role": worker.MAINTENANCE_ROLE,
            "members": ["serviceAccount:" + worker.MAINTENANCE_ACCOUNT]})
    if preflight_drift == "sandbox":
        runtime_rows[1]["template"]["containers"][0]["sandboxLauncher"] = False

    class Http:
        connections = {}
        uid = "123456789012345678901"

        def request(self, uri, method="GET", body=None, headers=None, redirections=0, **kwargs):
            assert redirections == 0 and auth.allowed(method, uri), (method, uri)
            seen.append((method, uri, body))
            path = urlsplit(uri).path
            if path.endswith(":getIamPolicy"):
                data = {} if "iam.googleapis.com" in uri else {"bindings": []}
            elif path.endswith("/keys"):
                data = {}
            elif path.endswith("/roles/hirewizDocumentServiceMaintenance"):
                data = metadata["maintenance_role"]
            elif "iam.googleapis.com" in uri:
                data = (metadata["maintenance_service_account"] if "hirewiz-document-refresh" in uri else
                        coordinator_account() if "hirewiz-resume-coordinator" in uri else runtime_rows[3])
                if "hirewiz-resume-coordinator" in uri and preflight_drift == "coordinator":
                    data["uniqueId"] = "1" * 21
            elif "cloudresourcemanager.googleapis.com" in uri:
                data = runtime_rows[0]
            elif path.endswith("/services/hirewiz-document-inspector"):
                data = runtime_rows[1]
            elif "/revisions/" in path:
                data = revision(runtime_rows[1])
            elif path.endswith("/jobs"):
                assert method == "POST"
                assert parse_qs(urlsplit(uri).query)["jobId"] == [j["name"].rsplit("/", 1)[-1]]
                sent = json.loads(body)
                assert "name" not in sent and sent == {k: v for k, v in p.desired(IMAGE, NONCE).items() if k != "name"}
                data = {"name": p.PARENT + "/operations/create"}
            elif path.endswith(":run"):
                assert method == "POST" and json.loads(body) == {"etag": j["etag"]}
                data = {"name": p.PARENT + "/operations/run"}
            elif "/operations/" in path:
                data = {"name": p.PARENT + "/operations/create", "done": True,
                        "response": {"@type": "type.googleapis.com/google.cloud.run.v2.Job", **j}}
            elif "/executions/" in path:
                data = ex
            elif path.endswith("/entries:list"):
                assert json.loads(body)["pageSize"] == 20
                data = {"entries": [event(j, ex, payload())]}
            elif "/jobs/" in path:
                data = {"name": p.PARENT + "/operations/delete"} if method == "DELETE" else j
            else:
                raise AssertionError("unexpected synthetic request route")
            if path.endswith("/services/hirewiz-document-inspector:getIamPolicy"):
                data = runtime_rows[2]
            return auth.Response({"status": "200", "content-type": "application/json"}), p.canonical(data)

        def close(self):
            pass

    selected = []
    def maintenance(uid):
        selected.append("maintenance")
        return Http()
    def release():
        selected.append("release")
        return Http()
    original_loader = p.load_source
    monkeypatch.setattr(p, "sdk_library", lambda: sdk)
    monkeypatch.setattr(p, "load_source", lambda name, path: (SimpleNamespace(ServiceHttp=maintenance, ReleaseReadOnlyHttp=release)
        if path.name == "document_refresh_auth.py" else original_loader(name, path)))
    monkeypatch.delenv("DOCUMENT_REFRESH_CONTROLLER_UID", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    clients = p.Clients(read_only_release=read_only_release)
    try:
        if preflight_drift:
            with pytest.raises((p.NativeProofDenied, worker.ConfigurationDenied)):
                clients.preflight()
            assert all(method == "GET" or uri.split("?", 1)[0].endswith(":getIamPolicy") for method, uri, _ in seen)
            return
        proof = clients.preflight()
        assert proof["service"]["template"]["containers"][0]["image"] == p.BASE_IMAGE
        assert proof["revision"]["name"] == proof["service"]["latestReadyRevision"]
        if maintenance_binding:
            assert proof["maintenance_role"] == metadata["maintenance_role"]
            assert any(urlsplit(uri).path.endswith("/roles/hirewizDocumentServiceMaintenance") for _, uri, _ in seen)
        if read_only_release:
            for mutation, argument in ((clients.create, p.desired(IMAGE, NONCE)), (clients.start, j), (clients.delete, j)):
                with pytest.raises(p.NativeProofDenied, match="release_mutation_refused"):
                    mutation(argument)
        else:
            assert clients.create(p.desired(IMAGE, NONCE))["name"].endswith("/create")
            assert clients.start(j)["name"].endswith("/run")
        assert clients.job(j["name"])["uid"] == JOB_UID
        assert clients.execution(ex["name"])["uid"] == EX_UID
        assert clients.operation(p.PARENT + "/operations/create")["done"] is True
        assert clients.revision(service()["latestReadyRevision"])["uid"] == EX_UID
        assert clients.entries("synthetic fixed query", None)["entries"][0]["jsonPayload"]["all_required_passed"] is True
        if not read_only_release:
            assert clients.delete(j)["name"].endswith("/delete")
        assert clients.run.num_retries == clients.logging.num_retries == 0
    finally:
        clients.close()
    assert selected == ["release" if read_only_release else "maintenance"]
    assert len(seen) == (15 if read_only_release else 18) + (3 if maintenance_binding else 0)


def coordinator_account():
    email = "hirewiz-resume-coordinator@" + p.PROJECT + ".iam.gserviceaccount.com"
    return {"name": "projects/" + p.PROJECT + "/serviceAccounts/" + email, "projectId": p.PROJECT,
            "email": email, "uniqueId": "109155185167233588129"}


def snapshot():
    rows = runtime()
    return {"service": service(), "service_iam": rows[2], "project_iam": rows[6],
            "service_account": rows[3], "service_account_keys": rows[4],
            "upload_coordinator_service_account": coordinator_account(),
            "upload_coordinator_service_account_keys": {}}


def test_loaded_source_executes_exact_source_bytes_despite_matching_stale_pyc(tmp_path):
    import os
    import py_compile

    source = tmp_path / "owned_source.py"
    source.write_text("VALUE = 'cached'\n")
    before = source.stat()
    cache = py_compile.compile(str(source), doraise=True)
    assert Path(cache).is_file()
    source.write_text("VALUE = 'source'\n")
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert source.stat().st_size == before.st_size
    assert p.load_source("hirewiz_test_exact_source_not_pyc", source).VALUE == "source"


@pytest.mark.parametrize("change", ["sandbox", "command", "resources", "network", "ingress", "scaling", "coordinator"])
def test_current_successor_keeps_original_serving_worker_configuration(native, change):
    native.created, native.ran = True, True
    native.j, native.ex = completed()
    s = service()
    proof = snapshot()
    if change == "sandbox":
        s["template"]["containers"][0]["sandboxLauncher"] = False
    elif change == "command":
        s["template"]["containers"][0]["command"] = ["/bin/sh"]
    elif change == "resources":
        s["template"]["containers"][0]["resources"]["limits"]["memory"] = "8Gi"
    elif change == "network":
        s["template"]["vpcAccess"] = {"connector": "unexpected"}
    elif change == "ingress":
        s["ingress"] = "INGRESS_TRAFFIC_ALL"
    elif change == "scaling":
        s["scaling"]["maxInstanceCount"] = 100
    else:
        proof["upload_coordinator_service_account"]["uniqueId"] = "1" * 21
    native.service = lambda _: copy.deepcopy(s)
    native.revision = lambda _: revision(s)
    native.preflight = lambda: proof
    with pytest.raises(p.NativeProofDenied):
        p.verify_current_successor(IMAGE, s)


@pytest.mark.parametrize("read_only_release", [False, True])
@pytest.mark.parametrize("tamper", [None, "worker_configuration", "job", "receipt", "logs"])
def test_explicit_release_route_uses_owned_identity_and_identical_native_proof(monkeypatch, native, read_only_release, tamper):
    native.created, native.ran = True, True
    native.j, native.ex = completed()
    s = service()
    if tamper == "worker_configuration":
        s["template"]["containers"][0]["sandboxLauncher"] = False
    elif tamper == "job":
        native.j["template"]["template"]["maxRetries"] = 1
    elif tamper == "receipt":
        data = payload()
        data["cases"][parser.NAMES.index("styled_multipage_fit")]["layout"]["unchanged_links"] = 0
        native.pages = {None: {"entries": [event(native.j, native.ex, data)]}}
    elif tamper == "logs":
        native.pages = {None: {"entries": []}}
    native.service = lambda _: copy.deepcopy(s)
    native.revision = lambda _: revision(s)
    calls = []
    def clients(**kwargs):
        calls.append(("client", kwargs))
        return native
    def build_for_release(*args):
        calls.append(("build", "release"))
        return build()
    def build_for_maintenance(*args):
        calls.append(("build", "maintenance"))
        return build()
    monkeypatch.setattr(p, "Clients", clients)
    monkeypatch.setattr(p, "build_module", lambda: SimpleNamespace(
        verify_current_build=build_for_maintenance, verify_current_build_release=build_for_release))
    monkeypatch.delenv("DOCUMENT_REFRESH_CONTROLLER_UID", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    verify = p.verify_current_successor_for_release if read_only_release else p.verify_current_successor
    if tamper:
        with pytest.raises(p.NativeProofDenied):
            verify(IMAGE, s)
    else:
        result = verify(IMAGE, s)
        assert result.image == IMAGE and result.job_uid == JOB_UID and result.execution_uid == EX_UID
    assert calls == [("build", "release" if read_only_release else "maintenance"),
                     ("client", {"read_only_release": True} if read_only_release else {})]
    assert native.calls["create"] == native.calls["start"] == native.calls["delete"] == 0


def test_current_digest_pins_cannot_authorize_a_different_document_service_origin(current_caller_build):
    services = caller_services()
    revs = {s["latestReadyRevision"]: revision(s) for s in services.values()}
    bad = revs[services[p.PARENT + "/services/hirewiz-resume-coordinator"]["latestReadyRevision"]]
    for item in bad["containers"][0]["env"]:
        if item["name"] == "DOCUMENT_WORKER_URL":
            item["value"] = "https://foreign.example/inspect"
    with pytest.raises(p.NativeProofDenied, match="caller_pin"):
        p.verify_caller_pins(SimpleNamespace(service=lambda key: services[key], revision=lambda key: revs[key]), NEXT)
    assert current_caller_build == [NEXT]
