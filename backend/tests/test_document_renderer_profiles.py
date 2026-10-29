"""Finite renderer-profile controls only; no Docker, credentials or cloud calls."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
import test_document_full_source_build as full
import test_document_refresh as local
import test_document_refresh_probe as native
from infra.gcp import document_refresh_auth as auth
from infra.gcp import document_refresh_probe as probe
from infra.gcp import document_renderer_profiles as profile
from infra.gcp import document_worker as worker

from scripts import document_refresh as refresh


def identity():
    return {**copy.deepcopy(profile.BUILD), "source_sha256": copy.deepcopy(profile.SOURCES),
            "local_native_proof_sha256": "f" * 64}


def proof():
    value = local.proof()
    value["source_sha256"] = copy.deepcopy(profile.SOURCES)
    renderer = value["docx_layout_corpus"][0]["layout"]["renderer"]
    renderer.update(renderer_version=profile.RENDERER, font_manifest_sha256=profile.FONT,
                    policy_sha256=profile.BUILD["policy_sha256"])
    return value


def test_reviewed_profile_preserves_all_legacy_default_and_runtime_source_pins():
    assert worker.ACCEPTED_FONT_MANIFEST == probe.FONT == probe.LEGACY_FONT == "abaf429caeeeb5fbcc000c878a315c89e23ecd3dc912bb74df10761c144f7d60"
    assert probe.renderer_identity(profile.IMAGE) == (profile.RENDERER, profile.FONT)
    assert probe.renderer_identity(native.IMAGE) == (probe.RENDERER, probe.FONT)
    assert probe.renderer_identity(native.IMAGE, historical_v1=True) == (probe.LEGACY_RENDERER, probe.LEGACY_FONT)
    assert refresh.accepted_source_hashes() == profile.SOURCES
    assert refresh.full_source_snapshot(source_revision=True)[1] != profile.SOURCES
    assert refresh.full_source_snapshot(source_revision=True)[2] != profile.BUILD["policy_sha256"]
    assert len(profile.SOURCES) == 13
    with pytest.raises(probe.NativeProofDenied, match="historical_renderer_profile_refused"):
        probe.renderer_identity(profile.IMAGE, historical_v1=True)


def test_local_25_case_proof_requires_registry_image_and_exact_build_profile():
    value = proof()
    facts = refresh.proof_contract(value, worker, profile.SOURCES, now=local.NOW,
                                   image=profile.IMAGE, build_identity=identity())
    assert facts["local_cases"] == 25 and facts["renderer_version"] == profile.RENDERER
    # Reporting the new font hash never selects a profile by itself.
    with pytest.raises(refresh.RefreshDenied, match="renderer_changed_review_required"):
        refresh.proof_contract(value, worker, profile.SOURCES, now=local.NOW)
    with pytest.raises(refresh.RefreshDenied, match="renderer_changed_review_required"):
        refresh.proof_contract(value, worker, profile.SOURCES, now=local.NOW, image=native.IMAGE)


@pytest.mark.parametrize("field", ["nonce", "build_id", "build_resource", "source_generation", "source_archive_sha256",
                                   "refresh_dockerfile_sha256", "policy_sha256", "kind", "fixed_renderer_base", "source_sha256"])
def test_no_other_build_or_source_gets_the_exact_image_exception(field):
    value = identity()
    value[field] = "changed"
    with pytest.raises(refresh.RefreshDenied, match="renderer_build_profile_changed"):
        refresh.proof_contract(proof(), worker, profile.SOURCES, now=local.NOW,
                               image=profile.IMAGE, build_identity=value)
    with pytest.raises(probe.NativeProofDenied, match="renderer_build_profile_changed"):
        probe.verify_renderer_build(value)


@pytest.mark.parametrize("mutation", ["font", "source", "policy", "image", "missing_case", "cleanup", "stale"])
def test_profile_does_not_relax_local_corpus_fidelity_or_freshness(mutation):
    value = proof()
    if mutation in {"font", "policy", "image"}:
        field = {"font": "font_manifest_sha256", "policy": "policy_sha256", "image": "image_digest"}[mutation]
        value["docx_layout_corpus"][0]["layout"]["renderer"][field] = "0" * 64
    elif mutation == "source":
        value["source_sha256"] = {"unreviewed": "0" * 64}
    elif mutation == "missing_case":
        value["corpus"].pop()
    elif mutation == "cleanup":
        value["request_clamd_corpus"][0]["native_log_removed"] = False
    else:
        for row in value["corpus"]:
            row["scan"]["definitions"] = "28153/2026-10-14T06:24:15+00:00"
    with pytest.raises(refresh.RefreshDenied):
        refresh.proof_contract(value, worker, profile.SOURCES, now=local.NOW,
                               image=profile.IMAGE, build_identity=identity())


def test_native_ten_case_receipt_and_acceptance_carry_the_exact_profile(monkeypatch):
    nonce = profile.BUILD["nonce"]
    job, execution = native.completed(native.job(profile.IMAGE, nonce))
    payload = native.payload(profile.IMAGE, execution["name"].rsplit("/", 1)[-1])
    payload["identity_preflight"]["font_manifest_sha256"] = profile.FONT
    monkeypatch.setattr(probe, "native_payload", lambda *args, **kwargs: copy.deepcopy(payload))
    result = probe.acceptance(SimpleNamespace(), profile.IMAGE, identity(), job, execution)
    assert result.contract_version == 2 and result.required_case_count == 10
    assert result.font_manifest_sha256 == profile.FONT and result.renderer_version == profile.RENDERER
    assert result.source_sha256 == profile.SOURCES
    payload["identity_preflight"]["font_manifest_sha256"] = probe.FONT
    with pytest.raises(probe.NativeProofDenied, match="renderer_or_fonts_changed"):
        probe.acceptance(SimpleNamespace(), profile.IMAGE, identity(), job, execution)


def test_full_source_provenance_does_not_adopt_future_daily_renderer_parent(monkeypatch):
    from infra.gcp import document_source_candidate as current
    contents = current.historical_v3_contents(refresh.ROOT)
    _, policy = current.frozen_v3(refresh.ROOT)
    monkeypatch.setattr(refresh, "full_source_snapshot", lambda: (contents, policy["source_sha256"], current.courier.POLICY_SHA256))
    value = full.build()
    recipe = refresh.full_source_snapshot()[1][refresh.FULL_SOURCE_DOCKERFILE]
    before = refresh.build_contract(value, native.NONCE, recipe, full_source=True)
    monkeypatch.setattr(refresh, "RENDERER_BASE", profile.IMAGE)
    assert refresh.build_contract(value, native.NONCE, recipe, full_source=True) == before
    assert before["fixed_renderer_base"] is None


def test_daily_successor_uses_new_font_only_with_verified_exact_renderer_parent():
    value = identity()
    value.update(kind="native_cloud_build", immutable_image=native.NEXT,
                 fixed_renderer_base=profile.IMAGE, build_id=native.BUILD_ID, nonce=native.NONCE)
    assert probe.renderer_identity(native.NEXT, build_identity=value) == (profile.RENDERER, profile.FONT)
    # An unrelated daily parent retains the default; an arbitrary hash is not an input.
    value["fixed_renderer_base"] = probe.BASE_IMAGE
    assert probe.renderer_identity(native.NEXT, build_identity=value) == (probe.RENDERER, probe.FONT)
    value["fixed_renderer_base"] = profile.IMAGE
    value["source_sha256"] = {"changed": "0" * 64}
    with pytest.raises(probe.NativeProofDenied, match="renderer_build_profile_changed"):
        probe.verify_renderer_build(value)


def test_new_storage_write_route_is_one_exact_create_only_resume_object():
    from urllib.parse import urlencode

    prefix = "https://storage.googleapis.com/upload/storage/v1/b/" + auth.PROJECT + "_cloudbuild/o?"
    name = "hirewiz-document-refresh-source/" + profile.BUILD["nonce"] + "/resume.json"
    def request(obj, generation="0"):
        return prefix + urlencode({"uploadType": "media", "name": obj, "ifGenerationMatch": generation})
    assert auth.allowed("POST", request(name))
    assert not auth.release_allowed("POST", request(name))
    for obj in (name.replace(profile.BUILD["nonce"], "f" * 32), name + ".extra", name.replace("resume.json", "other.json")):
        assert not auth.allowed("POST", request(obj))
    assert not auth.allowed("POST", request(name, "1"))


def test_reported_exact_image_cannot_borrow_a_different_descendant_build_profile():
    descendant = identity()
    descendant.update(kind="native_cloud_build", immutable_image=native.NEXT, fixed_renderer_base=profile.IMAGE)
    with pytest.raises(refresh.RefreshDenied, match="renderer_build_profile_changed"):
        refresh.proof_contract(proof(), worker, profile.SOURCES, now=local.NOW,
                               image=profile.IMAGE, build_identity=descendant)
