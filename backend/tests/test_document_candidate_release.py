"""Synthetic negative controls; these tests never assert native acceptance."""
from __future__ import annotations

import copy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
import test_document_candidate_probe as fixtures
import test_document_refresh_transition as maintenance
import test_document_worker_deployment as worker_fixtures
from infra.gcp import document_candidate_release as r
from infra.gcp import document_worker as worker


def evidence(monkeypatch):
    inventory, corpus = fixtures.inventory(), fixtures.value()
    for row in (inventory, corpus):
        row["worker_image"] = r.profile.IMAGE
        row["identity_preflight"].update(renderer_version=r.profile.RENDERER,
                                         font_manifest_sha256=r.profile.FONT)
    monkeypatch.setattr(r, "INVENTORY_RECEIPT_SHA256", r.candidate.sha(inventory))
    return inventory, corpus


def acceptance():
    return r.prior.NativeAcceptance(r.profile.IMAGE, r.BUILD_ID, "owned-build", "a" * 64,
        "b" * 64, {}, r.prior.PARENT + "/jobs/" + r.LABELS["hirewiz-refresh-job"],
        r.HANDLES["corpus"]["job_uid"], "owned-execution", r.HANDLES["corpus"]["execution_uid"],
        r.PAYLOAD_SHA256, "d" * 64, r.POLICY, r.profile.FONT, r.profile.RENDERER,
        "2026-10-30T00:00:00+00:00", 3, 11)


def test_exact_candidate_source_and_native_job_requests_stay_bound():
    assert len(r.source_contract()) == 20
    assert len(r.HANDLES) == 2
    assert r.HANDLES["inventory"]["nonce"] != r.HANDLES["corpus"]["nonce"] != r.BUILD_NONCE


def test_actual_evidence_contract_requires_both_inventory_and_eleven_cases(monkeypatch):
    inventory, corpus = evidence(monkeypatch)
    expires = r.validate_evidence(inventory, corpus, r.source_contract(), now=fixtures.historical.NOW)
    assert expires
    corpus["cases"].pop()
    with pytest.raises(r.prior.NativeProofDenied, match="corpus_invalid"):
        r.validate_evidence(inventory, corpus, r.source_contract(), now=fixtures.historical.NOW)


@pytest.mark.parametrize("change", ["inventory_hash", "runtime", "font", "policy", "cleanup", "stale"])
def test_stale_cross_profile_or_incomplete_evidence_refuses(monkeypatch, change):
    inventory, corpus = evidence(monkeypatch)
    now = fixtures.historical.NOW
    if change == "inventory_hash":
        inventory["elapsed_ms"] += 1
    elif change == "runtime":
        corpus["identity_preflight"]["runtime_hashes"]["/opt/document/docx_renderer.py"] = "0" * 64
    elif change == "font":
        corpus["identity_preflight"]["font_manifest_sha256"] = r.prior.FONT
    elif change == "policy":
        corpus["policy_sha256"] = r.prior.POLICY
    elif change == "cleanup":
        corpus["cases"][-1]["diagnostic"]["cleanup_code"] = 1
    else:
        now += timedelta(days=1)
    with pytest.raises(r.prior.NativeProofDenied):
        r.validate_evidence(inventory, corpus, r.source_contract(), now=now)


@pytest.mark.parametrize("change", ["missing_case", "wrong_policy", "unknown_policy", "v2_label"])
def test_transition_does_not_mislabel_or_downgrade_v3_authority(change):
    value = acceptance()
    if change == "missing_case":
        value = replace(value, required_case_count=10)
    elif change == "wrong_policy":
        value = replace(value, policy_sha256=r.prior.POLICY)
    elif change == "unknown_policy":
        value = replace(value, policy_sha256="0" * 64)
    elif change == "v2_label":
        value = replace(value, contract_version=2, required_case_count=10)
    with pytest.raises(r.transition.TransitionDenied, match="current_acceptance_required"):
        r.transition.require_current_acceptance(value, value.image, r)


def test_v2_authority_cannot_claim_candidate_image():
    value = replace(acceptance(), policy_sha256=r.prior.POLICY, contract_version=2, required_case_count=10)
    with pytest.raises(r.transition.TransitionDenied):
        r.transition.require_current_acceptance(value, r.profile.IMAGE, r.prior)


