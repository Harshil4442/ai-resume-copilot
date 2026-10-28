"""Acknowledged Build-only history; all native observations are synthetic GETs."""
from __future__ import annotations

import base64
import copy
import json
import shutil
from dataclasses import asdict
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from scripts import document_refresh as refresh

from backend.tests import test_document_refresh as local
from backend.tests import test_document_refresh_transition as serving
from infra.gcp import document_candidate_release as v3
from infra.gcp import document_refresh_probe as probe
from infra.gcp import document_refresh_recovery as recovery

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
                            archive=source, historical=historical, http_calls=[], native_calls=[],
                            job_status=404, proof_calls=[], builds={identity["build_id"]: value},
                            sources={(origin["bucket"], origin["object"], origin["generation"]): source},
                            nonces={NONCE})

    class BuildHttp:
        def request(self, uri, *, method, redirections, **kwargs):
            assert method == "GET" and redirections == 0  # No upload, Build, Job, claim or PATCH.
            state.http_calls.append((method, uri))
            build_prefix = (f"https://cloudbuild.googleapis.com/v1/projects/{refresh.PROJECT}/locations/"
                            f"{refresh.REGION}/builds/")
            if uri.startswith(build_prefix):
                return SimpleNamespace(status=200), json.dumps(state.builds[uri.removeprefix(build_prefix)]).encode()
            parsed = urlsplit(uri)
            assert parsed.hostname == "storage.googleapis.com"
            prefix = "/download/storage/v1/b/" + refresh.PROJECT + "_cloudbuild/o/"
            assert unquote(parsed.path).startswith(prefix)
            source_object = unquote(parsed.path).removeprefix(prefix)
            query = parse_qs(parsed.query)
            assert query.get("alt") == ["media"] and len(query.get("generation", [])) == 1
            key = (refresh.PROJECT + "_cloudbuild", source_object, query["generation"][0])
            return SimpleNamespace(status=200), state.archive if key == (
                origin["bucket"], origin["object"], origin["generation"]) else state.sources[key]

        def close(self):
            pass

    native_http = serving.Http()

    class Clients:
        errors = SimpleNamespace(HttpError=HttpError)
        http = native_http

        def job(self, name):
            assert name in {probe.PARENT + "/jobs/hirewiz-doc-six-" + nonce[:12] for nonce in state.nonces}
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


def completed_serving_history(stopped):
    """Sanitized Oct25-shaped completed artifact beside the abandoned Build."""
    root = stopped.history / "completed-serving"
    native, transition, prior = (root / name for name in
                                 ("native-proof", "transition", "prior-native-retirement"))
    for path in (root, native, transition, prior):
        path.mkdir(mode=0o700)
    image = stopped.native_http.services["hirewiz-document-inspector"]["template"]["containers"][0]["image"]
    nonce, build_id = "1" * 32, "11111111-1111-1111-1111-111111111111"
    identity = {"version": 1, "kind": "native_cloud_build", "nonce": nonce,
        "build_id": build_id, "build_resource": probe.PARENT + "/builds/" + build_id,
        "builder_service_account": refresh.BUILDER, "source_bucket": refresh.PROJECT + "_cloudbuild",
        "source_object": "hirewiz-document-refresh-source/synthetic.tar.gz", "source_generation": "1",
        "source_archive_sha256": "a" * 64, "refresh_dockerfile_sha256": "b" * 64,
        "fixed_renderer_base": refresh.RENDERER_BASE, "immutable_image": image,
        "source_sha256": serving.profile.SOURCES, "local_native_proof_sha256": "c" * 64}
    accepted = asdict(probe.NativeAcceptance(image, build_id, identity["build_resource"], "a" * 64,
        "b" * 64, serving.profile.SOURCES, probe.PARENT + "/jobs/hirewiz-doc-six-" + nonce[:12],
        "22222222-2222-2222-2222-222222222222", "synthetic-execution",
        "33333333-3333-3333-3333-333333333333", "d" * 64, "e" * 64,
        probe.POLICY, serving.profile.FONT, serving.profile.RENDERER,
        "2026-10-01T00:00:00+00:00", 2, 10))
    digest = recovery.sha(recovery.canonical(accepted))
    complete = {"status": "document_refresh_serving", "image": image,
        "prior_image": probe.REPOSITORY + "@sha256:" + "f" * 64,
        "native_acceptance_sha256": digest, "all_three_caller_pins_verified": True,
        "admission_restored": True, "synchronous_callers_drained": False,
        "current_native_proof_retained": True, "automatic_replay": False}
    cleanup = {"owned_resources_absent": True}
    files = {native / "binding.json": {"image": image, "build_identity": identity,
                                       "probe_sha256": accepted["probe_sha256"]},
        native / "acceptance.json": accepted,
        transition / "native-acceptance.json": accepted,
        transition / "complete.json": complete,
        prior / "cleanup.json": cleanup,
        root / "result.json": {**complete, "definition_expires_at": accepted["definition_expires_at"],
                               "prior_cleanup": cleanup}}
    for path, row in files.items():
        refresh.write_new(path, row)
    return native


