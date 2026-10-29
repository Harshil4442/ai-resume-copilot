"""Finite candidate continuation controls; no credentials, Docker or cloud calls."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from infra.gcp import document_refresh_resume as resume
from infra.gcp import document_renderer_profiles as profile

from scripts import document_refresh as refresh


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def partial(tmp_path, monkeypatch):
    """Owned synthetic extracted tree, retaining the real finite path inventory."""
    root = tmp_path / "original"
    root.mkdir(mode=0o700)
    values = {name: canonical({"synthetic_retained_file": name}) + b"\n" for name in profile.PARTIAL_FILES}
    identity = profile.BUILD
    source = {"bucket": identity["source_bucket"], "object": identity["source_object"], "generation": identity["source_generation"]}
    encoded = base64.urlsafe_b64encode(identity["build_id"].encode()).decode().rstrip("=")
    top = {"native-build-identity.json": identity, "source-upload-acknowledged.json": source,
        "build-started.json": {"nonce": identity["nonce"], "recipe_sha256": identity["refresh_dockerfile_sha256"],
            "source_archive_sha256": identity["source_archive_sha256"], "automatic_replay": False,
            "unknown_outcome": "reconcile_exact_native_object_and_build_only"},
        "build-create-started.json": {"nonce": identity["nonce"], "automatic_replay": False,
            "request_sha256": digest(canonical(refresh.full_source_build_request(
                identity["nonce"], source, historical_profile=True)))},
        "build-acknowledged.json": {"nonce": identity["nonce"], "build_id": identity["build_id"],
            "operation_name": "operations/build/" + refresh.PROJECT + "/" + encoded, "automatic_replay": False}}
    values.update({name: canonical(value) + b"\n" for name, value in top.items()})
    for name, raw in values.items():
        path = root / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(raw)
        path.chmod(0o600)
    for path in root.rglob("*"):
        if path.is_dir():
            path.chmod(0o700)
    expected = {name: digest(raw) for name, raw in values.items()}
    manifest = digest(canonical(expected))
    monkeypatch.setattr(profile, "PARTIAL_FILES", expected)
    monkeypatch.setattr(profile, "PARTIAL_MANIFEST_SHA256", manifest)
    infra = refresh.infra
    build = SimpleNamespace(**{name: getattr(refresh, name) for name in dir(refresh) if not name.startswith("__")})
    build.infra = lambda name: profile if name == "document_renderer_profiles" else infra(name)
    return SimpleNamespace(root=root, values=values, hashes=expected, manifest=manifest, build=build, tmp=tmp_path)


def test_exact_partial_tree_is_read_without_changing_original_bytes(partial):
    before = copy.deepcopy(partial.values)
    assert resume.partial_snapshot(partial.root, partial.build) == before
    assert {name: (partial.root / name).read_bytes() for name in before} == before
    assert not (partial.root / "resume.started.json").exists()


@pytest.mark.parametrize("name", ("build-started.json", "source-upload-acknowledged.json", "native-build-identity.json",
    "terminal-recoveries/hirewiz-doc-six-cebe8490883c/claim.acknowledged.json",
    "terminal-recoveries/hirewiz-doc-six-cebe8490883c/original/native-proof/run.started.json"))
def test_altered_build_source_or_consumed_history_never_admits_resume(partial, name):
    (partial.root / name).write_bytes(partial.values[name] + b" ")
    with pytest.raises(refresh.RefreshDenied):
        resume.partial_snapshot(partial.root, partial.build)


@pytest.mark.parametrize("name", ("native-proof/create.started.json", "transition/close.started.json", "result.json", "unknown.json"))
def test_unknown_native_intent_transition_or_result_is_not_skipped(partial, name):
    path = partial.root / name
    path.parent.mkdir(mode=0o700, exist_ok=True)
    path.write_bytes(b"{}\n")
    path.chmod(0o600)
    with pytest.raises(refresh.RefreshDenied):
        resume.partial_snapshot(partial.root, partial.build)


@pytest.mark.parametrize("kind", ("file_symlink", "directory_symlink", "hard_link", "world_readable", "missing"))
def test_partial_artifact_custody_requires_owned_exact_regular_files(partial, kind):
    target = partial.root / "build-started.json"
    if kind == "file_symlink":
        saved = partial.tmp / "outside.json"
        saved.write_bytes(target.read_bytes())
        saved.chmod(0o600)
        target.unlink()
        target.symlink_to(saved)
    elif kind == "directory_symlink":
        directory = partial.root / "terminal-recoveries"
        moved = partial.tmp / "outside-directory"
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    elif kind == "hard_link":
        (partial.tmp / "outside-link.json").hardlink_to(target)
    elif kind == "world_readable":
        target.chmod(0o644)
    else:
        target.unlink()
    with pytest.raises((refresh.RefreshDenied, OSError)):
        resume.partial_snapshot(partial.root, partial.build)


def test_retained_partial_bytes_remain_admissible_private_tree(partial):
    retained = partial.tmp / "retained"
    resume.retain_partial(resume.partial_snapshot(partial.root, partial.build), retained)
    assert resume.partial_snapshot(retained, partial.build) == partial.values


class Storage:
    """Strict fake of the one resume object; source uploads/Builds are unsupported."""

    def __init__(self, original, directory, *, lost_ack=False, existing=False):
        self.original, self.directory = original, directory
        self.lost_ack, self.closed = lost_ack, False
        self.object = b"already consumed" if existing else None
        self.calls = []

    def request(self, uri, *, method, redirections, body=None, headers=None):
        from urllib.parse import parse_qs, unquote, urlsplit

        parsed = urlsplit(uri)
        assert parsed.scheme == "https" and parsed.hostname == "storage.googleapis.com" and redirections == 0
        query = parse_qs(parsed.query)
        name = "hirewiz-document-refresh-source/" + profile.BUILD["nonce"] + "/resume.json"
        self.calls.append(method)
        if method == "POST":
            assert parsed.path == "/upload/storage/v1/b/" + refresh.PROJECT + "_cloudbuild/o"
            assert query == {"uploadType": ["media"], "name": [name], "ifGenerationMatch": ["0"]}
            assert headers == {"Content-Type": "application/json"}
            # Both durable intent files must exist before the only provider write.
            for root in (self.original, self.directory):
                marker = json.loads((root / "resume.started.json").read_bytes())
                assert marker["automatic_replay"] is False and marker["body_sha256"] == digest(body)
            assert self.object is None
            self.object = body
            if self.lost_ack:
                raise RuntimeError("synthetic_resume_unknown_ack")
            return SimpleNamespace(status=200), canonical({"name": name, "bucket": refresh.PROJECT + "_cloudbuild", "generation": "7"})
        assert method == "GET" and unquote(parsed.path.split("/o/", 1)[1]) == name
        if self.object is None:
            return SimpleNamespace(status=404), b""
        if parsed.path.startswith("/download/"):
            assert query == {"alt": ["media"], "generation": ["7"]}
            return SimpleNamespace(status=200), self.object
        assert not query
        return SimpleNamespace(status=200), b"{}"

    def close(self):
        self.closed = True


def claim_setup(partial, *, lost_ack=False, existing=False):
    directory = partial.tmp / "output"
    directory.mkdir(mode=0o700)
    identity = {**copy.deepcopy(profile.BUILD), "source_sha256": copy.deepcopy(profile.SOURCES),
                "local_native_proof_sha256": "a" * 64}
    storage = Storage(partial.root, directory, lost_ack=lost_ack, existing=existing)
    partial.build.native_http = lambda: storage
    return directory, identity, storage


def test_one_create_only_resume_claim_retains_original_and_native_intent_boundary(partial):
    directory, identity, storage = claim_setup(partial)
    ack = resume.claim(directory, partial.root, identity, partial.manifest, partial.build)
    assert ack["generation"] == "7" and storage.calls == ["GET", "POST", "GET"] and storage.closed
    body = json.loads(storage.object)
    assert body["original_manifest_sha256"] == partial.manifest and body["build_identity"] == identity
    assert body["automatic_replay"] is False
    assert {name: (partial.root / name).read_bytes() for name in partial.values} == partial.values
    assert not (directory / "native-proof").exists()
    assert (directory / "resume.claim.checked.json").is_file()


def test_existing_global_resume_claim_never_sends_a_second_create(partial):
    directory, identity, storage = claim_setup(partial, existing=True)
    with pytest.raises(refresh.RefreshDenied, match="claim_exists_or_unknown"):
        resume.claim(directory, partial.root, identity, partial.manifest, partial.build)
    assert storage.calls == ["GET"] and storage.closed
    assert not (partial.root / "resume.started.json").exists()


def test_unknown_claim_ack_keeps_both_intents_and_prevents_new_runner_replay(partial):
    directory, identity, storage = claim_setup(partial, lost_ack=True)
    with pytest.raises(RuntimeError, match="synthetic_resume_unknown_ack"):
        resume.claim(directory, partial.root, identity, partial.manifest, partial.build)
    assert storage.calls == ["GET", "POST"] and storage.closed
    assert (partial.root / "resume.started.json").is_file() and (directory / "resume.started.json").is_file()
    assert not (directory / "resume.claim.acknowledged.json").exists()
    another = partial.tmp / "another-output"
    another.mkdir(mode=0o700)
    with pytest.raises(refresh.RefreshDenied, match="claim_exists_or_unknown"):
        resume.claim(another, partial.root, identity, partial.manifest, partial.build)
    assert storage.calls.count("POST") == 1
    assert not (another / "native-proof").exists()


@pytest.mark.parametrize("change", ("image", "build", "source", "manifest"))
def test_unreviewed_candidate_cannot_claim_resume_before_provider_access(partial, change):
    directory, identity, storage = claim_setup(partial)
    manifest = partial.manifest
    if change == "manifest":
        manifest = "0" * 64
    elif change == "source":
        identity["source_sha256"] = {**profile.SOURCES, "backend/app/services/native_project.py": "0" * 64}
    else:
        identity["immutable_image" if change == "image" else "build_id"] = "foreign"
    with pytest.raises(refresh.RefreshDenied, match="claim_binding_changed"):
        resume.claim(directory, partial.root, identity, manifest, partial.build)
    assert not storage.calls and not (partial.root / "resume.started.json").exists()


@pytest.mark.parametrize("changed", (None, "build", "source", "generation"))
def test_partial_build_joins_fresh_observation_to_original_exact_acknowledgments(partial, changed):
    actual = {**copy.deepcopy(profile.BUILD), "source_sha256": copy.deepcopy(profile.SOURCES)}
    if changed == "build":
        actual["build_id"] = "another-build"
    elif changed == "generation":
        actual["source_generation"] = "999"
    elif changed == "source":
        actual["source_sha256"]["backend/app/services/native_project.py"] = "0" * 64
    reads = []
    partial.build.verify_current_build = lambda *args: (reads.append(args), actual)[1]
    if changed:
        with pytest.raises(refresh.RefreshDenied, match="fresh_build_changed"):
            resume.verify_partial_build(partial.values, partial.build)
    else:
        assert resume.verify_partial_build(partial.values, partial.build) == actual
    assert reads == [(profile.BUILD["build_id"], profile.BUILD["nonce"], profile.IMAGE)]


@pytest.mark.parametrize("outcome", ("success", "native_job_present", "bad_font"))
def test_resume_reuses_exact_build_and_fresh_proof_without_source_upload_or_build_replay(partial, monkeypatch, outcome):
    from backend.tests.test_document_refresh import proof as local_proof

    from scripts import document_test_image as engines

    directory = partial.tmp / "output"
    directory.mkdir(mode=0o700)
    actual = {**copy.deepcopy(profile.BUILD), "source_sha256": copy.deepcopy(profile.SOURCES)}
    calls = []
    partial.build.verify_current_build = lambda *args: (calls.append("fresh_build"), copy.deepcopy(actual))[1]
    partial.build.native_build = lambda *_a, **_k: pytest.fail("Existing Build must not be submitted again")
    partial.build.full_source_snapshot = lambda: ({}, copy.deepcopy(profile.SOURCES), profile.BUILD["policy_sha256"])
    storage = Storage(partial.root, directory)
    partial.build.native_http = lambda: storage
    current_infra = partial.build.infra
    class HttpError(Exception):
        status_code = 404
    class Clients:
        errors = SimpleNamespace(HttpError=HttpError)
        def preflight(self):
            calls.append("preflight")
        def job(self, name):
            assert name.endswith("/jobs/hirewiz-doc-six-" + profile.BUILD["nonce"][:12])
            if outcome == "native_job_present":
                calls.append("job_present")
                return {"name": name}
            calls.append("job_absent")
            raise HttpError()
        def close(self):
            pass
    probe = SimpleNamespace(Clients=Clients, PARENT="projects/" + refresh.PROJECT + "/locations/" + refresh.REGION)
    partial.build.infra = lambda name: probe if name == "document_refresh_probe" else current_infra(name)
    def carried(retained, build, observed_probe):
        assert observed_probe is probe and resume.partial_snapshot(retained, build) == partial.values
        calls.append("carried_history_reverified")
    monkeypatch.setattr(resume, "verify_carried_recovery", carried)
    issued = (datetime.now(UTC) - timedelta(hours=1)).replace(microsecond=0)
    proof = local_proof()
    proof["source_sha256"] = copy.deepcopy(profile.SOURCES)
    for row in proof["corpus"]:
        row["scan"]["definitions"] = "28155/" + issued.isoformat()
    renderer = proof["docx_layout_corpus"][0]["layout"]["renderer"]
    renderer.update(renderer_version=profile.RENDERER, font_manifest_sha256=profile.FONT,
                    policy_sha256=profile.BUILD["policy_sha256"])
    if outcome == "bad_font":
        renderer["font_manifest_sha256"] = "0" * 64
    monkeypatch.setattr(engines, "docker_command", lambda: ["synthetic-docker"])
    def check(platform, *, image):
        assert platform == "linux/amd64" and image == proof["image_id"]
        calls.append("fresh_local_25")
        return copy.deepcopy(proof)
    monkeypatch.setattr(engines, "check", check)
    def command(argv, *, seconds):
        assert argv in (["synthetic-docker", "pull", profile.IMAGE], ["synthetic-docker", "image", "inspect", profile.IMAGE])
        if "inspect" in argv:
            return canonical([{"Id": proof["image_id"], "RepoDigests": [profile.IMAGE]}])
        return b""
    partial.build.command = command
    partial.build.header = lambda *_a: ("ClamAV 1.4.3/28155/" + issued.strftime("%a %b %d %H:%M:%S %Y")).encode()
    def verify(value):
        assert profile.matches_build(value)
        assert value["local_native_proof_sha256"] == digest((directory / "local-native-proof.json").read_bytes())
        calls.append("fresh_build_bound_to_local_proof")
    partial.build.verify_native_build = verify
    if outcome != "success":
        with pytest.raises(refresh.RefreshDenied, match="native_job_exists" if outcome == "native_job_present" else "renderer_changed"):
            resume.resume_candidate(directory, partial.root, manifest_sha256=partial.manifest,
                                    publish=True, now=datetime.now(UTC), build=partial.build)
        assert not storage.calls and not (partial.root / "resume.started.json").exists()
        assert not (directory / "native-proof").exists()
        if outcome == "bad_font":
            assert calls == ["fresh_build", "carried_history_reverified", "fresh_local_25"]
        else:
            assert calls[-1] == "job_present"
        return
    result = resume.resume_candidate(directory, partial.root, manifest_sha256=partial.manifest,
                                     publish=True, now=datetime.now(UTC), build=partial.build)
    assert calls == ["fresh_build", "carried_history_reverified", "fresh_local_25",
                     "fresh_build_bound_to_local_proof", "preflight", "job_absent"]
    assert storage.calls == ["GET", "POST", "GET"]
    assert result["new_build_created"] is False and result["native_corpus_proven"] is False
    assert result["promotion_performed"] is False and result["status"] == "candidate_requires_native_acceptance"
    assert result["candidate_registry_digest"] == profile.IMAGE
    assert (directory / "original-candidate/native-build-identity.json").read_bytes() == partial.values["native-build-identity.json"]
    assert not (directory / "native-proof").exists() and not (directory / "transition").exists()


@pytest.mark.parametrize("name", ("native-proof", "transition", "unknown-directory"))
def test_even_empty_unrecognized_journals_are_not_a_fresh_partial_artifact(partial, name):
    (partial.root / name).mkdir(mode=0o700)
    with pytest.raises(refresh.RefreshDenied):
        resume.partial_snapshot(partial.root, partial.build)


def test_reviewed_fonts_do_not_authorize_a_different_registry_image_context():
    from backend.tests.test_document_refresh import proof as local_proof

    issued = (datetime.now(UTC) - timedelta(hours=1)).replace(microsecond=0)
    value = local_proof()
    value["source_sha256"] = copy.deepcopy(profile.SOURCES)
    for row in value["corpus"]:
        row["scan"]["definitions"] = "28155/" + issued.isoformat()
    value["docx_layout_corpus"][0]["layout"]["renderer"].update(
        renderer_version=profile.RENDERER, font_manifest_sha256=profile.FONT,
        policy_sha256=profile.BUILD["policy_sha256"])
    identity = {**copy.deepcopy(profile.BUILD), "source_sha256": copy.deepcopy(profile.SOURCES)}
    # The valid corpus first proves this profile itself is admissible.
    assert refresh.proof_contract(value, refresh.accepted(), profile.SOURCES, now=datetime.now(UTC),
                                  image=profile.IMAGE, build_identity=identity)["local_cases"] == 25
    foreign = refresh.REPOSITORY + "@sha256:" + "a" * 64
    with pytest.raises(refresh.RefreshDenied, match="renderer_build_profile_changed"):
        refresh.proof_contract(value, refresh.accepted(), profile.SOURCES, now=datetime.now(UTC),
                               image=foreign, build_identity=identity)
