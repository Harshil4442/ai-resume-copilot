"""Owned synthetic consistency controls, never native/provider qualification."""
from __future__ import annotations

import socket
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.domains.notification_consent.contracts import (
    MAX_GENERATION,
    MAX_SEND_WINDOW_MS,
    AccountBinding,
    AccountFacts,
    ConsentChange,
    ConsentHead,
    CredentialRevision,
    DigestDraft,
    DigestEnvelope,
    MailCapacityFacts,
    draft_consent_change,
    inspect_digest_candidate,
)

NOW = int(datetime(2026, 11, 5, 12, tzinfo=UTC).timestamp() * 1000)
BINDING = AccountBinding(11, "11111111-1111-4111-8111-111111111111",
                         "22222222-2222-4222-8222-222222222222", "a" * 64)
REVISION = CredentialRevision(BINDING, 1, "b" * 64)
FACTS = AccountFacts(REVISION, REVISION, "c" * 64, "c" * 64, True, True, False, True)
GRANTED = ConsentHead(BINDING, 1, "c" * 64, True, "digest-v1", None)
CAPACITY = MailCapacityFacts(100, 1000, 30, 300, 21, 210)  # Synthetic facts, not runtime policy.
CHANGE = ConsentChange(BINDING, 0, "c" * 64, True, "digest-v1", True)
ENVELOPE = DigestEnvelope(BINDING, 1, GRANTED.sha256, "c" * 64, "2026-W45",
                          "d" * 64, NOW - 1000, NOW + 60_000)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Pure F07 contracts forbid network and provider IO")
    for target, name in ((socket, "create_connection"), (socket, "getaddrinfo"),
                         (socket.socket, "connect"), (socket.socket, "connect_ex")):
        monkeypatch.setattr(target, name, denied)


def inspect(head=GRANTED, projection=GRANTED, envelope=ENVELOPE, facts=FACTS, **kwargs):
    return inspect_digest_candidate(head, projection, envelope, facts, now_ms=NOW,
                                    period_already_begun=False, capacity=CAPACITY, **kwargs)


def test_explicit_first_opt_in_is_only_a_draft_without_runtime_authority():
    result = draft_consent_change(None, CHANGE, FACTS)
    assert result.proposed == GRANTED and result.reason == "candidate_transition_only"
    assert result.runtime_authority == "UNAVAILABLE"
    candidate = inspect()
    assert candidate.candidate_consistent and candidate.runtime_authority == "UNAVAILABLE"
    with pytest.raises(TypeError):
        DigestDraft("forged", runtime_authority="ALLOW")


@pytest.mark.parametrize("facts,reason", [
    (None, "account_facts_missing"),
    (replace(FACTS, closure_open=False), "account_closed_or_denied"),
    (replace(FACTS, subject_active=False), "account_closed_or_denied"),
    (replace(FACTS, pending_denial=True), "account_closed_or_denied"),
    (replace(FACTS, session_current=False), "current_session_required"),
    (replace(FACTS, sql_revision=replace(REVISION, auth_generation=2)), "credential_projection_mismatch"),
    (replace(FACTS, current_email_sha256="e" * 64), "mailbox_mismatch"),
])
def test_preference_change_needs_fresh_account_session_mailbox_and_credential_facts(facts, reason):
    result = draft_consent_change(None, CHANGE, facts)
    assert result.reason == reason and result.proposed is None
    assert result.runtime_authority == "UNAVAILABLE"


@pytest.mark.parametrize("enabled", [False, True])
def test_consent_is_never_implicitly_adopted_from_a_sql_flag(enabled):
    result = draft_consent_change(None, replace(CHANGE, weekly_digest=enabled, explicit_confirmation=False), FACTS)
    assert result.proposed is None and result.reason == "explicit_confirmation_required"


