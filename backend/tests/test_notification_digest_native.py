"""Actual configured authority/SDK transactions over owned intercepted transports.

Synthetic operator baselines only; account and session provenance comes from
real enrollment/session ACKs. No emulator, provider, IAM or durability evidence.
"""
from __future__ import annotations

import copy
from datetime import UTC, datetime
from itertools import count
from uuid import uuid4

import pytest
from test_candidate_production_factory import NOW, PASSWORD, register
from test_candidate_production_factory import configured as configured

from app.domains.candidate_accounts.contracts import CandidateAccountIdentity
from app.domains.candidate_accounts.production import production_candidate_accounts
from app.domains.candidate_accounts.service import credential_digest, revision_from_row
from app.domains.notification_consent.unsubscribe import UnsubscribeTokens
from app.domains.notifications.mail_budget import MailBudgetError, ProviderResult
from app.domains.notifications.native import ConfiguredDigestNative
from app.domains.recovery.gcp_notification_digest import BEGINS, HEADS, HISTORY, OUTCOMES, iso_week
from app.domains.recovery.gcp_scoped_census import validate_scoped_rows
from app.domains.recovery.store import GuardDenied, GuardUnavailable
from app.models import CandidatePasswordAccount, User

EMAIL = credential_digest("synthetic@example.test")
POLICY = "weekly-digest-v1"
MESSAGE = "c" * 64


def account(c, monkeypatch):
    monkeypatch.setenv("NOTIFICATION_DIGESTS_ENABLED", "true")
    monkeypatch.setenv("LIFECYCLE_EMAILS_ENABLED", "true")
    # Account setup uses intercepted in-memory SDKs. Runner scheduling delay
    # is not their transport latency. Digest behavior uses the original clocks.
    composition = c.accounts._composition()
    ticks = count()
    with monkeypatch.context() as setup_clock:
        for registry in (composition.lifetime.registry, composition.scoped.publication.registry):
            assert registry.deadline_seconds == 5.0 and registry.max_attempts == 3
            setup_clock.setattr(registry, "monotonic", lambda: next(ticks) / 1000)
        uid = register(c)
        login = c.accounts.login(email="synthetic@example.test", password=PASSWORD)
    with c.accounts.sessions() as db:
        revision = revision_from_row(db.get(CandidatePasswordAccount, uid), db.get(User, uid).password_hash)
    native = ConfiguredDigestNative(c.accounts)
    return native, login.context, revision


def opt_in(c, monkeypatch):
    native, context, revision = account(c, monkeypatch)
    head = native.change(context, revision, expected_generation=0, enabled=True,
                         email_sha256=EMAIL, policy_version=POLICY)
    return native, context, revision, head


def send(native, revision, callback, **changes):
    kwargs = dict(email_sha256=EMAIL, consent_generation=1, policy_version=POLICY,
                  message_sha256=MESSAGE, callback=callback, before_retry=lambda receipt: None)
    kwargs.update(changes)
    return native.execute_once(revision, **kwargs)


def result(kind="sent"):
    return ProviderResult(kind, "d" * 64, {"sent": 200, "known_retryable": 429,
                                          "unknown": None, "permanent": 400}[kind])


def test_default_off_never_constructs_native_clients(configured, monkeypatch):
    monkeypatch.delenv("NOTIFICATION_DIGESTS_ENABLED", raising=False)
    with pytest.raises(GuardUnavailable, match="disabled"):
        ConfiguredDigestNative(configured.accounts)
    for client in configured.clients:
        client.assert_not_called()


def test_consent_native_history_generation_and_full_census(configured, monkeypatch):
    c = configured
    native, context, revision = account(c, monkeypatch)
    assert native.read_current(revision, EMAIL) is None
    first = native.change(context, revision, expected_generation=0, enabled=True,
                          email_sha256=EMAIL, policy_version=POLICY)
    second = native.change(context, revision, expected_generation=1, enabled=False,
                           email_sha256=EMAIL, policy_version=POLICY)
    assert (first.generation, first.enabled, second.generation, second.enabled) == (1, True, 2, False)
    assert native.read_current(revision, EMAIL) == second
    validate_scoped_rows(c.native.values())
    with pytest.raises(GuardDenied, match="generation"):
        native.change(context, revision, expected_generation=1, enabled=True,
                      email_sha256=EMAIL, policy_version=POLICY)


