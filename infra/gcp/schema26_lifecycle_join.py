"""Pure finite lifecycle data join; fingerprint only, never native/erasure authority."""

from __future__ import annotations

import hashlib
import re
from uuid import UUID

from app.domains.recovery.contracts import Revocation, fingerprint
from app.domains.recovery.gcp_contracts import OperationStatus, RegistryPin
from app.domains.recovery.gcp_partitioned_contracts import (
    GenerationHistory,
    PartitionedRecord,
    ScopedDenial,
    ScopedJournalIntent,
    ScopedPasswordReset,
    ScopedSubject,
    ScopedTombstone,
)
from app.domains.recovery.gcp_password_lifetime_contracts import (
    EnrollPasswordAccount,
    LifetimeGrantEvidence,
    RegisteredSubject,
)
from app.domains.recovery.gcp_password_reset import ResetProjectionEvidence
from app.domains.recovery.gcp_scoped_census import SCOPED_NAMESPACES, validate_scoped_rows
from app.domains.recovery.password_reauth import PasswordAccountBinding
from app.domains.recovery.store import GuardUnavailable

SQL_ENTITIES = (
    "User",
    "CandidatePasswordAccount",
    "UserProfile",
    "Resume",
    "JobMatch",
    "Opportunity",
    "ApplicationEvent",
    "ResumeVersion",
    "EvidenceItem",
    "Reminder",
    "OpportunityContact",
    "CareerMemoryEntry",
    "AnalysisRun",
    "UsageEvent",
    "ModelCallEvent",
    "NotificationOutbox",
)
SQL_FIELDS = frozenset(
    {
        "candidate_id",
        "subject_uuid",
        "account_binding_id",
        "principal_sha256",
        "auth_generation",
        "credential_sha256",
        "state",
        "user_id",
        "password_sha256",
    }
)
_HASH = re.compile(r"[a-f0-9]{64}\Z")


def need(value, code):
    if not value:
        raise GuardUnavailable("schema26_lifecycle_" + code)


def _rows(value, maximum):
    need(type(value) is dict and len(value) <= maximum, "rows_unavailable")
    need(
        all(
            type(key) is tuple
            and len(key) == 2
            and all(type(part) is str and part for part in key)
            and (row is None or type(row) is dict)
            for key, row in value.items()
        ),
        "rows_invalid",
    )
    return [
        {"namespace": key[0], "key": key[1], "value": row} for key, row in sorted(value.items())
    ]


