"""Expired predecessor recovery stays closed until exact retirement proof succeeds."""
from __future__ import annotations

import asyncio
import copy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from backend.tests import test_document_refresh_probe as native_fixtures
from backend.tests import test_document_refresh_transition as fixtures
from fastapi import HTTPException
from infra.gcp import document_candidate_release as candidate_release
from infra.gcp import document_refresh_probe as old_probe

from scripts import document_refresh as orchestrator

V2_POLICY = "d3baf46026934f48b8def219b48d348d7b03bc3b4d0cb713a6f222c7299df7f2"
V3_POLICY = "8ed5f38142d88d88883129681953e939e79157e8b92e3efc516920d2e4409c9f"


def recovery_probe(http, *, retire_error=None, current_error="document_native_definitions_expired"):
    accepted = old_probe.NativeAcceptance(
        fixtures.NEW, "01234567-0123-0123-0123-012345678901", "build", "a" * 64,
        "b" * 64, {}, "job", "job-uid", "execution", "execution-uid", "c" * 64,
        "d" * 64, V3_POLICY, fixtures.profile.FONT, fixtures.profile.RENDERER,
        "2099-01-01T00:00:00+00:00", 3, 11,
    )
    calls = []

    def current(image, metadata):
        if image != fixtures.NEW:
            raise old_probe.NativeProofDenied(current_error)
        calls.append("new-current")

    def retirement(image, metadata):
        assert image == fixtures.profile.IMAGE
        assert metadata == http.services[fixtures.transition.WORKER]
        assert len(http.writes) == 3
        for name in fixtures.transition.CALLERS:
            env = fixtures.transition.environment(http.services[name]["template"])
            assert all(env[key]["value"] == "false" for key in fixtures.transition.PAUSE[name])
        calls.append("retired-only")
        if retire_error:
            raise old_probe.NativeProofDenied(retire_error)

    def policy(metadata, image):
        env = fixtures.transition.environment(metadata["template"])
        return env["DOCUMENT_WORKER_POLICY_SHA256"]["value"]

    return SimpleNamespace(
        POLICY=V3_POLICY, BASE_IMAGE=old_probe.BASE_IMAGE, SERVICE_UID=old_probe.SERVICE_UID,
        NativeProofDenied=old_probe.NativeProofDenied,
        verify_successor=lambda *args: accepted,
        verify_current_successor=current,
        verify_expired_current_v2_for_retirement=retirement,
        verify_caller_pins=lambda *args, **kwargs: calls.append("pins"),
        validate_serving=old_probe.validate_serving,
        policy_for_serving=policy,
    ), calls


def promote(tmp_path, http, probe, *, recovery=True):
    return fixtures.transition.transition(
        fixtures.NEW, {"nonce": fixtures.NONCE, "build_id": "01234567-0123-0123-0123-012345678901"},
        tmp_path / "native", tmp_path / "transition", http=http, probe=probe,
        retire_expired_v2=recovery,
    )


def test_expired_v2_retirement_runs_only_after_all_three_callers_closed(tmp_path):
    http = fixtures.Http()
    probe, calls = recovery_probe(http)
    outcome = promote(tmp_path, http, probe)
    assert outcome["admission_restored"] is True
    assert "retired-only" in calls
    assert [name for name, _ in http.writes] == [*fixtures.transition.CALLERS,
        fixtures.transition.WORKER, *fixtures.transition.CALLERS, *fixtures.transition.CALLERS]
    record = (tmp_path / "transition" / "expired-v2-retirement-only.json").read_text()
    assert '"serving_authority":false' in record


def test_recovery_mode_requires_exact_expiry_error_before_any_patch(tmp_path):
    http = fixtures.Http()
    probe, _ = recovery_probe(http, current_error="document_native_current_service_changed")
    with pytest.raises(fixtures.transition.TransitionDenied, match="expired_predecessor_refused"):
        promote(tmp_path, http, probe)
    assert http.writes == []


