"""Synthetic build/Job transport controls; no Docker, credentials or cloud calls."""
from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import sys
import tarfile
from types import SimpleNamespace

import pytest
import test_document_refresh_probe as native
from infra.gcp import document_candidate as candidate
from infra.gcp import document_refresh_probe as probe
from infra.gcp import document_source_candidate as source_profile

from scripts import document_refresh as refresh

NONCE = native.NONCE
IMAGE = native.IMAGE


@pytest.fixture(autouse=True)
def frozen_v3_build_inputs(tmp_path_factory, monkeypatch):
    """Exercise the retained V3 publisher on its real frozen source bytes.

    The separate source-revision tests cover current bytes. This is not a
    substitution into candidate parsing or a native qualification receipt.
    """
    root = tmp_path_factory.mktemp("legacy-v3-build-inputs")
    for name, content in source_profile.historical_v3_contents(refresh.ROOT).items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    for folder, pattern in (("infra/gcp", "*.py"), ("infra/document-processing", "*.Dockerfile")):
        for path in (refresh.ROOT / folder).glob(pattern):
            target = root / folder / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    for path in (refresh.ROOT / "infra/gcp/document_accepted_v2").glob("*"):
        if path.is_file():
            target = root / "infra/gcp/document_accepted_v2" / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    monkeypatch.setattr(refresh, "ROOT", root)


def build():
    source = {"bucket": refresh.PROJECT + "_cloudbuild",
              "object": refresh.FULL_SOURCE_PREFIX + NONCE + "/source.tar.gz", "generation": "1234"}
    request = refresh.full_source_build_request(NONCE, source)
    return {**request, "id": native.BUILD_ID, "projectId": refresh.PROJECT,
            "name": probe.PARENT + "/builds/" + native.BUILD_ID, "status": "SUCCESS",
            "sourceProvenance": {"resolvedStorageSource": copy.deepcopy(source)},
            "results": {"images": [{"name": refresh.REPOSITORY + ":source-" + NONCE,
                                     "digest": IMAGE.split("@", 1)[1]}]}}


def identity(value=None):
    _, hashes, _ = refresh.full_source_snapshot()
    recipe = hashlib.sha256((refresh.ROOT / refresh.FULL_SOURCE_DOCKERFILE).read_bytes()).hexdigest()
    result = refresh.build_contract(value or build(), NONCE, recipe, full_source=True)
    result.update(source_archive_sha256=refresh.archive_full_source(refresh.full_source_archive()),
                  source_sha256=hashes, local_native_proof_sha256="f" * 64)
    return result


def archive(rows):
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w") as result:
            for row, content in rows:
                result.addfile(row, io.BytesIO(content) if row.isfile() else None)
    return output.getvalue()


def rows():
    with tarfile.open(fileobj=io.BytesIO(refresh.full_source_archive()), mode="r:gz") as source:
        return [(copy.deepcopy(row), source.extractfile(row).read()) for row in source]


def test_exact_source_archive_has_candidate_inventory_and_bound_policy_without_extracting(monkeypatch):
    contents, hashes, policy = refresh.full_source_snapshot()
    raw = refresh.full_source_archive()
    assert set(hashes) == candidate.SOURCES and len(hashes) == 21 and len(contents) == 22
    assert json.loads(contents[refresh.FULL_SOURCE_POLICY])["source_sha256"] == hashes
    assert policy == hashlib.sha256(contents[refresh.FULL_SOURCE_POLICY]).hexdigest()
    assert "backend/app/services/native_project.py" in hashes
    monkeypatch.setattr(tarfile.TarFile, "extractall", lambda *_: pytest.fail("No disk extraction"))
    assert refresh.archive_full_source(raw) == hashlib.sha256(raw).hexdigest()
    assert [row.name for row, _ in rows()] == sorted(contents)
    assert raw == refresh.full_source_archive()


@pytest.mark.parametrize("change", ["missing", "duplicate", "extra", "path", "symlink", "hardlink",
                                    "mode", "owner", "pax", "bytes", "policy", "order", "size"])
