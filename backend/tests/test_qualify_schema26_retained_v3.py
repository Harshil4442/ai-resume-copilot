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
    ("compatibility", "failed"), ("protocol", "unknown")])
def test_worktree_original_once_and_unknown_descendants_never_removed(monkeypatch, tmp_path, phase, outcome):
    root = tmp_path / "synthetic-root"
    root.mkdir()
    pins = {}
    for name in ("backend/uv.lock", "backend/pyproject.toml"):
        path = root / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("owned synthetic lock")
        pins[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(gate, "PINS", pins)
    monkeypatch.setattr(gate, "_environment", lambda *_a, **_k: {"RUNNER_TEMP": str(tmp_path)})
    monkeypatch.setattr(gate, "_identity", lambda *_a, **_k: (b"ownedlink", 1, 2, 3))
    monkeypatch.setattr(gate, "_verify", lambda *_: None)
    git_calls, child_calls = [], []

    def git(where, env, *args):
        git_calls.append(args)
        if args == ("rev-parse", "--show-toplevel"):
            return str(root).encode()
        if args[:2] == ("worktree", "add"):
            Path(args[3]).mkdir()
        elif args[:2] == ("worktree", "remove"):
            Path(args[-1]).rmdir()
        return b""

    def child(command, **kwargs):
        child_calls.append(command)
        if outcome == "failed":
            raise gate.RetainedV3Denied("owned_child_failed")
        if outcome == "unknown":
            raise gate._UnknownOutcome("owned_child_unknown")
        return b""

    def original_pytest(checkout, env, suites, report):
        return child(["pytest", *suites])

    monkeypatch.setattr(gate, "_git", git)
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
    assert bool(list(tmp_path.glob("hirewiz-schema26-retained-v3-*/source"))) is (outcome != "success")


def test_original_child_timeout_is_fixed_unknown_without_retry_or_raw_body(monkeypatch, tmp_path):
    calls = []

    def unknown(command, **kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, 30, output=b"private synthetic content")

    monkeypatch.setattr(gate.subprocess, "run", unknown)
    with pytest.raises(gate._UnknownOutcome, match="^retained_v3_child_outcome_unknown$") as error:
        gate._call(["owned"], cwd=tmp_path, env={})
    assert len(calls) == 1 and "private" not in str(error.value)
