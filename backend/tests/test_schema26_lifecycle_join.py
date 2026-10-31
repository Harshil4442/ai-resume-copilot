"""Typed synthetic full-validator inputs only; no native receipts or durability claim."""

from __future__ import annotations

import copy
import hashlib
from uuid import uuid4

import pytest
from infra.gcp import schema26_lifecycle_join as core
from pydantic import ValidationError

from app.domains.recovery.contracts import Revocation, canonical, fingerprint
from app.domains.recovery.gcp_contracts import JournalReceipt, OperationStatus, RegistryPin
from app.domains.recovery.gcp_partitioned_contracts import (
    GenerationHistory,
    PartitionedRecord,
    PartitionedReference,
    ScopedDenial,
    ScopedJournalIntent,
    ScopedPasswordReset,
    ScopedSession,
    ScopedSubject,
    ScopedTombstone,
)
from app.domains.recovery.gcp_password_lifetime_contracts import (
    CreateVerifiedPasswordWebSession,
    EnrollPasswordAccount,
    LifetimeGrantEvidence,
    LifetimeRecord,
    PasswordLifetimeEffects,
    PasswordLifetimeIntent,
    RegisteredSubject,
)
from app.domains.recovery.gcp_password_reset import ResetProjectionEvidence
from app.domains.recovery.password_reauth import PasswordAccountBinding
from app.domains.recovery.store import GuardUnavailable

NOW = 1_800_000_000_000


def raw(value):
    return value.model_dump(mode="json")


def retain(c, intent):
    data = raw(intent)
    operation = str(intent.operation_id)
    authority = hashlib.sha256(str(intent.pin.authority_id).encode()).hexdigest()
    reference = PartitionedReference(
        operation_id=intent.operation_id,
        sha256=fingerprint(data),
        partition=intent.partition,
        path=f"authority-intents/{authority}/{intent.partition}/{operation}.json",
        byte_size=len(canonical(data).encode()),
        kind=intent.kind,
    )
    parts = dict(
        reference=reference, segment_id="e" * 64, ordinal=len(c["records"]) + 1, previous="0" * 64
    )
    digest = fingerprint({**parts, "reference": raw(reference)})
    record = PartitionedRecord(**parts, chain_digest=digest, intent=data)
    c["values"]["v3_records", operation] = raw(record)
    c["records"].append(record)
    receipt = JournalReceipt(
        bucket="synthetic-candidate-journal",
        path=reference.path,
        generation="1",
        sha256=reference.sha256,
    )
    return OperationStatus(
        status="COMMITTED",
        operation_id=intent.operation_id,
        intent_sha256=reference.sha256,
        journal=receipt,
    )


def lifetime(c, command):
    # This fixture is typed synthetic custody, deliberately not an execution ACK.
    intent = PasswordLifetimeIntent(
        operation_id=uuid4(),
        event_id=uuid4(),
        pin=c["pin"],
        epoch_generation=1,
        command=command,
        subject_uuid=c["subject"].subject_uuid,
        created_at_ms=NOW,
        deadline_ms=NOW + 60_000,
        effects=PasswordLifetimeEffects(
            before=(LifetimeRecord(namespace="head", key="global", value=None),),
            after=(LifetimeRecord(namespace="head", key="global", value={"synthetic": True}),),
            event={"synthetic": True},
        ),
    )
    status = retain(c, intent)
    c["values"]["v3_activations", str(intent.operation_id)] = raw(
        LifetimeGrantEvidence(intent=intent, status=raw(status))
    )
    return intent


def add_session(c):
    subject = c["subject"]
    command = CreateVerifiedPasswordWebSession(
        account_binding_id=subject.account_binding_id,
        session_id=uuid4(),
        expected_auth_generation=subject.auth_generation,
        credential_sha256=subject.credential_sha256,
        expires_at_ms=NOW + 600_000,
    )
    intent = lifetime(c, command)
    session = ScopedSession(
        subject_uuid=subject.subject_uuid,
        account_binding_id=subject.account_binding_id,
        session_id=command.session_id,
        auth_generation=subject.auth_generation,
        principal_sha256=subject.principal_sha256,
        expires_at_ms=command.expires_at_ms,
        credential_sha256=subject.credential_sha256,
        activation_operation=intent.operation_id,
    )
    c["values"]["v3_sessions", str(session.session_id)] = raw(session)
    c["session"] = session


