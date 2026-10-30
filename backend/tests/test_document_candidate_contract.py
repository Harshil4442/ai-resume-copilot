"""Offline source inventories cannot be mistaken for accepted native images."""
from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from infra.gcp import document_candidate as candidate
from infra.gcp import document_refresh_probe as probe
from infra.gcp import document_renderer_profiles as profile
from infra.gcp import document_source_candidate as source_profile
from infra.gcp import document_worker as worker

from scripts import document_refresh as refresh
from scripts import document_test_image as engines


def test_candidate_exact_inventory_includes_installed_fonts_and_notices():
    contents, hashes, policy = refresh.full_source_snapshot(source_revision=True)
    assert len(candidate.SOURCES) == 21 and len(candidate.RUNTIME) == 20
    assert set(hashes) == source_profile.SOURCES == set(engines.SOURCES)
    assert candidate.SOURCES == source_profile.SOURCES - {source_profile.DOCKERFILE}
    assert candidate.RUNTIME == engines.RUNTIME
    assert candidate.POLICY_SHA256 == worker.CANDIDATE_POLICY_SHA256
    assert policy == source_profile.POLICY_SHA256 != candidate.POLICY_SHA256
    assert policy != worker.POLICY_SHA256 == probe.POLICY == profile.BUILD["policy_sha256"]
    assert len({destination: hashlib.sha256(contents[source]).hexdigest()
        for source, destination in candidate.RUNTIME.items()}) == 20
    with pytest.raises(RuntimeError, match="^document_source_policy_changed$"):
        probe.candidate_runtime_hashes()  # The historical draft cannot qualify the new source.
    for source, destination in candidate.RUNTIME.items():
        if source.endswith(".ttf"):
            assert destination.startswith("/usr/share/fonts/truetype/hirewiz-courier-prime/")
        elif source.endswith(("OFL.txt", "provenance.json")):
            assert destination.startswith("/usr/share/doc/hirewiz-courier-prime/")


def test_candidate_policy_requires_declared_fonts_and_new_native_evidence():
    policy = json.loads((refresh.ROOT / candidate.POLICY_PATH).read_bytes())
    renderer = policy["docx_layout"]["renderer"]
    declared = renderer["declared_source_fonts"]
    assert declared["source_and_candidate_checked"] is True
    assert declared["exact_installed_family_required"] is True
    assert declared["fontconfig_metric_compatible_substitution"] is False
    assert declared["rendered_font_family_checked_after_writer"] is True
    assert declared["unsupported_or_ambiguous_family_refusal"] == "unsupported_source_font"
    assert declared["silent_source_restyling"] is False
    assert renderer["bundled_fonts"]["family"] == "Courier Prime"
    assert renderer["bundled_fonts"]["modified"] is False
    assert renderer["bundled_fonts"]["exact_font_license_and_provenance_bytes_in_source_and_runtime_inventory"] is True
    assert policy["docx_layout"]["release"]["candidate_required_native_layout_cases"] == [
        "styled_multipage_fit", "rendered_reflow_refusal", "unsupported_source_font_refusal"]
    assert "fresh_exact_native_image_font_manifest_and_declared_source_font_preservation_corpus" in policy["required_release_evidence"]
    assert policy["deployment_proven"] is False


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "runtime_missing", "runtime_alias", "font_destination"])
def test_inventory_drift_refuses_before_reading_source(monkeypatch, mutation):
    sources, runtime = list(engines.SOURCES), dict(engines.RUNTIME)
    if mutation == "missing":
        sources.pop()
    elif mutation == "extra":
        sources.append("backend/unreviewed.py")
    elif mutation == "duplicate":
        sources.append(sources[0])
    elif mutation == "runtime_missing":
        runtime.pop(next(iter(runtime)))
    elif mutation == "runtime_alias":
        runtime[next(iter(runtime))] = next(reversed(runtime.values()))
    else:
        runtime["backend/evals/assets/courier_prime/CourierPrime-Regular.ttf"] = "/opt/document/evals/assets/courier_prime/CourierPrime-Regular.ttf"
    monkeypatch.setattr(engines, "SOURCES", tuple(sources))
    monkeypatch.setattr(engines, "RUNTIME", runtime)
    with pytest.raises(refresh.RefreshDenied, match="source_manifest_invalid"):
        refresh.full_source_snapshot()


