"""Pure fixture admission tests; mocked transport is never native qualification."""
from __future__ import annotations

import copy
import json

import pytest
from backend.tests import native_docx_ci_fixtures as fixture
from infra.gcp import document_candidate as legacy
from infra.gcp import document_source_candidate as source
from infra.gcp.document_source_payload import EXTRA_CASES


@pytest.fixture
def runtime_probe(monkeypatch, tmp_path):
    """Observe guard ordering without running Docker or manufacturing receipts."""
    calls = []
    fixture._runtime.cache_clear()
    monkeypatch.setattr(fixture, "_complete_corpus", lambda report, _: ([], report["docx_layout_corpus"]))
    monkeypatch.setattr(fixture.engine, "clamd_corpus", lambda: ())
    monkeypatch.setattr(fixture.engine, "docker_command", lambda: calls.append("docker") or ["unit-test-docker"])
    monkeypatch.setattr(fixture.engine, "image_metadata", lambda *args: calls.append(("metadata", args)))
    def container(*args):
        calls.append(("runtime", args))
        return 0, json.dumps({key: fixture.engine.source_hashes()[key] for key in fixture.engine.RUNTIME})
    monkeypatch.setattr(fixture.engine, "container", container)
    reports = []
    def read(report):
        path = tmp_path / ("unit-report-" + str(len(reports)) + ".json")
        path.write_text(json.dumps(report))
        reports.append(path)
        return fixture._runtime(str(path))
    yield read, calls
    fixture._runtime.cache_clear()


def report(*, source_profile=True):
    policy_path = source.POLICY_PATH if source_profile else legacy.POLICY_PATH
    policy = source.POLICY_SHA256 if source_profile else legacy.POLICY_SHA256
    sources = json.loads((fixture.engine.ROOT / policy_path).read_bytes())["source_sha256"]
    image = "sha256:" + "a" * 64
    value = {"scope": "local_native_docker_only", "cloud_run_isolation_proven": False,
             "runtime_source_bytes_verified": True, "restricted_container_metadata_verified": True,
             "platform": "linux/amd64", "source_sha256": sources, "image_id": image,
             "docx_layout_corpus": [{"case": "styled_multipage_fit", "layout": {"renderer": {
                 "image_digest": "local-document-image@" + image, "policy_sha256": policy}}}]}
    if source_profile:
        value["pdf_reading_order_corpus"] = [{"case": name, "passed": True} for name in EXTRA_CASES]
    return value


def test_legacy_default_requires_the_genuinely_legacy_source_map(runtime_probe, monkeypatch):
    value = report(source_profile=False)
    monkeypatch.setattr(fixture.engine, "source_hashes", lambda: value["source_sha256"])
    read, calls = runtime_probe
    observed = read(value)
    assert observed["policy"] == legacy.POLICY_SHA256
    assert observed["source_sha256"] == value["source_sha256"]
    assert calls[0] == "docker" and calls[1][0] == "metadata" and calls[2][0] == "runtime"
    assert calls[1][1] == (["unit-test-docker"], value["image_id"], "linux/amd64", value["source_sha256"])


def test_known_source_profile_retains_the_full_image_and_runtime_guards(runtime_probe):
    value = report()
    assert value["source_sha256"] == fixture.engine.source_hashes()
    read, calls = runtime_probe
    observed = read(value)
    assert observed["policy"] == source.POLICY_SHA256
    assert observed["identity"] == value["docx_layout_corpus"][0]["layout"]["renderer"]
    assert calls[0] == "docker" and calls[1][0] == "metadata" and calls[2][0] == "runtime"
    assert calls[2][1][3:] == (["-c", fixture.engine.HASH_PROGRAM], [])


@pytest.mark.parametrize("identity_policy", [legacy.POLICY_SHA256, "f" * 64, None])
def test_renderer_digest_cannot_choose_or_forge_the_source_profile(runtime_probe, identity_policy):
    value = report()
    value["docx_layout_corpus"][0]["layout"]["renderer"]["policy_sha256"] = identity_policy
    read, calls = runtime_probe
    with pytest.raises(AssertionError, match="renderer identity"):
        read(value)
    assert calls == []


@pytest.mark.parametrize("rewrite_policy", [False, True])
def test_removing_source_corpus_cannot_downgrade_the_new_runtime(runtime_probe, rewrite_policy):
    value = report()
    del value["pdf_reading_order_corpus"]
    if rewrite_policy:
        value["docx_layout_corpus"][0]["layout"]["renderer"]["policy_sha256"] = legacy.POLICY_SHA256
    read, calls = runtime_probe
    with pytest.raises(AssertionError, match="source map"):
        read(value)
    assert calls == []


@pytest.mark.parametrize("mode", ["unknown", "reordered", "missing", "failed", "duplicate", "truthy", "extra", "not_list"])
def test_source_profile_requires_the_complete_exact_ordered_corpus(runtime_probe, mode):
    value = report()
    rows = value["pdf_reading_order_corpus"]
    if mode == "unknown":
        rows[-1]["case"] = "unknown_profile_case"
    elif mode == "reordered":
        rows.reverse()
    elif mode == "missing":
        rows.pop()
    elif mode == "failed":
        rows[0]["passed"] = False
    elif mode == "duplicate":
        rows[-1] = copy.deepcopy(rows[0])
    elif mode == "truthy":
        rows[0]["passed"] = 1
    elif mode == "extra":
        rows[0]["policy_override"] = legacy.POLICY_SHA256
    else:
        value["pdf_reading_order_corpus"] = {}
    read, calls = runtime_probe
    with pytest.raises(AssertionError, match="known source-profile corpus"):
        read(value)
    assert calls == []


def test_known_profile_source_map_cannot_be_substituted(runtime_probe, monkeypatch):
    value = report()
    value["source_sha256"]["backend/app/services/parsing.py"] = "b" * 64
    # Even a transport claiming these bytes cannot change the reviewed policy map.
    monkeypatch.setattr(fixture.engine, "source_hashes", lambda: value["source_sha256"])
    read, calls = runtime_probe
    with pytest.raises(AssertionError, match="source map"):
        read(value)
    assert calls == []


def test_arbitrary_policy_file_cannot_become_a_known_profile(runtime_probe, monkeypatch, tmp_path):
    value = report()
    path = tmp_path / source.POLICY_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"source_sha256": value["source_sha256"]}))
    monkeypatch.setattr(fixture.engine, "ROOT", tmp_path)
    # The prior source/image check is separately retained; this reaches only the
    # policy-byte guard with an explicitly mocked source observation.
    monkeypatch.setattr(fixture.engine, "source_hashes", lambda: value["source_sha256"])
    read, calls = runtime_probe
    with pytest.raises(AssertionError, match="known native test policy changed"):
        read(value)
    assert calls == []