def projections(c):
    subject = c["subject"]
    identity = str(subject.subject_uuid)
    enrollment = c["enrollment"]
    owner = {
        "subject_uuid": identity,
        "account_binding_id": str(subject.account_binding_id),
        "candidate_id": subject.candidate_id,
    }
    c["execution"] = {
        ("subjects", identity): raw(
            RegisteredSubject(
                active=True,
                registration_event_id=enrollment.command.subject_registration_event_id,
                principal_sha256=subject.principal_sha256,
                auth_generation=subject.auth_generation,
            )
        ),
        ("pairing_account_bindings", str(subject.account_binding_id)): raw(
            PasswordAccountBinding(
                **owner,
                principal_sha256=subject.principal_sha256,
                registration_event_id=enrollment.event_id,
            )
        ),
        ("pairing_subject_accounts", identity): dict(owner),
        ("pairing_sql_accounts", fingerprint({"candidate_id": subject.candidate_id})): dict(owner),
        ("pairing_auth_high_water", identity): {
            "subject_uuid": identity,
            "auth_generation": subject.auth_generation,
            "principal_sha256": subject.principal_sha256,
        },
        ("password_credential_revisions", identity): {
            "subject_uuid": identity,
            "auth_generation": subject.auth_generation,
            "credential_sha256": subject.credential_sha256,
        },
        ("pairing_account_tombstones", str(subject.account_binding_id)): None,
        (
            "revocations",
            Revocation(
                subject_uuid=subject.subject_uuid, kind="subject", target=identity, revision=0
            ).key,
        ): None,
    }
    c["sql"] = [
        {
            **subject.model_dump(mode="json", exclude={"enrollment_operation"}),
            "state": "ACTIVE",
            "user_id": subject.candidate_id,
            "password_sha256": subject.credential_sha256,
        }
    ]


def fixture():
    pin = RegistryPin(
        database="projects/hirewiz-local-authority/databases/candidate-execution",
        database_uid=uuid4(),
        authority_id=uuid4(),
        incarnation=1,
        epoch_id=uuid4(),
    )
    subject = ScopedSubject(
        subject_uuid=uuid4(),
        account_binding_id=uuid4(),
        candidate_id=17,
        principal_sha256="a" * 64,
        auth_generation=1,
        credential_sha256=hashlib.sha256(b"synthetic-password-hash").hexdigest(),
        enrollment_operation=uuid4(),
    )
    c = {"pin": pin, "subject": subject, "values": {}, "records": [], "statuses": {}, "absent": {}}
    command = EnrollPasswordAccount(
        **subject.model_dump(exclude={"auth_generation", "enrollment_operation"}),
        subject_registration_event_id=uuid4(),
    )
    intent = lifetime(c, command)
    subject = ScopedSubject.model_validate(
        {**raw(subject), "enrollment_operation": str(intent.operation_id)}
    )
    c["subject"], c["enrollment"] = subject, intent
    c["values"]["v3_subjects", str(subject.subject_uuid)] = raw(subject)
    owner = subject.model_dump(
        mode="json",
        include={"subject_uuid", "account_binding_id", "candidate_id", "enrollment_operation"},
    )
    c["values"]["v3_account_owners", str(subject.account_binding_id)] = owner
    c["values"]["v3_candidate_owners", str(subject.candidate_id)] = owner
    c["values"]["v3_generation_history", f"{subject.subject_uuid}:1"] = raw(
        GenerationHistory(
            subject_uuid=subject.subject_uuid,
            previous_generation=0,
            auth_generation=1,
            operation_id=intent.operation_id,
            credential_sha256=subject.credential_sha256,
        )
    )
    add_session(c)
    projections(c)
    return c


