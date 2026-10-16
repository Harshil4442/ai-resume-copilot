"""Real SQL/signature attachment authority, using owned inline sealed bytes only."""
from __future__ import annotations

import ast
import asyncio
import base64
import copy
import os
import sys
import types
from datetime import timedelta
from types import SimpleNamespace
from typing import Literal
from unittest.mock import patch
from uuid import uuid4

import pytest
from backend.tests import test_browser_assistance_isolated as flow
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session


@pytest.fixture
def owned():
    fixture = flow.BrowserAssistanceFlow()
    fixture.setUp()
    os.environ["BROWSER_ASSISTANCE_ATTACHMENT_ENABLED"] = "true"
    try:
        yield fixture
    finally:
        fixture.tearDown()


def saved_artifact(owned):
    return owned.db.get(flow.employer.SealedApplicationArtifact, owned.app.artifact_id)


def seal_package(owned):
    posting = owned.db.get(flow.employer.EmployerPosting, owned.app.posting_id)
    source = owned.db.get(flow.employer.EmployerSource, posting.source_id)
    owned.app.package_digest = flow.employer_service._package_digest(
        owned.app, posting, source, saved_artifact(owned)
    )
    owned.db.commit()


def attachment_review(owned):
    owned.paired()
    application = owned.call("application", {
        "application_id": owned.app.id, "review_protocol_version": 3,
    })
    assert application["review_protocol_version"] == 3
    context = {"owned_attachment_form": True}
    return owned.call("inspect", {"application_id": owned.app.id, "inspection": {
        "review_protocol_version": 3, "outcome": "inspected", "origin": flow.service.ORIGIN,
        "url": application["target_url"], "frame_id": 0, "document_id": "owned_chrome_document",
        "context": context, "context_digest": flow.service.digest(context),
        "assisted_values": {field["id"]: "" for field in application["field_mapping"]},
        "attachment": {"input_id": "resume", "expected_empty": True},
        "manual_fields": [{"id": "country", "label": "Country", "required": True}],
    }})


def approve(owned, review):
    return flow.service.candidate(owned.db, 1, "approve", flow.schemas.CandidateRequest(
        identity=review["id"], confirmed=True, review_digest=review["review_digest"],
    ))


def approved_action(owned):
    review = attachment_review(owned)
    approve(owned, review)
    begun = owned.call("begin", owned.begin_body(review))
    return review, begun["lease"]


def delivery_body(review, lease):
    return {"review_id": review["id"], "action_id": lease["payload"]["action_id"],
            "review_digest": review["review_digest"]}


def delivery(owned, review, lease):
    return owned.call("artifact", delivery_body(review, lease))


def outcome_body(review, lease, outcome="selected"):
    return {"review_id": review["id"], "action_id": lease["payload"]["action_id"],
            "outcome": outcome}


def assert_no_settlement(owned):
    owned.db.refresh(owned.app)
    assert owned.app.status == "ready"
    assert owned.app.charged_credits == 0
    assert owned.app.receipt is None
    assert owned.db.query(flow.employer.ServiceCreditReservation).count() == 0


def assert_unclaimed(owned, lease):
    owned.db.rollback()
    action = owned.db.get(flow.models.BrowserAssistanceRequest, lease["payload"]["action_id"])
    assert action.response == {}
    assert owned.db.query(flow.models.BrowserAssistanceRequest).filter_by(operation="artifact").count() == 0