def test_build_context_cannot_smuggle_or_change_any_source(change):
    values = rows()
    row, content = values[0]
    if change == "missing":
        values.pop()
    elif change == "duplicate":
        values.append(copy.deepcopy(values[-1]))
    elif change == "extra":
        other = tarfile.TarInfo("credentials.json")
        other.size = 2
        values.append((other, b"{}"))
    elif change == "path":
        row.name = "../outside.py"
    elif change in {"symlink", "hardlink"}:
        row.type = tarfile.SYMTYPE if change == "symlink" else tarfile.LNKTYPE
        row.linkname = "/outside"
    elif change == "mode":
        row.mode = 0o777
    elif change == "owner":
        row.uid = 1
    elif change == "pax":
        row.pax_headers = {"comment": "unreviewed"}
    elif change == "bytes":
        values[0] = (row, b"x" + content[1:])
    elif change == "policy":
        index = next(i for i, pair in enumerate(values) if pair[0].name == refresh.FULL_SOURCE_POLICY)
        other, raw = values[index]
        values[index] = (other, b"x" + raw[1:])
    elif change == "order":
        values.reverse()
    else:
        row.size += 1
        values[0] = (row, content + b"x")
    with pytest.raises(refresh.RefreshDenied, match="document_source_archive_changed"):
        refresh.archive_full_source(archive(values))


def test_compressed_input_bound_precedes_archive_parser(monkeypatch):
    monkeypatch.setattr(tarfile, "open", lambda **_: pytest.fail("Oversized input must not parse"))
    with pytest.raises(refresh.RefreshDenied, match="archive_bound"):
        refresh.archive_full_source(b"x" * (refresh.FULL_SOURCE_MAX_BYTES + 1))


def test_local_source_drift_refuses_before_publish(tmp_path, monkeypatch):
    contents, _, policy = refresh.full_source_snapshot()
    for name, content in contents.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    monkeypatch.setattr(refresh, "ROOT", tmp_path)
    original_infra = refresh.infra
    monkeypatch.setattr(refresh, "infra", lambda name: candidate if name == "document_candidate" else original_infra(name))
    source = tmp_path / "backend/app/services/native_project.py"
    source.write_bytes(source.read_bytes() + b"# drift\n")
    monkeypatch.setattr(refresh, "native_http", lambda: pytest.fail("No provider call"))
    with pytest.raises(refresh.RefreshDenied, match="policy_changed"):
        refresh.full_source_archive()


@pytest.mark.parametrize("change", ["label", "recipe", "tag", "generation", "builder", "secret",
                                    "entrypoint", "timeout", "extra_image", "unfinished"])
def test_native_full_source_build_requires_exact_profile_and_provider_provenance(change):
    value = build()
    if change == "label":
        index = value["steps"][0]["args"].index("--label") + 1
        value["steps"][0]["args"][index] += "changed"
    elif change == "recipe":
        index = value["steps"][0]["args"].index("--file") + 1
        value["steps"][0]["args"][index] = "Dockerfile"
    elif change == "tag":
        value["results"]["images"][0]["name"] = refresh.REPOSITORY + ":refresh-" + NONCE
    elif change == "generation":
        value["sourceProvenance"]["resolvedStorageSource"]["generation"] = "5678"
    elif change == "builder":
        value["serviceAccount"] = "projects/other/serviceAccounts/other@example.test"
    elif change == "secret":
        value["availableSecrets"] = {"secretManager": [{"env": "KEY"}]}
    elif change == "entrypoint":
        value["steps"][0]["entrypoint"] = "sh"
    elif change == "timeout":
        value["timeout"] = "1800s"
    elif change == "extra_image":
        value["results"]["images"].append(value["results"]["images"][0])
    else:
        value["status"] = "WORKING"
    with pytest.raises(refresh.RefreshDenied):
        identity(value)


def test_full_source_is_distinct_from_refresh_and_never_historical_v1():
    result = identity()
    assert result["kind"] == refresh.FULL_SOURCE_KIND
    assert result["fixed_renderer_base"] is None
    assert result["policy_sha256"] == candidate.POLICY_SHA256
    assert result["policy_sha256"] != refresh.accepted().POLICY_SHA256
    assert result["source_object"].startswith(refresh.FULL_SOURCE_PREFIX)
    assert set(result["source_sha256"]) == candidate.SOURCES
    recipe = result["refresh_dockerfile_sha256"]
    with pytest.raises(refresh.RefreshDenied):
        refresh.build_contract(build(), NONCE, recipe)
    with pytest.raises(refresh.RefreshDenied, match="contract_version_invalid"):
        refresh.build_contract(build(), NONCE, recipe, full_source=True, historical_v1=True)
    with pytest.raises(refresh.RefreshDenied, match="historical_profile_refused"):
        refresh.verify_native_build(result, historical_v1=True)