@pytest.mark.parametrize("field", ["mailbox", "credential", "session"])
def test_preference_requires_current_native_session_mailbox_and_revision(configured, monkeypatch, field):
    c = configured
    native, context, revision = account(c, monkeypatch)
    if field == "mailbox":
        c.native.remove(c.protected_registry, "v3_recovery_mailboxes", str(revision.subject_uuid))
    elif field == "credential":
        revision = revision.model_copy(update={"credential_sha256": "9" * 64})
    else:
        c.accounts.logout(context)
    with pytest.raises((GuardDenied, GuardUnavailable)):
        native.change(context, revision, expected_generation=0, enabled=True,
                      email_sha256=EMAIL, policy_version=POLICY)
    assert not any(namespace == HISTORY for namespace, _ in c.native.values())


@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_consent_commit_never_returns_success(configured, monkeypatch, persisted):
    c = configured
    native, context, revision = account(c, monkeypatch)
    c.native.fault = lambda names: ("persist_loss" if persisted else "no_persist_loss") if HISTORY in names else None
    with pytest.raises(GuardUnavailable, match="unknown"):
        native.change(context, revision, expected_generation=0, enabled=True,
                      email_sha256=EMAIL, policy_version=POLICY)
    c.native.fault = None
    assert ((HEADS, str(revision.subject_uuid)) in c.native.values()) is persisted
    validate_scoped_rows(c.native.values())


def test_sent_callback_after_all_gates_no_sql_native_locks_and_no_week_reconstruction(configured, monkeypatch):
    c = configured
    native, _, revision, _ = opt_in(c, monkeypatch)
    calls = []
    def callback():
        assert not c.sql_active and not c.native.views
        calls.append(1)
        return result()
    assert send(native, revision, callback) == result()
    assert len(calls) == 1
    validate_scoped_rows(c.native.values())
    cold = ConfiguredDigestNative(production_candidate_accounts())
    with pytest.raises(GuardDenied):
        send(cold, revision, callback)
    assert len(calls) == 1


@pytest.mark.parametrize("responses,expected", [(["known_retryable", "sent"], 2),
    (["known_retryable"] * 3, 3), (["unknown"], 1), (["permanent"], 1)])
def test_internal_retry_only_exact_known429_with_three_attempt_bound(configured, monkeypatch, responses, expected):
    c = configured
    native, _, revision, _ = opt_in(c, monkeypatch)
    calls = []
    def callback():
        calls.append(1)
        return result(responses[len(calls)-1])
    receipt = send(native, revision, callback)
    assert len(calls) == expected and receipt.outcome == responses[-1]
    values = c.native.values()
    assert sum(namespace == BEGINS for namespace, _ in values) == expected
    assert sum(namespace == OUTCOMES for namespace, _ in values) == expected
    validate_scoped_rows(values)
    with pytest.raises(GuardDenied):
        send(ConfiguredDigestNative(c.accounts), revision, callback)
    assert len(calls) == expected


@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_begin_never_calls_callback_or_reuses_live_chain(configured, monkeypatch, persisted):
    c = configured
    native, _, revision, _ = opt_in(c, monkeypatch)
    calls = []
    c.native.fault = lambda names: ("persist_loss" if persisted else "no_persist_loss") if BEGINS in names else None
    with pytest.raises(GuardUnavailable):
        send(native, revision, lambda: calls.append(1) or result())
    c.native.fault = None
    with pytest.raises(GuardDenied):
        send(native, revision, lambda: calls.append(1) or result())
    assert calls == []
    assert sum(namespace == BEGINS for namespace, _ in c.native.values()) == int(persisted)


