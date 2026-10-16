"""Failure diagnostics with synthetic native results; no Docker or app imports."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "sha256:" + "a" * 64
PRIVATE = "source-base64-exception-stderr-auth-identity-/private/request.json"


@pytest.fixture
def checker(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "document_image_diagnostics_checker", ROOT / "backend/scripts/document_test_image.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.os, "umask", lambda mode: 0)
    return module


@pytest.mark.parametrize("failure", ["malformed_result", "execution_refusal"])
def test_failure_identifies_active_case_without_exporting_native_content(
    checker, monkeypatch, capsys, failure
):
    hashes = dict.fromkeys(checker.SOURCES, "b" * 64)
    monkeypatch.setattr(checker, "docker_command", lambda: ["synthetic-only"])
    monkeypatch.setattr(checker, "source_hashes", lambda: hashes)
    monkeypatch.setattr(checker, "image_metadata", lambda *args: None)
    monkeypatch.setattr(checker, "corpus", lambda: [
        ("clean_pdf", "pdf", b"synthetic PDF", "clean"),
        ("active_pdf", "pdf", b"synthetic active PDF", "refused"),
    ])
    calls = 0

    def native_result(docker, image, platform, arguments, mounts):
        nonlocal calls
        calls += 1
        if calls == 1:
            return 0, json.dumps({path: hashes[path] for path in checker.RUNTIME}).encode()
        if calls == 2:
            envelope = json.loads(mounts[0][0].read_bytes())
            return 0, json.dumps({
                "sha256": envelope["sha256"], "size_bytes": len(b"synthetic PDF"),
                "source_format": "pdf", "scan": {"engine": "ClamAV"},
            }).encode()
        assert calls == 3
        if failure == "execution_refusal":
            raise RuntimeError(PRIVATE * 10000)
        return 0, (PRIVATE * 10000).encode()

    monkeypatch.setattr(checker, "container", native_result)
    monkeypatch.setattr(checker.sys, "argv", [
        "document_test_image.py", "--platform", "linux/amd64", "--image-id", IMAGE,
    ])
    with pytest.raises(SystemExit) as stopped:
        checker.main()
    assert stopped.value.code == 1 and calls == 3
    captured = capsys.readouterr()
    assert captured.err == "" and len(captured.out.encode()) <= 512
    assert PRIVATE not in captured.out and IMAGE not in captured.out
    assert json.loads(captured.out) == {
        "error": "document_engine_check_refused",
        "stage": "case_execution" if failure == "execution_refusal" else "case_validation",
        "case": "active_pdf", "exit_code": None if failure == "execution_refusal" else 0,
        "completed_cases": 1,
    }
    assert checker._FAILURE.get() is None


def test_failure_projection_rejects_nonfinite_progress(checker, monkeypatch, capsys):
    def invalid_progress(*args, **kwargs):
        checker._progress(PRIVATE, case=PRIVATE, exit_code=True, completed_cases=10**100)
        raise RuntimeError(PRIVATE)

    monkeypatch.setattr(checker, "check", invalid_progress)
    monkeypatch.setattr(checker.sys, "argv", [
        "document_test_image.py", "--platform", "linux/amd64",
    ])
    with pytest.raises(SystemExit) as stopped:
        checker.main()
    assert stopped.value.code == 1
    captured = capsys.readouterr()
    assert captured.err == "" and len(captured.out.encode()) <= 512
    assert json.loads(captured.out) == {
        "error": "document_engine_check_refused", "stage": "setup", "case": None,
        "exit_code": None, "completed_cases": 0,
    }