@pytest.mark.parametrize("binding", [
    replace(BINDING, candidate_id=12),
    replace(BINDING, subject_uuid="33333333-3333-4333-8333-333333333333"),
    replace(BINDING, account_binding_id="44444444-4444-4444-8444-444444444444"),
    replace(BINDING, principal_sha256="f" * 64),
])
def test_another_owner_or_reused_numeric_identity_cannot_retarget_consent(binding):
    proposal = draft_consent_change(None, replace(CHANGE, binding=binding), FACTS)
    assert proposal.proposed is None and proposal.reason == "owner_binding_mismatch"
    assert inspect(envelope=replace(ENVELOPE, binding=binding)).reason == "owner_binding_mismatch"


def test_stale_preference_cas_and_restored_sql_opt_in_do_not_revive_consent():
    stale = draft_consent_change(GRANTED, CHANGE, FACTS)
    assert stale.proposed is None and stale.reason == "consent_generation_mismatch"
    revoked = draft_consent_change(GRANTED, replace(CHANGE, expected_generation=1, weekly_digest=False), FACTS).proposed
    assert revoked is not None and revoked.generation == 2 and not revoked.weekly_digest
    assert revoked.previous_sha256 == GRANTED.sha256
    assert inspect(head=revoked, projection=GRANTED).reason == "consent_projection_mismatch"
    assert inspect(head=revoked, projection=revoked).reason == "digest_opted_out"


def test_reconsent_does_not_reauthorize_old_queue_or_mint_a_second_weekly_slot():
    revoked = draft_consent_change(GRANTED, replace(CHANGE, expected_generation=1, weekly_digest=False), FACTS).proposed
    assert revoked is not None
    renewed = draft_consent_change(revoked, replace(CHANGE, expected_generation=2), FACTS).proposed
    assert renewed is not None and renewed.generation == 3
    assert inspect(head=renewed, projection=renewed).reason == "queued_consent_superseded"
    new_envelope = replace(ENVELOPE, consent_generation=3, consent_sha256=renewed.sha256)
    assert new_envelope.cadence_key == ENVELOPE.cadence_key
    begun = inspect_digest_candidate(renewed, renewed, new_envelope, FACTS, now_ms=NOW,
                                     period_already_begun=True, capacity=CAPACITY)
    assert begun.reason == "period_send_already_begun" and not begun.candidate_consistent


def test_unknown_send_keeps_cadence_consumed_even_if_sql_attempt_counters_restore():
    result = inspect_digest_candidate(GRANTED, GRANTED, ENVELOPE, FACTS, now_ms=NOW,
                                      period_already_begun=True, capacity=CAPACITY)
    assert result.reason == "period_send_already_begun" and result.runtime_authority == "UNAVAILABLE"


def test_missing_consent_or_projection_and_known_native_history_rollback_refuse():
    assert inspect(head=None).reason == "consent_missing"
    assert inspect(projection=None).reason == "consent_missing"
    assert inspect(native_later_generation_present=True).reason == "consent_projection_mismatch"
    change = draft_consent_change(GRANTED, replace(CHANGE, expected_generation=1), FACTS,
                                  native_later_generation_present=True)
    assert change.proposed is None and change.reason == "consent_history_rolled_back"


def test_sql_account_restore_cannot_bypass_native_generation_or_deletion():
    native_new = replace(REVISION, auth_generation=2, credential_sha256="e" * 64)
    assert inspect(facts=replace(FACTS, native_revision=native_new)).reason == "credential_projection_mismatch"
    assert inspect(facts=replace(FACTS, subject_active=False)).reason == "account_closed_or_denied"
    # Genuine credential rotation preserves independent email consent, but is
    # only logically consistent after BOTH native and SQL revisions advance.
    candidate = inspect(facts=replace(FACTS, native_revision=native_new, sql_revision=native_new))
    assert candidate.candidate_consistent and candidate.runtime_authority == "UNAVAILABLE"