def test_expired_predecessor_without_recovery_mode_stops_before_any_patch(tmp_path):
    http = fixtures.Http()
    probe, _ = recovery_probe(http)
    with pytest.raises(fixtures.transition.TransitionDenied, match="expired_predecessor_refused"):
        promote(tmp_path, http, probe, recovery=False)
    assert http.writes == []


def test_retirement_identity_failure_keeps_admission_closed_and_worker_unchanged(tmp_path):
    http = fixtures.Http()
    probe, _ = recovery_probe(http, retire_error="document_native_current_service_changed")
    before = copy.deepcopy(http.services[fixtures.transition.WORKER])
    with pytest.raises(old_probe.NativeProofDenied, match="current_service_changed"):
        promote(tmp_path, http, probe)
    assert [name for name, _ in http.writes] == list(fixtures.transition.CALLERS)
    assert http.services[fixtures.transition.WORKER] == before


def test_real_v2_retirement_verifier_accepts_only_expired_exact_current_identity(monkeypatch):
    fake = native_fixtures.Fake()
    fake.created = fake.ran = True
    fake.j, fake.ex = native_fixtures.completed()
    service = native_fixtures.service()
    fake.service = lambda _: copy.deepcopy(service)
    fake.revision = lambda _: native_fixtures.revision(service)
    stale = native_fixtures.payload(execution=fake.ex["name"].rsplit("/", 1)[-1])
    for case in stale["cases"]:
        if "scan" in case:
            case["scan"]["definitions"] = "28155/" + (
                native_fixtures.NOW - timedelta(days=4)).isoformat()
    fake.entries = lambda query, token: {"entries": [native_fixtures.event(fake.j, fake.ex, stale)]}
    monkeypatch.setattr(old_probe, "Clients", lambda **kwargs: fake)
    monkeypatch.setattr(old_probe, "build_module", lambda: SimpleNamespace(
            verify_current_build=lambda *args, **kwargs: native_fixtures.build(),
        verify_current_build_release=lambda *args, **kwargs: native_fixtures.build()))

    with pytest.raises(old_probe.NativeProofDenied, match="definitions_expired"):
        old_probe.verify_current_successor_for_release(native_fixtures.IMAGE, service)
    assert old_probe.verify_expired_current_v2_for_retirement(native_fixtures.IMAGE, service) is None
    tampered = copy.deepcopy(service)
    tampered["labels"]["hirewiz-refresh-nonce"] = "f" * 32
    with pytest.raises(old_probe.NativeProofDenied):
        old_probe.verify_expired_current_v2_for_retirement(native_fixtures.IMAGE, tampered)
    for case in stale["cases"]:
        if "scan" in case:
            case["scan"]["definitions"] = "28155/" + (
                native_fixtures.NOW - timedelta(hours=1)).isoformat()
    with pytest.raises(old_probe.NativeProofDenied, match="retirement_requires_expired_v2"):
        old_probe.verify_expired_current_v2_for_retirement(native_fixtures.IMAGE, service)
    legacy = copy.deepcopy(service)
    next(row for row in legacy["template"]["containers"][0]["env"]
         if row["name"] == "DOCUMENT_WORKER_POLICY_SHA256")["value"] = old_probe.LEGACY_POLICY
    with pytest.raises(old_probe.NativeProofDenied, match="retirement_requires_v2"):
        old_probe.verify_expired_current_v2_for_retirement(native_fixtures.IMAGE, legacy)
    assert fake.calls["create"] == fake.calls["start"] == fake.calls["delete"] == 0