def denial(c, scope):
    subject = c["subject"]
    session = c["session"]
    command = ScopedDenial(
        scope=scope,
        subject=subject,
        session=session,
        next_credential_sha256="f" * 64 if scope == "generation" else None,
    )
    intent = ScopedJournalIntent(
        operation_id=uuid4(),
        pin=c["pin"],
        command=command,
        created_at_ms=NOW,
        deadline_ms=NOW + 60_000,
    )
    retain(c, intent)
    namespace = "v3_pending_session_denials" if scope == "session" else "v3_pending_subject_denials"
    key = (
        str(session.session_id)
        if scope == "session"
        else f"{subject.subject_uuid}:{subject.auth_generation}"
    )
    c["values"][namespace, key] = {
        "operation_id": str(intent.operation_id),
        "intent_sha256": fingerprint(raw(intent)),
    }
    if scope != "generation":
        tombstone = raw(
            ScopedTombstone(
                subject_uuid=subject.subject_uuid,
                account_binding_id=subject.account_binding_id,
                session_id=session.session_id if scope == "session" else None,
                operation_id=intent.operation_id,
            )
        )
        c["values"][
            "v3_session_tombstones" if scope == "session" else "v3_subject_tombstones",
            str(session.session_id) if scope == "session" else str(subject.subject_uuid),
        ] = tombstone
        if scope == "subject":
            c["values"]["v3_account_tombstones", str(subject.account_binding_id)] = tombstone
            c["sql"] = []
            c["absent"] = {subject.candidate_id: dict.fromkeys(core.SQL_ENTITIES, 0)}
    return intent


def reset(c):
    subject = c["subject"]
    identity = str(subject.subject_uuid)
    request, event_id = uuid4(), uuid4()
    digest = hashlib.sha256(f"next-generation-{subject.auth_generation}".encode()).hexdigest()
    old = c["execution"]
    before = {
        ("subjects", identity): old["subjects", identity],
        ("pairing_account_bindings", str(subject.account_binding_id)): old[
            "pairing_account_bindings", str(subject.account_binding_id)
        ],
        ("password_credential_revisions", identity): old["password_credential_revisions", identity],
        ("head", "global"): {"sequence": subject.auth_generation, "digest": "1" * 64},
    }
    event = {
        "sequence": subject.auth_generation + 1,
        "previous": "1" * 64,
        "kind": "PASSWORD_CREDENTIAL_REVISION_PROJECTED",
        "event_id": str(event_id),
        "payload": {
            "request_id": str(request),
            "subject_uuid": identity,
            "account_binding_id": str(subject.account_binding_id),
            "candidate_id": subject.candidate_id,
            "principal_sha256": subject.principal_sha256,
            "old_auth_generation": subject.auth_generation,
            "old_credential_sha256": subject.credential_sha256,
            "auth_generation": subject.auth_generation + 1,
            "credential_sha256": digest,
        },
    }
    event["digest"] = fingerprint(event)
    after = {
        ("subjects", identity): {
            **old["subjects", identity],
            "auth_generation": subject.auth_generation + 1,
        },
        ("pairing_auth_high_water", identity): {
            "subject_uuid": identity,
            "auth_generation": subject.auth_generation + 1,
            "principal_sha256": subject.principal_sha256,
        },
        ("password_credential_revisions", identity): {
            "subject_uuid": identity,
            "auth_generation": subject.auth_generation + 1,
            "credential_sha256": digest,
        },
        ("pairing_clock", "observed"): {"now_ms": NOW},
        ("events", str(event_id)): event,
        ("head", "global"): {"sequence": event["sequence"], "digest": event["digest"]},
    }
    effects = PasswordLifetimeEffects(
        before=tuple(LifetimeRecord(namespace=n, key=k, value=v) for (n, k), v in before.items()),
        after=tuple(LifetimeRecord(namespace=n, key=k, value=v) for (n, k), v in after.items()),
        event=event,
    )
    command = ScopedPasswordReset(
        subject=subject,
        session=c["session"],
        next_credential_sha256=digest,
        request_id=request,
        event_id=event_id,
        native_effects=effects,
    )
    intent = ScopedJournalIntent(
        operation_id=uuid4(),
        pin=c["pin"],
        command=command,
        created_at_ms=NOW,
        deadline_ms=NOW + 60_000,
    )
    status = retain(c, intent)
    c["values"]["v3_pending_subject_denials", f"{identity}:{subject.auth_generation}"] = {
        "operation_id": str(intent.operation_id),
        "intent_sha256": fingerprint(raw(intent)),
    }
    c["values"]["v3_reset_completions", str(intent.operation_id)] = raw(
        ResetProjectionEvidence(intent=intent, status=status)
    )
    c["values"]["v3_generation_history", f"{identity}:{subject.auth_generation + 1}"] = raw(
        GenerationHistory(
            subject_uuid=subject.subject_uuid,
            previous_generation=subject.auth_generation,
            auth_generation=subject.auth_generation + 1,
            operation_id=intent.operation_id,
            credential_sha256=digest,
        )
    )
    c["subject"] = ScopedSubject.model_validate(
        {
            **raw(subject),
            "auth_generation": subject.auth_generation + 1,
            "credential_sha256": digest,
        }
    )
    c["values"]["v3_subjects", identity] = raw(c["subject"])
    c["statuses"][str(intent.operation_id)] = status
    projections(c)
    return intent


