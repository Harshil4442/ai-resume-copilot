"""Owned synthetic SQL/crypto/funding fixtures; no provider or employer calls."""
from datetime import timedelta

from backend.app.domains.browser_assistance import service as browser
from backend.app.domains.credits import runtime, service, service_quotes, submission
from backend.tests.fixtures.reviewed_runtime import reviewed_runtime
from backend.tests.test_browser_hosted_network import native_upload
from backend.tests.test_unified_execution import budgets, financial_policy, route


def configure(owned, monkeypatch, *, quantity=40, cost=13):
    selected = service_quotes.QualifiedRoute.model_validate(route(native=True).model_dump() | {
        "id": "native-submission", "operation": "verified_submission", "credits": 3,
        "output_contract_sha256": submission.delivery_contract(),
        "native_processing_minor": cost, "worker_processing_minor": 0,
        "supported_limits": {"artifact_bytes": 10 * 1024 * 1024, "answer_count": 64, "review_bytes": 65536}})
    current = runtime.RuntimeConfig(policy=financial_policy(),
        budgets=budgets(monthly_route_minor={"native-submission": 10000}), routes=(selected,))
    reviewed_runtime(monkeypatch, current)
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("UNIFIED_BROWSER_SUBMISSION_ENABLED", "true")
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", current.model_dump_json())
    if quantity:
        service.grant_credits(owned.db, user_id=1, quantity=quantity,
            source_type="payment_order", source_id="synthetic-unified-submission",
            funding_kind="paid", terms_snapshot={"synthetic": True},
            expires_at=browser.now() + timedelta(days=1), now=browser.now())
    owned.db.commit()
    return current


def review(owned, *, version=5):
    if not hasattr(owned, "unified_upload"):
        owned.unified_upload = native_upload(owned)
    evidence, context = owned.unified_upload
    application = owned.device("application", {"application_id": owned.app.id, "review_protocol_version": version})
    assert application["review_protocol_version"] == version
    upload = application["upload"]
    assert upload["native_upload"] == evidence
    return owned.device("inspect", {"application_id": owned.app.id, "inspection": {
        "review_protocol_version": version, "outcome": "inspected", "origin": browser.ORIGIN,
        "url": owned.target, "frame_id": 0, "document_id": "owned_document", "context_digest": "e" * 64,
        "assisted_values": owned.values, "manual_fields": [], "ready": True,
        "upload": upload, "native_context": context,
        "upload_continuity": {"review_id": upload["review_id"], "action_id": upload["action_id"],
            "artifact_sha256": owned.artifact_sha, "document_id": "owned_document", "target_url": owned.target}}})


def approve(owned, value):
    return owned.candidate("approve", value["id"], confirmed=True,
        review_digest=value["review_digest"], accepted_service_quote_id=value["billing"]["quote_id"])
