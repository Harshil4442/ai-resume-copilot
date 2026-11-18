"""Owned positive teardown receipts and inner processor outcomes."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from test_document_processor import load, payload, private_outcome, result_for
from test_document_synchronous_do import _request_handler
from test_document_synchronous_do import service as service


@pytest.fixture
def transport(service: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv(service.SANDBOX_TRANSPORT_ENV, service.SANDBOX_TRANSPORT)
    return service


@pytest.mark.parametrize("processor_code,status", [(0, 200), (1, 422), (2, 503)])
def test_reaped_synchronous_do_has_positive_teardown_for_bound_processor_outcomes(
    transport: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    processor_code: int, status: int,
) -> None:
    content = b"synthetic document"
    outcome = result_for(content) if processor_code == 0 else private_outcome(content, processor_code)
    directories = []

    def run(args: list[str], **options: Any) -> tuple[int, bytes]:
        assert not directories
        assert args[:2] == [transport.SANDBOX, "do"]
        directory = Path("/tmp") / args[2].removeprefix("--sandbox-name=")
        assert directory.is_dir() and not directory.is_symlink() and not list(directory.iterdir())
        directories.append(directory)
        assert args[3:] == ["--mount", "type=bind,source=" + str(directory) + ",destination=/tmp",
            "--env", transport.CLAMD_BINDING_ENV + "=" + transport.CLAMD_BINDING_MODE + ":" + "c" * 64,
            "--", "/usr/local/bin/python3", "-I", "/opt/document/processor.py", "--sandbox-stdin"]
        assert 0 < options.pop("timeout") <= 55
        assert options == {"limit": transport.MAX_OUTPUT, "allow_outcome": False, "input_bytes": payload(content)}
        return 0, json.dumps({"processor_code": processor_code, "result": outcome}).encode()

    monkeypatch.setattr(transport, "_run", run)
    handler = _request_handler(transport, content)
    responses = []
    handler._reply = lambda code, result: responses.append((code, result))
    handler.do_POST()
    assert responses == [(status, {
        **outcome, "worker_image": "registry.invalid/document@sha256:" + "a" * 64, "policy_sha256": "b" * 64,
    })]
    assert len(directories) == 1 and not directories[0].exists()
    stderr = capsys.readouterr().err
    if processor_code == 0:
        assert not stderr
    else:
        evidence = json.loads(stderr)
        assert evidence["launcher"] == "returned" and evidence["launcher_code"] == 0
        assert evidence["cleanup"] == "provider_teardown" and evidence["cleanup_code"] is None
        assert evidence["cleanup_elapsed_ms"] is None
        assert evidence["processor"] == ("refused" if processor_code == 1 else "unavailable")
        assert "synthetic document" not in stderr


@pytest.mark.parametrize("fault", ["unknown_exit", "deadline", "reap_failed", "never_authorized"])
@pytest.mark.parametrize("delete_fails", [False, True])
def test_uncertain_native_do_keeps_exact_delete_and_quarantine(
    transport: Any, monkeypatch: pytest.MonkeyPatch, fault: str, delete_fails: bool,
) -> None:
    calls: list[list[str]] = []
    acknowledged = fault != "never_authorized"

    def run(args: list[str], **options: Any) -> tuple[int, bytes]:
        calls.append(args)
        if args[1] == "do":
            status = {"unknown_exit": 1, "reap_failed": 0}.get(fault)
            outcome = "unexpected_exit" if fault == "unknown_exit" else fault
            raise transport._LauncherRefused(acknowledged, code=status, outcome=outcome)
        assert options == {"timeout": 5, "limit": 65536, "cleanup": True}
        if delete_fails:
            raise transport._LauncherRefused(True, code=1, outcome="unexpected_exit")
        return 0, b""

    monkeypatch.setattr(transport, "_run", run)
    diagnostic = transport._RequestDiagnostic()
    token = transport._REQUEST_DIAGNOSTIC.set(diagnostic)
    try:
        with pytest.raises(transport.InspectionDenied):
            transport.inspect_in_sandbox(payload(b"synthetic document"))
    finally:
        transport._REQUEST_DIAGNOSTIC.reset(token)
    assert diagnostic.processor_code is None and diagnostic.cleanup != "provider_teardown"
    name = calls[0][2].removeprefix("--sandbox-name=")
    assert calls[1:] == ([[transport.SANDBOX, "delete", name, "--force"]] if acknowledged else [])
    assert (Path("/tmp") / name).exists() is (acknowledged and delete_fails)
    assert transport._CREATION_FAILED.is_set() is (acknowledged and delete_fails)


@pytest.mark.parametrize("fault", ["invalid_code", "duplicate_key", "malformed", "bound_to_other_input", "wrong_outcome"])
def test_positive_teardown_does_not_authorize_unbound_or_malformed_envelope(
    transport: Any, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    content = b"synthetic document"
    outcome = result_for(content)
    value = {"processor_code": 0, "result": outcome}
    if fault == "invalid_code":
        value["processor_code"] = True
    elif fault == "bound_to_other_input":
        outcome["sha256"] = "0" * 64
    elif fault == "wrong_outcome":
        value["processor_code"] = 1
    raw = {
        "duplicate_key": b'{"processor_code":0,"processor_code":1,"result":{}}',
        "malformed": b'{"processor_code":0,"result":',
    }.get(fault, json.dumps(value).encode())
    calls: list[list[str]] = []

    def run(args: list[str], **_options: Any) -> tuple[int, bytes]:
        calls.append(args)
        return 0, raw

    monkeypatch.setattr(transport, "_run", run)
    diagnostic = transport._RequestDiagnostic()
    token = transport._REQUEST_DIAGNOSTIC.set(diagnostic)
    try:
        with pytest.raises(transport.InspectionDenied):
            transport.inspect_in_sandbox(payload(content))
    finally:
        transport._REQUEST_DIAGNOSTIC.reset(token)
    assert diagnostic.launcher_code == 0 and diagnostic.cleanup == "provider_teardown"
    assert diagnostic.processor_code is None and diagnostic.validation != "complete"
    assert len(calls) == 1
    assert not (Path("/tmp") / calls[0][2].removeprefix("--sandbox-name=")).exists()


def test_no_unacknowledged_teardown_receipt_or_unknown_transport_admission(
    transport: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostic = transport._RequestDiagnostic()
    with pytest.raises(transport.InspectionDenied):
        diagnostic.confirm_provider_teardown()
    diagnostic.end_run(1)
    with pytest.raises(transport.InspectionDenied):
        diagnostic.confirm_provider_teardown()
    assert diagnostic.cleanup == "not_required"

    monkeypatch.setenv(transport.SANDBOX_TRANSPORT_ENV, "unreviewed-mode")

    def unexpected_run(*_args: Any, **_options: Any) -> tuple[int, bytes]:
        pytest.fail("unknown mode reached the native launcher")

    monkeypatch.setattr(transport, "_run", unexpected_run)
    with pytest.raises(transport.InspectionDenied):
        transport.configuration()
    with pytest.raises(transport.InspectionDenied):
        transport.inspect_in_sandbox(payload(b"synthetic document"))


@pytest.mark.parametrize("transport_mode", [False, True])
@pytest.mark.parametrize("processor_code,oversized", [(0, False), (1, False), (2, False), (2, True)])
@pytest.mark.parametrize("layout", [False, True])
def test_owned_processor_emits_bound_outcome_with_separate_exit_modes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    transport_mode: bool, processor_code: int, oversized: bool, layout: bool,
) -> None:
    module = load("processor")
    content = b"synthetic document"
    binding = {"sha256": "a" * 64, "size_bytes": len(content), "source_format": "docx" if layout else "pdf"}
    output = {"version": 1, **binding}

    def process(_payload: bytes) -> dict:
        if oversized:
            return {**output, "synthetic_excess": "x" * module.MAX_OUTPUT}
        if processor_code == 1:
            raise module.InspectionDenied()
        if processor_code == 2:
            raise module.InspectionUnavailable()
        return output

    flag = ("--sandbox-" if transport_mode else "--") + ("docx-layout-stdin" if layout else "stdin")
    monkeypatch.setattr(sys, "argv", ["processor.py", flag])
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(content)))
    monkeypatch.setattr(module.sys, "path", module.sys.path.copy())
    monkeypatch.setattr(module.logging, "disable", lambda *_: None)
    monkeypatch.setattr(module.warnings, "simplefilter", lambda *_: None)
    monkeypatch.setattr(module.os, "umask", lambda *_: None)
    monkeypatch.setattr(module.resource, "setrlimit", lambda *_: None)
    monkeypatch.setattr(module, "decode_request", lambda _: ("pdf", content, binding["sha256"]))
    monkeypatch.setattr(module, "decode_docx_request", lambda _: (content, {"binding": binding}))
    monkeypatch.setattr(module, "inspect", process)
    monkeypatch.setattr(module, "validate_docx_edit", process)
    if transport_mode or processor_code == 0:
        module.main()
    else:
        with pytest.raises(SystemExit) as exit_status:
            module.main()
        assert exit_status.value.code == processor_code
    if processor_code == 1:
        output["error"] = module.DOCX_ERROR if layout else module.ERROR
        if layout:
            output.update(reason="document_safety", unit_id=None)
    elif processor_code == 2:
        output["error"] = module.DOCX_UNAVAILABLE if layout else module.UNAVAILABLE
    stdout = capsys.readouterr().out
    assert len(stdout.encode()) <= (32768 if layout else module.MAX_OUTPUT)
    assert json.loads(stdout) == (
        {"processor_code": processor_code, "result": output} if transport_mode else output
    )