def join(c):
    return core.join_lifecycle(
        c["values"], c["execution"], c["statuses"], c["sql"], c["absent"], c["pin"]
    )


@pytest.mark.parametrize(
    "scenario",
    ["live", "session_denial", "subject_denial", "reset", "two_rotations", "rotation_then_delete"],
)
def test_original_full_validator_typed_positive_join_is_hash_only_not_erasure_authority(scenario):
    c = fixture()
    if scenario == "session_denial":
        denial(c, "session")
    if scenario == "subject_denial":
        denial(c, "subject")
    if scenario in {"reset", "two_rotations", "rotation_then_delete"}:
        reset(c)
    if scenario == "two_rotations":
        add_session(c)
        reset(c)
    if scenario == "rotation_then_delete":
        add_session(c)
        denial(c, "subject")
    before = copy.deepcopy(c)
    result = join(c)
    assert len(result) == 64 and result == join(c)
    assert c == before


@pytest.mark.parametrize("state", ["PENDING", "RESET_PENDING", "DELETE_DENIED", "UNKNOWN"])
def test_no_nonactive_sql_state_can_be_declared_complete(state):
    c = fixture()
    c["sql"][0]["state"] = state
    with pytest.raises((GuardUnavailable, ValidationError)):
        join(c)


@pytest.mark.parametrize(
    "fault",
    [
        "missing_completion",
        "unknown",
        "missing_status",
        "extra_status",
        "native_generation",
        "native_credential",
        "sql_credential",
        "password_hash",
        "missing_history",
        "missing_owner",
        "missing_user",
        "old_sql_generation",
        "binding_event",
        "subject_event",
        "native_tombstone",
        "native_revocation",
        "foreign_pin",
        "orphan_sql",
        "orphan_execution",
        "orphan_scoped",
    ],
)
def test_completed_rotation_requires_full_original_provenance_and_latest_matching_sql(fault):
    c = fixture()
    intent = reset(c)
    subject = c["subject"]
    identity = str(subject.subject_uuid)
    operation = str(intent.operation_id)
    if fault == "missing_completion":
        c["values"].pop(("v3_reset_completions", operation))
    elif fault == "unknown":
        c["statuses"][operation] = OperationStatus(
            status="UNKNOWN",
            operation_id=intent.operation_id,
            intent_sha256=fingerprint(raw(intent)),
        )
    elif fault == "missing_status":
        c["statuses"].clear()
    elif fault == "extra_status":
        c["statuses"][str(uuid4())] = c["statuses"][operation]
    elif fault == "native_generation":
        c["execution"]["subjects", identity]["auth_generation"] -= 1
    elif fault == "native_credential":
        c["execution"]["password_credential_revisions", identity]["credential_sha256"] = "0" * 64
    elif fault == "sql_credential":
        c["sql"][0]["credential_sha256"] = "0" * 64
    elif fault == "password_hash":
        c["sql"][0]["password_sha256"] = "0" * 64
    elif fault == "missing_history":
        c["values"].pop(("v3_generation_history", identity + ":2"))
    elif fault == "missing_owner":
        c["values"].pop(("v3_account_owners", str(subject.account_binding_id)))
    elif fault == "missing_user":
        c["sql"][0]["user_id"] = None
    elif fault == "old_sql_generation":
        c["sql"][0]["auth_generation"] -= 1
    elif fault == "binding_event":
        c["execution"]["pairing_account_bindings", str(subject.account_binding_id)][
            "registration_event_id"
        ] = str(uuid4())
    elif fault == "subject_event":
        c["execution"]["subjects", identity]["registration_event_id"] = str(uuid4())
    elif fault == "native_tombstone":
        c["execution"]["pairing_account_tombstones", str(subject.account_binding_id)] = {
            "unknown": True
        }
    elif fault == "native_revocation":
        c["execution"][
            "revocations",
            Revocation(
                subject_uuid=subject.subject_uuid, kind="subject", target=identity, revision=0
            ).key,
        ] = {"unknown": True}
    elif fault == "foreign_pin":
        c["pin"] = RegistryPin.model_validate({**raw(c["pin"]), "database_uid": str(uuid4())})
    elif fault == "orphan_sql":
        c["sql"].append({**c["sql"][0], "candidate_id": 99, "user_id": 99})
    elif fault == "orphan_execution":
        c["execution"]["subjects", str(uuid4())] = {"unknown": True}
    else:
        c["values"]["v3_pending_subject_denials", "orphan"] = {"unknown": True}
    with pytest.raises((GuardUnavailable, ValidationError)):
        join(c)