def join_lifecycle(
    values: dict[tuple[str, str], dict],
    execution_rows: dict[tuple[str, str], dict | None],
    reset_statuses: dict[str, OperationStatus],
    sql_rows: list[dict],
    absent_counts: dict[int, dict[str, int]],
    expected_pin: RegistryPin,
) -> str:
    """Join complete fixed collector inputs; never emit identities or grant a write.

    Original full scoped validation is the first semantic operation. The outer
    controller must separately prove complete native scans, physical identity,
    quiescence and actual SQL reads; these dictionaries cannot establish those.
    """
    protected = _rows(values, 4096)
    # Do not filter namespaces or interpret a denial before this original check.
    validate_scoped_rows(values)
    need(
        all(
            namespace
            in SCOPED_NAMESPACES
            | {
                "v3_root",
                "v3_lanes",
                "v3_directory",
                "v3_registration_slots",
                "v3_segments",
                "v3_records",
                "v3_record_slots",
            }
            for namespace, _ in values
        ),
        "unknown_publication_namespace",
    )
    need(type(expected_pin) is RegistryPin, "exact_pin_required")
    execution = _rows(execution_rows, 8000)
    need(
        type(sql_rows) is list
        and len(sql_rows) <= 1000
        and type(absent_counts) is dict
        and len(absent_counts) <= 1000
        and all(type(key) is int and key > 0 for key in absent_counts)
        and type(reset_statuses) is dict
        and len(reset_statuses) <= 4096,
        "input_bound",
    )
    subjects = {
        key: ScopedSubject.model_validate(raw)
        for (namespace, key), raw in values.items()
        if namespace == "v3_subjects"
    }
    need(
        len(subjects) <= 1000
        and len({v.candidate_id for v in subjects.values()}) == len(subjects)
        and len({v.account_binding_id for v in subjects.values()}) == len(subjects),
        "subject_owner_duplicate",
    )
    histories = {
        (str(item.subject_uuid), item.auth_generation): item
        for (namespace, _), raw in values.items()
        if namespace == "v3_generation_history"
        for item in [GenerationHistory.model_validate(raw)]
    }
    records = {
        key: PartitionedRecord.model_validate(raw)
        for (namespace, key), raw in values.items()
        if namespace == "v3_records"
    }
    denied_subjects, resets = {}, {}
    authority = hashlib.sha256(str(expected_pin.authority_id).encode()).hexdigest()
    for operation, record in records.items():
        need(
            str(record.reference.operation_id) == operation
            and record.intent.get("operation_id") == operation
            and RegistryPin.model_validate(record.intent.get("pin")) == expected_pin,
            "record_pin_or_operation_changed",
        )
        if record.reference.kind != "scoped_admission":
            continue
        intent = ScopedJournalIntent.model_validate(record.intent)
        need(
            intent.pin == expected_pin
            and record.reference.partition == intent.partition
            and record.reference.path
            == f"authority-intents/{authority}/{intent.partition}/{operation}.json",
            "scoped_reference_changed",
        )
        command = intent.command
        if isinstance(command, ScopedPasswordReset):
            raw = values.get(("v3_reset_completions", operation))
            need(raw is not None, "reset_completion_missing")
            evidence = ResetProjectionEvidence.model_validate(raw)
            status = reset_statuses.get(operation)
            need(
                evidence.intent == intent
                and type(status) is OperationStatus
                and status.status == "COMMITTED"
                and status == evidence.status,
                "reset_status_unconfirmed",
            )
            subject = subjects.get(str(command.subject.subject_uuid))
            next_revision = histories.get(
                (str(command.subject.subject_uuid), command.subject.auth_generation + 1)
            )
            need(
                subject is not None
                and command.subject.auth_generation < subject.auth_generation
                and next_revision is not None
                and next_revision.operation_id == intent.operation_id
                and next_revision.credential_sha256 == command.next_credential_sha256,
                "reset_history_incomplete",
            )
            assert status is not None
            resets[operation] = status.model_dump(mode="json")
        elif isinstance(command, ScopedDenial):
            need(command.scope != "generation", "generic_generation_denial_unsupported")
            tombstone = ScopedTombstone(
                subject_uuid=command.subject.subject_uuid,
                account_binding_id=command.subject.account_binding_id,
                session_id=command.session.session_id if command.scope == "session" else None,
                operation_id=intent.operation_id,
            ).model_dump(mode="json")
            if command.scope == "session":
                need(
                    values.get(("v3_session_tombstones", str(command.session.session_id)))
                    == tombstone,
                    "session_denial_incomplete",
                )
            else:
                key = str(command.subject.subject_uuid)
                need(
                    subjects.get(key) == command.subject
                    and values.get(("v3_subject_tombstones", key)) == tombstone
                    and values.get(
                        ("v3_account_tombstones", str(command.subject.account_binding_id))
                    )
                    == tombstone
                    and key not in denied_subjects,
                    "subject_denial_incomplete",
                )
                denied_subjects[key] = intent
    need(set(reset_statuses) == set(resets), "reset_status_set_changed")

    sql = {}
    sql_subjects, sql_bindings = set(), set()
    for row in sql_rows:
        need(type(row) is dict and set(row) == SQL_FIELDS, "sql_projection_shape")
        candidate = row["candidate_id"]
        need(
            type(candidate) is int
            and candidate > 0
            and candidate not in sql
            and type(row["user_id"]) is int
            and row["user_id"] == candidate
            and type(row["auth_generation"]) is int
            and row["auth_generation"] > 0
            and row["state"] == "ACTIVE"
            and type(row["subject_uuid"]) is str
            and str(UUID(row["subject_uuid"])) == row["subject_uuid"]
            and type(row["account_binding_id"]) is str
            and str(UUID(row["account_binding_id"])) == row["account_binding_id"]
            and all(
                type(row[field]) is str and _HASH.fullmatch(row[field])
                for field in ("principal_sha256", "credential_sha256", "password_sha256")
            )
            and row["subject_uuid"] not in sql_subjects
            and row["account_binding_id"] not in sql_bindings,
            "sql_account_not_exact_active",
        )
        sql[candidate] = row
        sql_subjects.add(row["subject_uuid"])
        sql_bindings.add(row["account_binding_id"])
    classifications, required_execution, absent = [], set(), set()
    for identity, subject in sorted(subjects.items()):
        owner = {
            "account_binding_id": str(subject.account_binding_id),
            "candidate_id": subject.candidate_id,
            "subject_uuid": identity,
        }
        revoked = Revocation(
            subject_uuid=subject.subject_uuid, kind="subject", target=identity, revision=0
        )
        keys = {
            ("subjects", identity),
            ("pairing_account_bindings", str(subject.account_binding_id)),
            ("pairing_auth_high_water", identity),
            ("password_credential_revisions", identity),
            ("pairing_subject_accounts", identity),
            ("pairing_sql_accounts", fingerprint({"candidate_id": subject.candidate_id})),
            ("pairing_account_tombstones", str(subject.account_binding_id)),
            ("revocations", revoked.key),
        }
        required_execution.update(keys)
        registered = RegisteredSubject.model_validate(execution_rows.get(("subjects", identity)))
        binding = PasswordAccountBinding.model_validate(
            execution_rows.get(("pairing_account_bindings", str(subject.account_binding_id)))
        )
        enrollment = LifetimeGrantEvidence.model_validate(
            values.get(("v3_activations", str(subject.enrollment_operation)))
        ).intent
        need(
            isinstance(enrollment.command, EnrollPasswordAccount)
            and enrollment.pin == expected_pin
            and binding.registration_event_id == enrollment.event_id
            and registered.registration_event_id
            == enrollment.command.subject_registration_event_id,
            "execution_enrollment_origin_changed",
        )
        need(
            binding.model_dump(mode="json", exclude={"registration_event_id", "principal_sha256"})
            == owner
            and binding.principal_sha256 == subject.principal_sha256
            and registered.principal_sha256 == subject.principal_sha256
            and registered.auth_generation == subject.auth_generation
            and execution_rows.get(("pairing_subject_accounts", identity)) == owner
            and execution_rows.get(
                ("pairing_sql_accounts", fingerprint({"candidate_id": subject.candidate_id}))
            )
            == owner
            and execution_rows.get(("pairing_auth_high_water", identity))
            == {
                "subject_uuid": identity,
                "auth_generation": subject.auth_generation,
                "principal_sha256": subject.principal_sha256,
            }
            and execution_rows.get(("password_credential_revisions", identity))
            == {
                "subject_uuid": identity,
                "auth_generation": subject.auth_generation,
                "credential_sha256": subject.credential_sha256,
            },
            "execution_latest_revision_changed",
        )
        if identity in denied_subjects:
            # Original e23 scoped delete changes protected denial records only.
            # A separate execution denial needs its own complete event/effect
            # proof; these fixed current-value projections cannot qualify it.
            need(
                registered.active is True
                and execution_rows.get(
                    ("pairing_account_tombstones", str(subject.account_binding_id))
                )
                is None
                and execution_rows.get(("revocations", revoked.key)) is None,
                "terminal_execution_denial_cohort_unsupported",
            )
            counts = absent_counts.get(subject.candidate_id)
            need(
                subject.candidate_id not in sql
                and type(counts) is dict
                and set(counts) == set(SQL_ENTITIES)
                and all(type(value) is int and value == 0 for value in counts.values()),
                "terminal_denial_sql_not_absent",
            )
            absent.add(subject.candidate_id)
            classifications.append(
                {
                    "candidate_id": subject.candidate_id,
                    "classification": "terminal_denial_sql_absence",
                }
            )
        else:
            current_sql = sql.get(subject.candidate_id)
            expected = {
                **subject.model_dump(mode="json", exclude={"enrollment_operation"}),
                "state": "ACTIVE",
                "user_id": subject.candidate_id,
                "password_sha256": subject.credential_sha256,
            }
            need(
                registered.active is True
                and current_sql == expected
                and execution_rows.get(
                    ("pairing_account_tombstones", str(subject.account_binding_id))
                )
                is None
                and execution_rows.get(("revocations", revoked.key)) is None,
                "live_sql_latest_revision_changed",
            )
            classifications.append(
                {
                    "candidate_id": subject.candidate_id,
                    "classification": "live_exact_latest_revision",
                }
            )
    need(
        set(execution_rows) == required_execution
        and set(absent_counts) == absent
        and set(sql)
        == {v.candidate_id for key, v in subjects.items() if key not in denied_subjects},
        "orphan_or_missing_projection",
    )
    return fingerprint(
        {
            "version": 1,
            "purpose": "schema26_lifecycle_data_join_only",
            "pin": expected_pin.model_dump(mode="json"),
            "protected": protected,
            "execution": execution,
            "reset_statuses": resets,
            "sql": [sql[key] for key in sorted(sql)],
            "absent": [
                {"candidate_id": key, "counts": absent_counts[key]} for key in sorted(absent)
            ],
            "classifications": classifications,
        }
    )
