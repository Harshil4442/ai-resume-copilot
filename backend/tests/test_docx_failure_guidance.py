"""Pure response-to-guidance controls; no app, worker, database or network calls."""
from __future__ import annotations

import ast
import asyncio
import json
import ssl
import sys
import types
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_MARKER = "/private/synthetic-resume.docx candidate@example.test"


def _definitions(path: str, names: set[str], namespace: dict) -> None:
    tree = ast.parse((ROOT / path).read_bytes())
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names]
    assert {node.name for node in nodes} == names
    exec(compile(ast.Module(body=[node for node in nodes], type_ignores=[]), path, "exec"), namespace)


def _constants(path: str, names: set[str], namespace: dict) -> None:
    tree = ast.parse((ROOT / path).read_bytes())
    nodes = [node for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign)) and any(
        isinstance(target, ast.Name) and target.id in names
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target]))]
    found = {target.id for node in nodes for target in (node.targets if isinstance(node, ast.Assign) else [node.target]) if isinstance(target, ast.Name)}
    assert names <= found
    exec(compile(ast.Module(body=[node for node in nodes], type_ignores=[]), path, "exec"), namespace)


@pytest.fixture
def controls(monkeypatch):
    # Execute the actual selected source definitions. Dependency type identities
    # are their real classes; unrelated module initializers are never imported.
    prefix = "_docx_failure_guidance_control"
    for suffix in ("", ".services", ".domains", ".domains.analysis"):
        package = types.ModuleType(prefix + suffix)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, package.__name__, package)
    resume = types.ModuleType(prefix + ".services.resume_layout")
    _definitions("backend/app/services/resume_layout.py", {"ResumeLayoutError", "ResumeProcessingUnavailable"}, resume.__dict__)
    llm = types.ModuleType(prefix + ".services.llm_client")
    _definitions("backend/app/services/llm_client.py", {"LLMProviderError", "TailoringOutputError"}, llm.__dict__)
    costs = types.ModuleType(prefix + ".services.model_cost_policy")
    _definitions("backend/app/services/model_cost_policy.py", {"ModelCostUnavailable"}, costs.__dict__)
    ingestion = types.ModuleType(prefix + ".services.document_ingestion")
    ingestion.__dict__["_tls_context"] = lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    for module in (resume, llm, ingestion, costs):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    artifact = types.ModuleType(prefix + ".services.docx_artifact")
    artifact.__package__ = prefix + ".services"
    artifact.__dict__.update(asyncio=asyncio, json=json, httpx=httpx, ResumeLayoutError=resume.ResumeLayoutError)
    _constants("backend/app/services/docx_layout_validation.py", {"LAYOUT_REASONS"}, artifact.__dict__)
    _constants("backend/app/services/docx_artifact.py", {"MAX_RESPONSE", "_REFUSED", "_LAYOUT_REFUSED", "_LINK_REFUSED", "_FONT_REFUSED"}, artifact.__dict__)
    _definitions("backend/app/services/docx_artifact.py", {"_worker_http", "_check_binding", "_pairs", "DocxFontUnsupported"}, artifact.__dict__)
    failures = types.ModuleType(prefix + ".domains.analysis.tailoring_failures")
    failures.__package__ = prefix + ".domains.analysis"
    # This module contains only finite declarations; its provider imports are
    # inside persist_tailoring_failure and resolve to the isolated real types.
    source = ROOT / "backend/app/domains/analysis/tailoring_failures.py"
    exec(compile(source.read_bytes(), str(source), "exec"), failures.__dict__)
    return artifact, failures, llm


def _response(reason="hyperlink_changed"):
    expected = {"version": 1, "operation": "docx_layout", "sha256": "a" * 64,
                "size_bytes": 42, "source_format": "docx", "candidate_sha256": "b" * 64,
                "candidate_size_bytes": 43, "edits_sha256": "c" * 64}
    image = "synthetic-document@sha256:" + "d" * 64
    config = types.SimpleNamespace(audience="https://synthetic.run.app", image=image, policy="e" * 64)
    edits = [{"unit_id": PRIVATE_MARKER}]
    body = {**expected, "worker_image": image, "policy_sha256": config.policy,
            "error": "docx_layout_refused", "reason": reason, "unit_id": PRIVATE_MARKER}
    return config, expected, edits, body


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, body):
        self.body = body

    async def __aiter__(self):
        for offset in range(0, len(self.body), 37):
            yield self.body[offset:offset + 37]


