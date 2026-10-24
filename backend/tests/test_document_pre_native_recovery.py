"""Acknowledged Build-only history; all native observations are synthetic GETs."""
from __future__ import annotations

import base64
import copy
import json
import shutil
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from backend.tests import test_document_refresh as local
from backend.tests import test_document_refresh_transition as serving
from infra.gcp import document_refresh_probe as probe
from infra.gcp import document_refresh_recovery as recovery

from scripts import document_refresh as refresh

NONCE = "a" * 32
NEXT_NONCE = "f" * 32


class HttpError(Exception):
    def __init__(self, status):
        self.status_code = status


@pytest.fixture(params=(False, True), ids=("current_recipe", "frozen_v1_recipe"))
def stopped(tmp_path, monkeypatch, request):
    historical = request.param
    raw_recipe = (probe.legacy_source("refresh.Dockerfile") if historical else
                  (refresh.ROOT / "infra/document-processing/refresh.Dockerfile").read_bytes())
    source = refresh.source_archive(raw_recipe)
    value = local.build()
    identity = refresh.build_contract(value, NONCE, recovery.sha(raw_recipe), historical_v1=historical)
    identity["source_archive_sha256"] = recovery.sha(source)
    history = tmp_path / "history"
    history.mkdir(mode=0o700)
    original = history / "stopped-before-local-proof"
    original.mkdir(mode=0o700)
    origin = {"bucket": identity["source_bucket"], "object": identity["source_object"],
              "generation": identity["source_generation"]}
    encoded = base64.urlsafe_b64encode(identity["build_id"].encode()).decode().rstrip("=")
    entries = {"native-build-identity.json": identity,
        "source-upload-acknowledged.json": origin,
        "build-started.json": {"nonce": NONCE, "recipe_sha256": identity["refresh_dockerfile_sha256"],
            "source_archive_sha256": identity["source_archive_sha256"], "automatic_replay": False,
            "unknown_outcome": "reconcile_exact_native_object_and_build_only"},
        "build-create-started.json": {"nonce": NONCE, "automatic_replay": False,
            "request_sha256": recovery.sha(recovery.canonical(refresh.build_request(NONCE, origin)))},
        "build-acknowledged.json": {"nonce": NONCE, "build_id": identity["build_id"],
            "operation_name": "operations/build/" + refresh.PROJECT + "/" + encoded, "automatic_replay": False}}
    for name, row in entries.items():
        refresh.write_new(original / name, row)
    state = SimpleNamespace(history=history, original=original, identity=identity, value=value,
                            archive=source, http_calls=[], native_calls=[], job_status=404, proof_calls=[])

    class BuildHttp:
        def request(self, uri, *, method, redirections, **kwargs):
            assert method == "GET" and redirections == 0  # No upload, Build, Job, claim or PATCH.
            state.http_calls.append((method, uri))
            if uri == (f"https://cloudbuild.googleapis.com/v1/projects/{refresh.PROJECT}/locations/"
                       f"{refresh.REGION}/builds/{identity['build_id']}"):
                return SimpleNamespace(status=200), json.dumps(state.value).encode()
            parsed = urlsplit(uri)
            assert parsed.hostname == "storage.googleapis.com"
            assert unquote(parsed.path) == ("/download/storage/v1/b/" + origin["bucket"] + "/o/" + origin["object"])
            assert parse_qs(parsed.query) == {"alt": ["media"], "generation": [origin["generation"]]}
            return SimpleNamespace(status=200), state.archive

        def close(self):
            pass

    native_http = serving.Http()

    class Clients:
        errors = SimpleNamespace(HttpError=HttpError)
        http = native_http

        def job(self, name):
            assert name == probe.PARENT + "/jobs/hirewiz-doc-six-" + NONCE[:12]
            state.native_calls.append(("GET", name))
            if state.job_status == "unknown":
                raise RuntimeError("synthetic observation unavailable")
            if state.job_status != 200:
                raise HttpError(state.job_status)
            return {"name": name}

        def preflight(self):
            state.native_calls.append(("GET", "current-authority"))
            return {"service": native_http.services["hirewiz-document-inspector"]}

        def service(self, name):
            state.native_calls.append(("GET", name))
            return copy.deepcopy(native_http.services[name.rsplit("/", 1)[-1]])

        def revision(self, name):
            state.native_calls.append(("GET", name))
            return serving.transition.Controller(native_http, probe, None).revision(name)

        def close(self):
            pass

    state.native_http = native_http
    original_infra = refresh.infra
    monkeypatch.setattr(refresh, "native_http", BuildHttp)
    monkeypatch.setattr(refresh, "infra", lambda name: probe if name == "document_refresh_probe" else
                        serving.transition if name == "document_refresh_transition" else original_infra(name))
    monkeypatch.setattr(probe, "Clients", Clients)
    monkeypatch.setattr(probe, "verify_current_successor", lambda image, metadata:
                        state.proof_calls.append((image, metadata["uid"])))
    monkeypatch.setattr(recovery.uuid, "uuid4", lambda: SimpleNamespace(hex=NEXT_NONCE))
    return state


