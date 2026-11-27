"""Patched source proof and admission controls over owned, no-cloud ports."""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import test_document_refresh_transition as lifecycle
from infra.gcp import document_patched_engine_release as patched
from infra.gcp import document_refresh_transition as transition
from infra.gcp import document_source_candidate_v5 as v5
from infra.gcp import document_source_patched_engine as engine
from infra.gcp import document_source_release as source
from infra.gcp import schema27_expired_v5_recovery as expired
from infra.gcp import schema27_mixed_identity_profile as identities
from infra.gcp import schema27_mixed_maintenance as maintenance
from infra.gcp import schema27_native_admission_closure as admission
from infra.gcp import schema27_patched_engine_recovery as recovery
from test_document_source_revision import accepted
from test_document_source_v5_release import probe as probe
from test_schema26_app_successor import no_network as no_network
from test_schema27_api_identity_operator import controlled as controlled
from test_schema27_api_identity_promotion import handoff as handoff
from test_schema27_mixed_app_successor import profile as profile


@pytest.mark.parametrize("version", ["1.4.3", "1.4.6", "1.4.7"])
def test_definition_engine_requires_explicit_patched_profile(version):
    from scripts import document_refresh as refresh

    now = datetime(2026, 11, 24, 12, tzinfo=UTC)
    raw = ("ClamAV " + version + "/12345/Tue Nov 24 11:00:00 2026").encode()
    for selected in ("1.4.3", "1.4.7"):
        if selected == version:
            assert refresh.definition(raw, now=now, engine_version=selected)["status"] == "fresh"
        else:
            with pytest.raises(refresh.RefreshDenied, match="definition_header_invalid"):
                refresh.definition(raw, now=now, engine_version=selected)


@pytest.mark.parametrize("bad", ["old", "mixed", "missing", None])
def test_native_receipt_requires_all_nine_measured_patched_scans(probe, monkeypatch, bad):
    target = object.__new__(patched.Probe)
    rows = [{"scan": {"engine": "ClamAV", "version": "1.4.7"}} for _ in range(9)]
    if bad == "old":
        for row in rows:
            row["scan"]["version"] = "1.4.3"
    elif bad == "mixed":
        rows[-1]["scan"]["version"] = "1.4.6"
    elif bad == "missing":
        rows.pop()
    evidence = ({}, {}, {}, {"cases": rows}, datetime.now(UTC))
    monkeypatch.setattr(source.SourceProbe, "_verified_evidence", lambda *_: evidence)
    if bad:
        with pytest.raises(identities.rolling.RollingDenied, match="measured_version"):
            target._verified_evidence(object())
    else:
        assert target._verified_evidence(object()) is evidence


def test_new_context_is_exact_and_not_old_expired_recovery():
    context = object.__new__(recovery.Applications)
    class Impostor(recovery.Applications):
        pass
    assert maintenance.selected(context) and recovery.selected(context)
    assert not expired.selected(context)
    assert not recovery.selected(object.__new__(Impostor))
    assert not recovery.selected(SimpleNamespace(patched_engine=True))


