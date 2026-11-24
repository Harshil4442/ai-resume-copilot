"""The explicit recovery lane selects the same source from archive to promotion."""
from __future__ import annotations

import hashlib
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fixtures.retained_document_v5 import RETAINED_COMMIT, snapshot
from infra.gcp import document_source_candidate_v5 as profile

from scripts import document_refresh as refresh


def test_v5_archive_is_bound_to_the_forward_policy():
    # The current checkout has a separately qualified patched engine. Historical
    # V5 archive comparisons must use its exact retained committed source.
    selectors = dict(source_revision=True, source_commit=RETAINED_COMMIT, source_v5=True)
    raw = refresh.full_source_archive(**selectors)
    assert refresh.archive_full_source(raw, **selectors) == hashlib.sha256(raw).hexdigest()
    contents, runtime, policy = snapshot()
    assert set(contents) == {*runtime, profile.POLICY_PATH}
    assert policy == profile.POLICY_SHA256
    with pytest.raises(profile.v4.SourceDenied, match="synchronous_working_source_changed"):
        refresh.full_source_snapshot(source_revision=True, source_v5=True)
    with pytest.raises(refresh.RefreshDenied, match="contract_version_invalid"):
        refresh.full_source_archive(source_v5=True)


def test_v5_recovery_selects_same_claimed_nonce_and_expired_transition(tmp_path, monkeypatch):
    events = []
    nonce = "a" * 32
    application_context = object()
    identity = {"kind": profile.KIND}

    def prepare(directory, history, selection, **kwargs):
        assert kwargs == {
            "refresh_module": refresh, "retire_expired_v2": False, "rescue_expired_v2": False,
            "rescue_prior_journal": None, "consumed_history": None, "application_context": application_context,
            "source_revision": True, "recover_expired_source": True, "source_v5": True,
        }
        assert directory == tmp_path and history == tmp_path / "history" and selection is None
        events.append("claim")
        return nonce

    class Probe:
        def preflight(self):
            return {"service": {"template": {"containers": [{"image": "verified-expired-parent"}]}}}

        def close(self):
            pass

    def build(directory, *, publish, now, nonce: str, source_revision, source_v5):
        assert directory == tmp_path and publish and now.tzinfo is UTC
        assert len(nonce) == 32 and nonce == "a" * 32 and source_revision and source_v5
        events.append("build")
        return {"build_identity": identity}

    def promote(actual, native, transition, **kwargs):
        assert actual == identity and native == tmp_path / "native-proof" and transition == tmp_path / "transition"
        assert kwargs == {"application_context": application_context, "recover_expired_source": True}
        events.append("promotion")
        return {"test_observation": True}

    old_policy = refresh.infra("document_source_candidate").POLICY_SHA256
    replacements = {
        "document_refresh_probe": SimpleNamespace(Clients=Probe, policy_for_serving=lambda *_: old_policy),
        "document_refresh_recovery": SimpleNamespace(prepare=prepare),
        "document_source_release": SimpleNamespace(promote=promote),
    }
    monkeypatch.setattr(refresh, "infra", replacements.__getitem__)
    monkeypatch.setattr(refresh, "full_source_candidate", build)
    result = refresh.refresh(
        tmp_path, publish=True, now=datetime.now(UTC), history=tmp_path / "history",
        application_context=application_context, recover_expired_source=True,
        full_source=True, source_revision=True, source_v5=True,
    )
    assert result == {"test_observation": True}
    assert events == ["claim", "build", "promotion"]


def test_v5_cli_refuses_scheduled_run_before_creating_private_journal(tmp_path, monkeypatch):
    output = tmp_path / "would-be-journal"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.setattr(sys, "argv", [
        "document_refresh.py", "--mode", "recover-source-v5", "--publish-candidate",
        "--reviewed-application-context", "--prior-history-directory", str(tmp_path / "history"),
        "--consumed-history-bundle", str(tmp_path / "consumed"), "--output-directory", str(output),
    ])
    with pytest.raises(refresh.RefreshDenied, match="source_v5_requires_dispatch"):
        refresh.main()
    assert not output.exists()


def test_v5_definition_build_pins_the_same_positive_teardown_profile(monkeypatch):
    source = SimpleNamespace(
        commit=profile.git(refresh.ROOT, "rev-parse", "HEAD").decode().strip(), qualification_sha256="e" * 64,
        policy=profile.POLICY_SHA256,
    )

    def committed(commit, *, legacy, source_v5, source_patched_engine=False):
        assert commit == source.commit and not legacy and source_v5 and not source_patched_engine
        return source

    monkeypatch.setattr(refresh, "committed_source", committed)
    parent = refresh.REPOSITORY + "@sha256:" + "d" * 64
    nonce = "a" * 32
    archive = {
        "bucket": refresh.PROJECT + "_cloudbuild",
        "object": f"hirewiz-document-refresh-source/{nonce}/source.tar.gz",
        "generation": "12345",
    }
    body = refresh.source_definitions_build_request(nonce, archive, source.commit, parent, source_v5=True)
    assert body["timeout"] == "900s"
    assert body["tags"] == [profile.DEFINITIONS_KIND, source.commit, source.qualification_sha256, "d" * 64]
    assert body["images"] == [refresh.REPOSITORY + ":source-definitions-" + nonce]
    build_id = "12345678-1234-1234-1234-123456789abc"
    observed = {
        **body, "id": build_id, "projectId": refresh.PROJECT, "status": "SUCCESS",
        "name": f"projects/{refresh.PROJECT}/locations/{refresh.REGION}/builds/{build_id}",
        "results": {"images": [{"name": body["images"][0], "digest": "sha256:" + "f" * 64}]},
        "sourceProvenance": {"resolvedStorageSource": archive},
    }
    recipe = profile.definition_recipe(parent, refresh.ROOT, source_commit=source.commit)
    proof = refresh.build_contract(
        observed, nonce, hashlib.sha256(recipe).hexdigest(), source_definitions=True,
        source_parent=parent, source_commit=source.commit, source_v5=True,
    )
    assert proof["kind"] == profile.DEFINITIONS_KIND and proof["policy_sha256"] == profile.POLICY_SHA256
    assert proof["fixed_renderer_base"] == parent and proof["source_commit"] == source.commit
