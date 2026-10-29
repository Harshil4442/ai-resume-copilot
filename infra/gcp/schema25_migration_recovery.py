"""Exact, locally linked correction of the failed schema25 metadata import.

The old upgrade is never replayed. A fresh captured plan/journal receives one
corrected attempt only after a native read-only rollback proof. This module
performs no provider action at import and never prints a credential or row.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import unified_credit_schema25_release as release

ORIGINAL_PLAN_SHA256 = ("22b838a59c4cb1f00aa917590a9022be403"
                        "2886df22d95415895ad1e09761b5e")
ORIGINAL_STARTED_SHA256 = ("a4f81c8ea4b6f230638224843e9615776e"
                           "c62e9912d03bd9377eb33be2434182")
ORIGINAL_JOURNAL_PATH_SHA256 = ("9836bc7f32ac0e010707993ab0fbd6d4219"
                                "ad603de47c4be1527cca6c7b54f2f")
CANDIDATE = "8cbdfaeb9bb6cf6674c18ca8d59bc123fbf73a2a"
PREFIX = "schema25-recovery."


def need(value, code):
    release.rolling.need(value, "schema25_recovery_" + code)


def path_hash(path):
    return hashlib.sha256(str(path).encode()).hexdigest()


def source_hashes(expected_metadata):
    need(release.rolling.HASH.fullmatch(expected_metadata or ""), "metadata_source_pin_required")
    root = Path(__file__).resolve().parent
    result = {}
    for name in ("schema25_migration_metadata.py", "schema25_migration_recovery.py",
                 "unified_credit_schema25_release.py"):
        path = root / name
        need(path.is_file() and not path.is_symlink() and path.stat().st_size <= 1_000_000,
             "operator_source_unavailable")
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    need(result["schema25_migration_metadata.py"] == expected_metadata, "metadata_source_changed")
    return result


def validate_link(old, new):
    old.validate()
    new.validate()
    need(type(old) is release.Schema25Plan and type(new) is release.Schema25Plan
         and old.database_schema == new.database_schema == release.contract.BEFORE
         and old.release == new.release == CANDIDATE and old.nonce != new.nonce,
         "fresh_exact_candidate_plan_required")
    before, after = asdict(old), asdict(new)
    for value in (before, after):
        value.pop("nonce")
        for service in value["services"]:
            service.pop("candidate_template_sha256")
    need(before == after, "fresh_plan_preimage_changed")


def original_started(old, journal):
    need(type(journal) is release.rolling.Journal
         and journal.plan_hash == release.rolling.sha(asdict(old))
         and path_hash(journal.directory) == ORIGINAL_JOURNAL_PATH_SHA256,
         "exact_original_journal_required")
    allowed = {"owner.lock", "schema25-migration.started.json", "schema25-migration.checked.json",
               *(PREFIX + phase + ".json" for phase in ("proof", "started", "checked"))}
    need({path.name for path in journal.directory.iterdir()} <= allowed,
         "original_journal_scope_changed")
    path = journal.directory / "schema25-migration.started.json"
    need(path.is_file() and not path.is_symlink() and path.stat().st_size <= 131072
         and path.stat().st_uid == os.getuid() and path.stat().st_mode & 0o077 == 0
         and hashlib.sha256(path.read_bytes()).hexdigest() == ORIGINAL_STARTED_SHA256,
         "original_started_changed")
    started = journal.read("schema25-migration.started")
    need(started is not None and started.get("expected") == release.contract.BEFORE
         and started.get("target") == release.contract.SCHEMA
         and started.get("replay_authorized") is False
         and release.rolling.HASH.fullmatch(started.get("retained_sha256", ""))
         and type(started.get("schema_acl")) is list
         and journal.read("schema25-migration.checked") is None,
         "original_outcome_not_failed_uncommitted")
    return started


def journal_link(old, new, original, fresh, metadata_sha, *, attempted):
    validate_link(old, new)
    started = original_started(old, original)
    need(type(fresh) is release.rolling.Journal
         and fresh.plan_hash == release.rolling.sha(asdict(new))
         and fresh.directory != original.directory, "fresh_journal_required")
    allowed = {"owner.lock"}
    if attempted:
        allowed |= {"schema25-migration.started.json", "schema25-migration.checked.json"}
        need(fresh.read("schema25-migration.started") is not None,
             "corrected_attempt_not_started")
    else:
        need(fresh.read("schema25-migration.started") is None
             and fresh.read("schema25-migration.checked") is None, "fresh_journal_already_used")
    need({p.name for p in fresh.directory.iterdir()} <= allowed, "fresh_journal_scope_changed")
    binding = {"version": 1, "original_plan_sha256": ORIGINAL_PLAN_SHA256,
               "original_started_sha256": ORIGINAL_STARTED_SHA256,
               "new_plan_sha256": fresh.plan_hash,
               "new_journal_path_sha256": path_hash(fresh.directory),
               "candidate": CANDIDATE, "operator_sources": source_hashes(metadata_sha),
               "automatic_replay": False}
    return started, binding


def rollback_proof(boundary, started):
    """Fresh bound native reads plus a transaction-scoped coordination lock."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    identity = release.bootstrap_identity()
    password = release.docker.bootstrap_password(time.monotonic() + 60)
    database = release.writes.transfer.BoundDatabase(
        release.writes.transfer.NativeDatabaseIdentity(
            release.writes.TARGET_HOST, 5432, "neondb", release.writes.TARGET_CLUSTER,
            16395, identity.bootstrap, identity.bootstrap_oid, str(release.docker.CA)), password)
    engine = create_engine("postgresql+psycopg://", creator=database.connect,
                           poolclass=NullPool, echo=False, hide_parameters=True)
    try:
        with engine.connect() as connection, connection.begin():
            connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            connection.exec_driver_sql("SET LOCAL statement_timeout='30000ms'")
            connection.exec_driver_sql("SET LOCAL lock_timeout='1000ms'")
            release.identity_step.verify_identity(connection, identity)
            need(connection.execute(text("SELECT pg_try_advisory_xact_lock(:key)"),
                                    {"key": release.step.LOCK_ID}).scalar_one() is True,
                 "migration_lock_not_free")
            release.contract.verify_before_shape(connection, identity.namespace_oid, identity.namespace)
            acl = [list(row) for row in release.identity_step.schema_acl(connection, identity)]
            need(acl == started["schema_acl"], "original_acl_changed")
            connection.exec_driver_sql(f'SET LOCAL ROLE "{identity.migrator}"')
            release.identity_step.drained(connection, identity)
            retained = release.step.retained_digest(connection, identity.namespace_oid, identity.namespace)
            need(retained == started["retained_sha256"], "original_history_changed")
            connection.exec_driver_sql("RESET ROLE")
        # The read-only transaction ends here, releasing the advisory xact lock.
        return {"schema": release.contract.BEFORE, "new_table_count": 0,
                "retained_sha256": retained, "schema_acl": acl,
                "native_identity_verified": True, "advisory_lock_exclusive": True,
                "accepted_work_drained": True, "read_only_transaction": True}
    finally:
        engine.dispose()