@pytest.fixture
def binary_route():
    # Execute the shipping endpoint's body validation and response construction.
    # Inline only its threadpool hop so the owned SQLite connection stays on its
    # creating thread. No app bootstrap, auth provider or network is imported.
    tree = ast.parse((flow.ROOT / "routers/v1/browser_assistance.py").read_text())
    endpoint = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "device")
    endpoint.decorator_list = []
    headers = next(node for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "HEADERS" for target in node.targets))

    async def inline_threadpool(function, *args):
        return function(*args)

    def get_db():
        raise AssertionError("The isolated test passes its owned DB explicitly")

    namespace = {"asyncio": asyncio, "Literal": Literal, "Request": Request, "Session": Session,
        "Depends": Depends, "get_db": get_db, "HTTPException": HTTPException,
        "JSONResponse": JSONResponse, "Response": Response, "ValidationError": ValidationError,
        "schemas": flow.schemas, "service": flow.service, "run_in_threadpool": inline_threadpool}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[headers, endpoint], type_ignores=[])),
        "actual_browser_assistance_device_route", "exec"), namespace)
    return namespace["device"]


def http_request(owned, raw, *, headers=None, query=b""):
    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    supplied = {"content-type": "application/json", "origin": owned.origin, **(headers or {})}
    return Request({"type": "http", "method": "POST", "path": "/api/v1/browser-assistance/device/artifact",
        "scheme": "https", "server": ("www.hirewizhq.com", 443), "query_string": query,
        "headers": [(key.encode(), value.encode()) for key, value in supplied.items()]}, receive)