def completed_v3_history(stopped):
    """A completed V3 refresh can retain a consumed Build-only predecessor."""
    native = completed_serving_history(stopped)
    root = native.parent
    shutil.rmtree(root / "prior-native-retirement")
    old_binding = json.loads((native / "binding.json").read_bytes())
    identity = old_binding["build_identity"]
    identity.pop("local_native_proof_sha256")
    identity["fixed_renderer_base"] = v3.profile.IMAGE
    identity["refresh_dockerfile_sha256"] = v3.profile.REFRESH_SHA256
    identity["policy_sha256"] = v3.POLICY
    identity["source_sha256"] = refresh.full_source_snapshot()[1]
    identity["source_object"] = "hirewiz-document-refresh-source/" + identity["nonce"] + "/source.tar.gz"
    image = v3.prior.REPOSITORY + "@sha256:" + "9" * 64
    identity["immutable_image"] = image
    accepted = json.loads((native / "acceptance.json").read_bytes())
    accepted.update(image=image, refresh_dockerfile_sha256=v3.profile.REFRESH_SHA256,
                    source_sha256=refresh.full_source_snapshot()[1],
                    probe_sha256=recovery.sha(v3.candidate.rendered_payload(image).encode()),
                    policy_sha256=v3.POLICY, font_manifest_sha256=v3.profile.FONT,
                    renderer_version=v3.profile.RENDERER, contract_version=3, required_case_count=11)
    complete = json.loads((root / "transition/complete.json").read_bytes())
    complete["image"] = image
    complete["native_acceptance_sha256"] = recovery.sha(recovery.canonical(accepted))
    result = {**complete, "definition_expires_at": accepted["definition_expires_at"],
              "renderer_contract_version": 3, "required_case_count": 11,
              "renderer_parent": v3.profile.IMAGE, "prior_evidence_retained": True,
              "fresh_v2_upgraded": True}
    for path, row in {native / "binding.json": {"version": 3, "image": image, "build_identity": identity},
                      native / "acceptance.json": accepted,
                      root / "transition/native-acceptance.json": accepted,
                      root / "transition/complete.json": complete,
                      root / "result.json": result}.items():
        path.write_bytes(recovery.canonical(row))
    return native


def assert_no_native_writes(stopped):
    assert all(method == "GET" for method, _ in stopped.http_calls + stopped.native_calls)
    assert stopped.native_http.writes == []


