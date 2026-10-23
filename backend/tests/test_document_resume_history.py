"""Four finite history controls; synthetic provider observations, no live calls."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from backend.tests.test_document_candidate_resume import Storage, canonical, digest, partial
from infra.gcp import document_refresh_recovery as recovery
from infra.gcp import document_refresh_resume as resume
from infra.gcp import document_renderer_profiles as profile

from scripts import document_refresh as refresh

# Importing this fixture exposes only the owned synthetic nineteen-file tree.
__all__ = ["partial"]


def context(partial):
    history, output = partial.tmp / "history", partial.tmp / "next"
    history.mkdir(mode=0o700)
    output.mkdir(mode=0o700)
    resume.retain_partial(partial.values, history / "same-partial")
    previous = partial.build.infra
    partial.build.infra = lambda name: {
        "document_refresh_resume": resume, "document_refresh_recovery": recovery,
        "document_renderer_profiles": profile}.get(name) or previous(name)
    partial.build.native_http = lambda: pytest.fail("No provider write during same-operation history admission")
    return history, output


def test_explicit_same_partial_is_admitted_without_issuing_another_recovery_claim(partial):
    history, output = context(partial)
    assert recovery.prepare(output, history, None, refresh_module=partial.build,
                            resuming_original=partial.root) is None
    assert not list(output.iterdir())
    assert resume.partial_snapshot(history / "same-partial", partial.build) == partial.values


def test_same_operation_does_not_hide_a_separate_unknown_history_run(partial):
    history, output = context(partial)
    unknown = history / "unknown"
    unknown.mkdir(mode=0o700)
    refresh.write_new(unknown / "build-create-started.json", {"automatic_replay": False})
    with pytest.raises(refresh.RefreshDenied, match="prior_unknown_reconcile_only"):
        recovery.prepare(output, history, None, refresh_module=partial.build, resuming_original=partial.root)
    assert not list(output.iterdir())


def completed(partial):
    history, output = context(partial)
    run = history / "completed-resume"
    run.mkdir(mode=0o700)
    resume.retain_partial(partial.values, run / "original-candidate")
    refresh.write_new(run / "local-native-proof.json", {"synthetic_completed_local_25_case_proof": True})
    identity = {**copy.deepcopy(profile.BUILD), "source_sha256": copy.deepcopy(profile.SOURCES),
                "local_native_proof_sha256": digest((run / "local-native-proof.json").read_bytes())}
    storage = Storage(partial.root, run)
    partial.build.native_http = lambda: storage
    resume.claim(run, partial.root, identity, partial.manifest, partial.build)
    refresh.write_new(run / "resume-build-identity.json", identity)
    accepted = {"image": profile.IMAGE, "build_id": profile.BUILD["build_id"], "source_sha256": profile.SOURCES,
                "policy_sha256": profile.BUILD["policy_sha256"], "font_manifest_sha256": profile.FONT,
                "renderer_version": profile.RENDERER, "contract_version": 2, "required_case_count": 10}
    (run / "transition").mkdir(mode=0o700)
    refresh.write_new(run / "transition/native-acceptance.json", accepted)
    complete = {"status": "document_refresh_serving", "image": profile.IMAGE, "admission_restored": True,
                "native_acceptance_sha256": digest(canonical(accepted))}
    refresh.write_new(run / "transition/complete.json", complete)
    refresh.write_new(run / "result.json", {**complete, "original_manifest_sha256": partial.manifest,
                                            "resumed_existing_build": True})
    calls = []
    current = refresh.REPOSITORY + "@sha256:" + "b" * 64
    worker = {"template": {"containers": [{"image": current}]}}
    class Clients:
        def preflight(self):
            calls.append("fresh_current_preflight")
            return {"service": worker}
        def job(self, *_):
            pytest.fail("A retired old Job must not be fetched to consume this Build-only partial")
        def close(self):
            pass
    def verify(image, observed):
        assert image == current and observed == worker
        calls.append("fresh_current_v2_ten")
        return SimpleNamespace(contract_version=2, required_case_count=10, policy_sha256=profile.BUILD["policy_sha256"])
    def pins(_clients, image):
        assert image == current
        calls.append("fresh_current_caller_pins")
    probe = SimpleNamespace(Clients=Clients, POLICY=profile.BUILD["policy_sha256"],
                            verify_current_successor=verify, verify_caller_pins=pins)
    previous = partial.build.infra
    partial.build.infra = lambda name: probe if name == "document_refresh_probe" else previous(name)
    storage.calls.clear()
    return history, output, storage, calls


def test_completed_resume_consumes_only_the_exact_partial_with_fresh_claim_and_current_native_proof(partial):
    history, output, storage, calls = completed(partial)
    assert recovery.prepare(output, history, None, refresh_module=partial.build) is None
    assert storage.calls == ["GET"]  # The existing generation-bound claim is read, never created again.
    assert calls == ["fresh_current_preflight", "fresh_current_v2_ten", "fresh_current_caller_pins"]
    assert not list(output.iterdir())


def test_changed_consumed_claim_never_turns_the_partial_into_valid_history(partial):
    history, output, storage, calls = completed(partial)
    storage.object += b"changed immutable claim"
    with pytest.raises(refresh.RefreshDenied, match="completed_claim_unverified"):
        recovery.prepare(output, history, None, refresh_module=partial.build)
    assert storage.calls == ["GET"] and not calls and not list(output.iterdir())
