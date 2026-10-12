"""One-shot launcher contract controls; fake SDK replies are not native proof."""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
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
def test_named_one_shot_exact_command_private_stdin_and_unique_cleanup(
    service: Any, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    content = b"original synthetic source bytes"
    original = payload(content, kind)
    result = result_for(content)
    result["source_format"] = kind
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(args: list[str], **options: Any) -> tuple[int, bytes]:
        calls.append((args, options))
        if args[1] == "delete":
            return 0, b""
        assert args[0:2] == [service.SANDBOX, "do"]
        assert re.fullmatch(r"--sandbox-name=document-[a-f0-9]{32}", args[2])
        assert args[3:] == [
            "--write", "--", "/usr/local/bin/python3", "-I", "/opt/document/processor.py", "--stdin"
        ]
        assert options == {"timeout": 55, "limit": service.MAX_OUTPUT,
                           "allow_outcome": True, "input_bytes": original}
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
            [service.SANDBOX, "delete", name, "--force"], {"timeout": 5, "limit": 65536, "cleanup": True}
        )


@pytest.mark.parametrize("failure", ["missing", "timeout", "refused"])
@pytest.mark.parametrize("delete_fails", [False, True])
def test_unknown_one_shot_failure_deletes_once_and_never_retries_or_leaks(
    service: Any, monkeypatch: pytest.MonkeyPatch, failure: str, delete_fails: bool
) -> None:
    calls: list[str] = []

    def run(args: list[str], **options: Any) -> tuple[int, bytes]:
        calls.append(args[1])
        if args[1] == "delete":
            if delete_fails:
                raise service.InspectionDenied()
            return 0, b""
        if failure == "missing":
            raise FileNotFoundError("private synthetic SDK detail")
        if failure == "timeout":
            raise subprocess.TimeoutExpired("private synthetic SDK detail", 55)
        raise service.InspectionDenied()

    monkeypatch.setattr(service, "_run", run)
    with pytest.raises(service.InspectionDenied, match="^document_inspection_refused$"):
        service.inspect_in_sandbox(payload(b"original synthetic source bytes"))
    assert calls == ["do", "delete"]