@pytest.mark.parametrize(
    "fault",
    [
        "missing_tombstone",
        "missing_account_tombstone",
        "missing_pending",
        "remaining_owner",
        "missing_count",
        "boolean_count",
        "negative_count",
        "nonzero_count",
        "extra_count",
        "unrelated_absence",
    ],
)
def test_subject_denial_requires_exact_two_tombstones_and_complete_zero_counts(fault):
    c = fixture()
    original = copy.deepcopy(c["sql"])
    denial(c, "subject")
    subject = c["subject"]
    identity = str(subject.subject_uuid)
    if fault == "missing_tombstone":
        c["values"].pop(("v3_subject_tombstones", identity))
    elif fault == "missing_account_tombstone":
        c["values"].pop(("v3_account_tombstones", str(subject.account_binding_id)))
    elif fault == "missing_pending":
        c["values"].pop(("v3_pending_subject_denials", identity + ":1"))
    elif fault == "remaining_owner":
        c["sql"] = original
    elif fault == "missing_count":
        c["absent"][17].pop("EvidenceItem")
    elif fault == "boolean_count":
        c["absent"][17]["EvidenceItem"] = False
    elif fault == "negative_count":
        c["absent"][17]["EvidenceItem"] = -1
    elif fault == "nonzero_count":
        c["absent"][17]["EvidenceItem"] = 1
    elif fault == "extra_count":
        c["absent"][17]["Other"] = 0
    else:
        c["absent"][99] = dict.fromkeys(core.SQL_ENTITIES, 0)
    with pytest.raises((GuardUnavailable, ValidationError)):
        join(c)


def test_generic_generation_denial_and_incomplete_session_denial_refuse():
    c = fixture()
    denial(c, "generation")
    with pytest.raises((GuardUnavailable, ValidationError), match="generic_generation"):
        join(c)
    c = fixture()
    denial(c, "session")
    c["values"].pop(("v3_session_tombstones", str(c["session"].session_id)))
    with pytest.raises((GuardUnavailable, ValidationError), match="session_denial_incomplete"):
        join(c)


def test_original_validator_runs_before_interpreting_sql_and_bounds_stay_finite():
    c = fixture()
    c["values"]["v3_pending_subject_denials", "orphan"] = {"unknown": True}
    c["sql"] = [{}]
    with pytest.raises((GuardUnavailable, ValidationError), match="Full scoped census"):
        join(c)
    c = fixture()
    c["sql"] *= 1001
    with pytest.raises((GuardUnavailable, ValidationError), match="input_bound"):
        join(c)
    c = fixture()
    c["values"] = {("v3_records", str(i)): {} for i in range(4097)}
    with pytest.raises((GuardUnavailable, ValidationError), match="rows_unavailable"):
        join(c)


