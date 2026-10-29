"""Synthetic real-SQL fixtures for canary controls; never production evidence."""
from __future__ import annotations

import hashlib
from datetime import timedelta
from types import SimpleNamespace

import pytest
from backend.app import models
from backend.app.domains.common import payload_fingerprint
from backend.app.domains.credits import canary, finance, runtime
from backend.tests.test_unified_credits import NOW, factory
from backend.tests.test_unified_execution import budgets, financial_policy, route
from fastapi import HTTPException
from pydantic import ValidationError

assert factory


def baseline(db, *, start=0, count=19, duplicate=False, shadow=False, internal=False, owner=40):
    for number in range(start, start + count):
        db.add(models.AnalysisRun(id=f"{'dev_' if internal else 'run_'}baseline_{number}", user_id=owner,
            operation="skill_roi", status="succeeded", idempotency_key=f"baseline-key-{number}",
            input_fingerprint=payload_fingerprint("duplicate" if duplicate else number),
            input_payload={"schema_version": "synthetic-interview-development-v1"} if internal else {"shadow": shadow},
            estimated_units=0, generation_attempt_limit=0, created_at=NOW - timedelta(hours=2)))
    db.flush()


def prepare(db, monkeypatch, *, selected=(1, 2), now=NOW, config=None, review_id="fixture-canary-v1", population=40):
    # These are simulated genuine accounts in an isolated test DB; no fixture
    # review, invoice or consent is represented as real production evidence.
    for identity in range(1, population + 1):
        user = db.get(models.User, identity)
        if user is None:
            user = models.User(id=identity, email=f"fixture-business-{identity}@example.com")
            db.add(user)
        user.terms_accepted_at = user.age_confirmed_at = now - timedelta(days=4)
        user.terms_version = user.privacy_version = "fixture-notice"
    db.flush()
    config = config or runtime.RuntimeConfig(policy=financial_policy(), budgets=budgets(), routes=(route(),))
    notice = "Participate voluntarily in the bounded reviewed canary. Review quoted prices; you may stop new canary work at any time."
    review = canary.CanaryReview(id=review_id, runtime_sha256=payload_fingerprint(config.model_dump(mode="json")),
        notice=notice, notice_sha256=hashlib.sha256(notice.encode()).hexdigest(), reviewed_by="fixture-operator@example.com",
        review_evidence_sha256="a" * 64, real_business_accounts_and_exclusions_reviewed=True,
        census_at=now - timedelta(days=1), valid_from=now - timedelta(days=1),
        expires_at=min(now + timedelta(days=10), config.policy.expires_at, config.budgets.expires_at),
        traffic_started_at=now - timedelta(days=2),
        members=canary.census(db, census_at=now - timedelta(days=1), excluded_owner_ids=()),
        selected_owner_ids=selected, excluded_owner_ids=(), families=tuple(sorted(canary.FAMILIES)))
    digest = canary.save_review(db, review, runtime=config, now=now)
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_AUDIENCE", "canary")
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", config.model_dump_json())
    monkeypatch.setenv("UNIFIED_CREDIT_CANARY_REVIEW_ID", review.id)
    monkeypatch.setenv("UNIFIED_CREDIT_CANARY_REVIEW_SHA256", digest)
    return config, review, digest


def consent(db, config, review, digest, *, owner=1, choice="opt_in", key="explicit-owner-choice"):
    return canary.choose(db, user_id=owner, review_id=review.id, review_sha256=digest,
        notice_sha256=review.notice_sha256, choice=choice, idempotency_key=key, runtime=config, now=NOW)


def source(db, *, identity="run_candidate_1", owner=1, fingerprint=None):
    row = models.AnalysisRun(id=identity, user_id=owner, operation="resume_tailor", status="queued",
        idempotency_key=identity, input_fingerprint=fingerprint or payload_fingerprint(identity),
        input_payload={}, created_at=NOW, generation_attempt_limit=1)
    db.add(row)
    db.flush()
    return row


def accept(db, config, row):
    return canary.accept(db, user_id=row.user_id, runtime=config, operation=row.operation,
        source_id=row.id, input_fingerprint=row.input_fingerprint, now=NOW)