def bind_readback(monkeypatch, raw=None):
    calls = []
    http = SimpleNamespace(close=lambda: calls.append("close"))
    monkeypatch.setattr(refresh, "native_http", lambda: http)
    monkeypatch.setitem(sys.modules, "document_refresh_auth", SimpleNamespace(ReleaseReadOnlyHttp=lambda: http))
    def get(client, method, uri, body=None):
        assert client is http and method == "GET" and body is None
        assert uri.endswith("/builds/" + native.BUILD_ID)
        calls.append("build-read")
        return build()
    monkeypatch.setattr(refresh, "native_json", get)
    def source(client, observed):
        assert client is http and observed["source_generation"] == "1234"
        calls.append("generation-bound-source-read")
        return refresh.full_source_archive() if raw is None else raw
    monkeypatch.setattr(refresh, "read_native_source", source)
    return calls


@pytest.mark.parametrize("release_read_only", [False, True])
def test_readback_recognizes_actual_full_source_profile_and_rechecks_context(monkeypatch, release_read_only):
    expected = identity()
    calls = bind_readback(monkeypatch)
    verify = refresh.verify_current_build_release if release_read_only else refresh.verify_current_build
    actual = verify(native.BUILD_ID, NONCE, IMAGE)
    assert all(expected[key] == value for key, value in actual.items())
    assert calls == ["build-read", "generation-bound-source-read", "close"]
    assert refresh.verify_native_build(expected) == actual


def test_changed_remote_source_cannot_be_adopted_as_v2_build(monkeypatch):
    expected = identity()
    bind_readback(monkeypatch, refresh.source_archive(b"only the old recipe"))
    with pytest.raises(refresh.RefreshDenied, match="source_archive_changed"):
        refresh.verify_native_build(expected)


def test_new_candidate_cannot_borrow_existing_v2_renderer_acceptance(monkeypatch, tmp_path):
    expected = identity()
    bind_readback(monkeypatch)
    fake = native.Fake()
    monkeypatch.setattr(probe, "Clients", lambda: fake)
    monkeypatch.setattr(probe, "build_module", lambda: refresh)
    with pytest.raises(probe.NativeProofDenied, match="candidate_renderer_review_required"):
        probe.run_successor(IMAGE, expected, tmp_path / "native")
    assert fake.calls["create"] == fake.calls["start"] == 0
    assert not (tmp_path / "native").exists()


def test_full_source_publication_requires_explicit_opt_in_before_auth(monkeypatch, tmp_path):
    monkeypatch.setattr(refresh, "native_http", lambda: pytest.fail("No auth without opt-in"))
    with pytest.raises(refresh.RefreshDenied, match="explicit_native_build_required"):
        refresh.full_source_candidate(tmp_path, publish=False, now=native.NOW)


def test_gzip_bomb_or_extra_member_is_bounded_without_tar_parser(monkeypatch):
    genuine = refresh.full_source_archive()
    original = tarfile.open
    def only_trusted_writer(*args, **kwargs):
        assert kwargs.get("mode") == "w"
        return original(*args, **kwargs)
    monkeypatch.setattr(tarfile, "open", only_trusted_writer)
    for raw in (gzip.compress(b"x" * (2 * refresh.FULL_SOURCE_MAX_BYTES)),
                genuine + gzip.compress(b"extra-member"), genuine[:-1], genuine + b"trailing"):
        with pytest.raises(refresh.RefreshDenied, match="archive_changed"):
            refresh.archive_full_source(raw)