def test_old_recipient_refuses_after_sql_only_or_verified_mailbox_change():
    assert inspect(facts=replace(FACTS, current_email_sha256="e" * 64)).reason == "mailbox_mismatch"
    changed_mailbox = replace(FACTS, current_email_sha256="e" * 64, native_mailbox_sha256="e" * 64)
    assert inspect(facts=changed_mailbox).reason == "recipient_mismatch"
    assert draft_consent_change(None, CHANGE, changed_mailbox).reason == "recipient_mismatch"
    # Cadence is deliberately independent of recipient and message/preference edits.
    assert replace(ENVELOPE, recipient_sha256="e" * 64, message_sha256="f" * 64).cadence_key == ENVELOPE.cadence_key


def test_background_candidate_does_not_require_a_retained_browser_session():
    value = inspect(facts=replace(FACTS, session_current=False))
    assert value.candidate_consistent and value.runtime_authority == "UNAVAILABLE"


@pytest.mark.parametrize("envelope,reason", [
    (replace(ENVELOPE, created_at_ms=NOW + 1), "send_window_expired"),
    (replace(ENVELOPE, expires_at_ms=NOW), "send_window_expired"),
    (replace(ENVELOPE, iso_week="2026-W44"), "delivery_week_mismatch"),
    (replace(ENVELOPE, consent_generation=2), "queued_consent_superseded"),
    (replace(ENVELOPE, consent_sha256="e" * 64), "queued_consent_superseded"),
    (replace(ENVELOPE, recipient_sha256="e" * 64), "recipient_mismatch"),
])
def test_delivery_window_period_and_exact_queued_consent_are_bound(envelope, reason):
    result = inspect(envelope=envelope)
    assert result.reason == reason and not result.candidate_consistent


@pytest.mark.parametrize("changes", [
    {"weekly_digest": 1}, {"expected_generation": True}, {"expected_generation": -1},
    {"expected_generation": MAX_GENERATION}, {"recipient_sha256": "C" * 64},
    {"policy_version": ""}, {"explicit_confirmation": "true"},
])
def test_malformed_or_overflowing_changes_are_not_normalized_into_consent(changes):
    with pytest.raises(ValueError):
        replace(CHANGE, **changes)


@pytest.mark.parametrize("changes", [
    {"consent_generation": True}, {"iso_week": "2026-W00"}, {"iso_week": "2026-W99"},
    {"created_at_ms": True}, {"expires_at_ms": NOW - 2000},
    {"expires_at_ms": ENVELOPE.created_at_ms + MAX_SEND_WINDOW_MS + 1},
])
def test_invalid_queue_contract_refuses_before_any_runtime_boundary(changes):
    with pytest.raises(ValueError):
        replace(ENVELOPE, **changes)


@pytest.mark.parametrize("capacity,reason", [
    (None, "mail_capacity_facts_missing"),
    (replace(CAPACITY, security_day_headroom=0), "security_headroom_missing"),
    (replace(CAPACITY, security_day_headroom=1), "security_headroom_missing"),
    (replace(CAPACITY, security_day_headroom=2), "security_headroom_missing"),
    (replace(CAPACITY, security_month_headroom=1), "security_headroom_missing"),
    (replace(CAPACITY, security_month_headroom=2), "security_headroom_missing"),
    (replace(CAPACITY, day_units_available=32), "optional_mail_capacity_unavailable"),
    (replace(CAPACITY, month_units_available=302), "optional_mail_capacity_unavailable"),
    (replace(CAPACITY, optional_day_units_left=2), "optional_mail_capacity_unavailable"),
    (replace(CAPACITY, optional_month_units_left=2), "optional_mail_capacity_unavailable"),
])
def test_optional_digest_cannot_consume_security_headroom_or_skip_category_capacity(capacity, reason):
    result = inspect_digest_candidate(GRANTED, GRANTED, ENVELOPE, FACTS, now_ms=NOW,
                                      period_already_begun=False, capacity=capacity)
    assert result.reason == reason and not result.candidate_consistent
    assert result.runtime_authority == "UNAVAILABLE"