@pytest.mark.parametrize("failure", [None, "old_acceptance", "wrong_parent", "lost_ack", "new_admission_open", "closed_preimage_drift"])
def test_patched_transition_preserves_reads_and_never_reopens_processing(
    profile, probe, monkeypatch, tmp_path, failure,
):
    private, principal_boundary = profile
    policy = "7" * 64
    monkeypatch.setattr(engine, "POLICY_SHA256", policy)
    http = lifecycle.Http(lost_at=1 if failure == "lost_ack" else None)
    pins = dict(zip(transition.PINS, (lifecycle.OLD_V2, v5.POLICY_SHA256,
        source.courier.RENDERER, source.courier.FONT), strict=True))
    release = "b" * 40
    uids = dict(maintenance.applications.UIDS)
    for name, value in http.services.items():
        template = value["template"]
        if name == transition.WORKER:
            template["containers"][0]["image"] = lifecycle.OLD_V2
            template["containers"][0]["env"].append({"name": v5.TRANSPORT_ENV, "value": v5.TRANSPORT_MODE})
        else:
            value["uid"] = uids[name]
            template["serviceAccount"] = private.transition.new_api.email if name == identities.API else maintenance.applications.ACCOUNTS[name]
            template["labels"] = {"hirewiz-source": release}
            template["containers"][0]["env"].append({"name": "APP_RELEASE", "value": release})
            for row in template["containers"][0]["env"]:
                if row["name"] in transition.PAUSE[name]:
                    row["value"] = "true" if name == identities.API and row["name"] == "RESUME_DIRECT_UPLOAD_ENABLED" else "false"
            if name == identities.API:
                template["containers"][0]["env"].append({"name": "RESUME_UPLOAD_ADMISSION_ENABLED",
                    "value": "true" if failure == "new_admission_open" else "false"})
        for row in template["containers"][0]["env"]:
            if row["name"] in pins:
                row["value"] = pins[row["name"]]
        http.revisions[value["latestReadyRevision"]] = copy.deepcopy(template)
    parent = expired.ExpiredV5Predecessor(lifecycle.OLD_V2, "old-build", "f" * 40, "e" * 64,
        v5.POLICY_SHA256, probe.build["source_sha256"], source.courier.RENDERER, source.courier.FONT,
        "2026-11-21T00:00:00+00:00", "a" * 64, "b" * 64, "c" * 64, 5, 17)
    original_build = probe.build | {"immutable_image": lifecycle.OLD_V2}
    target = object.__new__(patched.Probe)
    target.__dict__.update(probe.__dict__)
    target.build = probe.build | {"kind": engine.KIND, "policy_sha256": policy}
    target.POLICY, target.identity_profile = policy, private
    fresh = replace(accepted(probe.source, target.build, target), image=lifecycle.NEW,
                    policy_sha256=policy, contract_version=5)
    monkeypatch.setattr(target, "verify_successor", lambda *_: parent if failure == "old_acceptance" else fresh)
    monkeypatch.setattr(target, "verify_current_successor", lambda image, _: replace(fresh, image=image))
    monkeypatch.setattr(source, "current_probe", lambda *_args, **_kwargs: SimpleNamespace(build=original_build, root=probe.root))
    def service(name):
        _, raw = http.request("https://run.googleapis.com/v2/" + name, method="GET", redirections=0)
        return json.loads(raw)
    clients = SimpleNamespace(service=service, revision=service, identity=principal_boundary.identity,
        preflight=lambda: {"service": copy.deepcopy(http.services[transition.WORKER])},
        document_policy=lambda name: copy.deepcopy(private.promotion.handoff["callers"][name]), close=lambda: None)
    context = object.__new__(recovery.Applications)
    context.identity_profile, context.clients, context.uids = private, clients, uids
    context.restored = SimpleNamespace(verify_custody=lambda: None, verify_current=lambda: None)
    context.require_active = lambda: None
    context.closed_preimage = {name: admission.projection(value, name)
        for name, value in http.services.items() if name in transition.CALLERS}
    if failure == "closed_preimage_drift":
        context.closed_preimage[identities.API]["etag"] = "different-closed-etag"
    context.registry = {release: maintenance.applications.Source(release, "sha256:" + "c" * 64,
        "11111111-1111-4111-8111-111111111111", 123)}
    context.provenance = lambda _: None
    context.probe = SimpleNamespace(Clients=lambda: clients, verify_expired_current_source_predecessor=lambda *_:
        replace(parent, contract_version=4) if failure == "wrong_parent" else parent)
    context.last_authorization = None
    directory = tmp_path / "patched-transition"
    before = copy.deepcopy(http.services)
    if failure:
        expected = {"old_acceptance": "current_acceptance_required", "wrong_parent": "patched_engine_parent_refused",
            "lost_ack": "outcome_unknown", "new_admission_open": "new_admission_open",
            "closed_preimage_drift": "closed_preimage_changed"}[failure]
        with pytest.raises((transition.TransitionDenied, lifecycle.transition.TransitionDenied, identities.rolling.RollingDenied), match=expected):
            transition.transition(lifecycle.NEW, target.build, tmp_path / "native", directory,
                http=http, probe=target, application_context=context, recover_expired_source=True)
        assert len(http.writes) == (1 if failure == "lost_ack" else 0)
        if failure == "lost_ack":
            with pytest.raises(transition.TransitionDenied, match="existing_journal"):
                transition.transition(lifecycle.NEW, target.build, tmp_path / "native", directory,
                    http=http, probe=target, application_context=context, recover_expired_source=True)
            assert len(http.writes) == 1
        return
    result = transition.transition(lifecycle.NEW, target.build, tmp_path / "native", directory,
        http=http, probe=target, application_context=context, recover_expired_source=True)
    assert result["admission_restored"] is False and result["new_admission_closed"] is True
    assert result["saved_source_capability_preserved"] is True and result["engine_version"] == "1.4.7"
    assert len(http.writes) == 10
    for name in transition.CALLERS:
        assert recovery.caller_admission_closed(name, http.services[name])
        assert http.services[name]["template"]["serviceAccount"] == before[name]["template"]["serviceAccount"]
        assert transition.environment(http.services[name]["template"])["DATABASE_URL"] == transition.environment(before[name]["template"])["DATABASE_URL"]
    assert transition.environment(http.services[identities.API]["template"])["RESUME_DIRECT_UPLOAD_ENABLED"]["value"] == "true"



