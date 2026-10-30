"""Synthetic process/filesystem controls; no Git checkout or native child runs."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import qualify_schema25_retained_v2 as runner

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def harness(tmp_path, monkeypatch):
    current, temporary = tmp_path / "current", tmp_path / "ci-temp"
    current.mkdir()
    temporary.mkdir()
    for name in runner.PINS:
        path = current / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / name).read_bytes())
    binary, report = temporary / "owned-redis", temporary / "native-report.json"
    binary.write_text("synthetic executable; never executed\n")
    binary.chmod(0o700)
    report.write_text("{}")  # Synthetic path prerequisite, not a native receipt.
    environment = {"CI": "true", "GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "12345",
        "GITHUB_RUN_ATTEMPT": "1", "RUNNER_TEMP": str(temporary), "PATH": "/synthetic/bin",
        "HIREWIZ_TEST_POSTGRES_URL": "postgresql+psycopg://hirewiz:synthetic@127.0.0.1:55433/hirewiz_admission_test",
        "HIREWIZ_OWNED_REDIS_SERVER": str(binary), "HIREWIZ_DOCUMENT_TEST_REPORT": str(report),
        "HIREWIZ_NATIVE_TEX_TEST_IMAGE": "sha256:" + "a" * 64}
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("HIREWIZ_APP25_TEST_ROOT", raising=False)
    monkeypatch.delenv("HIREWIZ_AUTHORITY_EMULATOR_PORT", raising=False)
    monkeypatch.setattr(runner.sys, "platform", "linux")
    state = SimpleNamespace(root=current, temporary=temporary, environment=environment, calls=[],
                            checkout=None, fail=None, changed=None, verifications=0,
                            error_output=b"private error never reflected")

    def fake(args, *, cwd, env, stdin, capture_output, timeout, check):
        assert stdin == subprocess.DEVNULL and capture_output is True and check is False
        assert env["APP_ENV"] == "test" and env["HIREWIZ_APP_COMPATIBILITY_REQUIRED"] == "true"
        assert env["HIREWIZ_AUTHORITY_EMULATOR_REQUIRED"] == "true"
        assert not ({"PYTHONPATH", "PYTHONHOME", "PYTHONOPTIMIZE", "PYTEST_ADDOPTS", "PYTEST_PLUGINS",
                     "GOOGLE_APPLICATION_CREDENTIALS", "LLM_API_KEY"} & env.keys())
        cwd = Path(cwd)
        if args[0] == "git":
            assert args[1:3] == ["-c", "core.hooksPath=" + runner.os.devnull]
            command = args[3:]
            if command[:2] == ["worktree", "add"]:
                phase = "add"
                assert cwd == current and command[2] == "--detach" and command[4] == runner.SOURCE
                checkout = Path(command[3])
                state.checkout = checkout
                checkout.mkdir()
                (checkout / ".git").write_text("gitdir: synthetic-owned-registry\n")
                for name in runner.PINS:
                    target = checkout / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes((current / name).read_bytes())
                if state.changed == "initial_lock":
                    (checkout / "backend/uv.lock").write_text("different source lock")
                output = b""
            elif command[:2] == ["worktree", "remove"]:
                phase, output = "remove", b""
                assert cwd == current and command[2:] == ["--force", str(state.checkout)]
            elif command == ["rev-parse", "HEAD"]:
                phase = "head"
                output = (("f" * 40 if state.changed == "head" and state.verifications else runner.SOURCE) + "\n").encode()
            elif command == ["rev-parse", "--show-toplevel"]:
                phase, output = "root", (str(cwd) + "\n").encode()
            elif command == ["status", "--porcelain", "--untracked-files=all"]:
                phase, output = "status", b""
            else:
                pytest.fail("Unexpected Git operation in fake transport")
        elif args[:5] == [runner.sys.executable, "-I", "-B", "-c", runner._VERIFY]:
            phase = "verify"
            state.verifications += 1
            assert args[5:] == [str(state.checkout), runner.SOURCE]
            assert cwd == state.checkout / "backend"
            prefix = ".venv/bin/python -I -B -m pytest --noconftest -q "
            output = json.dumps({"fingerprint": runner.FINGERPRINT, "app": runner.APP_SHA256, "files": 274,
                "compatibility": prefix + " ".join(runner.COMPATIBILITY),
                "protocol": prefix + " ".join(runner.PROTOCOL)}).encode()
            if state.changed == "verification":
                output = output.replace(runner.FINGERPRINT.encode(), b"0" * 64)
        else:
            assert args[:7] == [runner.sys.executable, "-I", "-B", "-m", "pytest", "--noconftest", "-q"]
            assert cwd == state.checkout / "backend" and timeout == 300
            phase = "compatibility" if tuple(args[7:]) == runner.COMPATIBILITY else "protocol"
            assert tuple(args[7:]) == (runner.COMPATIBILITY if phase == "compatibility" else runner.PROTOCOL)
            output = b"synthetic terminal test process; not a native result"
        state.calls.append((phase, list(args), cwd, dict(env)))
        if state.fail == (phase, "timeout"):
            raise subprocess.TimeoutExpired(args, timeout)
        if state.fail == (phase, "interrupt"):
            raise KeyboardInterrupt
        if state.fail == (phase, "error"):
            return SimpleNamespace(returncode=1, stdout=b"", stderr=state.error_output)
        if phase == "remove" and state.changed != "remove_no_ack":
            shutil.rmtree(state.checkout)  # This fake's invocation-owned scratch only.
        if phase == "compatibility" and state.changed == "link":
            (state.checkout / ".git").write_text("gitdir: another-registry\n")
        if phase == "compatibility" and state.changed == "directory":
            saved = state.checkout.parent / "unrelated-source-now-retained"
            state.checkout.rename(saved)
            shutil.copytree(saved, state.checkout)
        return SimpleNamespace(returncode=0, stdout=output, stderr=b"")

    monkeypatch.setattr(runner.subprocess, "run", fake)
    return state


def phases(harness):
    return [row[0] for row in harness.calls]


def test_original_two_commands_use_only_verified_historical_checkout_and_isolated_environment(harness, monkeypatch):
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONOPTIMIZE", "PYTEST_ADDOPTS", "PYTEST_PLUGINS", "GOOGLE_APPLICATION_CREDENTIALS", "LLM_API_KEY"):
        monkeypatch.setenv(key, "synthetic-injection-must-not-reach-child")
    runner.run(harness.root)
    assert phases(harness).count("compatibility") == phases(harness).count("protocol") == 1
    assert phases(harness).count("verify") == 3 and phases(harness).count("remove") == 1
    assert phases(harness).index("verify") < phases(harness).index("compatibility") < phases(harness).index("protocol")
    assert list(harness.temporary.glob("hirewiz-app25-retained-v2-*")) == []
    assert (harness.root / "backend/uv.lock").read_bytes() == (ROOT / "backend/uv.lock").read_bytes()


@pytest.mark.parametrize("phase", ["compatibility", "protocol"])
def test_failed_original_child_refuses_and_retains_unproven_descendant_source(harness, phase):
    harness.fail = (phase, "error")
    with pytest.raises(runner.RetainedV2Denied, match="child_failed"):
        runner.run(harness.root)
    assert phases(harness).count(phase) == 1 and "remove" not in phases(harness)
    assert harness.checkout.exists()
    if phase == "compatibility":
        assert "protocol" not in phases(harness)


def test_inner_timeout_reported_as_outer_pytest_exit_one_keeps_owned_checkout(harness):
    harness.fail = ("protocol", "error")
    harness.error_output = b"subprocess.TimeoutExpired: synthetic inner native test exceeded240s"
    with pytest.raises(runner.RetainedV2Denied, match="child_failed"):
        runner.run(harness.root)
    assert phases(harness).count("protocol") == 1 and "remove" not in phases(harness)
    assert harness.checkout.is_dir()


@pytest.mark.parametrize("phase", ["add", "verify", "compatibility", "protocol", "remove"])
def test_unknown_subprocess_outcome_is_retained_and_never_replayed(harness, phase):
    harness.fail = (phase, "timeout")
    with pytest.raises(runner.RetainedV2Denied, match="outcome_unknown"):
        runner.run(harness.root)
    assert phases(harness).count(phase) == 1 and harness.checkout.exists()
    if phase != "remove":
        assert "remove" not in phases(harness)


def test_interrupted_native_process_does_not_claim_cleanup(harness):
    harness.fail = ("compatibility", "interrupt")
    with pytest.raises(runner.RetainedV2Denied, match="outcome_unknown"):
        runner.run(harness.root)
    assert "remove" not in phases(harness) and harness.checkout.exists()


@pytest.mark.parametrize("change", ["head", "link", "directory"])
def test_changed_checkout_cannot_be_deleted_or_used_for_next_command(harness, change):
    harness.changed = change
    with pytest.raises(runner.RetainedV2Denied, match="head_changed|link_or_owner_changed"):
        runner.run(harness.root)
    assert "protocol" not in phases(harness) and "remove" not in phases(harness)
    assert harness.checkout.exists()


@pytest.mark.parametrize("change", ["initial_lock", "verification"])
def test_different_historical_source_or_contract_never_reaches_original_tests(harness, change):
    harness.changed = change
    with pytest.raises(runner.RetainedV2Denied, match="(historical_file|original_verification)_changed"):
        runner.run(harness.root)
    assert "compatibility" not in phases(harness) and "protocol" not in phases(harness)


@pytest.mark.parametrize("outcome", ["error", "missing_ack"])
def test_failed_cleanup_cannot_produce_qualification(harness, outcome):
    if outcome == "error":
        harness.fail = ("remove", "error")
    else:
        harness.changed = "remove_no_ack"
    with pytest.raises(runner.RetainedV2Denied, match="child_failed|cleanup_not_confirmed"):
        runner.run(harness.root)
    assert harness.checkout.exists() and phases(harness).count("remove") == 1


def test_changed_current_dependency_lock_fails_before_any_checkout(harness):
    (harness.root / "backend/uv.lock").write_text("different dependency set")
    with pytest.raises(runner.RetainedV2Denied, match="installed_lock_changed"):
        runner.run(harness.root)
    assert harness.calls == []


@pytest.mark.parametrize("key,value", [("CI", "false"), ("GITHUB_RUN_ID", "0"),
    ("HIREWIZ_TEST_POSTGRES_URL", "postgresql+psycopg://synthetic@cloud.invalid/production"),
    ("HIREWIZ_TEST_POSTGRES_URL", "postgresql+psycopg://synthetic@127.0.0.1:5432/production"),
    ("HIREWIZ_TEST_POSTGRES_URL", "postgresql+psycopg://synthetic@[invalid"),
    ("HIREWIZ_APP25_TEST_ROOT", "/unrelated/owned-looking-path"),
    ("HIREWIZ_AUTHORITY_EMULATOR_PORT", "58878"), ("HIREWIZ_NATIVE_TEX_TEST_IMAGE", "mutable:tag")])
def test_missing_or_unowned_service_context_refuses_without_process(harness, key, value):
    env = harness.environment | {key: value}
    with pytest.raises(runner.RetainedV2Denied):
        runner._environment(env, platform="linux")
    assert harness.calls == []


def test_macos_refuses_without_starting_any_subprocess(harness):
    with pytest.raises(runner.RetainedV2Denied, match="owned_linux_ci_required"):
        runner._environment(harness.environment, platform="darwin")
    assert harness.calls == []


def test_native_input_symlink_cannot_escape_the_owned_ci_directory(harness):
    link = harness.temporary / "report-link.json"
    link.symlink_to(Path(harness.environment["HIREWIZ_DOCUMENT_TEST_REPORT"]))
    env = harness.environment | {"HIREWIZ_DOCUMENT_TEST_REPORT": str(link)}
    with pytest.raises(runner.RetainedV2Denied, match="native_input_path"):
        runner._environment(env, platform="linux")
    assert harness.calls == []


@pytest.mark.parametrize("change", ["not_executable", "outside", "empty_report", "partial_transfer"])
def test_native_resource_paths_cannot_be_replaced_with_unrelated_inputs(harness, change, tmp_path):
    env = dict(harness.environment)
    if change == "not_executable":
        Path(env["HIREWIZ_OWNED_REDIS_SERVER"]).chmod(0o600)
    elif change == "outside":
        unrelated = tmp_path / "unrelated-report.json"
        unrelated.write_text("{}")
        env["HIREWIZ_DOCUMENT_TEST_REPORT"] = str(unrelated)
    elif change == "empty_report":
        Path(env["HIREWIZ_DOCUMENT_TEST_REPORT"]).write_bytes(b"")
    else:
        env["HIREWIZ_TRANSFER_PG_DUMP"] = env["HIREWIZ_OWNED_REDIS_SERVER"]
    with pytest.raises(runner.RetainedV2Denied):
        runner._environment(env, platform="linux")
    assert harness.calls == []
