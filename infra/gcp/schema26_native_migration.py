"""PRIVATE finite25→26 native caller; no action occurs on import/help.

Distinct from the frozen schema25 operator. Upgrade owns exactly one supplied
transaction; reconcile reads the same journal/target and never invokes DDL.
No provider IAM/flag/traffic/credential mutation is exposed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path[:0] = [
    str(Path(__file__).resolve().parent),
    str(Path(__file__).resolve().parents[2] / "backend"),
    str(Path(__file__).resolve().parents[2]),
]
import rolling_release as rolling  # noqa: E402
import schema26_app_rolling_v3 as app26  # noqa: E402
import schema26_maintenance_guard as guard  # noqa: E402
import schema26_migration_metadata as metadata  # noqa: E402

from scripts import evidence_writer_schema26_contract as contract  # noqa: E402
from scripts import evidence_writer_schema26_step as step  # noqa: E402

KIND = "hirewiz_schema26_native_migration_v3"
MARKER = "schema26-migration"
PATHS = (
    "infra/gcp/schema26_native_migration.py",
    "infra/gcp/schema26_maintenance_guard.py",
    "infra/gcp/schema26_lifecycle_join.py",
    "infra/gcp/v3_readonly_adapter.py",
    "infra/gcp/schema26_app_rolling_v3.py",
    "infra/gcp/schema26_migration_metadata.py",
    "backend/scripts/evidence_writer_schema26_step.py",
    "backend/scripts/schema25_app_release_contract_v3.py",
)


def need(value, code):
    rolling.need(value, "schema26_migration_" + code)


def operator_sources(root):
    result = []
    for name in PATHS:
        path = root / name
        need(
            path.is_file() and not path.is_symlink() and path.stat().st_size <= 1048576,
            "operator_source_required",
        )
        result.append((name, hashlib.sha256(path.read_bytes()).hexdigest()))
    return tuple(result)


@dataclass(frozen=True)
class Migration26Plan:
    nonce: str
    candidate: app26.PriorSource
    compatibility_sha256: str
    operator_sources: tuple[tuple[str, str], ...]
    maintenance_baseline: app26.ClosedApiBaseline
    runtime_secret: str
    observer_secret: str
    retired_migration_secret: str
    workers_release: str
    resources: tuple[app26.ResourcePin, ...]
    financial_sha256: str
    acl_sha256: str
    protected_liabilities: tuple[tuple[str, str, int], ...]
    retired_sha256: str
    lifecycle_sha256: str
    sql_lifecycle_sha256: str
    schema_contract: str = KIND
    database_schema: str = contract.BEFORE
    target_schema: str = contract.SCHEMA

    @property
    def release(self):
        return self.candidate.release

    @property
    def ci_run_id(self):
        return self.candidate.ci_run_id

    def validate(self):
        need(
            type(self) is Migration26Plan
            and self.schema_contract == KIND
            and self.database_schema == contract.BEFORE
            and self.target_schema == contract.SCHEMA,
            "distinct_plan_required",
        )
        need(
            type(self.nonce) is str
            and re.fullmatch(r"[a-f0-9]{32}", self.nonce)
            and type(self.candidate) is app26.PriorSource
            and re.fullmatch(r"[a-f0-9]{40}", self.release)
            and self.release != app26.PRIOR.release
            and re.fullmatch(r"sha256:[a-f0-9]{64}", self.candidate.image_digest)
            and re.fullmatch(r"[a-f0-9-]{36}", self.candidate.build_id)
            and type(self.ci_run_id) is int
            and self.ci_run_id > 0,
            "candidate_identity",
        )
        need(
            all(
                type(value) is str and rolling.HASH.fullmatch(value)
                for value in (
                    self.compatibility_sha256,
                    self.financial_sha256,
                    self.acl_sha256,
                    self.retired_sha256,
                    self.lifecycle_sha256,
                    self.sql_lifecycle_sha256,
                )
            ),
            "snapshot_identity",
        )
        need(
            tuple(name for name, _ in self.operator_sources) == PATHS
            and all(rolling.HASH.fullmatch(value) for _, value in self.operator_sources),
            "operator_closure",
        )
        need(type(self.maintenance_baseline) is app26.ClosedApiBaseline, "closed_baseline_required")
        self.maintenance_baseline.validate()
        need(
            all(
                type(v) is str and rolling.REF.fullmatch(v)
                for v in (self.runtime_secret, self.observer_secret, self.retired_migration_secret)
            )
            and len(
                {
                    v.split(":")[0]
                    for v in (
                        self.runtime_secret,
                        self.observer_secret,
                        self.retired_migration_secret,
                    )
                }
            )
            == 3
            and re.fullmatch(r"[a-f0-9]{40}", self.workers_release),
            "numeric_secrets_required",
        )
        expected_resources = {
            (kind, name)
            for kind, names in app26.maintenance.RESOURCE_NAMES.items()
            for name in names
        }
        expected_resources |= {("service", name) for name in app26.historical.EXTERNAL_SERVICES}
        need(
            len(self.resources) == len(expected_resources)
            and {(r.kind, r.name) for r in self.resources} == expected_resources
            and all(
                type(r) is app26.ResourcePin and rolling.HASH.fullmatch(r.fingerprint)
                for r in self.resources
            ),
            "resource_scope",
        )
        need(
            type(self.protected_liabilities) is tuple
            and len(self.protected_liabilities) <= 256
            and len({r[0] for r in self.protected_liabilities}) == len(self.protected_liabilities)
            and all(
                type(i) is str
                and re.fullmatch(r"[A-Za-z0-9_:-]{1,64}", i)
                and rolling.HASH.fullmatch(h)
                and type(n) is int
                and n > 0
                for i, h, n in self.protected_liabilities
            ),
            "liability_scope",
        )


class NativeMigration26Boundary:
    def __init__(
        self, root, *, maintenance_plan_path, maintenance_plan_sha256, maintenance_journal
    ):
        self.root = root.resolve()
        # Load the pinned application SDK stack before normal NativeRun can
        # inject its bundled SDK dependency paths into this process.
        guard._native_client_types()
        self.app = app26.NativeApp26Boundary(
            self.root,
            maintenance_plan_path=maintenance_plan_path,
            maintenance_plan_sha256=maintenance_plan_sha256,
            maintenance_journal=maintenance_journal,
        )

    def source(self, candidate, compatibility):
        need(
            Path(__file__).resolve() == self.root / "infra/gcp/schema26_native_migration.py"
            and Path(app26.__file__).resolve() == self.root / "infra/gcp/schema26_app_rolling_v3.py"
            and Path(guard.__file__).resolve()
            == self.root / "infra/gcp/schema26_maintenance_guard.py"
            and Path(metadata.__file__).resolve()
            == self.root / "infra/gcp/schema26_migration_metadata.py"
            and Path(step.__file__).resolve()
            == self.root / "backend/scripts/evidence_writer_schema26_step.py",
            "exact_source_modules_required",
        )
        self.app.observer.verify_source_image(**asdict(candidate))
        self.app.observer._verify_source_image_provenance(**asdict(app26.PRIOR))
        observed = app26.contract.source_fingerprint(
            self.root, app26.initial.prior_app_files(self.root, app26.PRIOR.release)
        )
        need(observed == compatibility, "qualified_source_changed")
        # The unchanged existing method requires genuine three exact backend
        # qualification steps in addition to source's actual all-five CI jobs.
        self.app._compatibility(
            type(
                "QualificationPins",
                (),
                {"compatibility_sha256": compatibility, "ci_run_id": candidate.ci_run_id},
            )()
        )

    def capture(
        self,
        *,
        release,
        image_digest,
        build_id,
        ci_run_id,
        nonce,
        observer_secret,
        retired_migration_secret,
        workers_release,
    ):
        candidate = app26.PriorSource(release, image_digest, build_id, ci_run_id)
        compatibility = app26.contract.source_fingerprint(
            self.root, app26.initial.prior_app_files(self.root, app26.PRIOR.release)
        )
        self.source(candidate, compatibility)
        baseline = self.app._capture_maintenance_baseline()
        runtime_secret = self.runtime_reference()
        with guard.NativeMaintenanceDdlGuard(
            self.app, observer_secret=observer_secret
        ) as current_guard:
            need(current_guard.baseline == baseline, "closed_baseline_changed")
            signature, liabilities, acl_sha = self.observe_database(
                runtime_secret=runtime_secret,
                observer_secret=observer_secret,
                retired_migration_secret=retired_migration_secret,
                schema=contract.BEFORE,
            )
            resources = self.app._resources(workers_release)
            result = Migration26Plan(
                nonce,
                candidate,
                compatibility,
                operator_sources(self.root),
                baseline,
                runtime_secret,
                observer_secret,
                retired_migration_secret,
                workers_release,
                resources,
                signature,
                acl_sha,
                liabilities,
                current_guard.retired_sha256,
                current_guard.lifecycle_sha256,
                current_guard.sql_sha256,
            )
            result.validate()
            self.verify(result, schema=contract.BEFORE)
            return result

    def runtime_reference(self):
        current = self.app.service("ai-resume-parser")
        reference = (
            rolling.environment(current["template"])
            .get("DATABASE_URL", {})
            .get("valueSource", {})
            .get("secretKeyRef", {})
        )
        need(
            set(reference) == {"secret", "version"}
            and type(reference["secret"]) is str
            and type(reference["version"]) is str,
            "runtime_numeric_secret_required",
        )
        value = reference["secret"] + ":" + reference["version"]
        need(rolling.REF.fullmatch(value), "runtime_numeric_secret_required")
        return value

    def observe_database(
        self, *, runtime_secret, observer_secret, retired_migration_secret, schema
    ):
        """Exact25/26 normal-manager observer+runtime reads; never zero unknowns."""
        module = self.app.observer._module(
            "backend/scripts/monetary_cutover_preflight.py", "hirewiz_schema26_migration_observer"
        )
        neon = module.neon_identity
        retired = self.app.observer.describe_release_secret(retired_migration_secret)
        name, version = retired_migration_secret.split(":")
        need(
            retired.get("name") == f"projects/157225724590/secrets/{name}/versions/{version}"
            and retired.get("state") == "DISABLED",
            "retired_migrator_changed",
        )
        collector = (
            neon.collect_schema25_release_target
            if schema == contract.BEFORE
            else neon.collect_schema26_release_target
        )
        signature, liabilities, acl_sha = None, None, None
        for alias, reference in (
            ("observer", observer_secret),
            ("replacement_runtime", runtime_secret),
        ):
            binding, target = collector(
                self.app.observer,
                reference,
                alias=alias,
                database_owner_oid=neon.FRESH_DATABASE_OWNER_OID,
                expected_schema=schema,
            )
            engine = target.engine()
            try:
                with engine.connect() as db, db.begin():
                    db.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    db.exec_driver_sql("SET LOCAL statement_timeout='2000ms'")
                    neon.verify_transport(db, binding)
                    neon.verify_native_target(db, binding, neon.FRESH_DATABASE_OID)
                    if alias == "observer":
                        sessions, _ = module._native_session_ids(
                            db, neon.FRESH_DATABASE_OID, neon_binding=binding
                        )
                        rolling.release.verify_fresh_role_phase(
                            db, neon.FRESH_RELEASE_ACTORS, "serving", sessions
                        )
                        app26.initial.identity_step.verify_financial_catalog(db, module)
                        app26.verify_permissions(
                            db,
                            "public",
                            neon.FRESH_RELEASE_ACTORS["replacement_runtime"][0],
                            neon.FRESH_RELEASE_ACTORS["replacement_migration"][0],
                            neon.FRESH_RELEASE_ACTORS["observer"][0],
                        )
                        app26.initial.previous.contract.verify_reset_permissions(
                            db,
                            "public",
                            neon.FRESH_RELEASE_ACTORS["replacement_runtime"][0],
                            neon.FRESH_RELEASE_ACTORS["replacement_migration"][0],
                            neon.FRESH_RELEASE_ACTORS["observer"][0],
                        )
                        signature = (
                            step.financial.observe(db)
                            if schema == contract.BEFORE
                            else contract.observe(db)
                        )
                        liabilities = step.financial.liability_pins(db)
                        identity = app26.bootstrap_identity()
                        acl_sha = rolling.sha(
                            {
                                "schema": step.identity_step.schema_acl(db, identity),
                                "tables": step.affected_acl(db, identity),
                            }
                        )
                        if schema == contract.SCHEMA:
                            contract.verify_writer_permissions(
                                db,
                                runtime=neon.FRESH_RELEASE_ACTORS["replacement_runtime"][0],
                                migrator=neon.FRESH_RELEASE_ACTORS["replacement_migration"][0],
                            )
            finally:
                engine.dispose()
        need(
            signature is not None
            and liabilities is not None
            and self.app.observer.describe_release_secret(retired_migration_secret) == retired,
            "database_observation_changed",
        )
        return signature, liabilities, acl_sha

    def verify(self, plan, *, schema):
        plan.validate()
        self.source(plan.candidate, plan.compatibility_sha256)
        need(self.runtime_reference() == plan.runtime_secret, "runtime_secret_binding_changed")
        need(operator_sources(self.root) == plan.operator_sources, "operator_source_changed")
        self.app._verify_maintenance_baseline(plan.maintenance_baseline)
        need(
            self.app._capture_maintenance_baseline() == plan.maintenance_baseline,
            "closed_service_changed",
        )
        need(self.app._resources(plan.workers_release) == plan.resources, "resources_changed")
        guard.readonly_producer_drain(self.app)
        signature, liabilities, acl_sha = self.observe_database(
            runtime_secret=plan.runtime_secret,
            observer_secret=plan.observer_secret,
            retired_migration_secret=plan.retired_migration_secret,
            schema=schema,
        )
        need(acl_sha == plan.acl_sha256, "database_acl_changed")
        need(liabilities == plan.protected_liabilities, "protected_liabilities_changed")
        if schema == contract.BEFORE:
            need(signature == plan.financial_sha256, "before_catalog_changed")
        reader = guard.metadata.ReadOnlyMaintenanceMetadata()
        try:
            need(
                guard.retirement_fingerprint(reader.observe(), plan.maintenance_baseline)
                == plan.retired_sha256,
                "retirement_changed",
            )
        finally:
            reader.close()

    def bootstrap_engine(self):
        """Original Neon manager and BoundDatabase TLS/cluster/OID handshake."""
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool

        original = app26.initial
        identity = app26.bootstrap_identity()
        password = original.docker.bootstrap_password(time.monotonic() + 60)
        bound = original.writes.transfer.BoundDatabase(
            original.writes.transfer.NativeDatabaseIdentity(
                original.writes.TARGET_HOST,
                5432,
                "neondb",
                original.writes.TARGET_CLUSTER,
                16395,
                identity.bootstrap,
                identity.bootstrap_oid,
                str(original.docker.CA),
            ),
            password,
        )
        return create_engine(
            "postgresql+psycopg://",
            creator=bound.connect,
            poolclass=NullPool,
            echo=False,
            hide_parameters=True,
        )

    def prepare_guard(self, plan):
        result = guard.NativeMaintenanceDdlGuard(self.app, observer_secret=plan.observer_secret)
        try:
            need(
                (result.baseline, result.retired_sha256, result.lifecycle_sha256, result.sql_sha256)
                == (
                    plan.maintenance_baseline,
                    plan.retired_sha256,
                    plan.lifecycle_sha256,
                    plan.sql_lifecycle_sha256,
                ),
                "guard_snapshot_changed",
            )
            return result
        except Exception:
            result.close()
            raise


def migration(plan, boundary, journal, *, reconcile=False):
    """One durable intent, one transaction; no DDL from any reconciliation path."""
    from sqlalchemy import text

    plan.validate()
    need(
        type(boundary) is NativeMigration26Boundary
        and type(journal) is rolling.Journal
        and journal.plan_hash == rolling.sha(asdict(plan)),
        "exact_owned_plan_required",
    )
    identity = app26.bootstrap_identity()
    with journal.locked():
        started = journal.read(MARKER + ".started")
        need(journal.read(MARKER + ".checked") is None, "already_observed_never_replay")
        ready_prior = journal.read(MARKER + ".transaction-ready")
        ack_prior = journal.read(MARKER + ".commit-acknowledged")
        need(started is not None or ready_prior is ack_prior is None, "orphan_transaction_record")
        if ack_prior is not None:
            need(
                ready_prior is not None
                and ack_prior
                == {
                    "schema": contract.SCHEMA,
                    "replay_authorized": False,
                    "plan_sha256": journal.plan_hash,
                },
                "commit_ack_changed",
            )
        need((started is not None) == reconcile, "started_reconcile_only")
        engine, final = None, None
        try:
            if started is None:
                boundary.verify(plan, schema=contract.BEFORE)
                final = boundary.prepare_guard(plan)  # all auth/native construction OUTSIDE SQL
            else:
                need(
                    started
                    == {
                        "expected": contract.BEFORE,
                        "target": contract.SCHEMA,
                        "operator_sources_sha256": rolling.sha(plan.operator_sources),
                        "maintenance_sha256": rolling.sha(asdict(plan.maintenance_baseline)),
                        "protected_liabilities_sha256": rolling.sha(plan.protected_liabilities),
                        "replay_authorized": False,
                        "plan_sha256": journal.plan_hash,
                    },
                    "started_lineage_changed",
                )
                # Source/closed native custody can be re-read before discovering
                # whether this same transaction committed; no25-only guard here.
                boundary.source(plan.candidate, plan.compatibility_sha256)
                need(
                    boundary.runtime_reference() == plan.runtime_secret,
                    "runtime_secret_binding_changed",
                )
                need(
                    operator_sources(boundary.root) == plan.operator_sources,
                    "operator_source_changed",
                )
                boundary.app._verify_maintenance_baseline(plan.maintenance_baseline)
                need(
                    boundary.app._capture_maintenance_baseline() == plan.maintenance_baseline,
                    "closed_service_changed",
                )
                guard.readonly_producer_drain(boundary.app)
                reader = guard.metadata.ReadOnlyMaintenanceMetadata()
                try:
                    need(
                        guard.retirement_fingerprint(reader.observe(), plan.maintenance_baseline)
                        == plan.retired_sha256,
                        "retirement_changed",
                    )
                finally:
                    reader.close()
            engine = boundary.bootstrap_engine()
            if started is None:
                journal.record(
                    MARKER + ".started",
                    {
                        "expected": contract.BEFORE,
                        "target": contract.SCHEMA,
                        "operator_sources_sha256": rolling.sha(plan.operator_sources),
                        "maintenance_sha256": rolling.sha(asdict(plan.maintenance_baseline)),
                        "protected_liabilities_sha256": rolling.sha(plan.protected_liabilities),
                        "replay_authorized": False,
                    },
                )
                with metadata.initialized_metadata(
                    boundary.root, boundary.app.observer, plan.runtime_secret
                ):
                    with engine.connect() as db, db.begin():
                        result = step.upgrade_transaction(
                            db,
                            identity,
                            root=boundary.root,
                            flags={name: "false" for name in step.previous.DISABLED_FLAGS},
                            maintenance_guard=final,
                        )
                        need(
                            result["committed"] is False
                            and result["transaction_ready_to_commit"] is True
                            and result["acl_sha256"] == plan.acl_sha256,
                            "transaction_result_changed",
                        )
                        journal.record(
                            MARKER + ".transaction-ready",
                            {
                                "affected_sha256": result["affected_sha256"],
                                "schema_acl": result["schema_acl"],
                                "source_sha256": result["source_sha256"],
                                "acl_sha256": result["acl_sha256"],
                                "replay_authorized": False,
                            },
                        )
                journal.record(
                    MARKER + ".commit-acknowledged",
                    {"schema": contract.SCHEMA, "replay_authorized": False},
                )
            with engine.connect() as db, db.begin():
                db.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                step.identity_step.verify_identity(db, identity)
                need(
                    db.execute(
                        text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": step.LOCK_ID}
                    ).scalar_one()
                    is True,
                    "original_transaction_still_active",
                )
                observed_rows = (
                    db.execute(text("SELECT version_num FROM public.alembic_version LIMIT 2"))
                    .scalars()
                    .all()
                )
                need(
                    len(observed_rows) == 1
                    and observed_rows[0] in {contract.BEFORE, contract.SCHEMA},
                    "unexpected_schema",
                )
                observed = observed_rows[0]
                if observed == contract.BEFORE:
                    step.previous.verify_shape(db, identity.namespace_oid, identity.namespace)
                    return {
                        "status": "schema25_observed_reconcile_only",
                        "schema": observed,
                        "replay_authorized": False,
                        "migration_replayed": False,
                    }
                need(observed == contract.SCHEMA, "unexpected_schema")
                ready = journal.read(MARKER + ".transaction-ready")
                need(
                    ready is not None
                    and set(ready)
                    == {
                        "affected_sha256",
                        "schema_acl",
                        "source_sha256",
                        "acl_sha256",
                        "replay_authorized",
                        "plan_sha256",
                    }
                    and ready["source_sha256"] == contract.verify_source(boundary.root)
                    and ready["replay_authorized"] is False
                    and ready["acl_sha256"] == plan.acl_sha256,
                    "same_transaction_ready_required",
                )
                db.exec_driver_sql(f'SET LOCAL ROLE "{identity.migrator}"')
                contract.observe(db, namespace_oid=identity.namespace_oid)
                need(
                    [list(row) for row in step.identity_step.schema_acl(db, identity)]
                    == ready["schema_acl"],
                    "committed_acl_changed",
                )
                need(
                    rolling.sha(
                        {
                            "schema": step.identity_step.schema_acl(db, identity),
                            "tables": step.affected_acl(db, identity),
                        }
                    )
                    == plan.acl_sha256,
                    "committed_table_acl_changed",
                )
                need(
                    step.affected_digest(db, identity, after=True) == ready["affected_sha256"],
                    "committed_rows_changed",
                )
                step.financial.verify_liability_pins(db, plan.protected_liabilities)
                db.exec_driver_sql("RESET ROLE")
            boundary.verify(plan, schema=contract.SCHEMA)
            journal.record(
                MARKER + ".checked",
                {
                    "schema": contract.SCHEMA,
                    "transaction_ready_sha256": rolling.sha(ready),
                    "replay_authorized": False,
                },
            )
            return {
                "status": "schema26_committed_and_freshly_observed",
                "schema": contract.SCHEMA,
                "api_admission": "closed",
                "replay_authorized": False,
                "migration_replayed": False,
                "migration_login_reenabled": False,
                "goal_complete": False,
            }
        finally:
            if final is not None:
                final.close()
            if engine is not None:
                engine.dispose()


def load_plan(raw):
    value = rolling.release._json(raw)
    need(type(value) is dict and value.get("schema_contract") == KIND, "distinct_plan_required")
    value["candidate"] = app26.PriorSource(**value["candidate"])
    value["maintenance_baseline"]["original_pilot_flags"] = tuple(
        tuple(row) for row in value["maintenance_baseline"]["original_pilot_flags"]
    )
    value["maintenance_baseline"] = app26.ClosedApiBaseline(**value["maintenance_baseline"])
    value["operator_sources"] = tuple(tuple(row) for row in value["operator_sources"])
    value["protected_liabilities"] = tuple(tuple(row) for row in value["protected_liabilities"])
    value["resources"] = tuple(app26.ResourcePin(**row) for row in value["resources"])
    plan = Migration26Plan(**value)
    plan.validate()
    need(raw == rolling.canonical(asdict(plan)) + b"\n", "canonical_plan_required")
    return plan


def save_plan(path, plan):
    need(
        path.is_absolute()
        and path.parent.resolve() == path.parent
        and path.parent.is_dir()
        and path.parent.stat().st_uid == os.getuid()
        and path.parent.stat().st_mode & 0o077 == 0,
        "owned_private_plan_directory_required",
    )
    raw = rolling.canonical(asdict(plan)) + b"\n"
    need(len(raw) <= 131072, "plan_bound")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as target:
        target.write(raw)
        target.flush()
        os.fsync(target.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("capture", "upgrade", "reconcile"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--maintenance-plan", type=Path, required=True)
    parser.add_argument("--maintenance-plan-sha256", required=True)
    parser.add_argument("--maintenance-journal", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--journal", type=Path)
    for name in (
        "release",
        "image-digest",
        "build-id",
        "nonce",
        "observer-secret",
        "retired-migration-secret",
        "workers-release",
    ):
        parser.add_argument("--" + name)
    parser.add_argument("--ci-run-id", type=int)
    args = parser.parse_args()
    try:
        boundary = NativeMigration26Boundary(
            args.root,
            maintenance_plan_path=args.maintenance_plan,
            maintenance_plan_sha256=args.maintenance_plan_sha256,
            maintenance_journal=args.maintenance_journal,
        )
        if args.mode == "capture":
            names = (
                "release",
                "image_digest",
                "build_id",
                "ci_run_id",
                "nonce",
                "observer_secret",
                "retired_migration_secret",
                "workers_release",
            )
            need(all(getattr(args, name) is not None for name in names), "capture_pins_required")
            plan = boundary.capture(**{name: getattr(args, name) for name in names})
            digest = save_plan(args.plan, plan)
            result = {
                "status": "schema26_migration_plan_captured_readonly",
                "plan_sha256": digest,
                "database_schema": contract.BEFORE,
                "replay_authorized": False,
                "goal_complete": False,
            }
        else:
            need(args.journal is not None, "owned_journal_required")
            plan = load_plan(app26.initial._read_pinned(args.plan, args.plan_sha256))
            journal = rolling.Journal(args.journal, plan)
            result = migration(plan, boundary, journal, reconcile=args.mode == "reconcile")
        print(json.dumps(result, sort_keys=True))
    except Exception:
        # No SQL/vendor/configuration/credential exception or traceback output.
        print(
            json.dumps(
                {
                    "status": "schema26_migration_stopped_reconcile_only",
                    "replay_authorized": False,
                    "migration_replayed": False,
                    "goal_complete": False,
                }
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