def second_abandoned(stopped, nonce=NEXT_NONCE):
    """A distinct terminal native Build, with its own source and absent Job."""
    value = local.build()
    build_id = "fedcba98-7654-3210-fedc-ba9876543210"
    tag = refresh.REPOSITORY + ":refresh-" + nonce
    value["id"] = build_id
    value["name"] = f"projects/{refresh.PROJECT}/locations/{refresh.REGION}/builds/{build_id}"
    value["steps"][0]["args"][4] = "DOCUMENT_DEFINITIONS_NONCE=" + nonce
    value["steps"][0]["args"][6] = tag
    value["steps"][1]["args"][1] = tag
    value["results"]["images"][0] = {"name": tag, "digest": "sha256:" + "c" * 64}
    source = value["source"]["storageSource"]
    source["object"] = "hirewiz-document-refresh-source/" + nonce + "/source.tar.gz"
    source["generation"] = "5678"
    value["sourceProvenance"]["resolvedStorageSource"] = copy.deepcopy(source)
    identity = refresh.build_contract(value, nonce, stopped.identity["refresh_dockerfile_sha256"],
                                      historical_v1=stopped.historical)
    identity["source_archive_sha256"] = recovery.sha(stopped.archive)
    root = stopped.history / "second-abandoned-build"
    root.mkdir(mode=0o700)
    encoded = base64.urlsafe_b64encode(build_id.encode()).decode().rstrip("=")
    entries = {"native-build-identity.json": identity,
        "source-upload-acknowledged.json": source,
        "build-started.json": {"nonce": nonce, "recipe_sha256": identity["refresh_dockerfile_sha256"],
            "source_archive_sha256": identity["source_archive_sha256"], "automatic_replay": False,
            "unknown_outcome": "reconcile_exact_native_object_and_build_only"},
        "build-create-started.json": {"nonce": nonce, "automatic_replay": False,
            "request_sha256": recovery.sha(recovery.canonical(refresh.build_request(nonce, source)))},
        "build-acknowledged.json": {"nonce": nonce, "build_id": build_id,
            "operation_name": "operations/build/" + refresh.PROJECT + "/" + encoded, "automatic_replay": False}}
    for name, row in entries.items():
        refresh.write_new(root / name, row)
    stopped.builds[build_id] = value
    stopped.sources[(source["bucket"], source["object"], source["generation"])] = stopped.archive
    stopped.nonces.add(nonce)
    return root


def chained_history(stopped):
    first = output(stopped, "first-disposition")
    assert recovery.prepare(first, stopped.history, None, refresh_module=refresh) == NEXT_NONCE
    successor = second_abandoned(stopped)
    shutil.copytree(first / "pre-native-reconciliations", successor / "pre-native-reconciliations")
    return successor


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


def test_expired_v2_pre_native_history_needs_explicit_retirement_and_exact_old_identity(stopped, monkeypatch):
    expected = stopped.native_http.services["hirewiz-document-inspector"]
    strict = []
    retired = []

    def fresh_only(image, metadata):
        strict.append((image, metadata["uid"]))
        raise probe.NativeProofDenied("document_native_definitions_expired")

    def expired_identity_only(image, metadata):
        assert image == expected["template"]["containers"][0]["image"]
        assert metadata["uid"] == probe.SERVICE_UID
        assert metadata["labels"] == expected["labels"]
        retired.append(image)
        return None  # An expired image cannot produce serving acceptance.

    monkeypatch.setattr(probe, "verify_current_successor", fresh_only)
    monkeypatch.setattr(probe, "verify_expired_current_v2_for_retirement", expired_identity_only)
    scheduled = output(stopped, "scheduled")
    with pytest.raises(probe.NativeProofDenied, match="definitions_expired"):
        recovery.prepare(scheduled, stopped.history, None, refresh_module=refresh)
    assert not list(scheduled.iterdir()) and strict and not retired

    manual = output(stopped, "manual")
    assert recovery.prepare(manual, stopped.history, None, refresh_module=refresh,
                            retire_expired_v2=True) == NEXT_NONCE
    assert retired == [expected["template"]["containers"][0]["image"]]
    assert json.loads((manual / "pre-native-reconciliations" / NONCE / "reconciliation.json").read_bytes())[
        "native_acceptance"] is False
    assert not (manual / "native-proof").exists() and not (manual / "transition").exists()
    assert_no_native_writes(stopped)


def test_expired_v2_retirement_cannot_abandon_history_with_changed_identity_or_pins(stopped, monkeypatch):
    original = copy.deepcopy(stopped.native_http.services["hirewiz-document-inspector"])

    def expired_identity_only(image, metadata):
        if metadata["labels"] != original["labels"]:
            raise probe.NativeProofDenied("document_native_current_labels_invalid")

    monkeypatch.setattr(probe, "verify_expired_current_v2_for_retirement", expired_identity_only)
    worker = stopped.native_http.services["hirewiz-document-inspector"]
    worker["labels"]["hirewiz-refresh-nonce"] = "f" * 32
    changed = output(stopped, "changed-label")
    with pytest.raises(probe.NativeProofDenied, match="current_labels_invalid"):
        recovery.prepare(changed, stopped.history, None, refresh_module=refresh,
                         retire_expired_v2=True)
    assert not list(changed.iterdir())

    worker["labels"] = copy.deepcopy(original["labels"])
    caller = stopped.native_http.services["ai-resume-parser"]["template"]["containers"][0]["env"]
    next(row for row in caller if row["name"] == "DOCUMENT_WORKER_IMAGE_DIGEST")["value"] = "unverified"
    changed_pins = output(stopped, "changed-pins")
    with pytest.raises(probe.NativeProofDenied):
        recovery.prepare(changed_pins, stopped.history, None, refresh_module=refresh,
                         retire_expired_v2=True)
    assert not list(changed_pins.iterdir())
    assert_no_native_writes(stopped)


