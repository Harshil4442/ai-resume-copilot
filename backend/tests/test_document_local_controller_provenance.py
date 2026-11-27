"""Native-shaped local controller provenance over inert, explicit read ports."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from infra.gcp import schema27_patched_engine_recovery as operator

apps = operator.maintenance.applications
HEAD = "a" * 40
CONTROLLER_CI, HISTORICAL_CI = 123456, 654321


def ci(release):
    return {"headSha": release, "workflowName": "CI", "status": "completed", "conclusion": "success",
        "jobs": [{"name": name, "status": "completed", "conclusion": "success", "steps": [
            {"name": step, "status": "completed", "conclusion": "success"}
            for step in operator.restoration.mixed.app.successor.REQUIRED_STEPS] if name == "backend" else []}
            for name in sorted(apps.CI_JOBS)]}


def workflow(release):
    return {"head_sha": release, "path": ".github/workflows/ci.yml", "event": "pull_request",
        "repository": {"full_name": apps.REPOSITORY}}


@pytest.fixture
def local(tmp_path, monkeypatch):
    for name in ("GITHUB_REPOSITORY", "GITHUB_REF", "GITHUB_SHA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(operator.identities, "ROOT", tmp_path)
    custody = []
    profile = operator.identities.Profile(None, "f" * 64)
    monkeypatch.setattr(operator.identities.Profile, "verify_custody", lambda _: custody.append("profile"))
    restored = object.__new__(operator.HistoricalRestore)
    restored.profile = profile
    restored.verify_custody = lambda: custody.append("restore")
    pin = apps.Source(operator.restoration.SOURCE70, "sha256:" + "b" * 64,
        "11111111-1111-4111-8111-111111111111", HISTORICAL_CI)
    restored.application_plan = SimpleNamespace(release=operator.restoration.SOURCE3F7,
        prior_source=SimpleNamespace(**pin.__dict__))
    context = object.__new__(operator.Applications)
    context.root, context.identity_profile, context.restored = tmp_path, profile, restored
    context.controller_ci_run_id, context._controller_verified = CONTROLLER_CI, None
    context.registry, context.verified = {pin.release: pin}, {}
    current = SimpleNamespace(commit=HEAD, qualification_sha256="c" * 64)
    source_checks, commands, builds = [], [], []

    def committed(*, root, clean):
        assert root == tmp_path and clean is True
        source_checks.append(True)
        return current

    monkeypatch.setattr(operator.engine, "committed", committed)
    monkeypatch.setattr(apps, "sources", lambda root: {} if root == tmp_path else pytest.fail("wrong root"))
    wire = {"backend/app/services/local-wire-fixture.py": b"synthetic immutable wire"}
    monkeypatch.setattr(apps, "WIRE", {name: hashlib.sha256(raw).hexdigest() for name, raw in wire.items()})
    controller, historical = ci(HEAD), ci(pin.release)
    controller_workflow, historical_workflow = workflow(HEAD), workflow(pin.release)
    tag = pin.image.split("@")[0] + ":" + pin.release
    build = {"projectId": apps.PROJECT, "id": pin.build_id, "status": "SUCCESS",
        "sourceProvenance": {"resolvedGitSource": {"revision": pin.release,
            "url": "https://github.com/" + apps.REPOSITORY + ".git", "dir": "."}},
        "steps": [{"name": "gcr.io/cloud-builders/docker", "dir": ".", "args": args}
                  for args in (["build", "--tag", tag, "."], ["push", tag])],
        "results": {"images": [{"name": tag, "digest": pin.image_digest}]}}

    def command(root, args):
        assert root == tmp_path
        commands.append(args)
        if args == ["git", "rev-parse", "HEAD"]:
            return HEAD.encode()
        if args[:3] == ["git", "merge-base", "--is-ancestor"]:
            assert args[3:] == [pin.release, current.commit]
            return b""
        if args[:2] == ["git", "show"]:
            release, path = args[2].split(":", 1)
            assert release == pin.release
            return wire[path]
        if args[:3] == ["gh", "run", "view"]:
            return json.dumps(controller if int(args[3]) == CONTROLLER_CI else historical).encode()
        if args[:2] == ["gh", "api"]:
            run = int(args[2].rsplit("/", 1)[-1])
            return json.dumps(controller_workflow if run == CONTROLLER_CI else historical_workflow).encode()
        pytest.fail("unexpected command")

    monkeypatch.setattr(apps, "command", command)

    def get_json(url):
        assert url == f"https://cloudbuild.googleapis.com/v1/{apps.PARENT}/builds/{pin.build_id}"
        builds.append(url)
        return copy.deepcopy(build)

    context.get_json = get_json
    return SimpleNamespace(context=context, pin=pin, current=current, controller=controller,
        controller_workflow=controller_workflow, historical=historical, build=build, commands=commands,
        builds=builds, custody=custody, source_checks=source_checks, wire=wire)


def test_local_success_has_no_github_environment_and_recustodies_before_cached_return(local):
    c = local
    c.context.provenance(c.pin)
    assert c.context.verified == {c.pin.release: c.pin} and len(c.builds) == 1
    before = len(c.commands)
    c.context.provenance(c.pin)
    assert len(c.commands) == before  # Successful remote proof is not repeated merely for age.
    assert c.source_checks == [True, True] and c.custody == ["profile", "restore"] * 2


@pytest.mark.parametrize("fault", ["head", "repository", "workflow", "event", "failed", "missing_job", "step"])
def test_forged_ci_environment_cannot_bypass_failed_or_foreign_native_controller_ci(local, monkeypatch, fault):
    c = local
    monkeypatch.setenv("GITHUB_REPOSITORY", apps.REPOSITORY)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_SHA", HEAD)
    if fault == "head":
        c.controller["headSha"] = c.controller_workflow["head_sha"] = "d" * 40
    elif fault == "repository":
        c.controller_workflow["repository"]["full_name"] = "foreign/project"
    elif fault == "workflow":
        c.controller_workflow["path"] = ".github/workflows/other.yml"
    elif fault == "event":
        c.controller_workflow["event"] = "workflow_dispatch"
    elif fault == "failed":
        c.controller["jobs"][0]["conclusion"] = "failure"
    elif fault == "missing_job":
        c.controller["jobs"].pop()
    else:
        next(job for job in c.controller["jobs"] if job["name"] == "backend")["steps"][0]["conclusion"] = "failure"
    with pytest.raises(operator.rolling.RollingDenied, match="ci"):
        c.context.provenance(c.pin)
    assert c.context.verified == {} and c.context._controller_verified is None and c.builds == []


def test_missing_selector_preserves_original_main_guard(local):
    local.context.controller_ci_run_id = None
    with pytest.raises(apps.ApplicationDenied, match="trusted_default_main_context_required"):
        local.context.provenance(local.pin)
    assert local.builds == []


@pytest.mark.parametrize("run_id", [0, -1, True, "123"])
def test_invalid_explicit_controller_selector_refuses_before_remote_reads(local, run_id):
    local.context.controller_ci_run_id = run_id
    with pytest.raises(operator.rolling.RollingDenied, match="local_controller_scope"):
        local.context.provenance(local.pin)
    assert local.commands == [] and local.builds == []


def test_new_clean_head_is_not_accepted_from_cached_controller_ci(local):
    local.context.provenance(local.pin)
    local.current.commit = "d" * 40
    with pytest.raises(operator.rolling.RollingDenied, match="local_controller_changed"):
        local.context.provenance(local.pin)
    assert len(local.builds) == 1


def test_real_dirty_git_head_refuses_before_policy_or_remote_reads(local, monkeypatch, tmp_path):
    root = tmp_path / "dirty"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-c", "user.name=Local test", "-c", "user.email=local@example.invalid",
                    "commit", "--allow-empty", "-qm", "owned test"], cwd=root, check=True)
    (root / "untracked").write_text("source drift")
    # The fixture replaced this one function, so recover its actual committed implementation.
    spec = importlib.util.spec_from_file_location("dirty_controller_engine", operator.engine.__file__)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(operator.engine, "committed", module.committed)
    monkeypatch.setattr(operator.identities, "ROOT", root)
    local.context.root = root
    with pytest.raises(operator.engine.base.SourceDenied, match="clean_committed_source_required"):
        local.context.provenance(local.pin)
    assert local.commands == [] and local.builds == []


@pytest.mark.parametrize("fault", ["pin", "registry", "wire", "historical_ci", "build"])
def test_local_controller_ci_never_replaces_historical_source_and_native_build_checks(local, fault):
    c = local
    pin = c.pin
    if fault == "pin":
        pin = apps.Source(pin.release, "sha256:" + "e" * 64, pin.build_id, pin.ci_run_id)
    elif fault == "registry":
        c.context.registry = {}
    elif fault == "wire":
        c.wire[next(iter(c.wire))] = b"changed wire bytes"
    elif fault == "historical_ci":
        c.historical["conclusion"] = "failure"
    else:
        c.build["results"]["images"][0]["digest"] = "sha256:" + "e" * 64
    with pytest.raises((operator.rolling.RollingDenied, apps.ApplicationDenied)):
        c.context.provenance(pin)
    assert c.context.verified == {} and c.context._controller_verified is None
