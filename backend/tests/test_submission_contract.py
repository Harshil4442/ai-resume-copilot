"""Pure protocol vectors, never a receipt, wallet grant or external action."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domains.credits import submission_contract as c

FIXTURE = Path(__file__).parent / "fixtures/browser_submission_v5.json"


def vector():
    return json.loads(FIXTURE.read_text())


def test_complete_synthetic_vectors_bind_exact_review_pricing_lease_and_approval():
    data = vector()
    assert data["authority"] is False
    preimage = c.SubmissionInput.model_validate(data["input_preimage"])
    assert preimage.package_inputs_sha256 == c.digest(data["package_inputs_preimage"])
    assert c.input_fingerprint(preimage) == data["input_fingerprint"]
    for name, protocol, fee in (("legacy4", 4, 20), ("unified5", 5, 3)):
        sample = data[name]
        response, lease, approve = (sample[key] for key in
            ("review_response", "lease_payload", "approve_request"))
        payload = response["payload"]
        assert response["review_protocol_version"] == lease["review_protocol_version"] == protocol
        assert response["fee_credits"] == lease["fee_credits"] == payload["fee_credits"] == fee
        assert response["review_digest"] == approve["review_digest"] == lease["review_digest"] == c.digest(payload)
        assert sample["review_sha256"] == c.digest(payload)
        assert sample["pricing_sha256"] == c.digest(payload["pricing"])
        assert sample["lease_sha256"] == c.digest(lease)
        assert payload["expected_application_sha256"] == c.digest(payload["expected_application"])
        assert payload["reviewed_answers_sha256"] == c.digest({
            "context_digest": payload["context_digest"], "reviewed_answers": payload["reviewed_answers"]})
        assert len(payload["answers"]) == len(payload["field_mapping"]) == 3
        assert payload["manual_fields"] == payload["fields"] == []
        for key in ("artifact_sha256", "target_url", "context_digest", "document_id"):
            expected = payload["artifact"]["sha256"] if key == "artifact_sha256" else payload[key]
            assert lease[key] == expected
        for key in ("upload", "upload_continuity", "submit_control", "receipt_contract",
                    "expected_application", "expected_application_sha256", "native_context"):
            assert lease[key] == payload[key]
        assert "signature" not in sample and "signature" not in lease
    new = data["unified5"]
    assert new["application_request"] == {"application_id": "apply_synthetic", "review_protocol_version": 5}
    assert new["inspect_request"]["inspection"]["review_protocol_version"] == 5
    assert new["availability"] == {"unified_submit_available": True, "submit_protocol_versions": [4, 5]}
    response, lease = new["review_response"], new["lease_payload"]
    billing = c.SubmissionBilling.model_validate(response["billing"])
    assert set(response["billing"]) == {
        "schema_version", "billing_currency", "operation", "credits", "tariff_version",
        "route_id", "route_version", "delivery_contract_sha256", "financial_policy_sha256",
        "quote_id", "input_fingerprint", "quote_contract_sha256", "quote_expires_at_ms"}
    assert billing.model_dump(mode="json") == response["payload"]["pricing"] == response["payload"]["billing"] == lease["billing"]
    assert c.billing_digest(billing) == new["pricing_sha256"]
    assert billing.input_fingerprint == c.input_fingerprint(preimage)
    assert billing.model_dump(include=set(c.PriceCore.model_fields)) == preimage.price.model_dump()
    assert c.SubmissionApproval.model_validate(new["approve_request"]).accepted_service_quote_id == billing.quote_id
    assert "accepted_service_quote_id" not in data["legacy4"]["approve_request"]
    assert "billing" not in data["legacy4"]["review_response"]


@pytest.mark.parametrize("field", tuple(c.SubmissionInput.model_fields))
def test_every_owned_input_component_is_bound_and_no_generated_hash_cycle(field):
    original = vector()["input_preimage"]
    changed = copy.deepcopy(original)
    if field == "schema_version":
        changed[field] = "unknown"
    elif field == "price":
        changed[field]["route_version"] = "different-version"
    elif field in {"user_id", "resume_id"}:
        changed[field] += 1
    elif field in {"device_id", "review_id"}:
        changed[field] = "00000000-0000-4000-8000-000000000099"
    elif field == "resume_version_id":
        changed["resume_choice"], changed[field] = "tailored", "different-version"
    elif field == "resume_choice":
        changed[field] = "custom"
    elif field == "target_url":
        changed[field] = original[field][:-1] + "9"
    elif field == "artifact_generation":
        changed[field] = "987654321"
    else:
        changed[field] = "9" * 64 if field.endswith("sha256") or field in {"opening_key", "employer_key", "context_digest"} else "different"
    try:
        parsed = c.SubmissionInput.model_validate(changed)
    except ValidationError:
        return
    assert c.input_fingerprint(parsed) != c.input_fingerprint(c.SubmissionInput.model_validate(original))
    assert not {"quote_id", "package_digest", "review_digest"} & set(original)


@pytest.mark.parametrize("field,value", [
    ("credits", True), ("credits", 3.0), ("credits", "3"), ("credits", 20),
    ("billing_currency", "INR"), ("operation", "job_application"),
    ("schema_version", "unknown"), ("quote_id", "csq_wrong"),
    ("quote_expires_at_ms", True), ("quote_expires_at_ms", 0),
    ("quote_expires_at_ms", 2**53), ("quote_contract_sha256", "A" * 64),
    ("financial_policy_sha256", "not-a-digest"), ("unreviewed", "extra"),
])
def test_billing_is_exact_units_and_closed_shape(field, value):
    data = vector()["unified5"]["review_response"]["billing"]
    data[field] = value
    with pytest.raises(ValidationError):
        c.SubmissionBilling.model_validate(data)


@pytest.mark.parametrize("field", tuple(c.SubmissionBilling.model_fields))
def test_every_wire_billing_key_is_explicit_without_inferred_defaults(field):
    data = vector()["unified5"]["review_response"]["billing"]
    del data[field]
    with pytest.raises(ValidationError):
        c.SubmissionBilling.model_validate(data)


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_approval_requires_literal_human_confirmation(value):
    data = vector()["unified5"]["approve_request"]
    data["confirmed"] = value
    with pytest.raises(ValidationError):
        c.SubmissionApproval.model_validate(data)


def test_approval_cannot_supply_price_or_skip_exact_quote_and_canonical_is_order_independent():
    data = vector()["unified5"]["approve_request"]
    with pytest.raises(ValidationError):
        c.SubmissionApproval.model_validate({k: v for k, v in data.items() if k != "accepted_service_quote_id"})
    with pytest.raises(ValidationError):
        c.SubmissionApproval.model_validate({**data, "credits": 3})
    assert c.digest({"unicode": "résumé", "n": 3}) == c.digest({"n": 3, "unicode": "résumé"})
    with pytest.raises(ValueError):
        c.digest({"nan": float("nan")})