@pytest.fixture
def realm(factory, monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    with factory() as db:
        config, review, digest = prepare(db, monkeypatch)
        db.commit()
    return factory, config, review, digest


def test_raw_unbounded_env_ids_never_grant_audience_or_fabricate_consent(realm, monkeypatch):
    maker, config, review, digest = realm
    monkeypatch.setenv("UNIFIED_CREDIT_CANARY_OWNER_IDS", ",".join(map(str, range(1, 1001))))
    with maker() as db:
        assert not runtime.enabled_for(1, db=db, now=NOW)
        assert not runtime.enabled_for(40, db=db, now=NOW)
        assert runtime.requires_unified_billing(db, 1)
        consent(db, config, review, digest)
        db.commit()
        assert runtime.enabled_for(1, db=db, now=NOW)
        assert not runtime.enabled_for(40, db=db, now=NOW)
        monkeypatch.delenv("UNIFIED_CREDIT_CANARY_REVIEW_SHA256")
        assert not runtime.enabled_for(1, db=db, now=NOW)
        assert runtime.requires_unified_billing(db, 40)  # No currency fallback on unverifiable configuration.


def test_selected_cohort_requires_a_bounded_real_subset_and_immutable_review(realm):
    maker, config, review, _ = realm
    with pytest.raises(ValidationError):
        canary.CanaryReview.model_validate(review.model_dump() | {"selected_owner_ids": (1, 2, 41)})
    with pytest.raises(ValidationError):
        canary.CanaryReview.model_validate(review.model_dump() | {"selected_owner_ids": tuple(range(1, 102))})
    with pytest.raises(ValidationError):
        canary.CanaryReview.model_validate(review.model_dump() | {"real_business_accounts_and_exclusions_reviewed": 1})
    with maker() as db:
        with pytest.raises(finance.FundingUnavailable):
            canary.save_review(db, review.model_copy(update={"notice": review.notice + " changed"}), runtime=config, now=NOW)
        db.rollback()
        assert db.query(models.AdminAuditEvent).filter_by(action=canary.REVIEW_ACTION).count() == 1


@pytest.mark.parametrize("genuine_baseline", [0, 4, 18, 19])
def test_small_real_population_is_limited_by_actual_traffic_not_account_fraction(factory, monkeypatch, genuine_baseline):
    monkeypatch.setenv("APP_ENV", "test")
    with factory() as db:
        config, review, digest = prepare(db, monkeypatch, selected=(1,), population=1)
        assert len(review.members) == len(review.selected_owner_ids) == 1
        consent(db, config, review, digest)
        db.commit()
        baseline(db, count=genuine_baseline, owner=1)
        row = source(db)
        if genuine_baseline < 19:
            with pytest.raises(finance.FundingUnavailable):
                accept(db, config, row)
            db.rollback()
            assert db.query(models.AdminAuditEvent).filter_by(action=canary.ACCEPT_ACTION).count() == 0
        else:
            proof = accept(db, config, row)
            assert (proof["canary_acceptances"], proof["eligible_business_acceptances"]) == (1, 20)
            db.commit()
            with pytest.raises(finance.FundingUnavailable):
                accept(db, config, source(db, identity="run_small_population_second"))
            db.rollback()
            assert db.query(models.AdminAuditEvent).filter_by(action=canary.ACCEPT_ACTION).count() == 1


def test_explicit_owner_choice_is_idempotent_revocable_and_not_reenabled_by_replay(realm):
    maker, config, review, digest = realm
    with maker() as db:
        first = consent(db, config, review, digest)
        assert consent(db, config, review, digest) == first
        db.commit()
        assert canary.require_participation(db, user_id=1, runtime=config, now=NOW)["consent_sequence"] == 1
        consent(db, config, review, digest, choice="revoke", key="explicit-revocation")
        db.commit()
        assert consent(db, config, review, digest) == first
        assert not runtime.enabled_for(1, db=db, now=NOW)
        assert db.query(models.AdminAuditEvent).filter_by(action=canary.CONSENT_ACTION).count() == 2
        with pytest.raises(finance.FundingUnavailable):
            consent(db, config, review, digest, owner=3)


@pytest.mark.parametrize("change", ["account", "runtime", "output_contract", "expired", "notice", "missing_record"])
def test_new_quote_participation_rechecks_actual_census_consent_runtime_and_expiry(realm, change, monkeypatch):
    maker, config, review, digest = realm
    with maker() as db:
        consent(db, config, review, digest)
        db.commit()
        selected = config
        now = NOW
        if change == "account":
            db.get(models.User, 40).privacy_version = "changed"
            db.commit()
        elif change == "runtime":
            selected = config.model_copy(update={"budgets": config.budgets.model_copy(update={"version": "changed"})})
        elif change == "output_contract":
            from backend.app.services import personal_claims
            monkeypatch.setattr(personal_claims, "VERSION", "changed-unreviewed-contract")
        elif change == "expired":
            now = review.expires_at
        elif change == "notice":
            with pytest.raises(finance.FundingUnavailable):
                canary.choose(db, user_id=1, review_id=review.id, review_sha256=digest,
                    notice_sha256="f" * 64, choice="opt_in", idempotency_key="different-notice", runtime=config, now=NOW)
            return
        else:
            db.query(models.AdminAuditEvent).filter_by(action=canary.CONSENT_ACTION).delete()
            db.commit()
        with pytest.raises(finance.FundingUnavailable):
            canary.require_participation(db, user_id=1, runtime=selected, now=now)


def test_nineteen_genuine_basic_acceptances_enable_one_canary_and_not_a_second(realm):
    maker, config, review, digest = realm
    with maker() as db:
        consent(db, config, review, digest)
        baseline(db)
        row = source(db)
        proof = accept(db, config, row)
        assert (proof["canary_acceptances"], proof["eligible_business_acceptances"]) == (1, 20)
        db.commit()
        assert accept(db, config, row) == proof
        second = source(db, identity="run_candidate_2")
        with pytest.raises(finance.FundingUnavailable):
            accept(db, config, second)
        db.rollback()
        assert db.query(models.AdminAuditEvent).filter_by(action=canary.ACCEPT_ACTION).count() == 1
        baseline(db, start=19, count=19)
        second = source(db, identity="run_candidate_2")
        assert accept(db, config, second)["eligible_business_acceptances"] == 40


@pytest.mark.parametrize("invalid", ["duplicates", "shadow", "internal", "other_owner", "outside_window", "reads_only"])
def test_forecast_synthetic_shadow_duplicate_and_unrelated_activity_never_inflate_denominator(realm, invalid):
    maker, config, review, digest = realm
    with maker() as db:
        consent(db, config, review, digest)
        if invalid != "reads_only":
            baseline(db, duplicate=invalid == "duplicates", shadow=invalid == "shadow", internal=invalid == "internal")
            if invalid == "other_owner":
                db.add(models.User(id=99, email="unreviewed@example.com"))
                db.flush()
                db.query(models.AnalysisRun).update({models.AnalysisRun.user_id: 99})
            if invalid == "outside_window":
                db.query(models.AnalysisRun).update({models.AnalysisRun.created_at: NOW - timedelta(days=3)})
        else:
            for _ in range(25):
                canary.enrollment(db, user_id=1, runtime=config, now=NOW)
        with pytest.raises(finance.FundingUnavailable):
            accept(db, config, source(db))
        db.rollback()
        assert db.query(models.AdminAuditEvent).filter_by(action=canary.ACCEPT_ACTION).count() == 0


def test_a_transaction_failure_does_not_consume_traffic_and_accepted_work_survives_optout(realm, monkeypatch):
    maker, config, review, digest = realm
    with maker() as db:
        consent(db, config, review, digest)
        baseline(db)
        db.commit()
        row = source(db)
        accept(db, config, row)
        db.rollback()
        assert db.query(models.AdminAuditEvent).filter_by(action=canary.ACCEPT_ACTION).count() == 0
        row = source(db)
        proof = accept(db, config, row)
        db.commit()
        consent(db, config, review, digest, choice="revoke", key="revoke-after-accept")
        db.commit()
        monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
        assert not runtime.enabled_for(1, db=db, now=NOW)
        from backend.app.domains.credits.models import CreditReservation
        reservation = CreditReservation(id="fixture-reservation", user_id=1, idempotency_key="fixture-reserve",
            operation="resume_tailor", source_id=row.id, input_fingerprint=row.input_fingerprint,
            quote_snapshot={}, execution_allowance_minor=1, reserved_credits=8, created_at=NOW)
        db.add(reservation)
        db.flush()
        hold = SimpleNamespace(snapshot={"canary": proof}, user_id=1, reservation_id=reservation.id)
        canary.require_accepted_hold(db, hold)
        reservation.source_id = "another-source"
        with pytest.raises(finance.FundingUnavailable):
            canary.require_accepted_hold(db, hold)


def test_new_legacy_provider_preflight_is_denied_but_old_accepted_work_remains(realm, monkeypatch):
    from backend.app.services.generation_budget import (
        GenerationBudgetExhausted,
        persistent_run_budget,
    )
    from backend.app.services.guardrails import billable_operation

    maker, _, _, _ = realm
    monkeypatch.setenv("OPTIONAL_AI_GENERATION_ENABLED", "true")
    with maker() as db:
        with pytest.raises(HTTPException):
            with billable_operation(db=db, user_id=40, operation="rewrite_bullets", amount=1):
                pytest.fail("New outside-audience legacy generation must never start")
        row = source(db, owner=40)
        row.generation_attempt_limit = 1
        db.commit()
        identity = row.id
    with persistent_run_budget(maker, identity) as budget:
        with pytest.raises(GenerationBudgetExhausted):
            budget.preflight("google", "synthetic-model", [{"content": "never sent"}], "https://generativelanguage.googleapis.com")
    with maker() as db:
        row = db.get(models.AnalysisRun, identity)
        assert db.query(models.ModelCallEvent).count() == db.query(models.ModelCostLiability).count() == 0
        row.created_at = NOW - timedelta(days=2)
        canary.require_accepted_provider_run(db, row)


@pytest.mark.parametrize("pause", ["active", "cleared", "invalid_public"])
def test_mixed_legacy_path_cannot_send_gemini_count_or_generation_outside_canary(realm, monkeypatch, pause):
    from backend.app.services import llm_client
    from backend.app.services.generation_budget import persistent_run_budget
    from google import genai

    maker, _, _, _ = realm
    if pause != "active":
        monkeypatch.delenv("UNIFIED_CREDIT_CANARY_REVIEW_ID")
        monkeypatch.delenv("UNIFIED_CREDIT_CANARY_REVIEW_SHA256")
        monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
        if pause == "invalid_public":
            monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_AUDIENCE", "reviewed_public")
    monkeypatch.setenv("OPTIONAL_AI_GENERATION_ENABLED", "true")
    monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.6-flash")
    monkeypatch.setattr(llm_client, "_api_key", lambda: "synthetic-never-sent")
    monkeypatch.setattr(llm_client, "count_gemini_input", lambda *_: pytest.fail("No token-count prompt may leave"))
    monkeypatch.setattr(genai, "Client", lambda **_: pytest.fail("No generation request may leave"))
    with maker() as db:
        # Mixed market paths may discover they need generation only inside the
        # helper. The durable provider boundary must still reject them first.
        row = source(db, owner=40)
        row.operation = "market_report"
        db.commit()
        identity = row.id
    with persistent_run_budget(maker, identity):
        with pytest.raises(llm_client.LLMProviderError) as caught:
            llm_client._chat([{"role": "user", "content": "Synthetic test content"}])
        assert caught.value.budget_exhausted is True
    with maker() as db:
        assert db.query(models.ModelCallEvent).count() == db.query(models.ModelCostLiability).count() == 0


def test_development_labels_never_authorize_a_production_or_unspecified_runner(realm, monkeypatch):
    maker, _, _, _ = realm
    monkeypatch.setenv("APP_ENV", "production")
    with maker() as db:
        row = source(db, identity="dev_forged_label", owner=40)
        row.input_payload = {"schema_version": "synthetic-interview-development-v1"}
        with pytest.raises(finance.FundingUnavailable, match="validated local runner"):
            canary.require_accepted_provider_run(db, row)
        assert db.query(models.ModelCallEvent).count() == db.query(models.ModelCostLiability).count() == 0



def test_many_owner_choices_never_make_revocation_unavailable(realm):
    maker, config, review, digest = realm
    with maker() as db:
        for number in range(130):
            consent(db, config, review, digest, key=f"distinct-explicit-choice-{number}")
        revoked = consent(db, config, review, digest, choice="revoke", key="final-explicit-stop")
        assert revoked["sequence"] == 131
        assert canary.enrollment(db, user_id=1, runtime=config, now=NOW)["status"] == "NOT_OPTED_IN"
        with pytest.raises(finance.FundingUnavailable):
            canary.require_participation(db, user_id=1, runtime=config, now=NOW)


def test_cleared_or_invalid_public_configuration_never_erases_rollout_currency_or_model_fence(realm, monkeypatch):
    from backend.app.services.guardrails import billable_operation

    maker, config, review, digest = realm
    with maker() as db:
        consent(db, config, review, digest)
        db.commit()
    monkeypatch.delenv("UNIFIED_CREDIT_CANARY_REVIEW_ID")
    monkeypatch.delenv("UNIFIED_CREDIT_CANARY_REVIEW_SHA256")
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
    for audience in ("canary", "reviewed_public"):
        monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_AUDIENCE", audience)
        with maker() as db:
            assert canary.requested(db)
            assert runtime.has_unified_contract(db, 1) and not runtime.has_unified_contract(db, 40)
            assert runtime.requires_unified_billing(db, 1)
            with pytest.raises(HTTPException):
                with billable_operation(db=db, user_id=40, operation="rewrite_bullets", amount=1):
                    pytest.fail("Missing rollout configuration cannot reopen legacy generation")
            assert db.query(models.ModelCallEvent).count() == db.query(models.ModelCostLiability).count() == 0
            old = source(db, owner=40, identity="legacy-before-first-canary")
            old.created_at = review.valid_from - timedelta(seconds=1)
            canary.require_accepted_provider_run(db, old)
            db.rollback()
