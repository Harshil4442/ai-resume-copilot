"""One-shot launcher contract controls; fake SDK replies are not native proof."""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import subprocess
import sys
import tarfile
import threading
from pathlib import Path
from typing import Any

import pytest
from test_document_processor import load, payload, private_outcome, result_for

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> Any:
    module = load("service")
    monkeypatch.setenv("DOCUMENT_WORKER_IMAGE_DIGEST", "registry.invalid/document@sha256:" + "a" * 64)
    monkeypatch.setenv("DOCUMENT_WORKER_POLICY_SHA256", "b" * 64)
    monkeypatch.setattr(module.os.path, "isfile", lambda _: True)
    monkeypatch.setattr(module.os, "access", lambda *_: True)
    return module


@pytest.mark.parametrize("kind", ["pdf", "docx"])
def test_named_one_shot_exact_command_private_import_and_unique_cleanup(
    service: Any, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    content = b"original synthetic source bytes"
    original = payload(content, kind)
    result = result_for(content)
    result["source_format"] = kind
    calls: list[tuple[list[str], dict[str, Any]]] = []
    archives: list[Path] = []

    def run(args: list[str], **options: Any) -> tuple[int, bytes]:
        calls.append((args, options))
        if args[1] == "delete":
            return 0, b""
        assert args[0:2] == [service.SANDBOX, "do"]
        assert re.fullmatch(r"--sandbox-name=document-[a-f0-9]{32}", args[2])
        assert args[3] == "--write" and args[4].startswith("--import-tar=")
        assert args[5:] == [
            "--", "/usr/local/bin/python3", "-I", "/opt/document/processor.py", "/tmp/request.json"
        ]
        assert options == {"timeout": 55, "limit": service.MAX_OUTPUT, "allow_outcome": True}
        archive_path = Path(args[4].split("=", 1)[1])
        archives.append(archive_path)
        assert archive_path.stat().st_mode & 0o777 == 0o600
        assert archive_path.parent.stat().st_mode & 0o777 == 0o700
        with tarfile.open(archive_path) as archive:
            entries = archive.getmembers()
            assert len(entries) == 1
            entry = entries[0]
            assert entry.isreg() and entry.name == "tmp/request.json"
            assert (entry.mode, entry.uid, entry.gid, entry.size) == (0o444, 0, 0, len(original))
            member = archive.extractfile(entry)
            assert member is not None and member.read() == original
        return 0, json.dumps(result).encode()

    monkeypatch.setattr(service, "_run", run)
    for _ in range(2):
        admitted = service.inspect_in_sandbox(original)
        assert admitted["sha256"] == hashlib.sha256(content).hexdigest()
        assert admitted["size_bytes"] == len(content) and admitted["source_format"] == kind
    assert [args[1] for args, _ in calls] == ["do", "delete", "do", "delete"]
    names = [calls[index][0][2].split("=", 1)[1] for index in (0, 2)]
    assert names[0] != names[1]
    for index, name in zip((1, 3), names, strict=True):
        assert calls[index] == (
            [service.SANDBOX, "delete", name, "--force"], {"timeout": 5, "limit": 65536}
        )
    assert all(not path.exists() and not path.parent.exists() for path in archives)


@pytest.mark.parametrize("failure", ["missing", "timeout", "refused"])
@pytest.mark.parametrize("delete_fails", [False, True])
def test_unknown_one_shot_failure_deletes_once_and_never_retries_or_leaks(
    service: Any, monkeypatch: pytest.MonkeyPatch, failure: str, delete_fails: bool
) -> None:
    calls: list[str] = []
    archives: list[Path] = []

    def run(args: list[str], **options: Any) -> tuple[int, bytes]:
        calls.append(args[1])
        if args[1] == "delete":
            if delete_fails:
                raise service.InspectionDenied()
            return 0, b""
        archives.append(Path(args[4].split("=", 1)[1]))
        if failure == "missing":
            raise FileNotFoundError("private synthetic SDK detail")
        if failure == "timeout":
            raise subprocess.TimeoutExpired("private synthetic SDK detail", 55)
        raise service.InspectionDenied()

    monkeypatch.setattr(service, "_run", run)
    with pytest.raises(service.InspectionDenied, match="^document_inspection_refused$"):
        service.inspect_in_sandbox(payload(b"original synthetic source bytes"))
    assert calls == ["do", "delete"]
    assert all(not path.exists() and not path.parent.exists() for path in archives)


@pytest.mark.parametrize("code", [0, 1, 2])
def test_already_destroyed_hint_is_never_treated_as_acknowledged_cleanup(
    service: Any, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    content = b"original synthetic source bytes"
    outcome = result_for(content) if code == 0 else private_outcome(content, code)
    calls: list[str] = []

    def run(args: list[str], **options: Any) -> tuple[int, bytes]:
        calls.append(args[1])
        if args[1] == "delete":
            raise service.InspectionDenied()
        return code, json.dumps(outcome).encode()

    monkeypatch.setattr(service, "_run", run)
    with pytest.raises(service.InspectionDenied):
        service.inspect_in_sandbox(payload(content))
    assert calls == ["do", "delete"]


@pytest.mark.parametrize("field,value", [("sha256", "c" * 64), ("size_bytes", 1), ("source_format", "docx")])
def test_clean_one_shot_result_must_bind_exact_imported_bytes(
    service: Any, monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    content = b"original synthetic source bytes"
    outcome = result_for(content)
    outcome[field] = value
    calls: list[str] = []

    def run(args: list[str], **options: Any) -> tuple[int, bytes]:
        calls.append(args[1])
        return (0, json.dumps(outcome).encode()) if args[1] == "do" else (0, b"")

    monkeypatch.setattr(service, "_run", run)
    with pytest.raises(service.InspectionDenied):
        service.inspect_in_sandbox(payload(content))
    assert calls == ["do", "delete"]


@pytest.mark.parametrize("code,status", [(0, 200), (1, 422), (2, 503)])
@pytest.mark.parametrize("delete_fails", [False, True])
def test_http_release_requires_coherent_one_shot_outcome_and_cleanup(
    service: Any, monkeypatch: pytest.MonkeyPatch, code: int, status: int, delete_fails: bool
) -> None:
    content = b"original synthetic source bytes"
    outcome = result_for(content) if code == 0 else private_outcome(content, code)
    calls: list[str] = []

    def run(args: list[str], **options: Any) -> tuple[int, bytes]:
        calls.append(args[1])
        if args[1] == "delete":
            if delete_fails:
                raise service.InspectionDenied()
            return 0, b""
        return code, json.dumps(outcome).encode()

    monkeypatch.setattr(service, "_run", run)
    server = service.InspectionHTTPServer(("127.0.0.1", 0), service.Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(server.server_address[0], server.server_address[1], timeout=3)
    try:
        connection.request("POST", "/inspect", body=payload(content), headers={
            "Content-Type": "application/json", "X-HireWiz-Document-Policy": "b" * 64
        })
        response = connection.getresponse()
        body = json.loads(response.read())
        assert response.status == (503 if delete_fails else status)
        assert response.getheader("Cache-Control") == "no-store"
        if delete_fails:
            assert body == {"error": "document_inspection_refused"}
        else:
            assert body == {
                **outcome, "worker_image": "registry.invalid/document@sha256:" + "a" * 64,
                "policy_sha256": "b" * 64,
            }
        assert calls == ["do", "delete"]
    finally:
        connection.close()
        server.server_close()
        thread.join(timeout=3)
    assert not thread.is_alive()


def test_supervisor_still_uses_private_minimum_environment_and_owned_process_group(
    service: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = service.subprocess.Popen
    observed: list[dict[str, Any]] = []
    monkeypatch.setenv("HTTP_PROXY", "http://synthetic.invalid")
    monkeypatch.setenv("SSLKEYLOGFILE", "/synthetic-keylog")

    def spawn(args: list[str], **options: Any) -> Any:
        observed.append(options)
        return original(args, **options)

    monkeypatch.setattr(service.subprocess, "Popen", spawn)
    assert service._run([sys.executable, "-I", "-c", "print('fixed')"], timeout=2, limit=100) == (0, b"fixed\n")
    assert len(observed) == 1
    assert observed[0]["env"] == {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "TZ": "UTC"}
    assert observed[0]["stdin"] == subprocess.DEVNULL
    assert observed[0]["stderr"] == subprocess.DEVNULL
    assert observed[0]["start_new_session"] is True


def test_canonical_policy_binds_exact_source_and_preserves_total_deadlines() -> None:
    path = ROOT / "infra/document-processing/policy.json"
    raw = path.read_bytes()
    value = json.loads(raw)
    assert raw == (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    assert value["required_native_deployment_controls"]["launcher_phase_seconds"] == {"do": 55, "delete": 5}
    assert sum(value["required_native_deployment_controls"]["launcher_phase_seconds"].values()) == 60
    assert value["required_native_deployment_controls"]["absolute_header_body_read_seconds"] == 10
    assert value["transport"]["http_deadline_seconds"] == 75
    assert value["deployment_proven"] is False
    assert value["scope"]["host_parser_fallback"] is False
    assert value["required_native_deployment_controls"]["forced_owned_sandbox_delete_before_release"] is True
    assert len(value["source_sha256"]) == 7
    for name, expected in value["source_sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