def test_historical_v2_policy_and_runtime_are_frozen_independently_of_candidate():
    raw = (refresh.ROOT / "infra/gcp/document_accepted_v2/policy.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == probe.POLICY
    assert json.loads(raw)["source_sha256"] == refresh.accepted_source_hashes() == profile.SOURCES
    historical = probe.runtime_hashes()
    assert len(historical) == 12
    assert historical["/opt/document/docx_renderer.py"] == profile.SOURCES["infra/document-processing/docx_renderer.py"]
    assert "/opt/document/app/services/docx_fonts.py" not in historical
    assert not any("courier-prime" in name for name in historical)
    assert len(probe.runtime_hashes(historical_v1=True)) == 11


@pytest.mark.parametrize("change", ["bytes", "symlink"])
def test_frozen_policy_drift_refuses_historical_observation(monkeypatch, tmp_path, change):
    relative = "infra/gcp/document_accepted_v2/policy.json"
    raw = (refresh.ROOT / relative).read_bytes()
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    if change == "bytes":
        path.write_bytes(raw + b" ")
    else:
        target = tmp_path / "same-policy.json"
        target.write_bytes(raw)
        path.symlink_to(target)
    monkeypatch.setattr(refresh, "ROOT", tmp_path)
    monkeypatch.setattr(refresh, "infra", lambda _: profile)
    monkeypatch.setattr(probe, "HERE", tmp_path / "infra/gcp")
    with pytest.raises(refresh.RefreshDenied, match="accepted_policy_changed"):
        refresh.accepted_source_hashes()
    with pytest.raises(probe.NativeProofDenied, match="policy_changed"):
        probe.runtime_hashes()


def test_offline_candidate_does_not_change_serving_api_pins():
    environment = worker.api_environment(worker.ACCEPTED_ORIGIN)
    assert environment["DOCUMENT_WORKER_POLICY_SHA256"] == probe.POLICY
    assert environment["DOCUMENT_WORKER_DOCX_FONT_MANIFEST_SHA256"] == worker.ACCEPTED_FONT_MANIFEST
    proposal = json.loads((refresh.ROOT / "infra/gcp/document-worker.disabled.json").read_bytes())
    assert proposal["policy_sha256"] == candidate.POLICY_SHA256
    assert proposal["image"] is None and proposal["deployment_proven"] is False
    assert proposal["api_wiring_enabled"] is False


def test_historical_request_reconstruction_requires_exact_old_nonce():
    old = refresh.full_source_build_request(profile.BUILD["nonce"], {}, historical_profile=True)
    labels = old["steps"][0]["args"]
    assert sum(value == "--label" for value in labels) == len(profile.SOURCES)
    assert "hirewiz.document-test.source.infra/document-processing/docx_renderer.py=" + profile.SOURCES[
        "infra/document-processing/docx_renderer.py"] in labels
    assert not any("courier_prime" in value or "docx_fonts" in value for value in labels)
    with pytest.raises(refresh.RefreshDenied, match="renderer_build_profile_changed"):
        refresh.full_source_build_request("a" * 32, {}, historical_profile=True)


@pytest.mark.parametrize("change", [None, "archive", "build_id", "generation", "source_label", "image"])
def test_old_build_readback_requires_exact_native_identity_and_frozen_archive(monkeypatch, change):
    retained = b"synthetic retained archive bytes"
    frozen = copy.deepcopy(profile.BUILD)
    frozen["source_archive_sha256"] = hashlib.sha256(retained).hexdigest()
    reviewed = SimpleNamespace(**{name: getattr(profile, name) for name in dir(profile) if not name.startswith("__")})
    reviewed.BUILD = frozen
    reviewed.matches_build = lambda value: all(value.get(key) == expected for key, expected in frozen.items()) and value.get("source_sha256") == profile.SOURCES
    original = refresh.infra
    monkeypatch.setattr(refresh, "infra", lambda name: reviewed if name == "document_renderer_profiles" else original(name))
    source = {key.removeprefix("source_"): frozen[key] for key in ("source_bucket", "source_object", "source_generation")}
    request = refresh.full_source_build_request(frozen["nonce"], source, historical_profile=True)
    build = {**request, "id": frozen["build_id"], "projectId": refresh.PROJECT,
        "name": frozen["build_resource"], "status": "SUCCESS", "sourceProvenance": {"resolvedStorageSource": copy.deepcopy(source)},
        "results": {"images": [{"name": refresh.REPOSITORY + ":source-" + frozen["nonce"], "digest": profile.IMAGE.split("@", 1)[1]}]}}
    if change == "build_id":
        build["id"] = "1" * 36
    elif change == "generation":
        build["source"]["storageSource"]["generation"] = "1"
        build["sourceProvenance"]["resolvedStorageSource"]["generation"] = "1"
    elif change == "source_label":
        args = build["steps"][0]["args"]
        args[args.index("--label") + 1] += "changed"
    elif change == "image":
        build["results"]["images"][0]["digest"] = "sha256:" + "f" * 64
    monkeypatch.setattr(refresh, "native_http", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(refresh, "native_json", lambda *args, **kwargs: build)
    monkeypatch.setattr(refresh, "read_native_source", lambda *args: retained + (b"changed" if change == "archive" else b""))
    if change:
        with pytest.raises(refresh.RefreshDenied):
            refresh.verify_current_build(frozen["build_id"], frozen["nonce"], profile.IMAGE)
    else:
        result = refresh.verify_current_build(frozen["build_id"], frozen["nonce"], profile.IMAGE)
        assert reviewed.matches_build(result)
        assert set(result["source_sha256"]) != candidate.SOURCES