@pytest.mark.parametrize("choice", ["original", "custom", "tailored"])
def test_exact_saved_source_is_approved_signed_delivered_once_and_selected_without_receipt(owned, monkeypatch, choice):
    artifact = saved_artifact(owned)
    if choice == "custom":
        owned.db.execute(flow.Base.metadata.tables["resumes"].insert().values(id=2))
        owned.app.resume_id = artifact.resume_id = 2
    if choice == "tailored":
        version = flow.ResumeVersion(id="owned_reviewed_version", user_id=1, resume_id=1,
            approval_state="approved", structured_content={"source_edits": ["owned_edit"]}, evidence_ids=[])
        owned.db.add(version)
        owned.app.resume_version_id = artifact.resume_version_id = version.id
    owned.app.resume_choice = choice
    seal_package(owned)
    expected_bytes = bytes(artifact.content)
    real_read = flow.artifact_reader.read
    reads = []

    def bounded_read(snapshot, *, max_bytes):
        assert not owned.db.in_transaction(), "Storage I/O must not retain application locks"
        reads.append(max_bytes)
        return real_read(snapshot, max_bytes=max_bytes)

    monkeypatch.setattr(flow.artifact_reader, "read", bounded_read)
    review, lease = approved_action(owned)
    assert reads == []
    assert review["review_protocol_version"] == review["payload"]["review_protocol_version"] == 3
    assert review["payload"]["allowed_actions"] == ["attach"]
    assert review["payload"]["fields"] == []
    assert review["payload"]["reviewed_answers"] == {}
    assert review["payload"]["fee_credits"] == 0
    descriptor = review["payload"]["artifact"]
    assert descriptor == {"artifact_id": artifact.id, "sha256": artifact.sha256,
        "filename": artifact.filename, "media_type": artifact.media_type, "size_bytes": len(expected_bytes),
        "resume_id": owned.app.resume_id, "resume_version_id": owned.app.resume_version_id,
        "resume_choice": choice}
    payload = lease["payload"]
    assert payload["allowed_actions"] == ["attach"]
    assert payload["artifact"] == descriptor
    assert payload["attachment"] == {"input_id": "resume", "expected_empty": True}
    assert payload["review_digest"] == review["review_digest"]
    assert payload["package_digest"] == review["payload"]["package_digest"]
    assert payload["field_id"] == "resume" and payload["control_type"] == "file"
    assert 0 < payload["deadline"] - payload["issued_at_ms"] <= 10_000
    signature = base64.urlsafe_b64decode(lease["signature"] + "==")
    key = flow.service._public_key(flow.service.public_signer())[1]
    key.verify(encode_dss_signature(int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")),
        b"hirewiz.browser-assistance.lease.v1\n" + flow.service.canonical(payload), ec.ECDSA(hashes.SHA256()))
    binary = delivery(owned, review, lease)
    assert isinstance(binary, flow.attachments.BinaryDelivery)
    assert binary.content == expected_bytes and binary.descriptor == descriptor
    assert reads == [10 * 1024 * 1024]
    completed = owned.call("outcome", outcome_body(review, lease))
    assert completed["state"] == "filled" and completed["filled_count"] == 1
    assert completed["attachment_status"] == "selected_upload_unconfirmed"
    assert_no_settlement(owned)


def test_candidate_approval_is_required_before_lease_or_binary_read(owned, monkeypatch):
    review = attachment_review(owned)
    monkeypatch.setattr(flow.artifact_reader, "read", lambda *_args, **_kwargs: pytest.fail("Unapproved file read"))
    with pytest.raises(HTTPException):
        owned.call("begin", owned.begin_body(review))
    owned.db.rollback()
    with pytest.raises(HTTPException):
        owned.call("artifact", {"review_id": review["id"], "review_digest": review["review_digest"], "action_id": str(uuid4())})
    owned.db.rollback()
    assert owned.db.query(flow.models.BrowserAssistanceRequest).filter_by(operation="attachment").count() == 0
    assert_no_settlement(owned)


@pytest.mark.parametrize("change", ["owner", "digest", "confirmation"])
def test_attachment_approval_cannot_be_granted_by_wrong_owner_or_changed_review(owned, change):
    review = attachment_review(owned)
    with pytest.raises(HTTPException):
        flow.service.candidate(owned.db, 2 if change == "owner" else 1, "approve", flow.schemas.CandidateRequest(
            identity=review["id"], confirmed=change != "confirmation",
            review_digest="0" * 64 if change == "digest" else review["review_digest"],
        ))
    owned.db.rollback()
    assert owned.db.get(flow.models.BrowserAssistanceReview, review["id"]).state == "draft"


@pytest.mark.parametrize("other_owner", [1, 2])
def test_binary_delivery_rejects_another_valid_paired_device_even_for_same_owner(owned, monkeypatch, other_owner):
    review, lease = approved_action(owned)
    monkeypatch.setattr(flow.artifact_reader, "read", lambda *_args, **_kwargs: pytest.fail("Wrong-device file read"))
    private = ec.generate_private_key(ec.SECP256R1())
    numbers = private.public_key().public_numbers()
    public = {"kty": "EC", "crv": "P-256", "x": flow.service._b64(numbers.x.to_bytes(32, "big")),
              "y": flow.service._b64(numbers.y.to_bytes(32, "big"))}
    with patch.object(owned, "private", private), patch.object(owned, "public", public), patch.object(owned, "device_id", str(uuid4())):
        prepared = owned.call("prepare", {"public_key": public, "extension_id": flow.service.EXTENSION_ID})
        flow.service.candidate(owned.db, other_owner, "confirm", flow.schemas.CandidateRequest(
            identity=owned.device_id, confirmed=True, key_sha256=prepared["key_sha256"],
        ))
        with pytest.raises(HTTPException) as failure:
            delivery(owned, review, lease)
        assert failure.value.status_code == 404
    assert_unclaimed(owned, lease)


@pytest.mark.parametrize("change", ["signature", "digest", "action", "origin", "extra_url"])
def test_signed_delivery_rejects_tampered_bindings_or_arbitrary_url_without_reading(owned, monkeypatch, change):
    review, lease = approved_action(owned)
    monkeypatch.setattr(flow.artifact_reader, "read", lambda *_args, **_kwargs: pytest.fail("Tampered file read"))
    body = delivery_body(review, lease)
    if change == "digest":
        body["review_digest"] = "0" * 64
    if change == "action":
        body["action_id"] = str(uuid4())
    if change == "extra_url":
        body["url"] = "https://foreign.invalid/arbitrary-file"
    request = owned.request("artifact", body)
    if change == "signature":
        request = request.model_copy(update={"body": {**body, "review_digest": "0" * 64}})
    with pytest.raises(HTTPException):
        flow.service.device(owned.db, "chrome-extension://" + "a" * 32 if change == "origin" else owned.origin, request)
    assert_unclaimed(owned, lease)


@pytest.mark.parametrize("change", ["hash", "size", "mime", "filename", "source"])
def test_invalid_sealed_descriptor_is_refused_before_approval_and_binary_read(owned, monkeypatch, change):
    artifact = saved_artifact(owned)
    if change == "hash":
        artifact.sha256 = "bad-hash"
    if change == "size":
        artifact.size_bytes = 10 * 1024 * 1024 + 1
    if change == "mime":
        artifact.media_type = "text/html"
    if change == "filename":
        artifact.filename = "../../resume.pdf"
    if change == "source":
        owned.app.resume_choice = "tailored"
    seal_package(owned)
    monkeypatch.setattr(flow.artifact_reader, "read", lambda *_args, **_kwargs: pytest.fail("Invalid descriptor read"))
    with pytest.raises(HTTPException):
        attachment_review(owned)
    owned.db.rollback()
    assert owned.db.query(flow.models.BrowserAssistanceReview).count() == 0


def test_byte_hash_tamper_fails_actual_saved_artifact_reader_without_claiming_delivery(owned):
    review, lease = approved_action(owned)
    artifact = saved_artifact(owned)
    artifact.content = b"X" * artifact.size_bytes
    owned.db.commit()
    with pytest.raises(HTTPException, match="integrity verification"):
        delivery(owned, review, lease)
    assert_unclaimed(owned, lease)


@pytest.mark.parametrize("same_request", [True, False])
def test_binary_delivery_is_consumed_before_reply_and_never_redelivered_on_replay(owned, monkeypatch, same_request):
    review, lease = approved_action(owned)
    request = owned.request("artifact", delivery_body(review, lease))
    binary = flow.service.device(owned.db, owned.origin, request)
    assert binary.content
    monkeypatch.setattr(flow.artifact_reader, "read", lambda *_args, **_kwargs: pytest.fail("Binary response replayed"))
    if not same_request:
        request = owned.request("artifact", delivery_body(review, lease))
    with pytest.raises(HTTPException):
        flow.service.device(owned.db, owned.origin, request)
    owned.db.rollback()
    assert owned.db.query(flow.models.BrowserAssistanceRequest).filter_by(operation="artifact").count() == 1
    assert owned.db.get(flow.models.BrowserAssistanceRequest, lease["payload"]["action_id"]).response == {"delivery_claimed": True}
    assert_no_settlement(owned)


def test_begin_replay_is_unknown_and_new_request_cannot_issue_another_attachment_lease(owned):
    review = attachment_review(owned)
    approve(owned, review)
    request = owned.request("begin", owned.begin_body(review))
    begun = flow.service.device(owned.db, owned.origin, request)
    replay = flow.service.device(owned.db, owned.origin, request)
    assert begun["lease"] and replay["state"] == "unknown" and "lease" not in replay
    with pytest.raises(HTTPException):
        owned.call("begin", owned.begin_body(review))
    owned.db.rollback()
    assert owned.db.query(flow.models.BrowserAssistanceRequest).filter_by(operation="attachment").count() == 1


def test_selected_outcome_requires_binary_delivery_claim(owned):
    review, lease = approved_action(owned)
    with pytest.raises(HTTPException):
        owned.call("outcome", outcome_body(review, lease))
    assert_unclaimed(owned, lease)
    assert owned.db.get(flow.models.BrowserAssistanceReview, review["id"]).next_index == 0


@pytest.mark.parametrize("boundary", ["application", "approval", "begin", "delivery"])
def test_attachment_flag_disabled_at_each_authority_boundary_refuses_new_disclosure(owned, monkeypatch, boundary):
    monkeypatch.setattr(flow.artifact_reader, "read", lambda *_args, **_kwargs: pytest.fail("Disabled attachment read"))
    if boundary == "application":
        owned.paired()

        def operation():
            return owned.call("application", {"application_id": owned.app.id, "review_protocol_version": 3})
    else:
        review = attachment_review(owned)
        if boundary == "approval":
            def operation():
                return approve(owned, review)
        else:
            approve(owned, review)
            if boundary == "begin":
                def operation():
                    return owned.call("begin", owned.begin_body(review))
            else:
                lease = owned.call("begin", owned.begin_body(review))["lease"]

                def operation():
                    return delivery(owned, review, lease)
    os.environ["BROWSER_ASSISTANCE_ATTACHMENT_ENABLED"] = "false"
    with pytest.raises(HTTPException) as failure:
        operation()
    assert failure.value.status_code == 503
    owned.db.rollback()
    assert owned.db.query(flow.models.BrowserAssistanceRequest).filter_by(operation="artifact").count() == 0


@pytest.mark.parametrize("change", ["cancel", "revoke", "flag", "service_flag", "deadline", "review_expiry", "package", "artifact", "source", "application", "owner"])
def test_delivery_rechecks_current_authority_after_unlocked_storage_io(owned, monkeypatch, change):
    clock = [flow.service.now()]
    monkeypatch.setattr(flow.service, "now", lambda: clock[0])
    review, lease = approved_action(owned)
    if change == "review_expiry":
        row = owned.db.get(flow.models.BrowserAssistanceReview, review["id"])
        row.expires_at = clock[0] + timedelta(seconds=5)
        owned.db.commit()
    real_read = flow.artifact_reader.read
    reads = []

    def read_then_change(snapshot, *, max_bytes):
        assert not owned.db.in_transaction()
        content = real_read(snapshot, max_bytes=max_bytes)
        reads.append(True)
        if change == "flag":
            os.environ["BROWSER_ASSISTANCE_ATTACHMENT_ENABLED"] = "false"
        elif change == "service_flag":
            os.environ["BROWSER_ASSISTANCE_ENABLED"] = "false"
        elif change == "deadline":
            clock[0] += timedelta(seconds=11)
        elif change == "review_expiry":
            clock[0] += timedelta(seconds=6)
        else:
            with Session(owned.engine) as independent:
                if change in {"cancel", "revoke"}:
                    flow.service.candidate(independent, 1, change, flow.schemas.CandidateRequest(
                        identity=owned.device_id if change == "revoke" else review["id"], confirmed=True,
                    ))
                elif change == "package":
                    app = independent.get(flow.employer.EmployerApplication, owned.app.id)
                    app.answers = {**app.answers, "first_name": "Changed after approval"}
                    app.package_digest = "0" * 64
                elif change == "artifact":
                    independent.get(flow.employer.SealedApplicationArtifact, owned.app.artifact_id).filename = "Changed.pdf"
                elif change == "source":
                    independent.get(flow.employer.EmployerSource, "source_test").enabled = False
                elif change == "application":
                    independent.get(flow.employer.EmployerApplication, owned.app.id).cancel_requested = True
                elif change == "owner":
                    independent.delete(independent.get(flow.User, 1))
                independent.commit()
        return content

    monkeypatch.setattr(flow.artifact_reader, "read", read_then_change)
    with pytest.raises(HTTPException):
        delivery(owned, review, lease)
    assert reads == [True]
    assert_unclaimed(owned, lease)
    assert_no_settlement(owned)


@pytest.mark.parametrize("terminal", ["filled", "unknown", "cancelled", "expired"])
def test_privacy_erasure_retains_durable_attachment_version_and_honest_outcome(owned, monkeypatch, terminal):
    review, lease = approved_action(owned)
    if terminal == "filled":
        delivery(owned, review, lease)
        owned.call("outcome", outcome_body(review, lease))
    elif terminal == "unknown":
        owned.call("outcome", outcome_body(review, lease, "unknown"))
    elif terminal == "cancelled":
        owned.call("cancel", {"review_id": review["id"]})
    original = copy.deepcopy(owned.db.get(flow.models.BrowserAssistanceReview, review["id"]).review_digest)
    monkeypatch.setattr(flow.service, "now", lambda: flow.common.utcnow() + timedelta(minutes=10))
    assert flow.service.expire_reviews(owned.db) > 0
    response = owned.call("status", {"review_id": review["id"]})["review"]
    assert response["payload"] == {} and response["state"] == terminal
    assert response["review_protocol_version"] == 3 and response["review_digest"] == original
    assert response["attachment_status"] == ("selected_upload_unconfirmed" if terminal == "filled" else "unknown")
    assert response["filled_count"] == (1 if terminal == "filled" else 0)
    assert_no_settlement(owned)


def test_device_route_emits_exact_binary_content_and_private_descriptor_headers(owned, binary_route):
    review, lease = approved_action(owned)
    signed = owned.request("artifact", delivery_body(review, lease))
    request = http_request(owned, signed.model_dump_json().encode())
    response = asyncio.run(binary_route(request, "artifact", owned.db))
    descriptor = review["payload"]["artifact"]
    assert response.status_code == 200 and bytes(response.body) == bytes(saved_artifact(owned).content)
    assert response.headers["content-type"] == descriptor["media_type"]
    assert response.headers["content-length"] == str(descriptor["size_bytes"])
    assert response.headers["x-artifact-sha256"] == descriptor["sha256"]
    assert response.headers["x-artifact-filename"] == descriptor["filename"]
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "set-cookie" not in response.headers and "authorization" not in response.headers
    assert owned.db.query(flow.models.BrowserAssistanceRequest).filter_by(operation="artifact").count() == 1
    assert_no_settlement(owned)


@pytest.mark.parametrize("change", ["cookie", "authorization", "content-type", "encoding", "query", "operation", "oversize", "malformed"])
def test_binary_route_refuses_transport_ambiguity_before_file_disclosure(owned, monkeypatch, binary_route, change):
    review, lease = approved_action(owned)
    raw = owned.request("artifact", delivery_body(review, lease)).model_dump_json().encode()
    headers = {}
    if change == "cookie":
        headers["cookie"] = "candidate-account-cookie"
    if change == "authorization":
        headers["authorization"] = "Bearer unrelated-candidate-token"
    if change == "content-type":
        headers["content-type"] = "application/octet-stream"
    if change == "encoding":
        headers["content-encoding"] = "gzip"
    if change == "oversize":
        raw = b"x" * 65_537
    if change == "malformed":
        raw = b'{"not": "a signed device request"}'
    monkeypatch.setattr(flow.artifact_reader, "read", lambda *_args, **_kwargs: pytest.fail("Rejected HTTP file read"))
    request = http_request(owned, raw, headers=headers, query=b"url=https://foreign.invalid/file" if change == "query" else b"")
    if change == "malformed":
        response = asyncio.run(binary_route(request, "artifact", owned.db))
        assert response.status_code == 422 and response.headers["cache-control"] == "private, no-store"
    else:
        with pytest.raises(HTTPException) as failure:
            asyncio.run(binary_route(request, "begin" if change == "operation" else "artifact", owned.db))
        assert failure.value.status_code == (413 if change == "oversize" else 403)
        assert failure.value.headers["Cache-Control"] == "private, no-store"
    assert_unclaimed(owned, lease)


@pytest.mark.parametrize("target", ["version", "evidence"])
@pytest.mark.parametrize("during_read", [False, True])
def test_tailored_version_and_evidence_revocation_prevent_attachment_disclosure(owned, monkeypatch, target, during_read):
    owned.db.add(flow.EvidenceItem(id="owned_evidence", user_id=1, approval_state="approved"))
    owned.db.add(flow.ResumeVersion(id="owned_tailored", user_id=1, resume_id=1,
        approval_state="approved", structured_content={"source_edits": ["owned_edit"]}, evidence_ids=["owned_evidence"]))
    owned.app.resume_choice, owned.app.resume_version_id = "tailored", "owned_tailored"
    saved_artifact(owned).resume_version_id = "owned_tailored"
    seal_package(owned)
    review, lease = approved_action(owned)

    def revoke():
        with Session(owned.engine) as independent:
            model, identity = (flow.ResumeVersion, "owned_tailored") if target == "version" else (flow.EvidenceItem, "owned_evidence")
            independent.get(model, identity).approval_state = "revoked"
            independent.commit()

    if during_read:
        real_read = flow.artifact_reader.read

        def read_then_revoke(snapshot, *, max_bytes):
            assert not owned.db.in_transaction()
            content = real_read(snapshot, max_bytes=max_bytes)
            revoke()
            return content

        monkeypatch.setattr(flow.artifact_reader, "read", read_then_revoke)
    else:
        revoke()
        monkeypatch.setattr(flow.artifact_reader, "read", lambda *_args, **_kwargs: pytest.fail("Revoked tailored artifact read"))
    with pytest.raises(HTTPException):
        delivery(owned, review, lease)
    assert_unclaimed(owned, lease)
    assert_no_settlement(owned)


def fake_gcs(monkeypatch, content):
    calls = {}

    def download(**options):
        calls["download"] = options
        return content

    def blob(name, *, generation):
        calls["blob"] = {"name": name, "generation": generation}
        return SimpleNamespace(download_as_bytes=download)

    def bucket(name):
        calls["bucket"] = name
        return SimpleNamespace(blob=blob)

    def client():
        calls["client"] = calls.get("client", 0) + 1
        return SimpleNamespace(bucket=bucket)

    google = types.ModuleType("google")
    cloud = types.ModuleType("google.cloud")
    storage = types.ModuleType("google.cloud.storage")
    google.cloud, cloud.storage, storage.Client = cloud, storage, client
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.storage", storage)
    return calls


@pytest.mark.parametrize("overflow_byte", [False, True])
def test_actual_gcs_artifact_reader_uses_generation_bounded_range_timeout_and_no_retry(owned, monkeypatch, overflow_byte):
    artifact = saved_artifact(owned)
    content = bytes(artifact.content)
    calls = fake_gcs(monkeypatch, content + (b"X" if overflow_byte else b""))
    snapshot = SimpleNamespace(size_bytes=len(content), sha256=artifact.sha256,
        gcs_object="gs://owned-fixture-bucket/applications/owned/immutable-resume", gcs_generation="17", content=None)
    if overflow_byte:
        with pytest.raises(HTTPException, match="integrity verification"):
            flow.artifact_reader.read(snapshot, max_bytes=10 * 1024 * 1024)
    else:
        assert flow.artifact_reader.read(snapshot, max_bytes=10 * 1024 * 1024) == content
    assert calls == {"client": 1, "bucket": "owned-fixture-bucket",
        "blob": {"name": "applications/owned/immutable-resume", "generation": 17},
        "download": {"if_generation_match": 17, "start": 0, "end": len(content), "timeout": 5, "retry": None}}


def test_actual_gcs_artifact_reader_refuses_declared_overflow_before_creating_storage_client(monkeypatch):
    calls = fake_gcs(monkeypatch, b"Never read")
    snapshot = SimpleNamespace(size_bytes=10 * 1024 * 1024 + 1, sha256="a" * 64,
        gcs_object="gs://owned-fixture-bucket/oversized", gcs_generation="17", content=None)
    with pytest.raises(HTTPException, match="delivery bound"):
        flow.artifact_reader.read(snapshot, max_bytes=10 * 1024 * 1024)
    assert calls == {}


@pytest.mark.parametrize("remaining_ms", [1, 1750, 10_000, 20_000])
def test_signed_attachment_lease_never_outlives_near_expiry_candidate_approval(owned, monkeypatch, remaining_ms):
    clock = [flow.service.now()]
    monkeypatch.setattr(flow.service, "now", lambda: clock[0])
    review = attachment_review(owned)
    row = owned.db.get(flow.models.BrowserAssistanceReview, review["id"])
    expires = flow.service._aware(row.expires_at)
    clock[0] = expires - timedelta(milliseconds=remaining_ms)
    approve(owned, review)
    lease = owned.call("begin", owned.begin_body(review))["lease"]
    payload = lease["payload"]
    deadline = min(expires, clock[0] + timedelta(seconds=10))
    assert payload["issued_at_ms"] == flow.service._ms(clock[0])
    assert payload["deadline"] == flow.service._ms(deadline)
    assert payload["deadline"] - payload["issued_at_ms"] == min(remaining_ms, 10_000)
    action = owned.db.get(flow.models.BrowserAssistanceRequest, payload["action_id"])
    assert flow.service._aware(action.lease_expires_at) == deadline
    signature = base64.urlsafe_b64decode(lease["signature"] + "==")
    signer = flow.service._public_key(flow.service.public_signer())[1]
    signer.verify(encode_dss_signature(int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")),
        b"hirewiz.browser-assistance.lease.v1\n" + flow.service.canonical(payload), ec.ECDSA(hashes.SHA256()))
    assert_no_settlement(owned)


@pytest.mark.parametrize("same_request", [True, False])
def test_delivery_commit_that_finishes_after_lease_expiry_returns_no_bytes_and_retains_claim(owned, monkeypatch, same_request):
    clock = [flow.service.now()]
    monkeypatch.setattr(flow.service, "now", lambda: clock[0])
    review, lease = approved_action(owned)
    request = owned.request("artifact", delivery_body(review, lease))
    action_id = lease["payload"]["action_id"]
    deadline = flow.service._aware(owned.db.get(flow.models.BrowserAssistanceRequest, action_id).lease_expires_at)
    before_commit = clock[0]
    commit = owned.db.commit
    commits = []

    def commit_then_expire():
        commit()
        commits.append(True)
        clock[0] = deadline + timedelta(milliseconds=1)

    monkeypatch.setattr(owned.db, "commit", commit_then_expire)
    with pytest.raises(HTTPException) as failure:
        flow.service.device(owned.db, owned.origin, request)
    assert failure.value.status_code == 409 and commits == [True]
    owned.db.rollback()  # Transport error cleanup cannot roll back an observed commit.
    with Session(owned.engine) as independent:
        assert independent.get(flow.models.BrowserAssistanceRequest, action_id).response == {"delivery_claimed": True}
        assert independent.query(flow.models.BrowserAssistanceRequest).filter_by(operation="artifact").count() == 1
        retained = independent.get(flow.models.BrowserAssistanceReview, review["id"])
        assert retained.state == "running" and retained.next_index == 0
    monkeypatch.setattr(owned.db, "commit", commit)
    monkeypatch.setattr(flow.artifact_reader, "read", lambda *_args, **_kwargs: pytest.fail("Expired commit caused binary redelivery"))
    # Even if the clock is corrected back inside the original lease, the durable
    # claim itself must prevent both identical and fresh request IDs from replay.
    clock[0] = before_commit
    if not same_request:
        request = owned.request("artifact", delivery_body(review, lease))
    with pytest.raises(HTTPException):
        flow.service.device(owned.db, owned.origin, request)
    owned.db.rollback()
    assert owned.db.get(flow.models.BrowserAssistanceRequest, action_id).response == {"delivery_claimed": True}
    assert owned.db.query(flow.models.BrowserAssistanceRequest).filter_by(operation="artifact").count() == 1
    assert_no_settlement(owned)