def test_chained_pre_native_history_consumes_prior_once_and_reconciles_only_successor(stopped, monkeypatch):
    successor = chained_history(stopped)
    prior_journal = completed_serving_history(stopped)
    monkeypatch.setattr(serving.transition, "verify_rescue_caller_pins",
                        lambda clients, contract, image: contract.verify_caller_pins(clients, image))
    monkeypatch.setattr(probe, "verify_expired_current_v2_for_retirement", lambda *_: None)
    third_nonce = "e" * 32
    monkeypatch.setattr(recovery.uuid, "uuid4", lambda: SimpleNamespace(hex=third_nonce))
    target = output(stopped, "third-disposition")
    assert recovery.prepare(target, stopped.history, None, refresh_module=refresh,
                            rescue_expired_v2=True, rescue_prior_journal=prior_journal) == third_nonce
    assert {path.name for path in (target / "pre-native-reconciliations").iterdir()} == {NEXT_NONCE}
    receipt = json.loads((target / "pre-native-reconciliations" / NEXT_NONCE / "reconciliation.json").read_bytes())
    assert receipt["successor_nonce"] == third_nonce and receipt["native_acceptance"] is False
    assert recovery.pre_native_snapshot(successor, refresh, history_root=True) == recovery.pre_native_snapshot(
        target / "pre-native-reconciliations" / NEXT_NONCE / "original", refresh)
    assert_no_native_writes(stopped)


@pytest.mark.parametrize("change", ["receipt", "original_bytes", "extra_entry", "replayed_build",
                                    "reused_prior_nonce", "reused_successor_nonce"])
def test_chained_history_rejects_tamper_or_replay_without_new_candidate(stopped, monkeypatch, change):
    successor = chained_history(stopped)
    record = successor / "pre-native-reconciliations" / NONCE
    if change == "receipt":
        path = record / "reconciliation.json"
        row = json.loads(path.read_bytes())
        row["native_acceptance"] = True
        path.write_bytes(recovery.canonical(row))
    elif change == "original_bytes":
        path = record / "original/build-started.json"
        row = json.loads(path.read_bytes())
        row["automatic_replay"] = True
        path.write_bytes(recovery.canonical(row))
    elif change == "extra_entry":
        refresh.write_new(record / "unknown.json", {"unexpected": True})
    elif change == "replayed_build":
        stopped.builds["fedcba98-7654-3210-fedc-ba9876543210"]["status"] = "WORKING"
    elif change == "reused_prior_nonce":
        monkeypatch.setattr(recovery.uuid, "uuid4", lambda: SimpleNamespace(hex=NONCE))
    else:
        monkeypatch.setattr(recovery.uuid, "uuid4", lambda: SimpleNamespace(hex=NEXT_NONCE))
    target = output(stopped, "refused-chain")
    with pytest.raises(refresh.RefreshDenied):
        recovery.prepare(target, stopped.history, None, refresh_module=refresh)
    assert not list(target.iterdir())
    assert_no_native_writes(stopped)


def test_chained_receipt_verifies_retained_original_after_older_root_ages_out(stopped, monkeypatch):
    chained_history(stopped)
    shutil.rmtree(stopped.original)
    third_nonce = "e" * 32
    monkeypatch.setattr(recovery.uuid, "uuid4", lambda: SimpleNamespace(hex=third_nonce))
    target = output(stopped, "after-prior-retention")
    assert recovery.prepare(target, stopped.history, None, refresh_module=refresh) == third_nonce
    assert {path.name for path in (target / "pre-native-reconciliations").iterdir()} == {NEXT_NONCE}
    assert_no_native_writes(stopped)