@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_outcome_commit_no_retry_or_cold_ack_adoption(configured, monkeypatch, persisted):
    c = configured
    native, _, revision, _ = opt_in(c, monkeypatch)
    calls = []
    c.native.fault = lambda names: ("persist_loss" if persisted else "no_persist_loss") if OUTCOMES in names else None
    with pytest.raises(GuardUnavailable):
        send(native, revision, lambda: calls.append(1) or result("known_retryable"))
    c.native.fault = None
    with pytest.raises(GuardDenied):
        send(ConfiguredDigestNative(c.accounts), revision, lambda: calls.append(1) or result())
    assert calls == [1]
    validate_scoped_rows(c.native.values())


def test_source_generation_recipient_and_message_cannot_make_new_weekly_slot(configured, monkeypatch):
    c = configured
    native, context, revision, _ = opt_in(c, monkeypatch)
    calls = []
    send(native, revision, lambda: calls.append(1) or result("unknown"))
    native.change(context, revision, expected_generation=1, enabled=True,
                  email_sha256=EMAIL, policy_version=POLICY)
    with pytest.raises(GuardDenied):
        send(ConfiguredDigestNative(c.accounts), revision, lambda: calls.append(1) or result(),
             consent_generation=2, message_sha256="e" * 64)
    assert calls == [1]


def test_callback_flag_loss_withholds_outcome_and_retry(configured, monkeypatch):
    c = configured
    native, _, revision, _ = opt_in(c, monkeypatch)
    calls = []
    def callback():
        calls.append(1)
        monkeypatch.setenv("NOTIFICATION_DIGESTS_ENABLED", "false")
        return result("known_retryable")
    with pytest.raises(GuardUnavailable):
        send(native, revision, callback)
    assert calls == [1] and not any(namespace == OUTCOMES for namespace, _ in c.native.values())


def test_signed_email_disable_requires_exact_generation_and_never_session_or_enable(configured, monkeypatch):
    c = configured
    native, context, revision, head = opt_in(c, monkeypatch)
    identity = CandidateAccountIdentity.model_validate(revision.model_dump(
        exclude={"auth_generation", "credential_sha256"}))
    tokens = UnsubscribeTokens(b"owned-synthetic-secret-no-provider" * 2)
    token = tokens.issue(identity, email_sha256=EMAIL, expected_generation=head.generation,
                         policy_version=POLICY, now_ms=NOW)
    proof = tokens.verify(token, now_ms=NOW)
    # No current browser session is required for an explicit disable-only link.
    c.accounts.logout(context)
    disabled = native.disable_verified_email(proof, revision)
    assert disabled.generation == 2 and disabled.enabled is False
    with pytest.raises(GuardDenied):
        native.disable_verified_email(proof, revision)
    with pytest.raises(GuardDenied):
        send(native, revision, result)
    validate_scoped_rows(c.native.values())


@pytest.mark.parametrize("damage", ["head", "history", "fork", "outcome"])
def test_complete_census_refuses_missing_rolledback_or_unowned_digest_rows(configured, monkeypatch, damage):
    c = configured
    native, context, revision, _ = opt_in(c, monkeypatch)
    native.change(context, revision, expected_generation=1, enabled=False,
                  email_sha256=EMAIL, policy_version=POLICY)
    values = copy.deepcopy(c.native.values())
    owner = str(revision.subject_uuid)
    if damage == "head":
        values[HEADS, owner] = values[HISTORY, f"{owner}:1"]
    elif damage == "history":
        values.pop((HISTORY, f"{owner}:1"))
    elif damage == "fork":
        values[HISTORY, f"{owner}:3"] = values[HISTORY, f"{owner}:2"]
    else:
        values[OUTCOMES, str(uuid4())] = {"outcome": "known_retryable"}
    with pytest.raises(GuardUnavailable):
        validate_scoped_rows(values)


def test_server_iso_week_handles_year_boundary():
    assert iso_week(int(datetime(2021, 1, 1, tzinfo=UTC).timestamp()*1000)) == "2020-W53"
    assert iso_week(int(datetime(2021, 1, 4, tzinfo=UTC).timestamp()*1000)) == "2021-W01"


