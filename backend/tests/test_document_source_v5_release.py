"""No-cloud V5 Job, serving and transition checks."""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
import test_document_refresh_transition as lifecycle
from infra.gcp import document_candidate as courier
from infra.gcp import document_refresh_transition as transition
from infra.gcp import document_source_candidate as v4
from infra.gcp import document_source_candidate_v5 as v5
from infra.gcp import document_source_release as release
from infra.gcp import document_worker as worker
from test_document_source_revision import accepted

IMAGE = lifecycle.NEW
PARENT = lifecycle.OLD_V2
NONCE = lifecycle.NONCE


@pytest.fixture
def probe(monkeypatch):
    contents, hashes, _ = v5.working_snapshot()
    source = v5.Source("a" * 40, hashes, contents[v5.POLICY_PATH], "b" * 64)
    monkeypatch.setattr(v5, "committed", lambda commit, *, root: source)
    build = {"version": 1, "nonce": NONCE, "immutable_image": IMAGE,
        "build_id": "01234567-0123-0123-0123-012345678901", "fixed_renderer_base": courier.IMAGE,
        "build_resource": "synthetic-build", "builder_service_account": "synthetic-builder",
        "source_bucket": "synthetic-bucket", "source_object": "synthetic-object", "source_generation": "11",
        "source_archive_sha256": "d" * 64, "refresh_dockerfile_sha256": "e" * 64,
        "local_native_proof_sha256": "f" * 64, **source.identity}
    return release.SourceProbe(build)


def test_v5_exact_job_and_worker_transport_modes(probe):
    for mode, handle in probe.handles(probe.build).items():
        job = probe.desired(IMAGE, handle["nonce"], mode)
        task = job["template"]["template"]
        container = task["containers"][0]
        assert container["env"][-1] == {"name": v5.TRANSPORT_ENV, "value": v5.TRANSPORT_MODE}
        assert task["maxRetries"] == 0 and container["sandboxLauncher"] is True
        assert "hirewiz_document_source_required_v5" in container["args"][2]
    proposal = worker.render(IMAGE, probe.source.commit, source_v5=True)
    service = proposal["service"]
    assert probe.policy_for_serving(service, IMAGE) == v5.POLICY_SHA256
    service["template"]["containers"][0]["env"].pop()
    with pytest.raises(probe.NativeProofDenied, match="serving_policy_unverified"):
        probe.policy_for_serving(service, IMAGE)


def test_v5_cannot_reuse_v4_journal_or_raw_acceptance(probe, tmp_path, monkeypatch):
    path = tmp_path / "binding.json"
    seen = []
    monkeypatch.setattr(probe, "build_identity", lambda **_: seen.append("build"))
    fresh = replace(accepted(probe.source, probe.build, probe), image=IMAGE, contract_version=5)
    monkeypatch.setattr(probe, "_acceptance", lambda _clients: fresh)
    monkeypatch.setattr(release.prior, "Clients", lambda **_: SimpleNamespace(
        preflight=lambda: None, close=lambda: None))
    record = {"image": IMAGE, "build_identity": probe.build}
    for version in (4, 5.0):
        path.write_text(json.dumps(record | {"version": version}))
        with pytest.raises(probe.NativeProofDenied, match="journal_changed"):
            probe.verify_successor(IMAGE, probe.build, tmp_path)
    assert seen == []
    with pytest.raises(transition.TransitionDenied, match="current_acceptance_required"):
        transition.require_current_acceptance(replace(fresh, contract_version=4), IMAGE, probe)
    path.write_text(json.dumps(record | {"version": 5}))
    assert probe.verify_successor(IMAGE, probe.build, tmp_path) == fresh
    assert seen == ["build"]