def validate_proof(proof, started):
    need(type(proof) is dict and proof == {
        "schema": release.contract.BEFORE, "new_table_count": 0,
        "retained_sha256": started["retained_sha256"], "schema_acl": started["schema_acl"],
        "native_identity_verified": True, "advisory_lock_exclusive": True,
        "accepted_work_drained": True, "read_only_transaction": True}, "rollback_not_proven")


def preflight(new, boundary):
    need(type(boundary) is release.NativeSchema25Boundary
         and boundary.schema == release.contract.BEFORE, "native_boundary_required")
    boundary.verify_global(new)
    boundary.observed_disabled_flags(new)
    boundary.verify_coordinator_closed()
    for pin in new.services:
        release.rolling.stage_body(new, pin, boundary.service(pin.service))


def execute(phase, old, new, original, fresh, boundary, metadata_sha):
    """Only upgrade may spend the single local correction claim; never replay it."""
    need(phase in {"prove", "upgrade", "reconcile"}, "phase_invalid")
    with original.locked():
        started, binding = journal_link(old, new, original, fresh, metadata_sha,
                                        attempted=phase == "reconcile")
        prior_attempt = original.read(PREFIX + "started")
        receipt = original.read(PREFIX + "proof")
        need(original.read(PREFIX + "checked") is None or prior_attempt is not None,
             "recovery_checked_without_attempt")
        if phase == "reconcile":
            need(prior_attempt is not None and prior_attempt.get("binding") == binding
                 and receipt is not None
                 and prior_attempt.get("proof_sha256") == release.rolling.sha(receipt),
                 "exact_corrected_attempt_required")
            # Existing migration reconciliation is strictly read-only. A checked
            # new journal is handled separately if its final local ACK was lost.
            checked = fresh.read("schema25-migration.checked")
            if checked is None:
                result = release.migration(new, boundary, fresh, reconcile=True)
            else:
                need(checked.get("schema") == release.contract.SCHEMA
                     and checked.get("retained_sha256") == started["retained_sha256"]
                     and checked.get("replay_authorized") is False,
                     "corrected_checked_changed")
                observed = release.NativeSchema25Boundary(boundary.root, release.contract.SCHEMA,
                                                          new.prior_sources)
                observed.verify_global(replace(new, database_schema=release.contract.SCHEMA))
                result = {"status": "schema25_committed_and_freshly_observed",
                          "schema": release.contract.SCHEMA, "replay_authorized": False}
        else:
            need(prior_attempt is None, "corrected_attempt_already_spent")
            preflight(new, boundary)
            proof = rollback_proof(boundary, started)
            validate_proof(proof, started)
            value = {"binding": binding, "rollback_proof": proof}
            if receipt is None:
                original.record(PREFIX + "proof", value)
                receipt = original.read(PREFIX + "proof")
            need(receipt is not None and receipt.get("binding") == binding
                 and receipt.get("rollback_proof") == proof, "recovery_proof_changed")
            if phase == "prove":
                return {"status": "schema21_rollback_proven_corrected_attempt_available",
                        "proof_sha256": release.rolling.sha(receipt),
                        "provider_mutations": 0, "replay_authorized": False}
            original.record(PREFIX + "started", {"binding": binding,
                            "proof_sha256": release.rolling.sha(receipt),
                            "replay_authorized": False})
            # The old journal remains locked and its create-only link is spent
            # before the new migration journal can record/send its one attempt.
            result = release.migration(new, boundary, fresh)
        if result.get("schema") == release.contract.SCHEMA:
            completed = {"binding_sha256": release.rolling.sha(binding),
                         "schema": release.contract.SCHEMA, "replay_authorized": False}
            checked = original.read(PREFIX + "checked")
            if checked is None:
                original.record(PREFIX + "checked", completed)
            else:
                need(all(checked.get(k) == v for k, v in completed.items()),
                     "recovery_checked_changed")
        return result | {"linked_original_plan_sha256": ORIGINAL_PLAN_SHA256,
                         "corrected_attempt_replay_authorized": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("phase", choices=("prove", "upgrade", "reconcile"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--original-plan", type=Path, required=True)
    parser.add_argument("--original-plan-sha256", required=True)
    parser.add_argument("--original-journal", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--metadata-sha256", required=True)
    args = parser.parse_args()
    need(args.original_plan_sha256 == ORIGINAL_PLAN_SHA256, "exact_original_plan_required")
    old = release.load_plan(release._read_pinned(args.original_plan, ORIGINAL_PLAN_SHA256))
    new = release.load_plan(release._read_pinned(args.plan, args.plan_sha256))
    original = release.rolling.Journal(args.original_journal, old)
    fresh = release.rolling.Journal(args.journal, new)
    boundary = release.NativeSchema25Boundary(args.root, new.database_schema, new.prior_sources)
    print(json.dumps(execute(args.phase, old, new, original, fresh, boundary,
                             args.metadata_sha256), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        code = "unclassified_failure"
        if type(error) is release.rolling.RollingDenied and len(error.args) == 1 \
                and re.fullmatch(r"schema25_recovery_[a-z_]{1,90}", str(error.args[0])):
            code = error.args[0]
        else:
            code = release.failure_response(error)["code"]
        print(json.dumps({"status": "schema25_recovery_stopped_reconcile_only", "code": code,
                          "replay_authorized": False}))
        raise SystemExit(2) from None