def test_completed_generic_generation_history_never_becomes_reset_completion():
    c = fixture()
    intent = denial(c, "generation")
    subject = c["subject"]
    c["values"]["v3_generation_history", f"{subject.subject_uuid}:2"] = raw(
        GenerationHistory(
            subject_uuid=subject.subject_uuid,
            previous_generation=1,
            auth_generation=2,
            operation_id=intent.operation_id,
            credential_sha256=intent.command.next_credential_sha256,
        )
    )
    c["subject"] = ScopedSubject.model_validate(
        {
            **raw(subject),
            "auth_generation": 2,
            "credential_sha256": intent.command.next_credential_sha256,
        }
    )
    c["values"]["v3_subjects", str(subject.subject_uuid)] = raw(c["subject"])
    projections(c)
    with pytest.raises(GuardUnavailable, match="generic_generation"):
        join(c)


@pytest.mark.parametrize("fault", ["inactive", "account_tombstone", "subject_revocation"])
def test_terminal_denial_refuses_unsupported_execution_denial_cohorts(fault):
    c = fixture()
    denial(c, "subject")
    subject = c["subject"]
    identity = str(subject.subject_uuid)
    if fault == "inactive":
        c["execution"]["subjects", identity]["active"] = False
    elif fault == "account_tombstone":
        c["execution"]["pairing_account_tombstones", str(subject.account_binding_id)] = {
            "unknown": True
        }
    else:
        c["execution"][
            "revocations",
            Revocation(
                subject_uuid=subject.subject_uuid, kind="subject", target=identity, revision=0
            ).key,
        ] = {"unknown": True}
    with pytest.raises(GuardUnavailable, match="terminal_execution_denial_cohort_unsupported"):
        join(c)


def test_unknown_flat_publication_namespace_and_changed_reset_receipt_refuse():
    c = fixture()
    c["values"]["unrecognized_authority", "orphan"] = {}
    with pytest.raises(GuardUnavailable, match="unknown_publication_namespace"):
        join(c)
    c = fixture()
    intent = reset(c)
    status = c["statuses"][str(intent.operation_id)]
    assert status.journal is not None
    changed = {**raw(status), "journal": {**raw(status.journal), "generation": "2"}}
    c["statuses"][str(intent.operation_id)] = OperationStatus.model_validate(changed)
    with pytest.raises(GuardUnavailable, match="reset_status_unconfirmed"):
        join(c)


@pytest.mark.parametrize("historical_reset_unknown", [False, True])
def test_retained_logout_two_completed_rotations_then_later_delete_uses_final_sql_absence(
    historical_reset_unknown,
):
    c = fixture()
    logout = denial(c, "session")
    logged_out_session = str(c["session"].session_id)
    add_session(c)
    first_reset = reset(c)
    add_session(c)
    second_reset = reset(c)
    add_session(c)
    deleted = denial(c, "subject")
    subject = c["subject"]
    identity = str(subject.subject_uuid)
    assert subject.auth_generation == 3 and deleted.command.subject == subject
    assert c["sql"] == [] and c["absent"] == {17: dict.fromkeys(core.SQL_ENTITIES, 0)}
    assert c["values"]["v3_pending_session_denials", logged_out_session]["operation_id"] == str(
        logout.operation_id
    )
    assert ("v3_session_tombstones", logged_out_session) in c["values"]
    for original in (first_reset, second_reset):
        operation = str(original.operation_id)
        evidence = ResetProjectionEvidence.model_validate(
            c["values"]["v3_reset_completions", operation]
        )
        assert evidence.intent == original and evidence.status == c["statuses"][operation]
        assert evidence.status.status == "COMMITTED"
        assert (
            c["values"][
                "v3_pending_subject_denials",
                f"{identity}:{original.command.subject.auth_generation}",
            ]["operation_id"]
            == operation
        )
    if historical_reset_unknown:
        c["statuses"][str(first_reset.operation_id)] = OperationStatus(
            status="UNKNOWN",
            operation_id=first_reset.operation_id,
            intent_sha256=fingerprint(raw(first_reset)),
        )
        with pytest.raises(GuardUnavailable, match="reset_status_unconfirmed"):
            join(c)
    else:
        assert len(join(c)) == 64