@pytest.mark.parametrize("unknown", [False, True])
def test_full_source_native_publisher_uses_exact_context_once_and_never_replays_unknown(monkeypatch, tmp_path, unknown):
    contents, _, _ = refresh.full_source_snapshot()
    expected_archive = refresh.full_source_archive()
    calls = []
    def upload(uri, **kwargs):
        calls.append("source-upload")
        assert kwargs["method"] == "POST" and kwargs["body"] == expected_archive
        assert "ifGenerationMatch=0" in uri
        return SimpleNamespace(status=200), json.dumps({
            "name": refresh.FULL_SOURCE_PREFIX + NONCE + "/source.tar.gz",
            "bucket": refresh.PROJECT + "_cloudbuild", "generation": "1234"}).encode()
    http = SimpleNamespace(request=upload, close=lambda: calls.append("close"))
    monkeypatch.setattr(refresh, "native_http", lambda: http)
    def native_json(client, method, uri, body=None):
        assert client is http
        if method == "POST":
            calls.append("build-create")
            assert body == refresh.full_source_build_request(NONCE, build()["source"]["storageSource"])
            if unknown:
                return {}
            return {"metadata": {"build": {"id": native.BUILD_ID}}, "name": "synthetic-operation"}
        assert method == "GET" and uri.endswith("/builds/" + native.BUILD_ID) and body is None
        calls.append("build-read")
        return build()
    monkeypatch.setattr(refresh, "native_json", native_json)
    monkeypatch.setattr(refresh, "read_native_source", lambda client, observed: expected_archive)
    if unknown:
        with pytest.raises(refresh.RefreshDenied, match="outcome_unknown_reconcile_only"):
            refresh.native_build(tmp_path, contents[refresh.FULL_SOURCE_DOCKERFILE], nonce=NONCE, full_source=True)
    else:
        result = refresh.native_build(tmp_path, contents[refresh.FULL_SOURCE_DOCKERFILE], nonce=NONCE, full_source=True)
        assert result["kind"] == refresh.FULL_SOURCE_KIND and result["immutable_image"] == IMAGE
        assert result["source_archive_sha256"] == hashlib.sha256(expected_archive).hexdigest()
    assert calls.count("source-upload") == calls.count("build-create") == 1
    with pytest.raises(FileExistsError):
        refresh.native_build(tmp_path, contents[refresh.FULL_SOURCE_DOCKERFILE], nonce=NONCE, full_source=True)
    assert calls.count("source-upload") == calls.count("build-create") == 1


def test_full_source_uses_existing_exact_transport_scope_without_new_iam_or_routes():
    from urllib.parse import quote, urlencode

    from infra.gcp import document_refresh_auth as auth

    source_name = refresh.FULL_SOURCE_PREFIX + NONCE + "/source.tar.gz"
    query = urlencode({"uploadType": "media", "name": source_name, "ifGenerationMatch": "0"})
    upload = "https://storage.googleapis.com/upload/storage/v1/b/" + refresh.PROJECT + "_cloudbuild/o?" + query
    read = "https://storage.googleapis.com/download/storage/v1/b/" + refresh.PROJECT + "_cloudbuild/o/" + quote(source_name, safe="") + "?alt=media&generation=1234"
    assert len(refresh.full_source_archive()) <= auth.MAX_BODY
    assert auth.allowed("POST", upload) and not auth.release_allowed("POST", upload)
    assert auth.allowed("GET", read) and auth.release_allowed("GET", read)
    assert not auth.allowed("POST", upload.replace("ifGenerationMatch=0", "ifGenerationMatch=1"))


def test_full_source_identity_policy_cannot_replace_actual_readback(monkeypatch):
    expected = identity()
    expected["policy_sha256"] = "0" * 64
    bind_readback(monkeypatch)
    with pytest.raises(refresh.RefreshDenied, match="readback_changed"):
        refresh.verify_native_build(expected)


def test_full_source_profile_cannot_relabel_a_definitions_only_build():
    import test_document_refresh as old

    recipe = identity()["refresh_dockerfile_sha256"]
    with pytest.raises(refresh.RefreshDenied):
        refresh.build_contract(old.build(), "a" * 32, recipe, full_source=True)


def test_local_symlink_is_refused_even_when_target_bytes_match(tmp_path, monkeypatch):
    contents, _, policy = refresh.full_source_snapshot()
    for name, content in contents.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    source = tmp_path / "backend/app/services/native_project.py"
    target = tmp_path / "same-bytes-outside-context.py"
    target.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(target)
    monkeypatch.setattr(refresh, "ROOT", tmp_path)
    original_infra = refresh.infra
    monkeypatch.setattr(refresh, "infra", lambda name: candidate if name == "document_candidate" else original_infra(name))
    with pytest.raises(refresh.RefreshDenied, match="path_invalid"):
        refresh.full_source_archive()
