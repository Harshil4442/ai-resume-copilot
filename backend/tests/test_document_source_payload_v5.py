"""Positive teardown is bound to the future payload, not inferred from delete."""
from __future__ import annotations

import ast
import json
from types import SimpleNamespace

import pytest
from infra.gcp import document_source_payload as original
from infra.gcp import document_source_payload_v5 as payload
from test_document_synchronous_do import service as service

IMAGE = (
    "us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/"
    "hirewiz-document-inspector@sha256:" + "a" * 64
)


@pytest.fixture
def native_program():
    source = SimpleNamespace(policy="b" * 64, runtime={})
    old = original.rendered(IMAGE, source)
    current = payload.rendered(IMAGE, source)
    assert ast.unparse(ast.parse(old)).count("diagnostic.launcher_code == 1") == 3
    assert "diagnostic.processor_code" not in old
    assert current.count("diagnostic.processor_code == 1") == 3
    namespace = {"__name__": "synthetic_scanner_payload"}
    exec(compile(current, "<scanner-proof-v5>", "exec"), namespace)
    assert namespace["SOURCE_CORPUS_EVENT"] == payload.CORPUS_EVENT
    assert namespace["SOURCE_INVENTORY_EVENT"] == payload.INVENTORY_EVENT
    assert namespace["SOURCE_EXTRA_CASES"] == original.EXTRA_CASES
    return namespace


def test_acknowledged_auxiliary_does_not_send_redundant_delete(native_program, service, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert argv[:2] == [service.SANDBOX, "do"]
        assert kwargs == {"timeout": 55, "limit": 16384, "allow_outcome": False}
        return 0, json.dumps({"synthetic": True}).encode()

    monkeypatch.setattr(service, "_run", run)
    diagnostic = service._RequestDiagnostic()
    assert native_program["auxiliary"](service, "synthetic inventory", diagnostic) == {"synthetic": True}
    assert len(calls) == 1
    assert native_program["cleanup_success"](diagnostic) is True
    assert native_program["diagnostic_projection"](diagnostic) == {
        "launcher": "returned",
        "cleanup": "provider_teardown",
        "processor": "not_validated",
        "processor_code": None,
        "validation": "not_started",
        "failure_phase": None,
        "launcher_code": 0,
        "cleanup_code": None,
        "launcher_elapsed_ms": diagnostic.launcher_elapsed_ms,
        "cleanup_elapsed_ms": None,
    }


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_unknown_owned_auxiliary_cannot_qualify(native_program, service, monkeypatch, cleanup_fails):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "do":
            raise service._LauncherRefused(True, code=1, outcome="unexpected_exit")
        assert argv == [service.SANDBOX, "delete", calls[0][2].removeprefix("--sandbox-name="), "--force"]
        assert kwargs == {"timeout": 5, "limit": 65536, "cleanup": True}
        if cleanup_fails:
            raise service._LauncherRefused(True, code=1, outcome="unexpected_exit")
        return 0, b""

    monkeypatch.setattr(service, "_run", run)
    diagnostic = service._RequestDiagnostic()
    with pytest.raises(service._LauncherRefused):
        native_program["auxiliary"](service, "synthetic inventory", diagnostic)
    assert len(calls) == 2
    assert native_program["cleanup_success"](diagnostic) is False
