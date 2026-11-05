"""Privileged one-shot reviewed payment-loss funding, never bank verification.

The fixed existing normal manager is the sole writer. Every phase requires the
genuine all-off Schema27 native bridge and its same-transaction migration receipt.
Prepare is read-only; an attempted apply can only be reconciled, never replayed.
No native/provider/credential/database operation occurs on import or --help.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "infra/gcp"), str(ROOT / "backend"), str(ROOT)]

import schema27_native_migration as native  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from scripts.reconcile_native_expenses import _json, _write, private_bytes  # noqa: E402

KIND = "reviewed-payment-funding-operator-v1"
MARKER = "payment-funding"
ACTION = "reviewed_payment_funding_operator_v1"
ACTOR = "harshilyogeshbhai886@gmail.com"
REASON = "Reviewed operator declarations; no bank or activation attestation"
SOURCE_FILES = (
    "infra/gcp/payment_funding_operator.py",
    "infra/gcp/schema27_native_migration.py",
    "backend/app/domains/credits/payment_expenses.py",
    "backend/app/domains/credits/payment_funding.py",
    "backend/app/domains/credits/finance.py",
    "backend/app/domains/credits/admission.py",
)


def need(value, code):
    native.rolling.need(value, "payment_funding_operator_" + code)


def now():
    return datetime.now(UTC)


def source_hash(root):
    need(root.resolve() == ROOT and Path(__file__).resolve() == root / SOURCE_FILES[0], "source_origin")
    values = {}
    for name in SOURCE_FILES:
        path = root / name
        need(path.is_file() and not path.is_symlink(), "source_file")
        values[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return native.rolling.sha(values)


@dataclass(frozen=True)
class FundingPlan:
    nonce: str
    native_plan_sha256: str
    source_sha256: str
    database_sha256: str
    facts_sha256: str
    expense_review: dict
    request: dict
    evidence: dict[str, str]
    created_at: str
    expires_at: str
    version: str = KIND

    def validate(self):
        need(type(self) is FundingPlan and self.version == KIND
             and type(self.nonce) is str and re.fullmatch(r"[a-f0-9]{32}", self.nonce), "plan_shape")
        need(all(type(value) is str and native.rolling.HASH.fullmatch(value) for value in
            (self.native_plan_sha256, self.source_sha256, self.database_sha256, self.facts_sha256)), "plan_hashes")
        start, end = datetime.fromisoformat(self.created_at), datetime.fromisoformat(self.expires_at)
        need(start.tzinfo is not None and end.tzinfo is not None
             and start < end <= start + timedelta(hours=1), "plan_time")
        checked_request(self.request)
        verify_evidence(self.request, self.evidence)
        need(type(self.expense_review) is dict and set(self.expense_review)
             == {"components", "total_components_count", "unresolved_count"}
             and type(self.expense_review["components"]) is list
             and len(self.expense_review["components"]) == len(self.request["allocations"])
             and all(type(self.expense_review[key]) is int and self.expense_review[key] >= 0
                     for key in ("total_components_count", "unresolved_count")), "expense_review_shape")


def expense_review(snapshot, allocations):
    keys = {row.component_key for row in allocations}
    selected = [row.model_dump(mode="json") for row in snapshot.components if row.key in keys]
    need(len(selected) == len(keys), "expense_component_missing")
    return {"components": sorted(selected, key=lambda row: row["key"]),
        "total_components_count": len(snapshot.components), "unresolved_count": len(snapshot.unresolved)}


def domain():
    from app.domains.credits import finance, payment_expenses, payment_funding

    for module, name in ((finance, "finance"), (payment_expenses, "payment_expenses"),
                         (payment_funding, "payment_funding")):
        need(Path(module.__file__).resolve() == ROOT / f"backend/app/domains/credits/{name}.py", "domain_origin")
    return finance, payment_expenses, payment_funding


def checked_request(value):
    finance, _, funding = domain()
    need(type(value) is dict and set(value) == {"policy", "declaration", "allocations"}
         and type(value["allocations"]) is list and len(value["allocations"]) <= 128
         and (value["declaration"] is not None or bool(value["allocations"])), "request_shape")
    policy = finance.FinancialPolicy.model_validate(value["policy"])
    declaration = (funding.FundingDeclaration.model_validate(value["declaration"])
                   if value["declaration"] is not None else None)
    allocations = tuple(funding.AllocationRequest.model_validate(row) for row in value["allocations"])
    need(len({row.component_key for row in allocations}) == len(allocations), "duplicate_component")
    canonical = {"policy": policy.model_dump(mode="json"),
        "declaration": declaration.model_dump(mode="json") if declaration else None,
        "allocations": [row.model_dump(mode="json") for row in allocations]}
    need(value == canonical, "canonical_request_required")
    return policy, declaration, allocations


def verify_evidence(request, evidence):
    need(type(evidence) is dict and 1 <= len(evidence) <= 32, "evidence_shape")
    required = {row["expense_evidence_sha256"] for row in request["allocations"]}
    if request["declaration"] is not None:
        required.add(request["declaration"]["evidence_sha256"])
    need(set(evidence) == required and all(type(sha) is str and native.rolling.HASH.fullmatch(sha)
        and type(path) is str for sha, path in evidence.items()), "evidence_scope")
    total = 0
    for sha, path in evidence.items():
        raw = private_bytes(Path(path), 16 * 1024 * 1024)
        total += len(raw)
        need(total <= 32 * 1024 * 1024 and hashlib.sha256(raw).hexdigest() == sha, "evidence_changed")


class NativeFundingBoundary:
    """Fixed genuine native context and normal manager; no DSN/plugin ports."""

    def __init__(self, root, target_root, prior_root, migration_plan, bridge_journal):
        need(root.resolve() == ROOT and type(migration_plan) is native.Migration27Plan, "native_context")
        migration_plan.validate()
        self.root, self.plan = root, migration_plan
        self.native = native.NativeMigration27Boundary(root, target_root, prior_root,
            migration_plan.bridge, bridge_journal)

    def verify(self):
        need(type(self) is NativeFundingBoundary and type(self.native) is native.NativeMigration27Boundary,
             "native_context")
        self.native.verify_live(self.plan)
        with self.native.readonly(native.contract.SCHEMA) as db:
            result = native.reconcile_sql(db, self.plan)
            need(result.get("migration_committed") is True, "schema27_migration_receipt_required")
            catalog = native.contract.observe(db, native.app26.bootstrap_identity())
            need(native.rolling.sha(native.step.old_acls(db, native.app26.bootstrap_identity()))
                 == self.plan.retained_acl_sha256, "retained_acl_changed")
        return native.rolling.sha({"native_plan": native.rolling.sha(asdict(self.plan)),
            "migration_receipt": result["receipt_sha256"], "catalog": catalog})

    @contextmanager
    def metadata(self):
        from sqlalchemy import event

        need(not any(name in sys.modules for name in ("app.database", "app.models", "backend.app.database",
            "backend.app.models")), "metadata_fresh_process_required")
        neon = self.native._neon()
        binding, target = neon.collect_schema27_release_target(self.native.bridge.observer,
            self.plan.bridge.runtime_secret, alias="replacement_runtime",
            database_owner_oid=neon.FRESH_DATABASE_OWNER_OID, expected_schema=native.contract.SCHEMA)
        need(binding.purpose == "schema27_fresh_release_capture", "metadata_purpose")
        url = target._url.render_as_string(hide_password=False)
        previous = {name: os.environ.get(name) for name in ("DATABASE_URL", "APP_ENV")}
        database = None

        def deny(*_args, **_kwargs):
            need(False, "unused_runtime_connection_forbidden")

        try:
            os.environ.update(DATABASE_URL=url, APP_ENV="production")
            database = importlib.import_module("app.database")
            origin = database.__file__
            need(isinstance(origin, str), "metadata_origin")
            assert isinstance(origin, str)
            need(Path(origin).resolve() == self.root / "backend/app/database.py"
                 and database.DATABASE_URL == url, "metadata_origin")
            event.listen(database.engine, "do_connect", deny)
            models = importlib.import_module("app.models")
            origin = models.__file__
            need(isinstance(origin, str), "metadata_origin")
            assert isinstance(origin, str)
            need(Path(origin).resolve() == self.root / "backend/app/models.py"
                 and models.User.metadata is database.Base.metadata, "metadata_origin")
            yield
        finally:
            if database is not None:
                database.engine.dispose()
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    @contextmanager
    def session(self, *, readonly, consistent=False):
        engine = self.native.bootstrap_engine()
        try:
            with Session(engine) as db:
                connection = db.connection()
                connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL "
                    + ("REPEATABLE READ " if consistent else "READ COMMITTED ")
                    + ("READ ONLY" if readonly else "READ WRITE"))
                connection.exec_driver_sql("SET LOCAL search_path=pg_catalog,public")
                connection.exec_driver_sql("SET LOCAL statement_timeout='10000ms'")
                connection.exec_driver_sql("SET LOCAL lock_timeout='1000ms'")
                connection.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
                native.step.identity_step.verify_identity(connection, native.app26.bootstrap_identity())
                need(connection.execute(text("SELECT version_num FROM public.alembic_version LIMIT 2")).scalars().all()
                     == [native.contract.SCHEMA],
                     "schema27_required")
                native.contract.observe(connection, native.app26.bootstrap_identity())
                yield db
                if readonly:
                    db.rollback()
        finally:
            engine.dispose()

    def close(self):
        self.native.bridge.close()


def prepare(boundary, request, evidence, *, nonce, instant=None):
    need(type(boundary) is NativeFundingBoundary, "native_context")
    with native.successor._original_journal_lock(boundary.native.bridge_journal):
        context = boundary.verify()
        policy, declaration, allocations = checked_request(request)
        verify_evidence(request, evidence)
        finance, expenses, _ = domain()
        finance.require_current_policy(policy, now=instant or now())
        with boundary.session(readonly=True, consistent=True) as db:
            snapshot = expenses.collect_expenses(db, policy=policy)
        need(boundary.verify() == context, "native_context_changed")
        # Native metadata can be slow. No pre-observation clock authorizes a
        # newly captured plan or extends the explicit manual review windows.
        stamp = instant or now()
        finance.require_current_policy(policy, now=stamp)
        reviews = tuple(row for row in (declaration, *allocations) if row is not None)
        need(all(row.reviewed_at <= stamp < row.review_valid_until for row in reviews), "manual_review_not_current")
        expiry = min(stamp + timedelta(hours=1), policy.expires_at,
                     *(row.review_valid_until for row in reviews))
        plan = FundingPlan(nonce, native.rolling.sha(asdict(boundary.plan)), source_hash(boundary.root),
            context, snapshot.facts_sha256, expense_review(snapshot, allocations), request, evidence, stamp.isoformat(),
            expiry.isoformat())
        plan.validate()
        return plan


def intent(plan):
    return {"version": KIND, "nonce": plan.nonce, "source_sha256": plan.source_sha256,
        "database_sha256": plan.database_sha256, "facts_sha256": plan.facts_sha256,
        "request_sha256": native.rolling.sha(plan.request), "replay_authorized": False}


def audit_id(plan):
    return "aud_payment_operator_" + plan.nonce


def applied(db, plan):
    from app.models import AdminAuditEvent

    row = db.get(AdminAuditEvent, audit_id(plan))
    if row is None:
        return None
    need(row.actor_user_id is None and row.actor_email == ACTOR and row.action == ACTION
         and row.target_type == "payment_loss_funding" and row.target_id == plan.nonce
         and row.correlation_id == plan.nonce and row.reason == REASON and row.before_state == intent(plan)
         and type(row.after_state) is dict and set(row.after_state)
         == {"plan_sha256", "domain_results", "domain_audits", "applied_at", "bank_cash_independently_verified", "activation_granted"}
         and row.after_state["plan_sha256"] == native.rolling.sha(asdict(plan))
         and row.after_state["bank_cash_independently_verified"] is False
         and row.after_state["activation_granted"] is False, "operator_receipt_conflict")
    recorded = datetime.fromisoformat(row.after_state["applied_at"])
    created = row.created_at.replace(tzinfo=UTC) if row.created_at.tzinfo is None else row.created_at
    need(recorded.tzinfo is not None and created == recorded
         and datetime.fromisoformat(plan.created_at) <= recorded < datetime.fromisoformat(plan.expires_at),
         "operator_receipt_time")
    _, _, funding = domain()
    identifiers = domain_ids(plan, row.after_state["domain_results"])
    need(funding.audit_rows(db, identifiers) == row.after_state["domain_audits"], "domain_audit_changed")
    return row.after_state


def domain_ids(plan, results):
    need(type(results) is dict and set(results) == {"declaration", "allocations"}
         and type(results["allocations"]) is list
         and len(results["allocations"]) == len(plan.request["allocations"]), "domain_result_shape")
    identifiers: list[str] = []
    if plan.request["declaration"] is None:
        need(results["declaration"] is None, "domain_result_shape")
    else:
        declaration = results["declaration"]
        need(type(declaration) is dict and set(declaration) == {"funding_id", "review_id"}, "domain_result_shape")
        identifiers.extend(declaration[key] for key in ("funding_id", "review_id"))
    for allocation in results["allocations"]:
        need(type(allocation) is dict and set(allocation) == {"allocation_id", "covered_minor", "required_minor"}
             and all(type(allocation[key]) is int and allocation[key] >= 0
                     for key in ("covered_minor", "required_minor")), "domain_result_shape")
        identifiers.append(allocation["allocation_id"])
    need(identifiers and len(identifiers) <= 256
         and all(type(value) is str and native.rolling.HASH.fullmatch(value) for value in identifiers), "domain_result_ids")
    return tuple(sorted(set(identifiers)))


def require_admission_current(plan, policy, declaration, allocations, stamp):
    finance, _, _ = domain()
    need(datetime.fromisoformat(plan.created_at) <= stamp < datetime.fromisoformat(plan.expires_at), "plan_expired")
    finance.require_current_policy(policy, now=stamp, commercial=True)
    need(all(row.reviewed_at <= stamp < row.review_valid_until
             for row in (declaration, *allocations) if row is not None), "manual_review_not_current")


def execute(plan, boundary, journal, *, reconcile=False, instant=None):
    plan.validate()
    need(type(boundary) is NativeFundingBoundary and type(journal) is native.rolling.Journal
         and journal.plan_hash == native.rolling.sha(asdict(plan))
         and plan.native_plan_sha256 == native.rolling.sha(asdict(boundary.plan))
         and plan.source_sha256 == source_hash(boundary.root), "exact_plan_required")
    with native.successor._original_journal_lock(boundary.native.bridge_journal), journal.locked():
        started = journal.read(MARKER + ".started")
        ack = journal.read(MARKER + ".commit-acknowledged")
        checked = journal.read(MARKER + ".checked")
        need(started is not None or ack is checked is None, "orphan_receipt")
        need((started is not None) == reconcile and (started is None or started
             == intent(plan) | {"plan_sha256": journal.plan_hash}), "started_reconcile_only")
        need(ack is None or (set(ack) == {"plan_sha256", "receipt_sha256"}
             and type(ack["receipt_sha256"]) is str and native.rolling.HASH.fullmatch(ack["receipt_sha256"])),
             "commit_ack_changed")
        need(boundary.verify() == plan.database_sha256, "native_context_changed")
        policy, declaration, allocations = checked_request(plan.request)
        finance, expenses, funding = domain()
        if not reconcile:
            stamp = instant or now()
            require_admission_current(plan, policy, declaration, allocations, stamp)
            # A durable original intent must ACK before ANY funding mutation.
            journal.record(MARKER + ".started", intent(plan))
            need(journal.read(MARKER + ".started") == intent(plan) | {"plan_sha256": journal.plan_hash}, "intent_changed")
            with boundary.session(readonly=False) as db:
                from app.domains.credits.admission import lock_money

                lock_money(db)
                snapshot = expenses.collect_expenses(db, policy=policy)
                need(snapshot.facts_sha256 == plan.facts_sha256 and expense_review(snapshot, allocations)
                     == plan.expense_review and applied(db, plan) is None, "expense_preimage_changed")
                # A waiting money lock or bounded SQL read may cross expiry.
                # Revalidate at the actual first-mutation admission point.
                stamp = instant or now()
                require_admission_current(plan, policy, declaration, allocations, stamp)
                results = {"declaration": funding.register_declaration(db, declaration, now=stamp)
                           if declaration is not None else None,
                    "allocations": [funding.allocate_loss(db, row, policy=policy, now=stamp) for row in allocations]}
                receipt = {"plan_sha256": journal.plan_hash, "domain_results": results,
                    "domain_audits": funding.audit_rows(db, domain_ids(plan, results)),
                    "applied_at": stamp.isoformat(),
                    "bank_cash_independently_verified": False, "activation_granted": False}
                from app.models import AdminAuditEvent

                db.add(AdminAuditEvent(id=audit_id(plan), actor_user_id=None, actor_email=ACTOR,
                    action=ACTION, target_type="payment_loss_funding", target_id=plan.nonce,
                    correlation_id=plan.nonce, reason=REASON,
                    before_state=intent(plan), after_state=receipt, created_at=stamp))
                db.flush()
                db.commit()
            known = {"receipt_sha256": native.rolling.sha(receipt), "plan_sha256": journal.plan_hash}
            journal.record(MARKER + ".commit-acknowledged", known)
            need(journal.read(MARKER + ".commit-acknowledged") == known, "commit_ack_changed")
            ack = known
        with boundary.session(readonly=True) as db:
            # This shared lock refuses an unacknowledged transaction still running.
            from app.domains.credits.admission import lock_money

            lock_money(db)
            observed = applied(db, plan)
        need(boundary.verify() == plan.database_sha256, "native_context_changed")
        need(ack is None or (observed is not None and ack == {"plan_sha256": journal.plan_hash,
             "receipt_sha256": native.rolling.sha(observed)}), "known_commit_missing_or_changed")
        result = {"status": "operator_transaction_observed" if observed is not None else "no_receipt_observed_reconcile_only",
            "transaction_observed": observed is not None, "receipt_sha256": native.rolling.sha(observed) if observed else None,
            "replay_authorized": False, "bank_cash_independently_verified": False, "activation_granted": False}
        need(checked is None or checked == result | {"plan_sha256": journal.plan_hash}, "checked_observation_changed")
        if observed is not None and checked is None:
            journal.record(MARKER + ".checked", result)
        return result


def load(raw):
    result = FundingPlan(**_json(raw))
    result.validate()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "apply", "reconcile"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--prior-root", type=Path, required=True)
    parser.add_argument("--migration-plan", type=Path, required=True)
    parser.add_argument("--migration-plan-sha256", required=True)
    parser.add_argument("--bridge-journal", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--accept-reviewed-declaration", action="store_true")
    args = parser.parse_args()
    migration = native.load(native.app26.initial._read_pinned(args.migration_plan, args.migration_plan_sha256))
    owner = native.rolling.Journal(args.bridge_journal, migration.bridge)
    boundary = NativeFundingBoundary(args.root, args.target_root, args.prior_root, migration, owner)
    try:
        # Native gates and27 physical reads occur before importing app metadata.
        boundary.verify()
        with boundary.metadata():
            if args.phase == "prepare":
                need(args.request is not None and args.plan_sha256 is None and args.journal is None
                     and not args.accept_reviewed_declaration, "prepare_arguments")
                value = _json(private_bytes(args.request, 131072))
                need(type(value) is dict and set(value) == {"request", "evidence"}, "request_envelope")
                plan = prepare(boundary, value["request"], value["evidence"], nonce=uuid4().hex)
                need(len(native.rolling.canonical(asdict(plan))) + 1 <= 131072, "plan_byte_bound")
                digest = _write(args.plan, asdict(plan))
                result = {"status": "reviewed_plan_prepared", "plan_sha256": digest, "database_mutations": 0,
                    "bank_cash_independently_verified": False, "activation_granted": False}
            else:
                need(args.request is None and args.journal is not None and args.plan_sha256 is not None
                     and args.accept_reviewed_declaration == (args.phase == "apply"), "explicit_phase_arguments")
                raw = private_bytes(args.plan, 131072)
                need(hashlib.sha256(raw).hexdigest() == args.plan_sha256, "plan_changed")
                plan = load(raw)
                result = execute(plan, boundary, native.rolling.Journal(args.journal, plan), reconcile=args.phase == "reconcile")
        print(json.dumps(result, sort_keys=True))
    finally:
        boundary.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(json.dumps({"status": "payment_funding_stopped_reconcile_same_plan", "replay_authorized": False,
            "bank_cash_independently_verified": False, "activation_granted": False}, sort_keys=True))
        raise SystemExit(1) from None