def test_patched_source_has_separate_policy_with_exact_unchanged_wire():
    contents, hashes, digest = engine.working_snapshot()
    old = v5.validate_policy((engine.ROOT / v5.POLICY_PATH).read_bytes())
    assert engine.KIND != v5.KIND and digest != v5.POLICY_SHA256
    assert engine.CONTRACT_VERSION == 5 and engine.REQUIRED_CASE_COUNT == 17
    assert set(hashes) == engine.SOURCES and set(contents) == {*hashes, engine.POLICY_PATH}
    assert {name for name in old if old[name] != hashes[name]} == {
        source.courier.DOCKERFILE, "infra/document-processing/processor.py"}
    assert json.loads(contents[engine.POLICY_PATH])["scanner"]["docx_request_daemon"]["engine_source_review"] == "ClamAV-1.4.7"
    with pytest.raises(v5.v4.SourceDenied, match="synchronous_working_source_changed"):
        v5.working_snapshot()


@pytest.mark.parametrize("fault", ["bounds", "transport", "source_scope"])
def test_rehashing_patched_policy_cannot_change_safety_or_source_scope(monkeypatch, fault):
    raw = (engine.ROOT / engine.POLICY_PATH).read_bytes()
    value = json.loads(raw)
    if fault == "bounds":
        value["processor_rlimits"]["cpu_seconds"] = 999
    elif fault == "transport":
        value["required_native_deployment_controls"]["sandbox_egress"] = True
    else:
        value["source_sha256"]["infra/document-processing/service.py"] = "1" * 64
    altered = engine.base.canonical(value) + b"\n"
    monkeypatch.setattr(engine, "POLICY_SHA256", engine.base.digest(altered))
    with pytest.raises(engine.base.SourceDenied, match="patched_engine_(policy_semantics_changed|source_scope)"):
        engine.validate_policy(altered)


