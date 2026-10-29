"""Synthetic transports/local files only; no genuine native or review evidence."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.domains.analysis import native_input_acquisition as acquisition
from app.domains.analysis import quality_packet as packet
from app.domains.analysis import supported_evaluation_native as native
from app.services import document_ingestion, native_tex_transport
from app.services.resume_layout import ResumeProcessingUnavailable
from tests.test_supported_evaluation import evaluation  # noqa: F401


@pytest.fixture
def capture(evaluation, tmp_path):  # noqa: F811 - imported pytest fixture
    original = evaluation["auth"]
    now = datetime.now(UTC)
    auth = acquisition.Authorization.model_validate({
        "schema_version": acquisition.VERSION, "purpose": "unreviewed_native_input_capture_no_model_no_retail",
        "state": "authorized", "authorized_by": "synthetic-test-only", "experiment_id": "synthetic-service-http-test",
        "journal_path": str(tmp_path / "journal"), "split": "development", "valid_from": now - timedelta(minutes=1),
        "expires_at": now + timedelta(hours=1), "packet_manifest_sha256": acquisition.inputs.MANIFEST_SHA,
        "native_manifest_sha256": original.native_manifest_sha256,
        "funding_evidence": original.funding_evidence.model_dump(), "request_limit": 400,
        "reserved_minor_per_request": 10, "maximum_native_reserved_minor": 4000, "currency": "INR",
        "inspector": {"url": "https://synthetic-worker.run.app/inspect", "image": original.worker_image, "policy": original.worker_policy_sha256},
        "compiler": {"url": "https://synthetic-compiler.run.app/compile", "image": original.compiler_image, "policy": original.compiler_policy_sha256}})
    registry = json.loads((evaluation["authorization_root"] / "capture-registry.json").read_bytes())

    class SyntheticTransport:
        def __init__(self):
            self.calls = []

        def inspect(self, binding, source, kind):
            self.calls.append(("inspect", packet.digest(source), kind))
            for entry in registry["captures"].values():
                if entry["source_sha256"] == packet.digest(source):
                    return (evaluation["authorization_root"] / entry["worker_response"]["path"]).read_bytes()
                compilation = entry.get("compilation", {})
                if compilation.get("pdf", {}).get("sha256") == packet.digest(source):
                    return (evaluation["authorization_root"] / compilation["pdf_inspection_response"]["path"]).read_bytes()
            raise AssertionError("No synthetic response for source")

        def compile(self, binding, source, kind):
            self.calls.append(("compile", packet.digest(source), kind))
            entry = next(e for e in registry["captures"].values() if e["source_sha256"] == packet.digest(source))
            return (evaluation["authorization_root"] / entry["compilation"]["worker_response"]["path"]).read_bytes()

    return {"common": (evaluation["packet_root"], evaluation["native_root"], auth, evaluation["authorization_root"]),
            "auth": auth, "journal": tmp_path / "journal", "transport": SyntheticTransport(), "tmp": tmp_path}


def run(capture, *, initialize=False, maximum_requests=1, transport=None):
    return acquisition.execute(*capture["common"], capture["journal"], initialize=initialize,
        maximum_requests=maximum_requests, transport=transport or capture["transport"])


def complete(capture):
    report = acquisition.plan(*capture["common"])
    remaining = report["maximum_http_requests"]
    first = True
    while remaining:
        count = min(remaining, 10)
        run(capture, initialize=first, maximum_requests=count)
        first = False
        remaining -= count
    return report


def test_plan_is_inert_full_registry_development_only_and_fully_reserved(capture, monkeypatch):
    monkeypatch.setattr(acquisition, "ServiceTransport", lambda: pytest.fail("Plan constructed transport"))
    original = acquisition.inputs.json.loads

    def guarded(raw, *args, **kwargs):
        value = original(raw, *args, **kwargs)
        if type(value) is dict and "case_id" in value:
            assert value["split"] == "development"
        return value

    monkeypatch.setattr(acquisition.inputs.json, "loads", guarded)
    report = acquisition.plan(*capture["common"])
    assert len(report["complete_case_registry"]) == 200
    assert len(report["selected_cases"]) == 40
    assert report["maximum_http_requests"] == sum(len(r["phases"]) for r in report["selected_cases"])
    assert report["reserved_minor"] == report["maximum_http_requests"] * 10
    assert report["provider_requests"] == 0 and report["actual_native_cost_minor"] is None
    assert not capture["journal"].exists()


def test_one_send_resume_and_raw_body_hash_are_exact(capture):
    first = run(capture, initialize=True)
    assert len(capture["transport"].calls) == 1
    row = first["cases"][0]
    assert row["phases"]["source"]["state"] == "captured_unreviewed"
    key = row["case_id"] + ".source"
    raw = (capture["journal"] / (key + ".raw.json")).read_bytes()
    intent = json.loads((capture["journal"] / (key + ".intent.json")).read_bytes())
    retained = json.loads((capture["journal"] / (key + ".retained.json")).read_bytes())
    assert retained["response"]["sha256"] == packet.digest(raw)
    assert intent["replay_authorized"] is False and intent["actual_native_cost_minor"] is None
    run(capture)
    assert len(capture["transport"].calls) == 2
    assert (capture["journal"] / (key + ".raw.json")).read_bytes() == raw


def test_composite_capture_reuses_existing_importer_without_approval(capture):
    report = complete(capture)
    destination = capture["tmp"] / "unreviewed"
    result = acquisition.export_unreviewed(*capture["common"], capture["journal"], destination)
    assert result["captured_cases"] == 40 and not result["quality_qualified"]
    registry = json.loads((destination / "capture-registry.unreviewed.json").read_bytes())
    assert registry["custody"] != "operator_reviewed_native_capture_v1"
    assert "reviewed_by" not in registry
    row = next(r for r in report["selected_cases"] if r["source_format"] in {"tex", "texzip"})
    source = acquisition.legacy._artifact(capture["common"][1], acquisition.legacy.Artifact.model_validate(row["source"])).read_bytes()
    imported = native.decode(destination, registry["captures"][row["case_id"]], source, row["source_format"],
        worker_image=capture["auth"].inspector.image, worker_policy=capture["auth"].inspector.policy,
        compiler_image=capture["auth"].compiler.image, compiler_policy=capture["auth"].compiler.policy)
    assert imported.source.parsed[0] == "" and imported.extraction.parsed[0]
    assert all(q["native_approval"] == "pending" for q in registry["quality"].values())
    assert len(capture["transport"].calls) == report["maximum_http_requests"]
    complete_calls = list(capture["transport"].calls)
    run(capture)
    assert capture["transport"].calls == complete_calls


@pytest.mark.parametrize("failure", [TimeoutError("synthetic-private-token"), KeyboardInterrupt(), ResumeProcessingUnavailable("synthetic-private-token")])
def test_lost_response_remains_unknown_reserved_and_cannot_resend(capture, failure):
    transport = capture["transport"]

    def unknown(*args):
        transport.calls.append(("one-send",))
        raise failure

    transport.inspect = unknown
    with pytest.raises(ValueError, match="unknown_reconcile_only"):
        run(capture, initialize=True)
    status = acquisition.inspect_local(*capture["common"], capture["journal"])
    assert status["cases"][0]["phases"]["source"] == {"state": "unknown_no_replay", "reserved_minor": 10, "actual_native_cost_minor": None}
    with pytest.raises(ValueError, match="unknown_reconcile_only"):
        run(capture)
    assert len(transport.calls) == 1
    assert "synthetic-private-token" not in "".join(p.read_text() for p in capture["journal"].iterdir() if p.suffix == ".json")


def test_compiler_unavailable_is_not_a_verified_refusal(capture):
    report = acquisition.plan(*capture["common"])
    transport = capture["transport"]

    def unknown(*args):
        transport.calls.append(("compile",))
        raise ResumeProcessingUnavailable("synthetic-not-a-verified-422")

    transport.compile = unknown
    with pytest.raises(ValueError, match="unknown_reconcile_only"):
        run(capture, initialize=True, maximum_requests=10)
    status = acquisition.inspect_local(*capture["common"], capture["journal"])
    assert any(p["state"] == "unknown_no_replay" for r in status["cases"] for p in r["phases"].values())
    assert not any(p["state"] == "refused_no_replay" for r in status["cases"] for p in r["phases"].values())
    assert report["reserved_minor"] > 0


def test_verified_refusal_is_retained_and_never_replayed(capture):
    def refused(*args):
        capture["transport"].calls.append(("verified-refusal",))
        raise document_ingestion.DocumentInspectionError(refused=True)

    capture["transport"].inspect = refused
    status = run(capture, initialize=True)
    assert status["cases"][0]["phases"]["source"]["state"] == "refused_no_replay"
    run(capture)
    assert len(capture["transport"].calls) == 2  # distinct case, not a retry


@pytest.mark.parametrize("mutation", ["raw", "result", "source", "intent", "orphan"])
def test_changed_custody_refuses_before_any_further_send(capture, mutation):
    status = run(capture, initialize=True)
    identity = status["cases"][0]["case_id"]
    key = identity + ".source"
    root = capture["journal"]
    if mutation == "raw":
        (root / (key + ".raw.json")).write_bytes(b"{}")
    elif mutation == "result":
        (root / (key + ".result.json")).write_bytes(packet.canonical({"state": "captured_unreviewed"}))
    elif mutation == "source":
        report = acquisition.plan(*capture["common"])
        (capture["common"][1] / report["selected_cases"][0]["source"]["path"]).write_bytes(b"changed")
    elif mutation == "intent":
        path = root / (key + ".intent.json")
        intent = json.loads(path.read_bytes())
        intent["replay_authorized"] = True
        path.write_bytes(packet.canonical(intent))
    else:
        (root / (key + ".intent.json")).unlink()
    with pytest.raises((ValueError, OSError)):
        run(capture)
    assert len(capture["transport"].calls) == 1


def test_exclusive_journal_prevents_second_concurrent_sender(capture):
    original = capture["transport"].inspect

    def competing(*args):
        with pytest.raises(BlockingIOError):
            run(capture)
        return original(*args)

    capture["transport"].inspect = competing
    run(capture, initialize=True)
    assert len(capture["transport"].calls) == 1


@pytest.mark.parametrize("change", ["draft", "expired", "other_journal", "insufficient_reserve", "held_out"])
def test_unfunded_or_different_authority_never_sends(capture, change):
    auth = capture["auth"]
    update = {}
    if change == "draft":
        update = {"state": "draft"}
    elif change == "expired":
        update = {"valid_from": datetime.now(UTC) - timedelta(hours=2), "expires_at": datetime.now(UTC) - timedelta(hours=1)}
    elif change == "other_journal":
        update = {"journal_path": str(capture["tmp"] / "different")}
    elif change == "insufficient_reserve":
        update = {"maximum_native_reserved_minor": 1}
    else:
        with pytest.raises(ValidationError):
            acquisition.Authorization.model_validate(auth.model_dump() | {"split": "held_out"})
        return
    capture["common"] = (*capture["common"][:2], auth.model_copy(update=update), capture["common"][3])
    with pytest.raises(ValueError):
        run(capture, initialize=True)
    assert not capture["transport"].calls and not capture["journal"].exists()


def test_cli_redacts_unknown_exception(capture, monkeypatch, capsys):
    script = Path(__file__).resolve().parents[1] / "scripts/acquire_native_quality_inputs.py"
    spec = importlib.util.spec_from_file_location("synthetic_acquisition_cli", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    auth_path = capture["tmp"] / "authorization.json"
    auth_path.write_text(capture["auth"].model_dump_json())
    monkeypatch.setattr(sys, "argv", [str(script), "execute", "--native", str(capture["common"][1]),
        "--authorization", str(auth_path), "--journal", str(capture["journal"])])
    monkeypatch.setattr(acquisition, "execute", lambda *a, **kw: (_ for _ in ()).throw(ValueError("synthetic-private-token")))
    assert module.main() == 2
    output = capsys.readouterr()
    assert "synthetic-private-token" not in output.out + output.err
    assert json.loads(output.err)["replay_authorized"] is False


def test_inspection_capture_uses_exact_existing_production_request(capture, monkeypatch):
    report = acquisition.plan(*capture["common"])
    row = report["selected_cases"][0]
    source = acquisition.legacy._artifact(capture["common"][1], acquisition.legacy.Artifact.model_validate(row["source"])).read_bytes()
    kind = row["source_format"]
    raw = capture["transport"].inspect(capture["auth"].inspector, source, kind)
    requests = []

    async def synthetic_http(config, payload, token):
        requests.append(payload)
        return raw

    monkeypatch.setattr(document_ingestion, "_identity_token", lambda audience: "synthetic-not-live")
    monkeypatch.setattr(document_ingestion, "_inspect_http", synthetic_http)
    monkeypatch.setenv("DOCUMENT_WORKER_URL", capture["auth"].inspector.url)
    monkeypatch.setenv("DOCUMENT_WORKER_IMAGE_DIGEST", capture["auth"].inspector.image)
    monkeypatch.setenv("DOCUMENT_WORKER_POLICY_SHA256", capture["auth"].inspector.policy)
    document_ingestion.inspect_resume_document_with_receipt(source, source_format=kind)
    actual = acquisition.ServiceTransport().inspect(capture["auth"].inspector, source, kind)
    assert actual == raw and len(requests) == 2 and requests[0] == requests[1]


def test_changed_development_text_still_fails_strict_export_check(capture):
    transport = capture["transport"]
    original = transport.inspect

    def changed(binding, source, kind):
        value = json.loads(original(binding, source, kind))
        if value["parsed"]["raw_text"]:
            value["parsed"]["raw_text"] = "Visible source hy-\nphen mismatch; not complete original paragraphs."
        return packet.canonical(value)

    transport.inspect = changed
    complete(capture)
    destination = capture["tmp"] / "unqualified"
    acquisition.export_unreviewed(*capture["common"], capture["journal"], destination)
    registry = json.loads((destination / "capture-registry.unreviewed.json").read_bytes())
    assert len(registry["quality"]) == 40
    assert all(q["reading_order_pass"] is False for q in registry["quality"].values())
    assert registry["native_review"] == "pending_no_admission_authority"


def test_explicit_refusal_retains_entire_development_denominator(capture):
    original = capture["transport"].inspect
    first = True

    def refused_once(*args):
        nonlocal first
        if first:
            first = False
            capture["transport"].calls.append(("verified-refusal",))
            raise document_ingestion.DocumentInspectionError(refused=True)
        return original(*args)

    capture["transport"].inspect = refused_once
    run(capture, initialize=True)
    for _ in range(8):
        run(capture, maximum_requests=10)
    destination = capture["tmp"] / "failed-case-retained"
    result = acquisition.export_unreviewed(*capture["common"], capture["journal"], destination)
    status = json.loads((destination / "acquisition-status.json").read_bytes())
    assert result["captured_cases"] == 39 and result["selected_cases"] == 40
    assert len(status["cases"]) == 40
    assert status["cases"][0]["phases"]["source"]["state"] == "refused_no_replay"


@pytest.mark.parametrize("capture_mode", [True, False])
def test_tex_raw_capture_preserves_actual_bytes_and_legacy_result(monkeypatch, capture_mode):
    import google.oauth2.id_token

    payload = b'{"synthetic":"no-provider"}'
    response = {"protocol": "hirewiz-tex-v1", "pdf": "JVBERi0=", "sha256": "e" * 64, "size": 5,
        "renderer": "pdftex-no-shell-escape-v1", "request_sha256": packet.digest(payload),
        "renderer_image": "example.invalid/native@sha256:" + "a" * 64, "isolation_policy_sha256": "b" * 64}
    raw = b" \n" + json.dumps(response, indent=3).encode() + b"\n"
    calls = []

    def handler(request):
        calls.append(request.content)
        return httpx.Response(200, content=raw)

    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(google.oauth2.id_token, "fetch_id_token", lambda *a: "synthetic-not-live")
    function = native_tex_transport.remote_compile_capture if capture_mode else native_tex_transport.remote_compile
    actual = function(payload, url="https://synthetic.run.app/compile",
        image=response["renderer_image"], policy=response["isolation_policy_sha256"])
    assert actual == (raw if capture_mode else response) and calls == [payload]


def test_forged_first_response_stays_failed_without_admission_or_retry(capture):
    original = capture["transport"].inspect

    def forged(*args):
        value = json.loads(original(*args))
        value["policy_sha256"] = "f" * 64
        return packet.canonical(value)

    capture["transport"].inspect = forged
    status = run(capture, initialize=True)
    assert status["cases"][0]["phases"]["source"]["state"] == "invalid_response_no_replay"
    run(capture)
    assert len(capture["transport"].calls) == 2
    destination = capture["tmp"] / "failed-unreviewed"
    result = acquisition.export_unreviewed(*capture["common"], capture["journal"], destination)
    assert result["captured_cases"] == 0 and not result["quality_qualified"]
    assert len(json.loads((destination / "acquisition-status.json").read_bytes())["cases"]) == 40


@pytest.mark.parametrize("unsafe", ["mode", "symlink", "hardlink"])
def test_unsafe_journal_artifact_refuses_before_new_send(capture, unsafe):
    status = run(capture, initialize=True)
    path = capture["journal"] / (status["cases"][0]["case_id"] + ".source.intent.json")
    if unsafe == "mode":
        path.chmod(0o644)
    else:
        retained = capture["tmp"] / "other-intent.json"
        path.rename(retained)
        if unsafe == "symlink":
            path.symlink_to(retained)
        else:
            path.hardlink_to(retained)
    with pytest.raises(ValueError, match="private_owned_path"):
        run(capture)
    assert len(capture["transport"].calls) == 1
