"""Fixed source child boundaries; no execution of provider or native adapters."""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from infra.gcp import schema27_app_source as source


@pytest.fixture
def child(tmp_path, monkeypatch):
    pin = source.QualifiedSource("a" * 40, "b" * 64, 316, "c" * 64, "d" * 64)
    monkeypatch.setattr(source.successor, "_git", lambda _root, *args:
        (pin.release + "\n").encode() if args[0] == "rev-parse" else b"")
    calls = []
    reply = SimpleNamespace(returncode=0, stdout=json.dumps(asdict(pin)).encode(), stderr=b"private-synthetic-diagnostic")

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return reply

    monkeypatch.setattr(source.subprocess, "run", run)
    return SimpleNamespace(root=tmp_path.resolve(), pin=pin, calls=calls, reply=reply)


def test_each_checkout_has_an_isolated_secret_free_bounded_interpreter(child, monkeypatch):
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/private/synthetic-only")
    monkeypatch.setenv("DATABASE_URL", "synthetic-private-value")
    assert source.qualify(child.root, child.pin.release) == child.pin
    args, options = child.calls[0]
    assert args[0] == [sys.executable, "-I", "-B", "-c", source._CHILD, str(child.root)]
    assert options["timeout"] == 90 and options["stdin"] is subprocess.DEVNULL
    assert options["env"] == {"PATH": source.os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}
    assert options["cwd"] == child.root and options["capture_output"] is True
    assert "current_application_identity" in source._CHILD
    assert "authorizes_retail" in source._CHILD


@pytest.mark.parametrize("change", ["failed", "oversize", "foreign_release", "foreign_field", "bool_count", "invalid_json"])
def test_bad_child_evidence_stops_without_reflecting_stderr(child, change):
    value = asdict(child.pin)
    if change == "failed":
        child.reply.returncode = 1
    elif change == "oversize":
        child.reply.stdout = b"x" * 2049
    elif change == "foreign_release":
        value["release"] = "f" * 40
    elif change == "foreign_field":
        value["processing_authorized"] = True
    elif change == "bool_count":
        value["app_files"] = True
    else:
        child.reply.stdout = b"not-json"
    if change in {"foreign_release", "foreign_field", "bool_count"}:
        child.reply.stdout = json.dumps(value).encode()
    with pytest.raises(source.scope.rolling.RollingDenied) as denied:
        source.qualify(child.root, child.pin.release)
    assert "private" not in str(denied.value) and "diagnostic" not in str(denied.value)


def test_dirty_checkout_stops_before_source_child(child, monkeypatch):
    monkeypatch.setattr(source.successor, "_git", lambda _root, *args:
        (child.pin.release + "\n").encode() if args[0] == "rev-parse" else b" M backend/app/main.py\n")
    with pytest.raises(source.scope.rolling.RollingDenied, match="source_checkout"):
        source.qualify(child.root, child.pin.release)
    assert not child.calls