def test_no_retry_hook_or_refused_provider_wait_stops_live_chain(configured, monkeypatch):
    c = configured
    native, _, revision, _ = opt_in(c, monkeypatch)
    calls = []
    assert send(native, revision, lambda: calls.append(1) or result("known_retryable"),
                before_retry=None).outcome == "known_retryable"
    assert calls == [1]
    with pytest.raises(GuardDenied):
        send(ConfiguredDigestNative(c.accounts), revision, lambda: calls.append(1) or result())


def test_retry_wait_outside_locks_can_stop_and_cannot_reconstruct(configured, monkeypatch):
    c = configured
    native, _, revision, _ = opt_in(c, monkeypatch)
    calls = []
    def refuse(receipt):
        assert receipt == result("known_retryable") and not c.sql_active and not c.native.views
        raise RuntimeError("provider wait exceeds owned deadline")
    with pytest.raises(RuntimeError, match="wait"):
        send(native, revision, lambda: calls.append(1) or result("known_retryable"), before_retry=refuse)
    with pytest.raises(GuardDenied):
        send(ConfiguredDigestNative(c.accounts), revision, lambda: calls.append(1) or result())
    assert calls == [1]


@pytest.mark.parametrize("acknowledged", [False, True])
def test_budget_error_only_original_postcommit_knownreceipt_can_retry(configured, monkeypatch, acknowledged):
    c = configured
    native, _, revision, _ = opt_in(c, monkeypatch)
    calls = []
    def callback():
        calls.append(1)
        if len(calls) == 1:
            raise MailBudgetError("known_retryable", committed_result=result("known_retryable") if acknowledged else None)
        return result()
    if acknowledged:
        assert send(native, revision, callback).outcome == "sent"
        assert calls == [1, 1]
        validate_scoped_rows(c.native.values())
    else:
        with pytest.raises(GuardUnavailable, match="unavailable"):
            send(native, revision, callback)
        assert calls == [1]


def test_execution_postwitness_closure_never_reaches_provider(configured, monkeypatch):
    from app.domains.recovery.gcp_closure_contracts import closure_path
    c = configured
    native, _, revision, _ = opt_in(c, monkeypatch)
    calls = []
    def close_after_begin(names):
        if BEGINS in names:
            c.objects[closure_path(c.execution)] = b"synthetic closure denial", "999"
    c.native.mutate = close_after_begin
    with pytest.raises(GuardUnavailable):
        send(native, revision, lambda: calls.append(1) or result())
    c.native.mutate = None
    c.objects.pop(closure_path(c.execution))
    with pytest.raises(GuardDenied):
        send(ConfiguredDigestNative(c.accounts), revision, lambda: calls.append(1) or result())
    assert calls == []


def test_unknown_unpersisted_begin_fresh_create_beats_delayed_old_transaction(configured, monkeypatch):
    """Absence grants nothing: a new fully guarded conditional commit must win.

    The old unknown server transaction cannot later overwrite the winning exact
    weekly key; its original read-set/preconditions make the delayed commit abort.
    """
    from google.api_core.exceptions import Aborted, ServiceUnavailable
    c = configured
    native, _, revision, _ = opt_in(c, monkeypatch)
    original = c.native.commit
    delayed = []
    transport = c.native.sdk.client.transport
    def lose_before_commit(request, **kwargs):
        names = {__import__("json").loads(write.update.fields["body"].bytes_value)["namespace"]
                 for write in request.writes}
        if BEGINS in names and not delayed:
            token = request.transaction
            delayed.append((copy.deepcopy(request), copy.deepcopy(c.native.views.pop(token)),
                            c.native.reads.pop(token)))
            raise ServiceUnavailable("Owned server commit remains delayed and unknown")
        return original(request, **kwargs)
    monkeypatch.setattr(transport.commit, "side_effect", lose_before_commit)
    calls = []
    with pytest.raises(GuardUnavailable):
        send(native, revision, lambda: calls.append(1) or result())
    assert calls == []
    # A cold owner does not adopt the old transaction; it wins a new atomic
    # create on the same server-derived subject/week/ordinal under all guards.
    fresh = ConfiguredDigestNative(production_candidate_accounts())
    assert send(fresh, revision, lambda: calls.append(1) or result()).outcome == "sent"
    request, view, reads = delayed[0]
    c.native.views[request.transaction], c.native.reads[request.transaction] = view, reads
    with pytest.raises(Aborted):
        original(request)
    c.native.views.pop(request.transaction)
    c.native.reads.pop(request.transaction)
    assert calls == [1]
    validate_scoped_rows(c.native.values())