@pytest.fixture
def patched_source(tmp_path, monkeypatch):
    from fixtures.retained_native_source import local_git

    from scripts import document_refresh as refresh

    root = tmp_path / "patched-source"
    root.mkdir(mode=0o700)
    contents, _, _ = engine.working_snapshot()
    for name in {*engine.CONTRACT_PATHS, v5.POLICY_PATH, engine.base.POLICY_PATH,
                 source.courier.POLICY_PATH, "infra/gcp/document_candidate_payload.py"}:
        contents[name] = (engine.ROOT / name).read_bytes()
    contents.update({str(path.relative_to(engine.ROOT)): path.read_bytes()
        for path in (engine.ROOT / "infra/gcp").glob("*.py")})
    for name, raw in contents.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    for args in (("init", "-q"), ("add", "--all"), ("-c", "user.name=Owned fixture", "-c",
                 "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "Synthetic exact patched source")):
        local_git(root, *args)
    current = engine.committed(root=root, clean=True)
    monkeypatch.setattr(refresh, "ROOT", root)
    return root, current


def test_patched_build_binds_new_archive_git_and_payload_without_old_authority(patched_source, profile, monkeypatch):
    from fixtures.retained_native_source import local_git

    from scripts import document_refresh as refresh

    root, current = patched_source
    storage = {"bucket": refresh.PROJECT + "_cloudbuild", "object": refresh.FULL_SOURCE_PREFIX + lifecycle.NONCE + "/source.tar.gz", "generation": "1234"}
    request = refresh.full_source_build_request(lifecycle.NONCE, storage, source_revision=True,
        source_commit=current.commit, source_patched_engine=True)
    assert request["tags"] == [engine.KIND, current.commit, current.qualification_sha256]
    args = request["steps"][0]["args"]
    assert args[args.index("--file") + 1] == engine.DOCKERFILE
    value = request | {"id": "01234567-0123-0123-0123-012345678901", "projectId": refresh.PROJECT,
        "name": source.prior.PARENT + "/builds/01234567-0123-0123-0123-012345678901", "status": "SUCCESS",
        "sourceProvenance": {"resolvedStorageSource": storage},
        "results": {"images": [{"name": request["images"][0], "digest": lifecycle.NEW.split("@", 1)[1]}]}}
    identity = refresh.build_contract(value, lifecycle.NONCE, engine.base.digest((root / engine.DOCKERFILE).read_bytes()),
        full_source=True, source_revision=True, source_commit=current.commit, source_patched_engine=True)
    archive = refresh.full_source_archive(source_revision=True, source_commit=current.commit, source_patched_engine=True)
    identity.update(source_archive_sha256=engine.base.digest(archive), source_sha256=current.sources,
                    local_native_proof_sha256="f" * 64)
    assert refresh.archive_full_source(archive, source_revision=True, source_commit=current.commit,
        source_patched_engine=True) == identity["source_archive_sha256"]
    assert identity["kind"] == engine.KIND and identity["policy_sha256"] == engine.POLICY_SHA256
    with pytest.raises(source.prior.NativeProofDenied, match="wrong_build_kind"):
        source.SourceProbe(identity, root=root)
    target = patched.Probe(identity, profile[0], root=root)
    assert target.source_contract() == current.runtime
    for mode, handle in target.handles(identity).items():
        job = target.desired(lifecycle.NEW, handle["nonce"], mode)
        task = job["template"]["template"]
        assert task["maxRetries"] == 0
        assert task["containers"][0]["sandboxLauncher"] is True
        program = task["containers"][0]["args"][2]
        assert engine.POLICY_SHA256 in program and "hirewiz_document_source_required_v5" in program
        assert current.sources["infra/document-processing/clamav_supplier.py"] in program
    changed = root / "infra/gcp/document_patched_engine_release.py"
    changed.write_bytes(changed.read_bytes() + b"\n# uncommitted drift\n")
    with pytest.raises(engine.base.SourceDenied, match="working_contract_changed"):
        engine.require_working_contract(current, root)
    with pytest.raises(engine.base.SourceDenied, match="clean_committed_source_required"):
        engine.committed(root=root, clean=True)
    assert engine.committed(current.commit, root=root) == current
    assert local_git(root, "rev-parse", "HEAD").decode().strip() == current.commit


def test_refresh_build_stops_before_any_native_job_or_promotion(monkeypatch, tmp_path):
    from scripts import document_refresh as refresh

    calls = []
    worker = {"template": {"containers": [{"image": lifecycle.OLD_V2}]}}
    clients = SimpleNamespace(preflight=lambda: {"service": worker}, close=lambda: None)
    context = object.__new__(recovery.Applications)
    context.before_refresh = lambda: calls.append("closed-before-refresh")
    context.probe = SimpleNamespace(Clients=lambda: clients,
        policy_for_serving=lambda *_: v5.POLICY_SHA256)
    real_infra = refresh.infra
    def infra(name):
        if name == "document_refresh_probe":
            return object()
        if name == "document_refresh_recovery":
            return SimpleNamespace(prepare=lambda *args, **kwargs: calls.append(("prepare", kwargs)) or lifecycle.NONCE)
        return real_infra(name)
    monkeypatch.setattr(refresh, "infra", infra)
    identity = {"kind": engine.KIND, "policy_sha256": engine.POLICY_SHA256}
    def candidate(*args, **kwargs):
        calls.append(("candidate", kwargs))
        return {"build_identity": identity}
    monkeypatch.setattr(refresh, "full_source_candidate", candidate)
    def record(value, directory, **kwargs):
        calls.append(("record_build", value, directory, kwargs))
        return {"completed_stage": "build", "next_stage_required": "inventory", "next_stage_started": False}
    monkeypatch.setattr(recovery, "record_build", record)
    monkeypatch.setattr(recovery, "promote", lambda *_a, **_k: pytest.fail("build continued to promotion"))
    monkeypatch.setattr(patched, "run_candidate", lambda *_a, **_k: pytest.fail("build continued to a native Job"))
    output = tmp_path / "new"
    result = refresh.refresh(output, publish=True, now=datetime.now(UTC), history=tmp_path / "history",
        application_context=context, recover_expired_source=True, full_source=True,
        source_revision=True, source_patched_engine=True)
    assert result == {"completed_stage": "build", "next_stage_required": "inventory", "next_stage_started": False}
    assert calls[0] == "closed-before-refresh"
    prepare, candidate, promoted = calls[1:]
    assert prepare[1]["source_revision"] is True and prepare[1]["recover_expired_source"] is True
    assert prepare[1]["application_context"] is context
    assert candidate[1]["source_patched_engine"] is True and candidate[1]["application_context"] is context
    assert promoted[1] == {"build_identity": identity} and promoted[2:] == (output,
        {"application_context": context})


@pytest.mark.parametrize("context", [None, SimpleNamespace(patched_engine=True)])
def test_generic_refresh_cannot_select_patched_paid_path(monkeypatch, tmp_path, context):
    from scripts import document_refresh as refresh

    monkeypatch.setattr(refresh, "native_http", lambda: pytest.fail("invalid mode reached a native provider"))
    with pytest.raises(refresh.RefreshDenied, match="patched_engine_mode_refused"):
        refresh.refresh(tmp_path / "new", publish=True, now=datetime.now(UTC), application_context=context,
            recover_expired_source=True, full_source=True, source_revision=True, source_patched_engine=True)


@pytest.fixture
def staged_jobs(probe, monkeypatch):
    target = object.__new__(patched.Probe)
    target._v5, target.read_only_release = True, False
    target.build = probe.build | {"kind": engine.KIND, "policy_sha256": engine.POLICY_SHA256}
    target.identity_profile = SimpleNamespace(digest="a" * 64)
    target.profile, target.candidate = source.courier, target
    target.source_contract = lambda: {"fixture": "b" * 64}
    target.build_identity = lambda **_: target.build
    state = SimpleNamespace(jobs={}, operations={}, writes=[], paused=False, lose=None)
    def desired(image, nonce, mode):
        return {"name": "synthetic-" + mode, "template": {"taskCount": 1, "parallelism": 1,
            "template": {"timeout": "600s", "maxRetries": 0,
                "containers": [{"resources": {"limits": {"cpu": "2", "memory": "4Gi"}}}]}}}
    target.desired = desired
    target.validate_job = lambda job, _: None
    target.validate_inventory = lambda *_: {"renderer_version": source.courier.RENDERER,
        "font_manifest_sha256": source.courier.FONT}
    acceptance = replace(accepted(probe.source, target.build, probe), policy_sha256=engine.POLICY_SHA256,
                         contract_version=5)
    target.verify_successor = lambda *_: acceptance
    class Clients:
        def __init__(self, *args, **kwargs):
            pass
        def preflight(self):
            pass
        def job(self, name):
            if name not in state.jobs:
                raise patched.diagnostics.ForwardDenied("missing", "run_job_read", SimpleNamespace(status_code=404))
            return copy.deepcopy(state.jobs[name])
        def create(self, body):
            state.writes.append(("create", body["name"]))
            state.jobs[body["name"]] = body | {"uid": "job-" + body["name"], "etag": "initial"}
            ack = "create-" + body["name"]
            state.operations[ack] = copy.deepcopy(state.jobs[body["name"]])
            if state.lose == "create":
                raise RuntimeError("lost-create-ack")
            return {"name": ack}
        def start(self, job):
            state.writes.append(("start", job["name"]))
            mode = job["name"].removeprefix("synthetic-")
            execution = {"name": "execution-" + mode, "uid": "execution-uid-" + mode,
                "createTime": "2026-11-24T12:00:00Z", "startTime": "2026-11-24T12:00:01Z",
                "completionTime": "2026-11-24T12:00:02Z"}
            state.operations["run-" + job["name"]] = execution
            if state.lose == "start":
                raise RuntimeError("lost-start-ack")
            return {"name": "run-" + job["name"]}
        def close(self):
            pass
    def observation(_clients, mode, *_):
        return copy.deepcopy(state.jobs["synthetic-" + mode]), copy.deepcopy(state.operations["run-synthetic-" + mode]), {"inventory_or_corpus": mode}
    target._observation = observation
    monkeypatch.setattr(patched, "Clients", Clients)
    monkeypatch.setattr(patched.prior, "wait_operation", lambda *_: None)
    monkeypatch.setattr(patched.prior, "operation_result", lambda _clients, ack, _kind: copy.deepcopy(state.operations[ack["name"]]))
    def active():
        recovery.require_active(state.directory)
    state.active, state.target = active, target
    return state


def test_native_inventory_and_corpus_are_separate_once_only_invocations(staged_jobs, tmp_path):
    c = staged_jobs
    c.directory = tmp_path
    journal = tmp_path / "native-proof"
    first = patched.run_candidate(c.target, journal, stage="inventory", require_active=c.active)
    assert c.writes == [("create", "synthetic-inventory"), ("start", "synthetic-inventory")]
    assert first["stage"] == "inventory" and first["native_acceptance"] is False
    assert not (journal / "acceptance.json").exists()
    assert not (journal / "corpus.create.started.json").exists()
    with pytest.raises(identities.rolling.RollingDenied, match="fresh_native_journal"):
        patched.run_candidate(c.target, journal, stage="inventory", require_active=c.active)
    assert len(c.writes) == 2
    second = patched.run_candidate(c.target, journal, stage="corpus", require_active=c.active)
    assert c.writes == [("create", "synthetic-inventory"), ("start", "synthetic-inventory"),
        ("create", "synthetic-corpus"), ("start", "synthetic-corpus")]
    assert second["stage"] == "corpus" and (journal / "acceptance.json").is_file()
    with pytest.raises(identities.rolling.RollingDenied, match="existing_stage_reconcile_only"):
        patched.run_candidate(c.target, journal, stage="corpus", require_active=c.active)
    assert len(c.writes) == 4


@pytest.mark.parametrize("fault", ["lost_create", "lost_start", "inventory_drift", "pause"])
def test_corpus_never_follows_unknown_inventory_or_pause(staged_jobs, tmp_path, fault):
    c = staged_jobs
    c.directory = tmp_path
    journal = tmp_path / "native-proof"
    if fault.startswith("lost_"):
        c.lose = fault.removeprefix("lost_")
        with pytest.raises(RuntimeError, match="lost-"):
            patched.run_candidate(c.target, journal, stage="inventory", require_active=c.active)
        assert not (journal / "inventory.verified.json").exists()
    else:
        patched.run_candidate(c.target, journal, stage="inventory", require_active=c.active)
        if fault == "inventory_drift":
            c.operations["run-synthetic-inventory"]["uid"] = "different-execution"
        else:
            (tmp_path / "operator.pause-requested").touch(mode=0o600)
    count = len(c.writes)
    with pytest.raises((identities.rolling.RollingDenied, source.prior.build_module().RefreshDenied, FileNotFoundError)):
        patched.run_candidate(c.target, journal, stage="corpus", require_active=c.active)
    assert len(c.writes) == count
    assert not any(name == "synthetic-corpus" for _, name in c.writes)


def test_combined_native_runner_refused_before_provider_use(staged_jobs, tmp_path):
    c = staged_jobs
    c.directory = tmp_path
    with pytest.raises(identities.rolling.RollingDenied, match="explicit_job_stage_required"):
        patched.run_candidate(c.target, tmp_path / "native-proof", require_active=c.active)
    assert c.writes == []


@pytest.mark.parametrize("fault", [None, "root_copy", "nonce", "profile", "pause"])
def test_owned_staged_candidate_binds_frozen_source_root_nonce_and_profile(patched_source, tmp_path, monkeypatch, fault):
    import shutil

    from scripts import document_refresh as refresh

    root, current = patched_source
    monkeypatch.setattr(recovery.identities, "ROOT", root)
    directory = tmp_path / "stages"
    directory.mkdir(mode=0o700)
    closure_journal = tmp_path / "closed-journal"
    closure_journal.mkdir(mode=0o700)
    context = object.__new__(recovery.Applications)
    context.stage_directory, context.identity_profile = directory, SimpleNamespace(digest="e" * 64)
    context.restored = SimpleNamespace(closed=SimpleNamespace(verify_custody=lambda: None,
        plan=SimpleNamespace(digest="f" * 64), journal=SimpleNamespace(directory=closure_journal)))
    build = current.identity | {"nonce": lifecycle.NONCE, "immutable_image": lifecycle.NEW}
    value = recovery.record_build({"build_identity": build}, directory, application_context=context)
    assert value["completed_stage"] == "build" and value["next_stage_required"] == "inventory"
    assert value["next_stage_started"] is False
    if fault == "root_copy":
        other = tmp_path / "copied-stages"
        shutil.copytree(directory, other)
        directory = other
    elif fault == "nonce":
        path = directory / "candidate-build.json"
        altered = refresh.decode(path.read_bytes()) | {"nonce": "9" * 32}
        path.write_bytes(json.dumps(altered, sort_keys=True).encode())
    elif fault == "profile":
        context.identity_profile = SimpleNamespace(digest="d" * 64)
    elif fault == "pause":
        (directory / "operator.pause-requested").touch(mode=0o600)
        with pytest.raises(identities.rolling.RollingDenied, match="operator_paused"):
            context.require_active()
        return
    if fault:
        with pytest.raises(identities.rolling.RollingDenied, match="owned_stage_scope_changed"):
            recovery.load_staged_build(directory, context)
    else:
        assert recovery.load_staged_build(directory, context) == build
