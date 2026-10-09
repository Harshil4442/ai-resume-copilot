"""Bounded local orchestration failures; actual product assertions live in Chromium."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest
from backend.scripts import run_cold_browser_journey as journey_runner
from backend.scripts.run_cold_browser_journey import (
    _owned_status,
    _run_bounded,
    _spawn_owned,
    _stop,
    _wait,
    run_journey,
)


def test_timeout_stops_the_owned_parent_and_child_process_group(tmp_path):
    marker = tmp_path / "owned-pids.json"
    code = f'''
import json, os, signal, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
def stop(*_):
    child.wait(timeout=5)
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
open({str(marker)!r}, "w").write(json.dumps([os.getpid(), child.pid]))
time.sleep(30)
'''
    with pytest.raises(AssertionError, match="bounded deadline"):
        _run_bounded([sys.executable, "-c", code], cwd=tmp_path, env={}, timeout=1)
    assert marker.is_file(), "Fixture process must have started before timeout"
    for pid in json.loads(marker.read_text()):
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_exited_server_cannot_be_counted_as_ready():
    process = subprocess.Popen([sys.executable, "-c", "raise SystemExit(7)"])
    process.wait(timeout=5)
    with pytest.raises(AssertionError, match="exited before readiness"):
        _wait("http://127.0.0.1:1/api/health", process, seconds=1)


def test_unowned_evidence_is_not_reused_or_deleted(tmp_path, monkeypatch):
    monkeypatch.delenv("HIREWIZ_TEST_POSTGRES_URL", raising=False)
    source = tmp_path / "credential-free-source"
    source.mkdir()
    monkeypatch.setattr(journey_runner, "ROOT", source)
    evidence = tmp_path / "unowned-evidence"
    evidence.mkdir()
    existing = evidence / "summary.json"
    existing.write_text('{"status":"passed","unrelated":true}')
    with pytest.raises(RuntimeError, match="previously owned"):
        run_journey(evidence)
    assert json.loads(existing.read_text()) == {"status": "passed", "unrelated": True}


def _wait_exited_without_reaping(process):
    deadline = time.monotonic() + 5
    while _owned_status(process) is None:
        assert time.monotonic() < deadline, "Synthetic leader did not exit"
        time.sleep(0.02)


def test_exited_leader_with_inherited_stdout_cannot_leave_an_owned_child(tmp_path):
    marker = tmp_path / "exited-leader.json"
    code = f"""
import json, os, pathlib, subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
pathlib.Path({str(marker)!r}).write_text(json.dumps([os.getpid(), child.pid]))
raise SystemExit(0)
"""
    with pytest.raises(AssertionError, match="bounded deadline"):
        _run_bounded([sys.executable, "-c", code], cwd=tmp_path, env={}, timeout=0.7)
    leader, child = json.loads(marker.read_text())
    for pid in (leader, child):
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_successful_command_also_stops_child_that_closed_output(tmp_path):
    marker = tmp_path / "success-child.json"
    code = f"""
import json, os, pathlib, subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
pathlib.Path({str(marker)!r}).write_text(json.dumps([os.getpid(), child.pid]))
print("actual output")
raise SystemExit(7)
"""
    result = _run_bounded([sys.executable, "-c", code], cwd=tmp_path, env={}, timeout=5)
    assert result.returncode == 7
    assert result.stdout == "actual output\n"
    for pid in json.loads(marker.read_text()):
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_already_vanished_owned_group_is_not_signalled_again(tmp_path, monkeypatch):
    process = _spawn_owned([sys.executable, "-c", "raise SystemExit(0)"],
        cwd=tmp_path, env={}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _wait_exited_without_reaping(process)
    proof = _stop(process)
    with pytest.raises(ProcessLookupError):
        os.killpg(process.pid, 0)
    calls = []
    monkeypatch.setattr(os, "killpg", lambda *args: calls.append(args))
    assert _stop(process) == proof
    assert proof["verified_stopped"] is True
    assert calls == []


def test_reaped_leader_fails_closed_without_signalling_reusable_group(tmp_path, monkeypatch):
    process = _spawn_owned([sys.executable, "-c", "raise SystemExit(0)"],
        cwd=tmp_path, env={}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    process.wait(timeout=5)  # Deliberately violate the private ownership contract.
    calls = []
    monkeypatch.setattr(os, "killpg", lambda *args: calls.append(args))
    with pytest.raises(AssertionError, match="reaped before group cleanup"):
        _stop(process)
    assert calls == []


def test_termination_escalation_is_bounded_and_checks_group(tmp_path):
    marker = tmp_path / "term-ignored.json"
    code = f"""
import os, pathlib, signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))
time.sleep(30)
"""
    started = time.monotonic()
    with pytest.raises(AssertionError, match="bounded deadline"):
        _run_bounded([sys.executable, "-c", code], cwd=tmp_path, env={}, timeout=0.7)
    assert time.monotonic() - started < 10
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)