def test_native_replacement_of_known_job_is_refused_before_payload_read():
    handle = r.HANDLES["inventory"]
    job = fixtures.historical.job(r.profile.IMAGE, handle["nonce"])
    job.update(r.candidate.desired(r.profile.IMAGE, handle["nonce"], "inventory"))
    client = SimpleNamespace(job=lambda _: job)
    with pytest.raises(r.prior.NativeProofDenied, match="job_replaced"):
        r._observation(client, "inventory")


def test_known_worker_configuration_selects_exact_policy_without_acceptance_claim():
    snapshot = worker_fixtures.snapshots(worker)
    for value in (snapshot["service"]["template"], snapshot["revision"]):
        value["containers"][0]["image"] = r.profile.IMAGE
        value["containers"][0]["env"] = worker.render(r.profile.IMAGE, worker_fixtures.RELEASE,
            candidate_v3=True)["service"]["template"]["containers"][0]["env"]
    result = worker.check_snapshot(snapshot, r.profile.IMAGE, worker_fixtures.RELEASE, candidate_v3=True)
    assert result["runtime_isolation_proven"] is False
    assert r.prior.policy_for_serving(snapshot["service"], r.profile.IMAGE) == r.POLICY
    with pytest.raises(worker.ConfigurationDenied):
        worker.check_snapshot(snapshot, r.profile.IMAGE, worker_fixtures.RELEASE)
    # A metadata shape check admits no build authority. Successors still need
    # the fixed native parent, runtime hashes, inventory and all eleven cases.
    assert worker.render(fixtures.IMAGE, worker_fixtures.RELEASE, candidate_v3=True)["api_wiring_enabled"] is False
    snapshot["service"]["template"]["containers"][0]["env"][-1]["value"] = r.prior.POLICY
    with pytest.raises(r.prior.NativeProofDenied, match="serving_policy"):
        r.prior.policy_for_serving(snapshot["service"], r.profile.IMAGE)


def test_exact_v3_promotes_worker_then_three_pins_and_restores_captured_flags(monkeypatch, tmp_path):
    http = maintenance.Http()
    before = copy.deepcopy(http.services)
    # Exercise absent and false flags as well as true; restore means the actual
    # captured state, not universally enabling admission.
    name = r.transition.CALLERS[1]
    env = http.services[name]["template"]["containers"][0]["env"]
    env[:] = [row for row in env if row["name"] != r.transition.PAUSE[name][1]]
    next(row for row in env if row["name"] == r.transition.PAUSE[name][0])["value"] = "false"
    before[name] = copy.deepcopy(http.services[name])
    checks = []

    def pins(clients, image, *, policy_sha256=None):
        if image == r.profile.IMAGE:
            return r.verify_caller_pins(clients, image, policy_sha256=policy_sha256)
        assert image == maintenance.profile.IMAGE and policy_sha256 == r.prior.POLICY

    facade = SimpleNamespace(POLICY=r.POLICY, BASE_IMAGE=r.BASE_IMAGE, SERVICE_UID=r.SERVICE_UID,
        validate_serving=r.validate_serving, policy_for_serving=r.policy_for_serving,
        verify_successor=lambda *_: acceptance(),
        verify_current_successor=lambda image, _: checks.append(image), verify_caller_pins=pins)
    result = r.transition.transition(r.profile.IMAGE, {"build_id": r.BUILD_ID, "nonce": r.BUILD_NONCE},
        tmp_path / "native", tmp_path / "rollout", http=http, probe=facade)
    assert [name for name, _ in http.writes] == [*r.transition.CALLERS, r.transition.WORKER,
                                                *r.transition.CALLERS, *r.transition.CALLERS]
    for name in r.transition.CALLERS:
        current = r.transition.environment(http.services[name]["template"])
        original = r.transition.environment(before[name]["template"])
        assert all(current.get(key) == original.get(key) for key in r.transition.PAUSE[name])
        assert all(current[key] == {"name": key, "value": value} for key, value in r.environment().items())
        assert http.services[name]["template"]["containers"][0]["image"] == maintenance.APP
        assert current["DATABASE_URL"] == original["DATABASE_URL"]
    assert http.services[r.transition.WORKER]["labels"].items() >= r.LABELS.items()
    assert result["admission_restored"] is True
    assert checks == [maintenance.profile.IMAGE, r.profile.IMAGE, r.profile.IMAGE, r.profile.IMAGE]


def test_unobserved_build_cannot_enter_native_verification(monkeypatch, tmp_path):
    with pytest.raises(r.prior.NativeProofDenied, match="candidate_changed"):
        r.verify_successor(r.profile.IMAGE, {"status": "SUCCESS"}, tmp_path)