def test_completed_successor_receipt_does_not_reopen_consumed_build(stopped, monkeypatch):
    monkeypatch.setattr(recovery.uuid, "uuid4", lambda: SimpleNamespace(hex="1" * 32))
    first = output(stopped, "completed-disposition")
    assert recovery.prepare(first, stopped.history, None, refresh_module=refresh) == "1" * 32
    completed = completed_serving_history(stopped).parent
    shutil.copytree(first / "pre-native-reconciliations", completed / "pre-native-reconciliations")
    target = output(stopped, "after-completion")
    assert recovery.prepare(target, stopped.history, None, refresh_module=refresh) is None
    assert not list(target.iterdir())
    assert_no_native_writes(stopped)


def test_completed_v3_successor_receipt_consumes_build_without_reopening_it(stopped, monkeypatch):
    monkeypatch.setattr(recovery.uuid, "uuid4", lambda: SimpleNamespace(hex="1" * 32))
    first = output(stopped, "v3-completed-disposition")
    assert recovery.prepare(first, stopped.history, None, refresh_module=refresh) == "1" * 32
    completed = completed_v3_history(stopped).parent
    shutil.copytree(first / "pre-native-reconciliations", completed / "pre-native-reconciliations")
    target = output(stopped, "after-v3-completion")
    assert recovery.prepare(target, stopped.history, None, refresh_module=refresh) is None
    assert not list(target.iterdir())
    assert_no_native_writes(stopped)


@pytest.mark.parametrize("change", ["case_count", "policy", "result_link", "transition_acceptance", "extra_cleanup"])
def test_completed_v3_history_rejects_tampering_before_provider(stopped, change):
    native = completed_v3_history(stopped)
    root = native.parent
    if change == "extra_cleanup":
        (root / "prior-native-retirement").mkdir(mode=0o700)
    else:
        path = {"case_count": native / "acceptance.json", "policy": native / "acceptance.json",
                "result_link": root / "result.json",
                "transition_acceptance": root / "transition/native-acceptance.json"}[change]
        row = json.loads(path.read_bytes())
        if change == "case_count":
            row["required_case_count"] = 10
        elif change == "policy":
            row["policy_sha256"] = probe.POLICY
        elif change == "transition_acceptance":
            row["receipt_sha256"] = "0" * 64
        else:
            row["native_acceptance_sha256"] = "0" * 64
        path.write_bytes(recovery.canonical(row))
    with pytest.raises(refresh.RefreshDenied, match="completed_v3_receipt_changed"):
        recovery.completed_serving(root, json.loads((root / "result.json").read_bytes()), native,
                                   refresh, probe, allow_v3=True)
    assert_no_native_writes(stopped)


def test_completed_v3_history_never_authorizes_exact_v2_rescue(stopped):
    native = completed_v3_history(stopped)
    root = native.parent
    with pytest.raises(refresh.RefreshDenied, match="completed_v3_receipt_changed"):
        recovery.completed_serving(root, json.loads((root / "result.json").read_bytes()), native,
                                   refresh, probe)
    assert_no_native_writes(stopped)


def test_expired_v2_history_is_historical_only_for_explicit_rescue(stopped, monkeypatch):
    strict, retired = [], []
    old_journal = completed_serving_history(stopped)
    monkeypatch.setattr(serving.transition, "verify_rescue_caller_pins",
                        lambda clients, contract, image: contract.verify_caller_pins(clients, image))

    def fresh_only(image, metadata):
        strict.append((image, metadata["uid"]))
        raise probe.NativeProofDenied("document_native_definitions_expired")

    def old_identity_only(image, metadata):
        assert image == stopped.native_http.services["hirewiz-document-inspector"]["template"]["containers"][0]["image"]
        retired.append(image)
        return None

    monkeypatch.setattr(probe, "verify_current_successor", fresh_only)
    monkeypatch.setattr(probe, "verify_expired_current_v2_for_retirement", old_identity_only)
    default = output(stopped, "default")
    with pytest.raises(probe.NativeProofDenied, match="definitions_expired"):
        recovery.prepare(default, stopped.history, None, refresh_module=refresh)
    assert strict and not retired and not list(default.iterdir())
    manual = output(stopped, "manual")
    assert recovery.prepare(manual, stopped.history, None, refresh_module=refresh,
                            rescue_expired_v2=True, rescue_prior_journal=old_journal) == NEXT_NONCE
    assert len(retired) == 1 and not (manual / "native-proof").exists()
    assert json.loads((manual / "pre-native-reconciliations" / NONCE / "reconciliation.json").read_bytes())[
        "native_acceptance"] is False
    assert_no_native_writes(stopped)


