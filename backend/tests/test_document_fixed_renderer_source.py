"""Pure source/provenance controls; synthetic transports never qualify an image."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from infra.gcp import document_candidate as courier
from infra.gcp import document_source_candidate as profile
from infra.gcp import document_source_release as release
from test_document_source_revision import NONCE, build
from test_document_source_revision import source as _source_fixture

from scripts import document_refresh as builder
from scripts import document_test_image as engines

source = _source_fixture

LEGACY_COMMIT = "41c57ffb3de9b4553f18c87121b35d4186b7333c"
LEGACY_QUALIFICATION = "949cbfed439fb42474494d63ec0dcf0c570f6dd99c02070ddcc6095d1aa1389a"


def test_production_overlay_preserves_renderer_and_copies_exact_python_runtime():
    raw = (profile.ROOT / profile.DOCKERFILE).read_text()
    lines = raw.splitlines()
    assert [line.split()[1] for line in lines if line.startswith("FROM ")] == [courier.IMAGE] * 2
    assert not any(word in raw for word in ("apt-get", "pip install", "fc-cache", "--write-font-manifest", "write_clamd_native_manifest"))
    copies = {}
    for line in lines:
        if line.startswith("COPY ") and "--from=" not in line:
            names = line.split()[1:]
            for name in names[:-1]:
                target = names[-1] + name.rsplit("/", 1)[-1] if names[-1].endswith("/") else names[-1]
                copies[name] = target
    assert copies == {name: target for name, target in courier.RUNTIME.items() if name.endswith(".py")}
    assert "COPY --from=definitions /var/lib/clamav /var/lib/clamav" in raw
    assert raw.count("RUN rm -rf /var/lib/clamav") == 1
    assert "freshclam --stdout --datadir=/var/lib/clamav" in raw
    assert set(engines.SOURCES) == profile.SOURCES and len(engines.RUNTIME) == 20


def test_public_ci_copies_and_labels_all_inputs_but_executes_only_public_recipe(monkeypatch):
    hashes = engines.source_hashes()
    seen = []

    def command(arguments, **_):
        context = Path(arguments[-1])
        actual = {str(path.relative_to(context)): profile.digest(path.read_bytes())
                  for path in context.rglob("*") if path.is_file()}
        assert actual == hashes and len(actual) == 22
        assert arguments[arguments.index("--file") + 1] == str(context / courier.DOCKERFILE)
        assert sum(row == "--label" for row in arguments) == 22
        public = (context / courier.DOCKERFILE).read_text()
        assert "FROM public.ecr.aws/" in public and "docker.pkg.dev" not in public
        Path(arguments[arguments.index("--iidfile") + 1]).write_text("sha256:" + "f" * 64)
        seen.append(arguments)
        return 0, b""

    monkeypatch.setattr(engines, "command", command)
    assert engines.build(["synthetic-no-docker"], "linux/amd64", hashes) == "sha256:" + "f" * 64
    assert len(seen) == 1


@pytest.mark.parametrize("change", ["recipe", "base", "kind", "archive"])
def test_production_recipe_parent_kind_and_archive_cannot_be_swapped(source, change):
    _, current = source
    value, identity = build(source)
    if change == "recipe":
        args = value["steps"][0]["args"]
        args[args.index("--file") + 1] = courier.DOCKERFILE
        with pytest.raises(builder.RefreshDenied, match="steps_changed"):
            builder.build_contract(value, NONCE, identity["refresh_dockerfile_sha256"], full_source=True,
                source_revision=True, source_commit=current.commit)
    elif change == "archive":
        raw = builder.full_source_archive(source_revision=True, source_commit=current.commit)
        with pytest.raises(builder.RefreshDenied, match="archive_changed"):
            builder.archive_full_source(raw + b"unreviewed", source_revision=True, source_commit=current.commit)
    else:
        identity["fixed_renderer_base" if change == "base" else "kind"] = (
            builder.RENDERER_BASE if change == "base" else profile.LEGACY_KIND)
        with pytest.raises(release.prior.NativeProofDenied, match="wrong_build_kind"):
            release.SourceProbe(identity, root=source[0])


@pytest.mark.parametrize("change", ["missing_recipe", "unknown_source"])
def test_rehashed_policy_cannot_change_finite_source_inventory(monkeypatch, change):
    value = json.loads((profile.ROOT / profile.POLICY_PATH).read_bytes())
    if change == "missing_recipe":
        value["source_sha256"].pop(profile.DOCKERFILE)
    else:
        value["source_sha256"]["unreviewed.py"] = "f" * 64
    raw = profile.canonical(value)
    monkeypatch.setattr(profile, "POLICY_SHA256", profile.digest(raw))
    with pytest.raises(profile.SourceDenied, match="inventory_changed"):
        profile.validate_policy(raw)


def test_original_full_source_tuple_remains_exact_read_only(monkeypatch):
    old = profile.committed(LEGACY_COMMIT, legacy=True)
    assert old.policy == profile.LEGACY_POLICY_SHA256 and len(old.sources) == 21
    assert old.qualification_sha256 == LEGACY_QUALIFICATION and len(old.runtime) == 20
    assert old.kind == profile.LEGACY_KIND and old.dockerfile == courier.DOCKERFILE
    with pytest.raises(profile.SourceDenied, match="legacy_write_refused"):
        profile.committed(LEGACY_COMMIT, legacy=True, clean=True)
    with pytest.raises(profile.SourceDenied, match="legacy_write_refused"):
        profile.require_working_contract(old)
    storage = {"bucket": builder.PROJECT + "_cloudbuild", "object": builder.FULL_SOURCE_PREFIX + NONCE + "/source.tar.gz", "generation": "1234"}
    request = builder.full_source_build_request(NONCE, storage, source_revision=True,
        source_commit=old.commit, legacy_source=True)
    value = request | {"id": "11111111-2222-3333-4444-555555555555", "projectId": builder.PROJECT,
        "name": release.prior.PARENT + "/builds/11111111-2222-3333-4444-555555555555", "status": "SUCCESS",
        "sourceProvenance": {"resolvedStorageSource": copy.deepcopy(storage)},
        "results": {"images": [{"name": request["images"][0], "digest": "sha256:" + "a" * 64}]}}
    recipe_sha = old.sources[old.dockerfile]
    identity = builder.build_contract(value, NONCE, recipe_sha, full_source=True, source_revision=True,
        source_commit=old.commit, legacy_source=True)
    raw = builder.full_source_archive(source_revision=True, source_commit=old.commit, legacy_source=True)
    identity.update(source_archive_sha256=profile.digest(raw), source_sha256=old.sources)
    assert identity["fixed_renderer_base"] is None and identity["kind"] == profile.LEGACY_KIND
    serving = release.SourceProbe(identity, serving_read=True)
    assert serving.source_contract() == old.runtime and serving.POLICY == old.policy
    with pytest.raises(release.prior.NativeProofDenied, match="wrong_build_kind"):
        release.SourceProbe(identity)
    calls = []
    monkeypatch.setattr(builder, "native_http", lambda: SimpleNamespace(close=lambda: None))

    def read(_http, method, url, *_):
        calls.append((method, url))
        assert method == "GET"
        return value

    monkeypatch.setattr(builder, "native_json", read)
    monkeypatch.setattr(builder, "read_native_source", lambda _http, actual: raw if actual["source_generation"] == "1234" else pytest.fail("Wrong generation"))
    assert builder.verify_current_build(value["id"], NONCE, identity["immutable_image"]) == identity
    assert len(calls) == 1


def test_original_policy_can_only_select_retained_serving_reads():
    image = builder.REPOSITORY + "@sha256:" + "a" * 64
    metadata = {"template": {"containers": [{"image": image, "env": [
        {"name": "DOCUMENT_WORKER_POLICY_SHA256", "value": profile.LEGACY_POLICY_SHA256}]}]}}
    assert release.prior.policy_for_serving(metadata, image) == profile.LEGACY_POLICY_SHA256
    assert release.SourceProbe.policy_for_serving(metadata, image) == profile.LEGACY_POLICY_SHA256
    with pytest.raises(release.prior.NativeProofDenied):
        release.prior.policy_for_serving(metadata, courier.IMAGE)


def retained_source_custody():
    """Reconstruct the reviewed nonsecret 41065248592 artifact from Git bytes."""
    import base64

    from infra.gcp import document_refresh_recovery as recovery

    nonce = "40a80b1c8e1b484fb4299fddae9cae1a"
    build_id = "f2dbba80-2c48-4e49-b1cf-8f30e4be109a"
    storage = {"bucket": builder.PROJECT + "_cloudbuild", "object": builder.FULL_SOURCE_PREFIX + nonce + "/source.tar.gz",
               "generation": "1793367816554954"}
    request = builder.full_source_build_request(nonce, storage, source_revision=True,
        source_commit=LEGACY_COMMIT, legacy_source=True)
    value = request | {"id": build_id, "projectId": builder.PROJECT,
        "name": "projects/157225724590/locations/us-central1/builds/" + build_id, "status": "SUCCESS",
        "sourceProvenance": {"resolvedStorageSource": copy.deepcopy(storage)},
        "results": {"images": [{"name": request["images"][0], "digest":
            "sha256:245a24793a73b09bd3df799d48bf087dffb2749eb217e42ec45f22504a70116a"}]}}
    old = profile.committed(LEGACY_COMMIT, legacy=True)
    identity = builder.build_contract(value, nonce, old.sources[old.dockerfile], full_source=True,
        source_revision=True, source_commit=LEGACY_COMMIT, legacy_source=True)
    archive = builder.full_source_archive(source_revision=True, source_commit=LEGACY_COMMIT, legacy_source=True)
    assert profile.digest(archive) == "9598c3e2eec31dc36ca82f876b38fa50fbf4e8f237f79569b2a0311f57c17a93"
    identity.update(source_sha256=old.sources, source_archive_sha256=profile.digest(archive))
    rows = {"native-build-identity.json": identity, "source-upload-acknowledged.json": storage,
        "build-started.json": {"automatic_replay": False, "nonce": nonce,
            "recipe_sha256": identity["refresh_dockerfile_sha256"], "source_archive_sha256": profile.digest(archive),
            "unknown_outcome": "reconcile_exact_native_object_and_build_only"},
        "build-create-started.json": {"automatic_replay": False, "nonce": nonce,
            "request_sha256": profile.digest(profile.canonical(request))},
        "build-acknowledged.json": {"automatic_replay": False, "nonce": nonce, "build_id": build_id,
            "operation_name": "operations/build/" + builder.PROJECT + "/" + base64.urlsafe_b64encode(build_id.encode()).decode().rstrip("=")}}
    files = {name: (json.dumps(row, sort_keys=True, indent=2) + "\n").encode() for name, row in rows.items()}
    assert recovery.manifest(files) == recovery.SOURCE_PRE_NATIVE_MANIFEST
    return files, value, archive, identity


@pytest.fixture(scope="module")
def retained_source():
    return retained_source_custody()


@pytest.mark.parametrize("change", [None, "artifact", "tag", "archive", "generation", "corpus_present",
                                    "inventory_present", "inventory_unknown", "inventory_permission"])
def test_retained_successful_build_can_only_be_abandoned_after_two_proven_absences(retained_source, monkeypatch, change):
    from infra.gcp import document_refresh_recovery as recovery

    original, observed, raw, identity = retained_source
    files, value, archive = dict(original), copy.deepcopy(observed), raw
    if change == "artifact":
        files["build-started.json"] += b" "
    elif change == "tag":
        value["tags"][2] = "f" * 64
    elif change == "archive":
        archive += b"extra"
    elif change == "generation":
        value["sourceProvenance"]["resolvedStorageSource"]["generation"] = "1"
    calls = []

    class HttpError(Exception):
        status_code = 404

    class Http:
        def request(self, url, *, method, redirections, **_):
            assert method == "GET" and redirections == 0
            calls.append((method, url))
            if url.startswith("https://cloudbuild.googleapis.com/"):
                return SimpleNamespace(status=200), json.dumps(value).encode()
            assert "generation=" + identity["source_generation"] in url
            return SimpleNamespace(status=200), archive

        def close(self):
            pass

    corpus = release.prior.PARENT + "/jobs/hirewiz-doc-six-" + identity["nonce"][:12]
    inventory = release.prior.PARENT + "/jobs/hirewiz-doc-six-" + profile.digest(("inventory:" + identity["nonce"]).encode())[:12]

    def job(name):
        assert name in (corpus, inventory)
        calls.append(("GET", name))
        if change == "corpus_present" and name == corpus or change == "inventory_present" and name == inventory:
            return {"name": name}
        if name == inventory and change == "inventory_unknown":
            raise RuntimeError("Synthetic read unavailable")
        error = HttpError()
        if name == inventory and change == "inventory_permission":
            error.status_code = 403
        raise error

    monkeypatch.setattr(builder, "native_http", Http)
    clients = SimpleNamespace(job=job, errors=SimpleNamespace(HttpError=HttpError))
    before = copy.deepcopy(files)
    if change is None:
        fact = recovery.verify_pre_native(files, clients, release.prior, builder)
        assert fact["absent_native_jobs"] == [corpus, inventory]
        assert fact["source_disposition"] == "retained_renderer_refusal_abandoned_not_acceptance"
        assert fact["native_job_http_status"] == 404 and len(calls) == 4
        assert "acceptance" not in fact and "successor_nonce" not in fact
    else:
        with pytest.raises((builder.RefreshDenied, RuntimeError)):
            recovery.verify_pre_native(files, clients, release.prior, builder)
    assert files == before and original == retained_source[0]
    assert all(method == "GET" for method, _ in calls)


def test_legacy_worker_snapshot_is_read_only_and_retains_all_original_configuration_fences():
    from infra.gcp import document_worker as worker
    from test_document_worker_deployment import IMAGE, RELEASE, snapshots

    value = snapshots(worker)
    for row in (value["service"]["template"]["containers"][0], value["revision"]["containers"][0]):
        next(item for item in row["env"] if item["name"] == "DOCUMENT_WORKER_POLICY_SHA256")["value"] = profile.LEGACY_POLICY_SHA256
    worker.check_snapshot(value, IMAGE, RELEASE, source_revision=True, legacy_source_revision=True)
    with pytest.raises(worker.ConfigurationDenied):
        worker.check_snapshot(value, IMAGE, RELEASE, source_revision=True)
    with pytest.raises(worker.ConfigurationDenied):
        worker.check_snapshot(value, IMAGE, RELEASE, legacy_source_revision=True)
    current = worker.render(IMAGE, RELEASE, source_revision=True)
    assert current["service"]["template"]["containers"][0]["env"][1]["value"] == profile.POLICY_SHA256
    value["service"]["template"]["containers"][0]["sandboxLauncher"] = False
    with pytest.raises(worker.ConfigurationDenied):
        worker.check_snapshot(value, IMAGE, RELEASE, source_revision=True, legacy_source_revision=True)