def proof_for(revision, generation=1):
    identity = CandidateAccountIdentity.model_validate(revision.model_dump(
        exclude={"auth_generation", "credential_sha256"}))
    tokens = UnsubscribeTokens(b"owned-synthetic-secret-no-provider" * 2)
    token = tokens.issue(identity, email_sha256=EMAIL, expected_generation=generation,
                         policy_version=POLICY, now_ms=NOW)
    return tokens.verify(token, now_ms=NOW)


@pytest.mark.parametrize("flag", ["NOTIFICATION_DIGESTS_ENABLED", "LIFECYCLE_EMAILS_ENABLED"])
def test_paused_delivery_still_accepts_only_signed_existing_consent_disable(configured, monkeypatch, flag):
    c = configured
    native, _, revision, _ = opt_in(c, monkeypatch)
    proof = proof_for(revision)
    monkeypatch.setenv(flag, "false")
    # Normal construction, enabling and sending remain closed.
    with pytest.raises(GuardUnavailable):
        ConfiguredDigestNative(c.accounts)
    calls = []
    with pytest.raises(GuardUnavailable):
        send(native, revision, lambda: calls.append(1) or result())
    unsubscribe = ConfiguredDigestNative.for_unsubscribe(c.accounts)
    assert not hasattr(unsubscribe, "execute_once") and not hasattr(unsubscribe, "change")
    head = unsubscribe.disable_verified_email(proof, revision)
    assert head.enabled is False and head.generation == 2 and calls == []
    validate_scoped_rows(c.native.values())


def test_paused_unsubscribe_never_initializes_absent_consent_and_malformed_flag_denies(configured, monkeypatch):
    c = configured
    _, _, revision = account(c, monkeypatch)
    proof = proof_for(revision)
    monkeypatch.setenv("NOTIFICATION_DIGESTS_ENABLED", "false")
    with pytest.raises(GuardDenied):
        ConfiguredDigestNative.for_unsubscribe(c.accounts).disable_verified_email(proof, revision)
    assert not any(namespace == HISTORY for namespace, _ in c.native.values())
    monkeypatch.setenv("NOTIFICATION_DIGESTS_ENABLED", "yes")
    with pytest.raises(GuardUnavailable):
        ConfiguredDigestNative.for_unsubscribe(c.accounts)


def current_revision(c, uid):
    with c.accounts.sessions() as db:
        return revision_from_row(db.get(CandidatePasswordAccount, uid), db.get(User, uid).password_hash)


def test_independent_consent_survives_password_rotation_but_old_revision_never_sends(configured, monkeypatch):
    from test_candidate_production_factory import REPLACEMENT
    c = configured
    native, context, old, head = opt_in(c, monkeypatch)
    c.accounts.change_password(context, current_password=PASSWORD, new_password=REPLACEMENT)
    fresh = current_revision(c, old.candidate_id)
    assert fresh.auth_generation == old.auth_generation + 1
    assert native.read_current(fresh, EMAIL) == head
    with pytest.raises(GuardDenied):
        send(native, old, result)
    # No consent rewrite is required: current credentials are independently
    # verified while consent remains bound to exact identity/mailbox/generation.
    assert send(native, fresh, result).outcome == "sent"
    validate_scoped_rows(c.native.values())
    # The original signed opt-out also remains valid with current credentials.
    disabled = native.disable_verified_email(proof_for(old), fresh)
    assert disabled.generation == 2 and not disabled.enabled


