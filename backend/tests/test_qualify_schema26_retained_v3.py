"""Synthetic custody/subprocess controls, no actual retained archive execution."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import qualify_schema26_retained_v3 as gate


def test_retained_anchor_is_the_genuinely_qualified45b3_not_unqualified_bdea():
    assert gate.SOURCE == "45b3e808c88d78c1d7d182cd4af4482df25c13f0"
    assert gate.APP_SHA256 == "f2775c3b9f8d8df1f4f3d97abbe190cf5d2eb46b87beeb942b5252c3840d8670"
    assert gate.FINGERPRINT == "ae56969b6a6aeb41d6b8202deca891c839e3f8564efc5743457777f2c1bd8fcb"
    assert gate.COMPATIBILITY_COMMAND == ("-I", "-B", "scripts/qualify_schema26_compatibility.py")
    assert gate.ROLLING == ("tests/test_schema25_app_rolling_v3.py",)
    assert gate.PROTOCOL == ("tests/test_schema25_app_protocol_v3.py",)


def test_isolated_source_verifier_resolves_historical_namespace_import(tmp_path):
    backend = tmp_path / "backend"
    infra = tmp_path / "infra/gcp"
    (backend / "scripts").mkdir(parents=True)
    infra.mkdir(parents=True)
    (infra / "document_candidate.py").write_text('FINGERPRINT = "owned-fingerprint"\n')
    (backend / "scripts/schema25_app_release_contract_v3.py").write_text(
        'PRIOR_RELEASE = "owned-prior"\nCOMPATIBILITY_COMMAND = "owned-compatibility"\n'
        'PROTOCOL_COMMAND = "owned-protocol"\n'
        'def source_fingerprint(root, prior):\n'
        '    from infra.gcp.document_candidate import FINGERPRINT\n'
        '    return FINGERPRINT\n'
    )
    (infra / "unified_credit_schema25_release.py").write_text(
        'def prior_app_files(root, source):\n    return {source: b"owned"}\n'
        'def prior_app_digest(app):\n    return "owned-app"\n'
    )
    raw = gate._call([sys.executable, "-I", "-B", "-c", gate._VERIFY, str(tmp_path.resolve()), "owned-source"],
                     cwd=backend, env={key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ})
    assert json.loads(raw) == {"fingerprint": "owned-fingerprint", "app": "owned-app", "files": 1,
                               "compatibility": "owned-compatibility", "protocol": "owned-protocol"}


@pytest.mark.parametrize("damage", [None, "app", "fingerprint", "files", "command", "pin"])
def test_original_source_verification_requires_exact_app_protocol_and_public_pins(monkeypatch, tmp_path, damage):
    file = tmp_path / "fixture.py"
    file.write_bytes(b"owned public synthetic fixture")
    monkeypatch.setattr(gate, "PINS", {"fixture.py": hashlib.sha256(file.read_bytes()).hexdigest()})
    observed = {"fingerprint": gate.FINGERPRINT, "app": gate.APP_SHA256, "files": 290,
        "compatibility": ".venv/bin/python -I -B scripts/qualify_schema26_compatibility.py",
        "protocol": ".venv/bin/python -I -B -m pytest --noconftest -q " + " ".join(gate.PROTOCOL)}
    if damage in {"app", "fingerprint"}:
        observed[damage] = "0" * 64
    elif damage == "files":
        observed["files"] = 294
    elif damage == "command":
        observed["compatibility"] = "replacement test"
    elif damage == "pin":
        file.write_text("changed")
    monkeypatch.setattr(gate, "_call", lambda *_a, **_k: json.dumps(observed).encode())
    if damage:
        with pytest.raises(gate.RetainedV3Denied):
            gate._verify(tmp_path, {})
    else:
        gate._verify(tmp_path, {})


@pytest.mark.parametrize("state", ["passed", "skipped", "failure", "error", "missing", "foreign"])
def test_retained_original_pytest_needs_nonempty_passing_each_selected_suite(monkeypatch, tmp_path, state):
    report = tmp_path / "junit.xml"
    body = ('<testcase classname="test_owned" name="test_original"/>' if state == "passed" else
        '<testcase classname="test_owned" name="test_original"><' + state + '/></testcase>')
    if state == "missing":
        body = ""
    elif state == "foreign":
        body = '<testcase classname="different_suite" name="test_original"/>'
    calls = []

    def call(command, **kwargs):
        calls.append(command)
        report.write_text("<testsuite>" + body + "</testsuite>")
        return b""

    monkeypatch.setattr(gate, "_call", call)
    if state == "passed":
        gate._pytest(tmp_path, {}, ("tests/test_owned.py",), report)
        assert not report.exists()
    else:
        with pytest.raises(gate.RetainedV3Denied):
            gate._pytest(tmp_path, {}, ("tests/test_owned.py",), report)
        assert report.exists()
    assert len(calls) == 1 and calls[0][1:7] == ["-I", "-B", "-m", "pytest", "--noconftest", "-q"]


@pytest.mark.parametrize("phase,outcome", [("compatibility", "success"), ("protocol", "success"),
    ("compatibility", "failed"), ("compatibility", "unknown"), ("protocol", "unknown")])
def test_worktree_original_once_and_unknown_descendants_never_removed(monkeypatch, tmp_path, phase, outcome):
    root = tmp_path / "synthetic-root"
    root.mkdir()
    pins = {}
    for name in ("backend/uv.lock", "backend/pyproject.toml"):
        path = root / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("owned synthetic lock")
        pins[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    plugin = root / "backend/scripts" / gate.LEARNING_FIXTURE
    plugin.parent.mkdir()
    plugin.write_text("# owned test-only clock overlay\n")
    tracked = Path("backend/tests/test_unified_synchronous.py")
    original = b"# frozen synthetic fixture bytes\n"
    overlay_name = "_" + gate.LEARNING_FIXTURE
    baseline_env = {"RUNNER_TEMP": str(tmp_path)}
    monkeypatch.setattr(gate, "PINS", pins)
    monkeypatch.setattr(gate, "_environment", lambda *_a, **_k: baseline_env)
    monkeypatch.setattr(gate, "_identity", lambda *_a, **_k: (b"ownedlink", 1, 2, 3))
    git_calls, child_calls, verifications = [], [], []

    def check_tracked(checkout):
        source = checkout / tracked
        assert source.read_bytes() == original
        return source.with_name(overlay_name)

    def verify(checkout, env):
        assert env == baseline_env and "PYTEST_PLUGINS" not in env
        assert not check_tracked(checkout).exists()
        verifications.append(checkout)

    def git(where, env, *args):
        git_calls.append(args)
        if args == ("rev-parse", "--show-toplevel"):
            return str(root).encode()
        if args[:2] == ("worktree", "add"):
            source = Path(args[3]) / tracked
            source.parent.mkdir(parents=True)
            source.write_bytes(original)
        elif args[:2] == ("worktree", "remove"):
            checkout = Path(args[-1])
            assert not check_tracked(checkout).exists()
            source = checkout / tracked
            source.unlink()
            source.parent.rmdir()
            source.parent.parent.rmdir()
            checkout.rmdir()
        return b""

    def child(command, *, cwd, env, **kwargs):
        child_calls.append(command)
        overlay = check_tracked(cwd.parent)
        if tuple(command[1:]) == gate.COMPATIBILITY_COMMAND:
            assert overlay.read_bytes() == plugin.read_bytes()
            assert env == baseline_env | {"PYTEST_PLUGINS": "tests." + overlay.stem}
            assert kwargs["timeout"] == 900
        else:
            assert not overlay.exists() and env == baseline_env
        if outcome == "failed":
            raise gate.RetainedV3Denied("owned_child_failed")
        if outcome == "unknown":
            raise gate._UnknownOutcome("owned_child_unknown")
        return b""

    def original_pytest(checkout, env, suites, report):
        return child(["pytest", *suites], cwd=checkout / "backend", env=env)

    monkeypatch.setattr(gate, "_git", git)
    monkeypatch.setattr(gate, "_verify", verify)
    monkeypatch.setattr(gate, "_call", child)
    monkeypatch.setattr(gate, "_pytest", original_pytest)
    if outcome == "success":
        gate.run(root, phase)
    else:
        with pytest.raises(gate.RetainedV3Denied):
            gate.run(root, phase)
    assert child_calls
    assert child_calls[0] == ([sys.executable, *gate.COMPATIBILITY_COMMAND] if phase == "compatibility"
                               else ["pytest", *gate.PROTOCOL])
    assert len(child_calls) == (2 if phase == "compatibility" and outcome == "success" else 1)
    assert sum(args[:2] == ("worktree", "add") for args in git_calls) == 1
    assert sum(args[:2] == ("worktree", "remove") for args in git_calls) == int(outcome == "success")
    assert len(verifications) == (2 if outcome == "success" else 1)
    retained = list(tmp_path.glob("hirewiz-schema26-retained-v3-*/source"))
    assert bool(retained) is (outcome != "success")
    if retained:
        overlay = check_tracked(retained[0])
        assert overlay.exists() is (phase == "compatibility")
        if overlay.exists():
            assert overlay.read_bytes() == plugin.read_bytes()
    assert baseline_env == {"RUNNER_TEMP": str(tmp_path)}


@pytest.mark.parametrize("select_exact", [True, False])
def test_original_isolated_pytest_loads_only_the_exact_learning_variant(tmp_path, select_exact):
    checkout = tmp_path / "retained"
    backend = checkout / "backend"
    (backend / "tests").mkdir(parents=True)
    (backend / "scripts").mkdir()
    (backend / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["..", "."]\n')
    frozen_fixture = '''\
from datetime import UTC, datetime
from backend.tests.test_unified_credits import grant
NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
'''
    (backend / "tests/test_unified_synchronous.py").write_text(frozen_fixture)
    credits_fixture = '''\
from datetime import UTC, datetime, timedelta
NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
def grant(db, *, quantity=40, expiry=NOW+timedelta(days=30), now=NOW):
    return quantity, expiry, now
'''
    (backend / "tests/test_unified_credits.py").write_text(credits_fixture)
    (backend / "tests/test_unified_execution.py").write_text('''\
from backend.tests.test_unified_credits import NOW
def financial_dates():
    return NOW
''')
    suite = backend / "tests/test_unified_synchronous_postgres.py"
    suite.write_text('''\
from datetime import UTC, datetime, timedelta
import pytest
from backend.tests import test_unified_synchronous as synchronous_checks
from backend.tests import test_unified_execution as inputs

FIXED = synchronous_checks.NOW

@pytest.fixture
def owned_input_before_setup():
    quantity, expiry, now = synchronous_checks.grant(None)
    assert quantity == 40 and now == inputs.financial_dates()
    assert abs(now-datetime.now(UTC)) < timedelta(minutes=1)
    assert expiry == now+timedelta(days=30)

@pytest.mark.parametrize("operation", ["enhanced_qa", "learning_strategy", "bullet_rewrite"])
def test_unsupported_personal_claim_refunds_once_with_supplier_cost_retained_on_postgres(monkeypatch, operation, owned_input_before_setup):
    if operation == "learning_strategy":
        assert abs(synchronous_checks.NOW - datetime.now(UTC)) < timedelta(minutes=1)
    else:
        assert synchronous_checks.NOW == FIXED

def test_other_case_has_frozen_clock(monkeypatch):
    assert synchronous_checks.NOW == FIXED
    assert inputs.NOW == FIXED and synchronous_checks.grant(None)[2] == FIXED
''')
    selection = ["-k", "frozen_clock"] if not select_exact else []
    (backend / gate.COMPATIBILITY_COMMAND[-1]).write_text(
        "import subprocess, sys\n"
        "raise SystemExit(subprocess.call([sys.executable, '-I', '-B', '-m', 'pytest', '--noconftest', '-q',"
        " 'tests/test_unified_synchronous_postgres.py'] + " + repr(selection) + "))\n")
    root = Path(gate.__file__).resolve().parents[2]
    env = {"PATH": os.defpath, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}

    if select_exact:
        gate._compatibility(root, checkout, env)
    else:
        with pytest.raises(gate.RetainedV3Denied, match="^retained_v3_child_failed$"):
            gate._compatibility(root, checkout, env)

    assert suite.with_name("_" + gate.LEARNING_FIXTURE).exists() is not select_exact
    assert suite.with_name("test_unified_synchronous.py").read_text() == frozen_fixture
    assert suite.with_name("test_unified_credits.py").read_text() == credits_fixture
    assert "PYTEST_PLUGINS" not in env


@pytest.fixture
def compatibility_failure(monkeypatch, tmp_path):
    source = tmp_path / gate.COMPATIBILITY_COMMAND[-1]
    source.parent.mkdir()
    source.write_bytes(b'PUBLIC_CASES = {"test_owned": ("test_public_failure", "test_public_setup")}\n')
    monkeypatch.setitem(gate.PINS, "backend/" + gate.COMPATIBILITY_COMMAND[-1],
                        hashlib.sha256(source.read_bytes()).hexdigest())
    report = {
        "schema_version": "schema26-ci-failure-v1", "qualified": False,
        "report_state": "observed_unqualified", "child_returncode": 1,
        "counts": {"passed": 2, "failure": 1, "error": 1, "skipped": 0},
        "cases": [
            {"case_id": "test_owned::test_public_failure", "status": "failure", "ordinal": 2,
             "source_line_hints": [12, 27], "diagnostic": "SYNTHETIC_PRIVATE_VALUE"},
            {"case_id": "test_owned::test_public_setup", "status": "error", "ordinal": 4,
             "source_line_hints": [], "diagnostic_type": "SYNTHETIC_PRIVATE_TYPE"},
        ],
        "private": "SYNTHETIC_PRIVATE_TOP_LEVEL",
    }
    return source, report


def _failed_child(monkeypatch, args, raw, *, code=1):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert command == args and kwargs["capture_output"] is True and kwargs["check"] is False
        return subprocess.CompletedProcess(command, code, raw, b"SYNTHETIC_PRIVATE_STDERR")

    monkeypatch.setattr(gate.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("pytest_code", [1, 2])
def test_original_failed_compatibility_exports_only_validated_observation(
        monkeypatch, tmp_path, capsys, compatibility_failure, pytest_code):
    _, report = compatibility_failure
    report["child_returncode"] = pytest_code
    args = [sys.executable, *gate.COMPATIBILITY_COMMAND]
    calls = _failed_child(monkeypatch, args, json.dumps(report).encode() + b"\n")

    with pytest.raises(gate.RetainedV3Denied, match="^retained_v3_child_failed$"):
        gate._call(args, cwd=tmp_path, env={}, timeout=900)

    captured = capsys.readouterr()
    assert calls == [args] and captured.err == ""
    assert "SYNTHETIC_PRIVATE" not in captured.out and str(tmp_path) not in captured.out
    observation = json.loads(captured.out)["retained_v3_compatibility_report"]
    assert observation["qualified"] is False and observation["counts"] == report["counts"]
    fields = ("case_id", "status", "ordinal", "source_line_hints")
    assert [{key: case[key] for key in fields} for case in observation["cases"]] == [
        {key: case[key] for key in fields} for case in report["cases"]]


@pytest.mark.parametrize("fault", ["bad-json", "extra-stream", "oversized", "foreign-case", "changed-source",
                                   "bad-count", "bad-hint", "success-claim"])
def test_failed_compatibility_withholds_unverified_report(monkeypatch, tmp_path, capsys, compatibility_failure, fault):
    source, report = compatibility_failure
    if fault == "foreign-case":
        report["cases"][0]["case_id"] = "test_owned::test_foreign"
    if fault == "changed-source":
        source.write_bytes(source.read_bytes() + b"# different retained bytes\n")
    if fault == "bad-count":
        report["counts"]["failure"] = 2
    if fault == "bad-hint":
        report["cases"][0]["source_line_hints"] = [True]
    if fault == "success-claim":
        report["qualified"] = True
    raw = json.dumps(report).encode()
    raw += {"bad-json": b"{", "extra-stream": b"\nSYNTHETIC_PRIVATE_EXTRA",
            "oversized": b" " * 65538}.get(fault, b"")
    args = [sys.executable, *gate.COMPATIBILITY_COMMAND]
    calls = _failed_child(monkeypatch, args, raw)

    with pytest.raises(gate.RetainedV3Denied, match="^retained_v3_child_failed$"):
        gate._call(args, cwd=tmp_path, env={})

    assert calls == [args] and capsys.readouterr() == ("", "")


def test_other_failed_command_cannot_publish_compatibility_report(monkeypatch, tmp_path, capsys,
                                                            compatibility_failure):
    _, report = compatibility_failure
    args = [sys.executable, "-I", "-B", "-m", "pytest", *gate.ROLLING]
    calls = _failed_child(monkeypatch, args, json.dumps(report).encode())

    with pytest.raises(gate.RetainedV3Denied, match="^retained_v3_child_failed$"):
        gate._call(args, cwd=tmp_path, env={})

    assert calls == [args] and capsys.readouterr() == ("", "")


def test_original_child_timeout_is_fixed_unknown_without_retry_or_raw_body(monkeypatch, tmp_path, capsys):
    calls = []
    args = [sys.executable, *gate.COMPATIBILITY_COMMAND]

    def unknown(command, **kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, 30, output=b"private synthetic content", stderr=b"also private")

    monkeypatch.setattr(gate.subprocess, "run", unknown)
    with pytest.raises(gate._UnknownOutcome, match="^retained_v3_child_outcome_unknown$") as error:
        gate._call(args, cwd=tmp_path, env={})
    assert calls == [args] and "private" not in str(error.value)
    assert capsys.readouterr() == ("", "")