def test_expired_rescue_rejects_ambiguous_mode_and_changed_caller_pin(stopped, monkeypatch):
    old_journal = completed_serving_history(stopped)
    monkeypatch.setattr(serving.transition, "verify_rescue_caller_pins",
                        lambda clients, contract, image: contract.verify_caller_pins(clients, image))
    destination = output(stopped, "refused")
    with pytest.raises(refresh.RefreshDenied, match="recovery_mode_invalid"):
        recovery.prepare(destination, stopped.history, "hirewiz-doc-six-" + NONCE[:12],
                         refresh_module=refresh, rescue_expired_v2=True, rescue_prior_journal=old_journal)
    assert not list(destination.iterdir())
    monkeypatch.setattr(probe, "verify_expired_current_v2_for_retirement", lambda *_: None)
    caller = stopped.native_http.services["ai-resume-parser"]["template"]["containers"][0]["env"]
    next(row for row in caller if row["name"] == "DOCUMENT_WORKER_IMAGE_DIGEST")["value"] = "unreviewed"
    with pytest.raises(probe.NativeProofDenied):
        recovery.prepare(destination, stopped.history, None, refresh_module=refresh,
                         rescue_expired_v2=True, rescue_prior_journal=old_journal)
    assert not list(destination.iterdir()) and stopped.native_http.writes == []


@pytest.mark.parametrize("changed", ["receipt", "completion", "journal"])
def test_rescue_rejects_changed_completed_serving_artifact_before_provider(stopped, changed):
    old_journal = completed_serving_history(stopped)
    root = old_journal.parent
    if changed == "receipt":
        path = old_journal / "acceptance.json"
        row = json.loads(path.read_bytes())
        row["receipt_sha256"] = "0" * 64
        path.write_bytes(recovery.canonical(row))
    elif changed == "completion":
        path = root / "transition/complete.json"
        row = json.loads(path.read_bytes())
        row["native_acceptance_sha256"] = "0" * 64
        path.write_bytes(recovery.canonical(row))
    else:
        old_journal = stopped.history / "wrong-artifact/native-proof"
    target = output(stopped, "refused-completed")
    with pytest.raises(refresh.RefreshDenied, match="completed_(receipt|journal)_changed"):
        recovery.prepare(target, stopped.history, None, refresh_module=refresh,
                         rescue_expired_v2=True, rescue_prior_journal=old_journal)
    assert not stopped.http_calls and not stopped.native_calls and not list(target.iterdir())


def test_rescue_classifies_two_abandoned_builds_and_one_completed_serving_artifact(stopped, monkeypatch):
    old_journal = completed_serving_history(stopped)
    second = stopped.history / "second-abandoned-build"
    shutil.copytree(stopped.original, second)
    second_identity = second / "native-build-identity.json"
    row = json.loads(second_identity.read_bytes())
    row["nonce"] = "b" * 32
    second_identity.write_bytes(recovery.canonical(row))
    observed = []

    def verify_pre_native(files, clients, contract, build):
        nonce = json.loads(files["native-build-identity.json"])["nonce"]
        observed.append(nonce)
        return {"nonce": nonce, "image": "synthetic-unaccepted-" + nonce,
                "absent_native_job": probe.PARENT + "/jobs/hirewiz-doc-six-" + nonce[:12]}

    monkeypatch.setattr(recovery, "verify_pre_native", verify_pre_native)
    monkeypatch.setattr(recovery, "joined", lambda *args, **kwargs:
                        observed.append((kwargs["rescue_expired_v2"], args[3])))
    target = output(stopped, "three-artifacts")
    assert recovery.prepare(target, stopped.history, None, refresh_module=refresh,
                            rescue_expired_v2=True, rescue_prior_journal=old_journal) == NEXT_NONCE
    assert set(observed[:2]) == {NONCE, "b" * 32}
    assert observed[2][0] is True
    assert set((target / "pre-native-reconciliations").iterdir()) == {
        target / "pre-native-reconciliations" / NONCE,
        target / "pre-native-reconciliations" / ("b" * 32)}
    assert not (target / "native-proof").exists() and not (target / "transition").exists()
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