def _receive(artifact, config, expected, edits, body, monkeypatch):
    raw = json.dumps(body).encode()
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(422, headers={"Content-Type": "application/json"}, stream=_Chunks(raw))

    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return httpx.AsyncClient(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setitem(artifact.__dict__, "httpx", types.SimpleNamespace(AsyncClient=client, Timeout=httpx.Timeout))
    try:
        return asyncio.run(artifact._worker_http(config, b"synthetic bounded request", "synthetic-token", expected, edits))
    finally:
        assert len(calls) == 1


@pytest.mark.parametrize(("native_reason", "public_reason", "action"), [
    ("hyperlink_changed", "source_links_protected", "keep_link_text"),
    ("line_wrap_changed", "source_layout_changed", "keep_original_layout"),
    ("outside_region_pixels", "source_layout_changed", "keep_original_layout"),
    ("document_safety", "validation_failed", "use_original_or_custom"),
    ("unsupported_source_font", "font_unsupported", "use_supported_font"),
])
def test_only_bound_native_refusals_reach_fixed_public_guidance(controls, monkeypatch, native_reason, public_reason, action):
    artifact, failures, llm = controls
    config, expected, edits, body = _response(native_reason)
    with pytest.raises(artifact.ResumeLayoutError) as refused:
        _receive(artifact, config, expected, edits, body, monkeypatch)
    wrapper = llm.TailoringOutputError("Could not apply useful changes while preserving the resume format")
    wrapper.__cause__ = refused.value
    marker = failures.persist_tailoring_failure(wrapper)
    guidance = failures.public_tailoring_guidance("resume_tailor", "failed", marker)
    assert guidance == {"reason_code": public_reason, "next_action": action}
    assert PRIVATE_MARKER not in str(refused.value) + marker + json.dumps(guidance)
    assert refused.value.unit_id == PRIVATE_MARKER  # Internal repair attribution is retained.
    if native_reason == "unsupported_source_font":
        assert refused.value.content_repair_allowed is False


@pytest.mark.parametrize("change", [
    {"reason": "hyperlink_changed " + PRIVATE_MARKER},
    {"sha256": "f" * 64},
    {"candidate_sha256": "f" * 64},
    {"worker_image": "unjoined-image"},
    {"policy_sha256": "f" * 64},
    {"unit_id": "unjoined-private-unit"},
    {"provider_stderr": PRIVATE_MARKER},
])
def test_unbound_unknown_or_extra_native_bodies_do_not_acquire_specific_guidance(controls, monkeypatch, change):
    artifact, failures, llm = controls
    config, expected, edits, body = _response()
    body.update(change)
    with pytest.raises(ValueError) as unavailable:
        _receive(artifact, config, expected, edits, body, monkeypatch)
    assert not isinstance(unavailable.value, artifact.ResumeLayoutError)
    wrapper = llm.TailoringOutputError(PRIVATE_MARKER, reason_code="source_links_protected")
    wrapper.__cause__ = unavailable.value
    marker = failures.persist_tailoring_failure(wrapper)
    guidance = failures.public_tailoring_guidance("resume_tailor", "failed", marker)
    assert guidance == {"reason_code": "validation_failed", "next_action": "use_original_or_custom"}
    assert PRIVATE_MARKER not in marker + json.dumps(guidance)


def test_existing_generic_failures_keep_other_operations_and_statuses_unaffected(controls):
    artifact, failures, _ = controls
    marker = failures.persist_tailoring_failure(artifact.ResumeLayoutError(artifact._REFUSED))
    assert failures.public_tailoring_guidance("resume_tailor", "failed", marker) == {
        "reason_code": "validation_failed", "next_action": "use_original_or_custom"}
    assert failures.public_tailoring_guidance("interview_questions", "failed", marker) is None
    assert failures.public_tailoring_guidance("resume_tailor", "succeeded", marker) is None