def output(stopped, name="next"):
    target = stopped.history.parent / name
    target.mkdir(mode=0o700)
    return target


def change_entry(stopped, name, update):
    path = stopped.original / name
    row = json.loads(path.read_bytes())
    row.update(update)
    path.write_bytes(recovery.canonical(row))


def assert_no_native_writes(stopped):
    assert all(method == "GET" for method, _ in stopped.http_calls + stopped.native_calls)
    assert stopped.native_http.writes == []


def test_terminal_build_and_absent_job_allow_new_nonce_without_adopting_old_candidate(stopped):
    before = recovery.pre_native_snapshot(stopped.original, refresh)
    target = output(stopped)
    assert recovery.prepare(target, stopped.history, None, refresh_module=refresh) == NEXT_NONCE
    assert recovery.pre_native_snapshot(stopped.original, refresh) == before
    retained = target / "pre-native-reconciliations" / NONCE
    assert recovery.pre_native_snapshot(retained / "original", refresh) == before
    receipt = json.loads((retained / "reconciliation.json").read_bytes())
    assert receipt["status"] == "pre_native_candidate_abandoned_not_acceptance"
    assert receipt["manifest_sha256"] == recovery.manifest(before)
    assert receipt["successor_nonce"] == NEXT_NONCE and receipt["successor_nonce"] != NONCE
    assert receipt["native_job_http_status"] == 404
    assert receipt["old_build_replayed"] is receipt["native_acceptance"] is receipt["promotion_performed"] is False
    assert not (target / "native-proof").exists() and not (target / "transition").exists()
    assert not (target / "result.json").exists()
    assert not (retained / "original/native-proof").exists()
    assert len(stopped.http_calls) == 2 and stopped.proof_calls
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in target.rglob("*.json"))
    assert_no_native_writes(stopped)


@pytest.mark.parametrize("status", [200, 403, 503, "unknown"])
def test_existing_or_uncertain_native_job_is_never_treated_as_absent(stopped, status):
    stopped.job_status = status
    target = output(stopped)
    with pytest.raises((refresh.RefreshDenied, RuntimeError)):
        recovery.prepare(target, stopped.history, None, refresh_module=refresh)
    assert not list(target.iterdir()) and not stopped.proof_calls
    assert_no_native_writes(stopped)


@pytest.mark.parametrize("name,update", [
    ("build-acknowledged.json", {"operation_name": "operations/foreign"}),
    ("build-create-started.json", {"request_sha256": "0" * 64}),
    ("source-upload-acknowledged.json", {"generation": "999"}),
    ("build-started.json", {"automatic_replay": True}),
    ("native-build-identity.json", {"fixed_renderer_base": "unreviewed-parent"}),
    ("native-build-identity.json", {"refresh_dockerfile_sha256": "0" * 64}),
])
def test_changed_ack_intent_or_recipe_refuses_before_provider_reads(stopped, name, update):
    change_entry(stopped, name, update)
    target = output(stopped)
    with pytest.raises(refresh.RefreshDenied):
        recovery.prepare(target, stopped.history, None, refresh_module=refresh)
    assert not stopped.http_calls and not stopped.native_calls and not list(target.iterdir())


