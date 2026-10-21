"""Failure diagnostics with synthetic native results; no Docker or app imports."""

import hashlib
import importlib.util
import json
import sys
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


@pytest.mark.parametrize("output,reason", [
    ((PRIVATE + " 429 Too Many Requests").encode(), "http_429_observed"),
    ((PRIVATE + " failed to resolve source metadata").encode(), "base_image_resolution"),
    (PRIVATE.encode(), "unclassified"),
])
def test_failed_build_keeps_exit_and_bounded_fingerprint_without_raw_output(
    checker, monkeypatch, output, reason
):
    calls = []
    def fail(args, **options):
        calls.append((args, options))
        return 17, output
    monkeypatch.setattr(checker, "command", fail)
    token = checker._FAILURE.set({"stage": "build", "completed_cases": 0})
    try:
        with pytest.raises(checker.DocumentImageError):
            checker.build(["synthetic-docker"], "linux/amd64", checker.source_hashes())
        assert len(calls) == 1 and calls[0][1] == {"timeout": 600, "merge_stderr": True}
        report = checker._failure_report()
        assert report["exit_code"] == 17 and report["build_error"] == reason
        assert report["build_output_sha256"] == hashlib.sha256(output).hexdigest()
        assert report["build_output_bytes"] == len(output)
        assert 1 <= len(report["build_error_detail"]) <= 1024
        assert PRIVATE not in json.dumps(report) and PRIVATE not in repr(checker._FAILURE.get())
        checker._progress("image_metadata")
        assert "build_error" not in checker._failure_report()
    finally:
        checker._FAILURE.reset(token)


def test_build_stderr_capture_is_explicit_and_uses_existing_combined_output_bound(checker, capsys):
    args = [sys.executable, "-I", "-c", "import os;os.write(2,b'private-build-error');raise SystemExit(7)"]
    assert checker.command(args) == (7, b"")
    assert checker.command(args, merge_stderr=True) == (7, b"private-build-error")
    with pytest.raises(checker.DocumentImageError):
        checker.command(args, merge_stderr=True, limit=4)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_final_docker_error_detail_keeps_cause_and_exit_but_removes_sensitive_values(checker):
    output = (
        b"WARNING: antivirus CDN returned HTTP 429 Too Many Requests\n"
        b"ERROR: fetching https://user:URL_VALUE@public.ecr.aws/v2/blobs?token=QUERY_VALUE\n"
        b'ERROR: failed to solve: process "/bin/sh -c echo QUOTED_VALUE" did not complete successfully: exit code: 1\n'
    )
    detail = checker._safe_build_detail(output)
    assert "429 Too Many Requests" in detail and "exit code: 1" in detail
    assert all(value not in detail for value in ("URL_VALUE", "QUERY_VALUE", "QUOTED_VALUE"))
    assert len(detail) <= 1024 and all(32 <= ord(char) <= 126 for char in detail)
    private = checker._safe_build_detail(b"ERROR: /Users/owner/local-file " + b"gsk_" + b"a" * 60)
    assert "/Users/" not in private and "gsk_" not in private
    assert checker._safe_build_detail(b"ERROR: authorization Bearer TOKEN_VALUE") == "Docker authentication detail redacted."