@pytest.mark.parametrize("authorized", [False, True])
def test_constructor_refusal_deletes_only_after_possible_gate_release(
    service: Any, monkeypatch: pytest.MonkeyPatch, authorized: bool
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(args: list[str], **options: Any) -> tuple[int, bytes]:
        calls.append((args, options))
        if args[1] == "do":
            raise service._LauncherRefused(authorized)
        assert args[1] == "delete" and args[-1] == "--force"
        assert options == {"timeout": 5, "limit": 65536, "cleanup": True}
        return 0, b""

    monkeypatch.setattr(service, "_run", run)
    with pytest.raises(service.InspectionDenied, match="^document_inspection_refused$"):
        service.inspect_in_sandbox(payload(b"original synthetic source bytes"))
    assert [args[1] for args, _ in calls] == (["do", "delete"] if authorized else ["do"])


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
def test_clean_one_shot_result_must_bind_exact_stdin_bytes(
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
    service: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    code: int, status: int, delete_fails: bool
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
    captured = capsys.readouterr()
    assert captured.out == ""
    if code == 0 and not delete_fails:
        assert captured.err == ""
    else:
        event = _diagnostic_event(captured.err)
        assert event["phase"] == ("cleanup" if delete_fails else "processor")
        assert event["launcher"] == "returned" and event["launcher_code"] == code
        assert event["processor"] == {0: "clean", 1: "refused", 2: "unavailable"}[code]
        assert event["cleanup"] == ("failed" if delete_fails else "returned")
        assert event["cleanup_code"] == (None if delete_fails else 0)
        assert event["validation"] == "complete"
        assert hashlib.sha256(content).hexdigest() not in captured.err
        assert "registry.invalid" not in captured.err and "original synthetic" not in captured.err


def _diagnostic_event(output: str) -> dict[str, Any]:
    assert len(output.encode()) <= 1024 and len(output.splitlines()) == 1
    event = json.loads(output)
    assert set(event) == {
        "event", "version", "diagnostic_id", "phase", "validation", "processor",
        "launcher", "launcher_code", "launcher_elapsed_ms", "cleanup", "cleanup_code",
        "cleanup_elapsed_ms", "elapsed_ms",
    }
    assert event["event"] == "document_inspection_failure" and event["version"] == 1
    assert re.fullmatch(r"[a-f0-9]{32}", event["diagnostic_id"])
    for field in ("launcher_code", "cleanup_code"):
        assert event[field] is None or type(event[field]) is int and -255 <= event[field] <= 255
    for field in ("launcher_elapsed_ms", "cleanup_elapsed_ms", "elapsed_ms"):
        assert event[field] is None or type(event[field]) is int and 0 <= event[field] <= 120000
    return event


def _request_handler(service: Any, content: bytes) -> Any:
    handler = service.Handler.__new__(service.Handler)
    handler.path = "/inspect"
    handler.headers = http.client.HTTPMessage()
    request = payload(content)
    for key, value in {
        "Content-Type": "application/json", "Content-Length": str(len(request)),
        "X-HireWiz-Document-Policy": "b" * 64,
    }.items():
        handler.headers[key] = value
    handler.rfile = io.BytesIO(request)
    handler._finish_request_read = lambda **_: None
    return handler


@pytest.mark.parametrize("failure,phase,validation", [
    ("decode", "validation", "decode"), ("binding", "validation", "binding"),
    ("outcome", "validation", "outcome"), ("unknown_launcher", "launcher", "not_started"),
    ("deadline", "launcher", "not_started"),
])
def test_http_failure_diagnostic_never_copies_document_output_or_exception_details(
    service: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    failure: str, phase: str, validation: str,
) -> None:
    content = b"SECRET_DOCUMENT_TEXT email@example.invalid /private/source"
    secret = "SECRET_OUTPUT_TOKEN /private/sandbox stderr-marker"
    outcome = private_outcome(content, 1)
    if failure == "binding":
        outcome["sha256"] = "c" * 64
    if failure == "outcome":
        outcome["error"] = secret
    calls: list[str] = []

    def run(args: list[str], **options: Any) -> tuple[int, bytes]:
        calls.append(args[1])
        if args[1] == "delete":
            return 0, b""
        if failure == "unknown_launcher":
            raise service._LauncherRefused(True)
        if failure == "deadline":
            raise subprocess.TimeoutExpired(secret, 55, output=content, stderr=secret.encode())
        return 1, secret.encode() if failure == "decode" else json.dumps(outcome).encode()

    monkeypatch.setattr(service, "_run", run)
    handler = _request_handler(service, content)
    replies: list[tuple[int, dict[str, Any]]] = []
    handler._reply = lambda status, body: replies.append((status, body))
    handler.do_POST()
    assert replies == [(503, {"error": "document_inspection_refused"})]
    assert calls == ["do", "delete"] and service._REQUEST_DIAGNOSTIC.get() is None
    captured = capsys.readouterr()
    assert captured.out == ""
    event = _diagnostic_event(captured.err)
    assert event["phase"] == phase and event["validation"] == validation
    assert event["processor"] == "not_validated" and event["cleanup"] == "returned"
    assert event["launcher"] == ("deadline" if failure == "deadline" else "failed" if failure == "unknown_launcher" else "returned")
    assert event["launcher_code"] == (None if phase == "launcher" else 1)
    for forbidden in (secret, content.decode(), hashlib.sha256(content).hexdigest(), "email@", "/private/", "stderr-marker"):
        assert forbidden not in captured.err


@pytest.mark.parametrize("code", [0, 1, 2])
def test_native_inspection_without_http_context_keeps_stdout_and_stderr_unchanged(
    service: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], code: int,
) -> None:
    content = b"original synthetic source bytes"
    outcome = result_for(content) if code == 0 else private_outcome(content, code)
    monkeypatch.setattr(service, "_run", lambda args, **_: (code, json.dumps(outcome).encode()) if args[1] == "do" else (0, b""))
    assert service.inspect_in_sandbox(payload(content))["sha256"] == hashlib.sha256(content).hexdigest()
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_reply_failure_restores_enclosing_request_context_and_emits_one_safe_event(
    service: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    content = b"original synthetic source bytes"
    handler = _request_handler(service, content)
    monkeypatch.setattr(service, "inspect_in_sandbox", lambda _: result_for(content))
    replies: list[tuple[int, dict[str, Any]]] = []

    def reply(status: int, body: dict[str, Any]) -> None:
        if status == 200:
            raise OSError("SECRET_RESPONSE_TOKEN /private/path")
        replies.append((status, body))

    handler._reply = reply
    enclosing = service._RequestDiagnostic()
    token = service._REQUEST_DIAGNOSTIC.set(enclosing)
    try:
        handler.do_POST()
        assert service._REQUEST_DIAGNOSTIC.get() is enclosing
    finally:
        service._REQUEST_DIAGNOSTIC.reset(token)
    assert replies == [(503, {"error": "document_inspection_refused"})]
    captured = capsys.readouterr()
    assert captured.out == "" and "SECRET_RESPONSE_TOKEN" not in captured.err
    event = _diagnostic_event(captured.err)
    assert event["phase"] == "response" and event["diagnostic_id"] != enclosing.identifier


@pytest.mark.parametrize("field,value", [
    ("failure_phase", "SECRET_ENUM_TOKEN"), ("processor", "SECRET_ENUM_TOKEN"),
    ("launcher_code", True), ("cleanup_elapsed_ms", "SECRET_ENUM_TOKEN"),
])
def test_diagnostic_emitter_refuses_unapproved_fields(
    service: Any, capsys: pytest.CaptureFixture[str], field: str, value: Any,
) -> None:
    diagnostic = service._RequestDiagnostic()
    diagnostic.fail("launcher")
    setattr(diagnostic, field, value)
    diagnostic.emit()
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_unexpected_launcher_exit_retains_only_safe_numeric_metadata(
    service: Any, capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(service._LauncherRefused) as caught:
        service._run([sys.executable, "-I", "-c", "print('SECRET_STDOUT');raise SystemExit(3)"], timeout=2, limit=100)
    assert caught.value.code == 3 and caught.value.outcome == "unexpected_exit"
    assert str(caught.value) == "document_inspection_refused" and caught.value.__cause__ is None
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_supervisor_still_uses_private_minimum_environment_and_owned_process_group(
    service: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = service.subprocess.Popen
    observed: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setenv("HTTP_PROXY", "http://synthetic.invalid")
    monkeypatch.setenv("SSLKEYLOGFILE", "/synthetic-keylog")

    def spawn(args: list[str], **options: Any) -> Any:
        observed.append((args, options))
        return original(args, **options)

    monkeypatch.setattr(service.subprocess, "Popen", spawn)
    assert service._run([sys.executable, "-I", "-c", "print('fixed')"], timeout=2, limit=100) == (0, b"fixed\n")
    assert len(observed) == 1
    args, options = observed[0]
    assert args == [sys.executable, "-I", "-c", service._BOOTSTRAP,
                    str(options["pass_fds"][0]), sys.executable, "-I", "-c", "print('fixed')"]
    assert len(options["pass_fds"]) == 1
    assert options["env"] == {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "TZ": "UTC"}
    assert options["stdin"] == subprocess.DEVNULL
    assert options["stderr"] == subprocess.DEVNULL
    assert options["start_new_session"] is True


def _wait_for_constructor_cleanup(service: Any) -> None:
    deadline = time.monotonic() + 3
    while service._CREATION_PENDING.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not service._CREATION_PENDING.is_set()


def _assert_child_reaped_and_pipes_closed(child: Any) -> None:
    assert child.returncode is not None
    assert all(stream is None or stream.closed for stream in (child.stdin, child.stdout))


def test_actual_constructor_create_then_delay_never_releases_gate_and_reaps_late_child(
    service: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original = service.subprocess.Popen
    children: list[Any] = []
    marker = tmp_path / "synthetic-target-started"
    program = "from pathlib import Path;Path(" + repr(str(marker)) + ").write_text('started')"

    def delayed_constructor(args: list[str], **options: Any) -> Any:
        child = original(args, **options)
        children.append(child)
        time.sleep(0.4)  # Actual fault: process exists before Popen ownership returns.
        return child

    monkeypatch.setattr(service.subprocess, "Popen", delayed_constructor)
    started = time.monotonic()
    with pytest.raises(service._LauncherRefused) as caught:
        service._run([sys.executable, "-I", "-c", program], timeout=0.05,
                     limit=100, input_bytes=b"fixed synthetic payload")
    assert not caught.value.authorized
    assert time.monotonic() - started < 0.3
    assert len(children) == 1 and service._CREATION_PENDING.is_set()
    with pytest.raises(service._LauncherRefused):
        service._run([sys.executable, "-I", "-c", "print('must not start')"], timeout=0.05, limit=100)
    assert len(children) == 1 and not marker.exists()
    _wait_for_constructor_cleanup(service)
    assert not service._CREATION_FAILED.is_set() and not marker.exists()
    _assert_child_reaped_and_pipes_closed(children[0])


def test_actual_gate_allocate_then_delay_closes_both_descriptors_without_spawn(
    service: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_pipe = service.os.pipe
    descriptors: list[int] = []
    spawns: list[list[str]] = []

    def delayed_pipe() -> tuple[int, int]:
        pair = original_pipe()
        descriptors.extend(pair)
        time.sleep(0.4)  # Actual fault: descriptors exist before pipe returns ownership.
        return pair

    def forbidden_spawn(args: list[str], **options: Any) -> None:
        spawns.append(args)
        pytest.fail("spawn after cancellation")

    monkeypatch.setattr(service.os, "pipe", delayed_pipe)
    monkeypatch.setattr(service.subprocess, "Popen", forbidden_spawn)
    started = time.monotonic()
    with pytest.raises(service._LauncherRefused) as caught:
        service._run([sys.executable, "-I", "-c", "print('never started')"], timeout=0.05, limit=100)
    assert not caught.value.authorized
    assert time.monotonic() - started < 0.3 and service._CREATION_PENDING.is_set()
    _wait_for_constructor_cleanup(service)
    assert len(descriptors) == 2 and spawns == []
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    assert not service._CREATION_FAILED.is_set()


def test_thread_start_failure_allocates_no_gate_and_quarantines_admission(
    service: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    descriptors: list[int] = []
    original_pipe = service.os.pipe

    def pipe() -> tuple[int, int]:
        pair = original_pipe()
        descriptors.extend(pair)
        return pair

    monkeypatch.setattr(service.os, "pipe", pipe)
    monkeypatch.setattr(service.threading.Thread, "start",
                        lambda _: (_ for _ in ()).throw(RuntimeError("cannot start new thread")))
    with pytest.raises(service._LauncherRefused) as caught:
        service._run([sys.executable, "-I", "-c", "print('never started')"], timeout=1, limit=100)
    assert not caught.value.authorized and descriptors == []
    assert not service._CREATION_PENDING.is_set() and service._CREATION_FAILED.is_set()
    with pytest.raises(service._LauncherRefused):
        service._run([sys.executable, "-I", "-c", "print('never started')"], timeout=1, limit=100)


def test_actual_seven_mib_stdin_and_stdout_backpressure_preserves_count_and_hash(
    service: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = service.subprocess.Popen
    children: list[Any] = []
    observed: list[tuple[list[str], dict[str, Any]]] = []
    data = b"x" * service.MAX_REQUEST
    # Output exceeds pipe capacity before reading input: sequential IO would deadlock.
    program = ("import hashlib,json,sys;sys.stdout.buffer.write(b'p'*131072);"
               "sys.stdout.buffer.flush();d=sys.stdin.buffer.read();"
               "print(json.dumps({'count':len(d),'sha256':hashlib.sha256(d).hexdigest()}))")

    def spawn(args: list[str], **options: Any) -> Any:
        observed.append((args, options))
        child = original(args, **options)
        children.append(child)
        return child

    monkeypatch.setattr(service.subprocess, "Popen", spawn)
    code, raw = service._run([sys.executable, "-I", "-c", program], timeout=3,
                             limit=200000, input_bytes=data)
    assert code == 0 and raw[:131072] == b"p" * 131072
    assert json.loads(raw[131072:]) == {"count": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    assert len(children) == 1
    args, options = observed[0]
    assert options["stdin"] == subprocess.PIPE and options["stderr"] == subprocess.DEVNULL
    assert options["env"] == {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "TZ": "UTC"}
    assert len(options["pass_fds"]) == 1 and options["start_new_session"] is True
    assert data[:1024].decode() not in "\n".join(args)
    _assert_child_reaped_and_pipes_closed(children[0])


def test_canonical_policy_binds_exact_source_and_declares_work_and_reap_bounds() -> None:
    path = ROOT / "infra/document-processing/policy.json"
    raw = path.read_bytes()
    value = json.loads(raw)
    assert raw == (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    assert value["required_native_deployment_controls"]["launcher_phase_seconds"] == {"do": 55, "delete": 5}
    controls = value["required_native_deployment_controls"]
    assert controls["launcher_phase_seconds_exclude_reap"] is True
    assert controls["owned_process_reap_timeout_seconds"] == 2
    assert controls["request_only_bounded_stdin"] is True
    assert controls["request_stdin_max_bytes"] == 7 * 1024 * 1024
    assert controls["launcher_stdout_max_bytes"] == 1024 * 1024
    assert controls["constructor_signal_free_owned_worker"] is True
    assert controls["pending_or_unknown_constructor_quarantines_admission"] is True
    assert "request_only_internal_regular_file_tar" not in controls
    assert value["required_native_deployment_controls"]["absolute_header_body_read_seconds"] == 10
    assert value["transport"]["http_deadline_seconds"] == 75
    assert value["deployment_proven"] is False
    assert value["scope"]["host_parser_fallback"] is False
    assert value["required_native_deployment_controls"]["forced_owned_sandbox_delete_before_release"] is True
    assert len(value["source_sha256"]) == 7
    for name, expected in value["source_sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
