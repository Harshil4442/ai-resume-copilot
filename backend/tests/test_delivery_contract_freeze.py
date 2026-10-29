"""Actual helper/constant qualification binding, without qualification claims."""
import re
from types import SimpleNamespace

import pytest
from backend.app.domains.analysis.operations import _ensure_prompt_version
from backend.app.domains.credits import execution, finance, pricing, service_quotes
from backend.app.services import delivery_contracts, interview_contract, llm_client, personal_claims
from backend.app.services.generation_budget import GenerationBudget, generation_budget
from backend.tests.test_paid_qa_quality import _database
from backend.tests.test_unified_execution import financial_policy, route


def _changed_prompt(*args, **kwargs):
    return [{"role": "system", "content": "A changed but unversioned prompt"}]


@pytest.mark.parametrize("operation", sorted(delivery_contracts.AFFECTED_OPERATIONS))
def test_new_affected_route_requires_explicit_current_output_evidence(operation):
    selected = route(attempts=1).model_copy(update={
        "operation": operation, "credits": pricing.SERVICE_CREDITS[operation],
        "output_contract_sha256": delivery_contracts.current_output_contract(operation)})
    service_quotes.require_current_output_contract(selected)
    old = selected.model_dump(mode="json")
    old.pop("output_contract_sha256")
    restored = service_quotes.QualifiedRoute.model_validate(old)
    assert restored.output_contract_sha256 is None  # Historical metadata remains readable.
    with pytest.raises(finance.FundingUnavailable, match="different output contract"):
        service_quotes.require_current_output_contract(restored)


def test_unversioned_prompt_helper_and_shared_constant_changes_invalidate_qualification(monkeypatch):
    original = delivery_contracts.current_output_contract("interview_questions")
    with monkeypatch.context() as patch:
        patch.setattr(interview_contract, "build_interview_messages", _changed_prompt)
        assert delivery_contracts.current_output_contract("interview_questions") != original
    current = {name: delivery_contracts.current_output_contract(name)
               for name in delivery_contracts.AFFECTED_OPERATIONS}
    monkeypatch.setitem(personal_claims._RELATIONS, "leadership", re.compile(r"\bled\b"))
    assert all(delivery_contracts.current_output_contract(name) != digest
               for name, digest in current.items())
    assert delivery_contracts.current_output_contract("job_discovery_batch") is None
    assert delivery_contracts.current_output_contract("enhanced_job_match") == delivery_contracts.current_output_contract("job_match")
    service_quotes.require_route(route(native=True), financial_policy(),
                                  now=financial_policy().valid_from)


def test_unavailable_source_introspection_refuses_affected_routes_cleanly(monkeypatch):
    selected = route(attempts=1)

    def unavailable(value):
        raise OSError("Synthetic unavailable source")

    monkeypatch.setattr(delivery_contracts.inspect, "getsource", unavailable)
    with pytest.raises(finance.FundingUnavailable, match="new qualification"):
        service_quotes.require_current_output_contract(selected)
    service_quotes.require_current_output_contract(route(native=True))


def test_interview_registration_freezes_actual_prompt_builder_and_contract_constants(monkeypatch):
    engine, factory = _database()
    try:
        with factory() as db:
            def register():
                return _ensure_prompt_version(db, operation="interview_questions",
                    version=interview_contract.PROMPT_VERSION,
                    callable_object=llm_client.generate_interview_questions,
                    linked_callables=(interview_contract.build_interview_messages,),
                    contract_identity={"output_contract_sha256":
                        delivery_contracts.current_output_contract("interview_questions")})
            register()
            db.commit()
            with monkeypatch.context() as patch:
                patch.setattr(interview_contract, "build_interview_messages", _changed_prompt)
                with pytest.raises(RuntimeError, match="without a version bump"):
                    register()
            monkeypatch.setitem(personal_claims._RELATIONS, "leadership", re.compile(r"\bled\b"))
            with pytest.raises(RuntimeError, match="without a version bump"):
                register()
    finally:
        engine.dispose()


def test_alternate_existing_budget_cannot_bypass_current_output_contract():
    stale = route(attempts=1).model_copy(update={"output_contract_sha256": None})
    budget = GenerationBudget(limit=1, run_id="run-existing")
    with generation_budget(budget):
        with pytest.raises(finance.FundingUnavailable, match="different output contract"):
            with execution.persistent_execution_budget(lambda: pytest.fail("No DB or provider call"),
                    user_id=1, hold_id="hold-old", route=stale,
                    policy=financial_policy(), analysis_run_id="run-existing"):
                pytest.fail("A matching existing budget is not output qualification")
    assert budget.used == 0


def test_old_quote_remains_readable_without_requalifying_its_snapshot():
    selected = route(attempts=1).model_dump(mode="json")
    selected.pop("output_contract_sha256")
    now = financial_policy().valid_from
    quote = SimpleNamespace(id="csq-history", operation="resume_tailor", input_fingerprint="a" * 64,
                            snapshot={"route": selected}, created_at=now, expires_at=now)
    assert service_quotes.quote_view(quote)["quote_id"] == quote.id
    assert "output_contract_sha256" not in quote.snapshot["route"]