def test_actual_candidate_module_probe_retires_only_after_all_callers_close(monkeypatch, tmp_path):
    http = fixtures.Http()
    accepted = old_probe.NativeAcceptance(
        fixtures.NEW, "01234567-0123-0123-0123-012345678901", "build", "a" * 64,
        "b" * 64, {}, "job", "job-uid", "execution", "execution-uid", "c" * 64,
        "d" * 64, V3_POLICY, fixtures.profile.FONT, fixtures.profile.RENDERER,
        "2099-01-01T00:00:00+00:00", 3, 11,
    )
    calls = []

    def current(image, metadata):
        if image != fixtures.NEW:
            raise old_probe.NativeProofDenied("document_native_definitions_expired")
        calls.append("new-current")
        return accepted

    def retirement(image, metadata, *, read_only_release):
        assert image == fixtures.profile.IMAGE
        assert metadata == http.services[fixtures.transition.WORKER]
        assert read_only_release is False
        assert len(http.writes) == 3
        for name in fixtures.transition.CALLERS:
            env = fixtures.transition.environment(http.services[name]["template"])
            assert all(env[key]["value"] == "false" for key in fixtures.transition.PAUSE[name])
        calls.append("maintenance-retirement")

    monkeypatch.setattr(candidate_release, "verify_successor", lambda *args: accepted)
    monkeypatch.setattr(candidate_release, "verify_current_successor", current)
    monkeypatch.setattr(candidate_release, "verify_caller_pins", lambda *args, **kwargs: None)
    monkeypatch.setattr(candidate_release.prior, "verify_expired_current_v2_for_retirement", retirement)
    outcome = candidate_release.transition.transition(fixtures.NEW,
        {"nonce": fixtures.NONCE, "build_id": "01234567-0123-0123-0123-012345678901"},
        tmp_path / "native", tmp_path / "transition", http=http,
        probe=candidate_release, retire_expired_v2=True)
    assert outcome["admission_restored"] is True
    assert calls == ["maintenance-retirement", "new-current", "new-current", "new-current"]


def test_release_probe_retirement_uses_read_only_owner_route(monkeypatch):
    observed = []
    monkeypatch.setattr(candidate_release.prior, "verify_expired_current_v2_for_retirement",
        lambda image, metadata, *, read_only_release: observed.append(read_only_release))
    candidate_release.ReleaseProbe.verify_expired_current_v2_for_retirement(
        fixtures.profile.IMAGE, {})
    assert observed == [True]


def test_legacy_parse_respects_closed_document_admission(monkeypatch):
    from app.routers import resume

    monkeypatch.setenv("RESUME_DIRECT_UPLOAD_ENABLED", "false")
    with pytest.raises(HTTPException) as caught:
        asyncio.run(resume.parse_resume.__wrapped__(request=None, file=None, db=None, current_user=None))
    assert caught.value.status_code == 503


def test_explicit_refresh_dispatch_routes_expired_v2_to_v3_without_old_builder(monkeypatch, tmp_path):
    calls = []
    old = native_fixtures.IMAGE
    probe = SimpleNamespace(POLICY=V2_POLICY, BASE_IMAGE=old_probe.BASE_IMAGE,
        Clients=lambda: SimpleNamespace(preflight=lambda: {"service": {
            "template": {"containers": [{"image": old}]}}}, close=lambda: calls.append("closed")),
        policy_for_serving=lambda *_: V2_POLICY)
    modules = {
        "document_refresh_recovery": SimpleNamespace(prepare=lambda *args, **kwargs:
            calls.append(("prepare", kwargs["retire_expired_v2"])) or "a" * 32),
        "document_refresh_probe": probe,
        "document_candidate_release": SimpleNamespace(refresh=lambda *args, **kwargs:
            calls.append((args, kwargs)) or {"status": "v3"}),
    }
    monkeypatch.setattr(orchestrator, "infra", modules.__getitem__)
    monkeypatch.setattr(orchestrator, "candidate", lambda *args, **kwargs:
        pytest.fail("V2 builder must not run in explicit V3 recovery"))
    result = orchestrator.refresh(tmp_path, publish=True, now=native_fixtures.NOW,
                                  retire_expired_v2=True)
    assert result == {"status": "v3"}
    assert calls == [("prepare", True), "closed", ((tmp_path,), {"publish": True, "now": native_fixtures.NOW,
                                         "nonce": "a" * 32, "retire_expired_v2": True})]


@pytest.mark.parametrize("changes", [{"publish": False}, {"full_source": True},
                                    {"recover_failed_native_job": "hirewiz-doc-six-" + "a" * 12}])