def test_rotation_during_known429_chain_stops_current_retry(configured, monkeypatch):
    from test_candidate_production_factory import REPLACEMENT
    c = configured
    native, context, revision, _ = opt_in(c, monkeypatch)
    calls = []
    def callback():
        calls.append(1)
        c.accounts.change_password(context, current_password=PASSWORD, new_password=REPLACEMENT)
        return result("known_retryable")
    with pytest.raises(GuardDenied):
        send(native, revision, callback)
    assert calls == [1]
    validate_scoped_rows(c.native.values())


def test_next_week_uses_current_rotated_credentials_without_reconsent(configured, monkeypatch):
    from test_candidate_production_factory import REPLACEMENT

    from app.domains.candidate_accounts import production
    c = configured
    native, context, old, head = opt_in(c, monkeypatch)
    assert send(native, old, result).outcome == "sent"
    c.accounts.change_password(context, current_password=PASSWORD, new_password=REPLACEMENT)
    fresh = current_revision(c, old.candidate_id)
    later = NOW + 7 * 24 * 60 * 60 * 1000
    monkeypatch.setattr(production.time, "time_ns", lambda: later * 1_000_000)
    assert native.read_current(fresh, EMAIL) == head
    assert send(native, fresh, result).outcome == "sent"
    assert sum(namespace == BEGINS for namespace, _ in c.native.values()) == 2
    validate_scoped_rows(c.native.values())


@pytest.mark.parametrize("offset", [-7, 7])
def test_explicit_queued_old_or_future_week_cannot_create_begin(configured, monkeypatch, offset):
    c = configured
    native, _, revision, _ = opt_in(c, monkeypatch)
    calls = []
    expected = iso_week(NOW + offset * 24 * 60 * 60 * 1000)
    with pytest.raises(GuardDenied, match="week"):
        send(native, revision, lambda: calls.append(1) or result(), expected_week=expected)
    assert calls == [] and not any(namespace == BEGINS for namespace, _ in c.native.values())
    # Invalid queue intent cannot consume the rightful current native slot.
    assert send(native, revision, result, expected_week=iso_week(NOW)).outcome == "sent"


def test_explicit_queue_week_rechecked_inside_commit_stage_across_monday(configured, monkeypatch):
    from datetime import timedelta

    from app.domains.candidate_accounts import production
    from app.domains.recovery.gcp_partitioned_contracts import (
        ScopedDigestSendBegin,
        ScopedJournalIntent,
    )
    c = configured
    native, _, revision, _ = opt_in(c, monkeypatch)
    now = datetime.fromtimestamp(NOW / 1000, UTC)
    monday = (now + timedelta(days=7 - now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    monday_ms = int(monday.timestamp() * 1000)
    clock = [monday_ms - 1]
    monkeypatch.setattr(production.time, "time_ns", lambda: clock[0] * 1_000_000)
    # Observe the real staged transaction after its complete original record is
    # staged but before BEGIN's condition/create. Rollover must abort it all.
    publication = c.accounts._composition().scoped.publication
    original = publication._append
    def rollover(tx, raw):
        record = original(tx, raw)
        if isinstance(ScopedJournalIntent.model_validate_json(raw).command, ScopedDigestSendBegin):
            clock[0] = monday_ms
        return record
    monkeypatch.setattr(publication, "_append", rollover)
    calls = []
    with pytest.raises(GuardDenied, match="week"):
        send(native, revision, lambda: calls.append(1) or result(), expected_week=iso_week(monday_ms - 1))
    assert calls == [] and not any(namespace == BEGINS for namespace, _ in c.native.values())
    monkeypatch.setattr(publication, "_append", original)
    # The new week's legitimate queued message still wins its own original slot.
    assert send(native, revision, result, expected_week=iso_week(monday_ms)).outcome == "sent"
    validate_scoped_rows(c.native.values())