@pytest.mark.parametrize(("mode", "prior_policy"), [
    ("expired", v4.POLICY_SHA256),
    ("expired", v4.LEGACY_POLICY_SHA256),
    ("definitions", v5.POLICY_SHA256),
    ("definitions", v4.POLICY_SHA256),
    ("definitions_stale", v5.POLICY_SHA256),
])
def test_v5_transition_uses_exact_predecessor_and_restores_original_admission(
    probe, tmp_path, monkeypatch, mode, prior_policy,
):
    recovery = mode == "expired"
    if not recovery:
        probe = release.SourceProbe(probe.build | {"kind": v5.DEFINITIONS_KIND, "fixed_renderer_base": PARENT})
    http = lifecycle.Http()
    pins = dict(zip(transition.PINS, (PARENT, prior_policy, courier.RENDERER, courier.FONT), strict=True))
    for name, service in http.services.items():
        container = service["template"]["containers"][0]
        if name == transition.WORKER:
            container["image"] = PARENT
            if prior_policy == v5.POLICY_SHA256:
                container["env"].append({"name": v5.TRANSPORT_ENV, "value": v5.TRANSPORT_MODE})
        for row in container["env"]:
            if row["name"] in pins:
                row["value"] = pins[row["name"]]
        http.revisions[service["latestReadyRevision"]] = copy.deepcopy(service["template"])
    fresh = replace(accepted(probe.source, probe.build, probe), image=IMAGE, contract_version=5)
    observations = []

    def expired(image, metadata):
        assert image == PARENT and metadata == http.services[transition.WORKER]
        observations.append(image)
        return release.ExpiredSourcePredecessor(PARENT, "old-build", "f" * 40, "e" * 64, prior_policy, {},
            courier.RENDERER, courier.FONT, "2026-11-15T00:00:00+00:00",
            "a" * 64, "b" * 64, release.corpus.sha(metadata))

    def verify_current(image, metadata):
        if not recovery and image == PARENT:
            assert probe.policy_for_serving(metadata, image) == v5.POLICY_SHA256
            if mode == "definitions_stale":
                raise probe.NativeProofDenied("document_source_revision_definitions_stale")
            observations.append(PARENT)
            return replace(fresh, image=PARENT)
        assert image == IMAGE and probe.policy_for_serving(metadata, image) == v5.POLICY_SHA256
        return fresh

    def verify_old_callers(_client, image, *, policy_sha256, **_options):
        assert image == PARENT and policy_sha256 == v4.POLICY_SHA256

    monkeypatch.setattr(probe, "verify_successor", lambda *_: fresh)
    monkeypatch.setattr(probe, "verify_current_successor", verify_current)
    monkeypatch.setattr(probe, "verify_expired_current_source_predecessor", expired)
    monkeypatch.setattr(release.prior, "verify_caller_pins", verify_old_callers)
    monkeypatch.syspath_prepend(str(v5.ROOT / "infra/gcp"))
    from infra.gcp import document_application_context as context

    application = SimpleNamespace(observe=lambda: {}, phase_traffic=context.phase_traffic,
        serving=lifecycle.probe.validate_serving)
    path = tmp_path / "transition"
    if (recovery and prior_policy != v4.POLICY_SHA256) or (not recovery and prior_policy != v5.POLICY_SHA256):
        expected = "expired_source_parent_changed" if recovery else "v5_definitions_parent_changed"
        with pytest.raises(transition.TransitionDenied, match=expected):
            transition.transition(IMAGE, probe.build, tmp_path / "native", path, http=http, probe=probe,
                application_context=application, recover_expired_source=recovery)
        assert http.writes == [] and observations == []
        return
    if mode == "definitions_stale":
        with pytest.raises(probe.NativeProofDenied, match="definitions_stale"):
            transition.transition(IMAGE, probe.build, tmp_path / "native", path, http=http, probe=probe,
                application_context=application)
        assert http.writes == []
        return
    report = transition.transition(IMAGE, probe.build, tmp_path / "native", path, http=http, probe=probe,
        application_context=application, recover_expired_source=recovery)
    assert report["admission_restored"] is True and observations == ([PARENT, PARENT] if recovery else [PARENT])
    assert [name for name, _ in http.writes] == [
        *transition.CALLERS, transition.WORKER, *transition.CALLERS, *transition.CALLERS]
    assert transition.environment(http.services[transition.WORKER]["template"])[v5.TRANSPORT_ENV] == {
        "name": v5.TRANSPORT_ENV, "value": v5.TRANSPORT_MODE}
    for name in transition.CALLERS:
        env = transition.environment(http.services[name]["template"])
        assert v5.TRANSPORT_ENV not in env
        assert env["DOCUMENT_WORKER_POLICY_SHA256"]["value"] == v5.POLICY_SHA256
        assert all(env[key]["value"] == "true" for key in transition.PAUSE[name])


def test_v5_definitions_observes_its_parent_build_separately(probe, monkeypatch):
    candidate = release.SourceProbe(probe.build | {"kind": v5.DEFINITIONS_KIND, "fixed_renderer_base": PARENT})
    metadata = worker.render(PARENT, probe.source.commit, source_v5=True)["service"]
    seen = []

    def parent_readback(image, value, *, read_only_release):
        assert image == PARENT and value == metadata and read_only_release is False
        seen.append(image)
        return "synthetic-parent-observed"

    monkeypatch.setattr(release, "verify_current_successor", parent_readback)
    assert candidate.verify_current_successor(PARENT, metadata) == "synthetic-parent-observed"
    assert seen == [PARENT]