def test_expired_v2_route_refuses_mode_combinations_before_cloud_or_build(monkeypatch, tmp_path, changes):
    monkeypatch.setattr(orchestrator, "infra", lambda _: pytest.fail("No module or provider call"))
    with pytest.raises(orchestrator.RefreshDenied, match="expired_v2_requires_explicit_v3_refresh"):
        arguments = {"publish": True, "now": native_fixtures.NOW, "retire_expired_v2": True}
        arguments.update(changes)
        orchestrator.refresh(tmp_path, **arguments)


def test_scheduled_event_cannot_request_retirement_mode(monkeypatch, tmp_path):
    import sys

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.setattr(sys, "argv", ["document_refresh.py", "--mode", "refresh",
        "--publish-candidate", "--retire-expired-v2", "--output-directory", str(tmp_path / "output")])
    with pytest.raises(orchestrator.RefreshDenied, match="expired_v2_requires_dispatch"):
        orchestrator.main()
    assert not (tmp_path / "output").exists()


def test_candidate_release_expired_v2_preflight_preserves_retirement_mode(monkeypatch, tmp_path):
    calls = []
    old = native_fixtures.IMAGE
    image = old_probe.REPOSITORY + "@sha256:" + "9" * 64
    service = {"template": {"containers": [{"image": old}]}}
    monkeypatch.setattr(candidate_release, "source_contract", lambda: {})
    monkeypatch.setattr(candidate_release.prior, "Clients", lambda: SimpleNamespace(
        preflight=lambda: {"service": service}, close=lambda: calls.append("closed")))
    monkeypatch.setattr(candidate_release, "policy_for_serving", lambda *args: V2_POLICY)
    monkeypatch.setattr(candidate_release.prior, "verify_current_successor", lambda *args:
        (_ for _ in ()).throw(old_probe.NativeProofDenied("document_native_definitions_expired")))
    monkeypatch.setattr(candidate_release, "verify_caller_pins", lambda *args, **kwargs: calls.append("pins"))
    monkeypatch.setattr(candidate_release.prior, "build_module", lambda: SimpleNamespace(
        native_build=lambda *args, **kwargs: calls.append(("build", kwargs)) or {"immutable_image": image},
        native_http=lambda: SimpleNamespace(close=lambda: calls.append("http_closed"))))
    monkeypatch.setattr(candidate_release, "run_successor", lambda *args: SimpleNamespace(
        definition_expires_at="2099-01-01T00:00:00+00:00"))
    monkeypatch.setattr(candidate_release.transition, "transition", lambda *args, **kwargs:
        calls.append(("transition", kwargs["retire_expired_v2"])) or {"status": "serving"})
    result = candidate_release.refresh(tmp_path, publish=True, now=native_fixtures.NOW,
                                       nonce="a" * 32, retire_expired_v2=True)
    assert result["status"] == "serving"
    assert ("transition", True) in calls
    assert ("build", {"nonce": "a" * 32, "candidate_v3": True}) in calls


def test_candidate_release_rejects_unexpected_v2_proof_failure_before_build(monkeypatch, tmp_path):
    old = native_fixtures.IMAGE
    monkeypatch.setattr(candidate_release, "source_contract", lambda: {})
    monkeypatch.setattr(candidate_release.prior, "Clients", lambda: SimpleNamespace(
        preflight=lambda: {"service": {"template": {"containers": [{"image": old}]}}},
        close=lambda: None))
    monkeypatch.setattr(candidate_release, "policy_for_serving", lambda *args: V2_POLICY)
    monkeypatch.setattr(candidate_release.prior, "verify_current_successor", lambda *args:
        (_ for _ in ()).throw(old_probe.NativeProofDenied("document_native_current_service_changed")))
    monkeypatch.setattr(candidate_release.prior, "build_module", lambda:
        pytest.fail("Wrong predecessor proof must not build"))
    with pytest.raises(old_probe.NativeProofDenied, match="expired_v2_predecessor_unverified"):
        candidate_release.refresh(tmp_path, publish=True, now=native_fixtures.NOW,
                                  retire_expired_v2=True)