@pytest.mark.parametrize("change", ["working", "foreign_builder", "source_generation", "source_bytes", "image"])
def test_fresh_build_or_generation_archive_mismatch_never_becomes_disposition(stopped, change):
    if change == "working":
        stopped.value["status"] = "WORKING"
    elif change == "foreign_builder":
        stopped.value["serviceAccount"] = "projects/foreign/serviceAccounts/foreign@example.test"
    elif change == "source_generation":
        stopped.value["sourceProvenance"]["resolvedStorageSource"]["generation"] = "999"
    elif change == "source_bytes":
        stopped.archive = b"different unverified archive"
    else:
        stopped.value["results"]["images"][0]["digest"] = "sha256:" + "c" * 64
    target = output(stopped)
    with pytest.raises(refresh.RefreshDenied):
        recovery.prepare(target, stopped.history, None, refresh_module=refresh)
    assert not list(target.iterdir()) and not stopped.native_calls
    assert_no_native_writes(stopped)


@pytest.mark.parametrize("marker", ["local-native-proof.json", "native-proof/create.started.json",
                                   "transition/close.started.json", "unrecognized.json"])
def test_other_partial_or_later_phase_markers_remain_unknown_history(stopped, marker):
    path = stopped.original / marker
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    refresh.write_new(path, {})
    target = output(stopped)
    with pytest.raises(refresh.RefreshDenied, match="prior_unknown_reconcile_only"):
        recovery.prepare(target, stopped.history, None, refresh_module=refresh)
    assert not stopped.http_calls and not stopped.native_calls and not list(target.iterdir())


@pytest.mark.parametrize("change", ["symlink", "hard_link", "world_readable", "missing_ack"])
def test_original_file_custody_cannot_be_inferred_from_a_native_build(stopped, change):
    path = stopped.original / "build-acknowledged.json"
    if change == "symlink":
        moved = stopped.original.parent.parent / "outside.json"
        path.rename(moved)
        path.symlink_to(moved)
    elif change == "hard_link":
        (stopped.original.parent.parent / "outside.json").hardlink_to(path)
    elif change == "world_readable":
        path.chmod(0o644)
    else:
        path.unlink()
    target = output(stopped)
    with pytest.raises((refresh.RefreshDenied, OSError)):
        recovery.prepare(target, stopped.history, None, refresh_module=refresh)
    assert not stopped.http_calls and not stopped.native_calls and not list(target.iterdir())


@pytest.mark.parametrize("change", ["candidate_serving", "caller_pin", "not_ready"])
def test_abandonment_requires_current_accepted_authority_and_matching_caller_pins(stopped, change):
    if change == "candidate_serving":
        row = stopped.native_http.services["hirewiz-document-inspector"]
        row["template"]["containers"][0]["image"] = stopped.identity["immutable_image"]
    elif change == "caller_pin":
        row = stopped.native_http.services["ai-resume-parser"]["template"]["containers"][0]["env"]
        next(value for value in row if value["name"] == "DOCUMENT_WORKER_IMAGE_DIGEST")["value"] = "unverified"
    else:
        stopped.native_http.services["ai-resume-parser"]["observedGeneration"] = "999"
    target = output(stopped)
    with pytest.raises((refresh.RefreshDenied, probe.NativeProofDenied)):
        recovery.prepare(target, stopped.history, None, refresh_module=refresh)
    assert not list(target.iterdir())
    assert_no_native_writes(stopped)


@pytest.mark.parametrize("change", ["duplicate", "same_nonce", "separate_unknown"])
def test_known_pre_native_record_does_not_hide_duplicates_replay_or_other_unknown_work(stopped, monkeypatch, change):
    if change == "duplicate":
        shutil.copytree(stopped.original, stopped.history / "duplicate")
    elif change == "same_nonce":
        monkeypatch.setattr(recovery.uuid, "uuid4", lambda: SimpleNamespace(hex=NONCE))
    else:
        unknown = stopped.history / "other-unknown"
        unknown.mkdir(mode=0o700)
        refresh.write_new(unknown / "build-create-started.json", {"automatic_replay": False})
    target = output(stopped)
    with pytest.raises(refresh.RefreshDenied):
        recovery.prepare(target, stopped.history, None, refresh_module=refresh)
    assert not list(target.iterdir())
    assert_no_native_writes(stopped)
