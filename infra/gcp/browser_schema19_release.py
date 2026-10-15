"""Explicit schema19 migration and immutable rolling release on the F7 target.

Old F7/schema18 CLIs retain their exact contracts. This CLI uses native readers,
the existing bootstrap owner session and the existing one-shot rolling journal.
No login/credential revival, fresh transfer, admission opening or automatic retry.
"""

# ruff: noqa: E402 -- standalone CLI establishes its explicit module roots first

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal, cast

sys.path[:0] = [
    str(Path(__file__).resolve().parent),
    str(Path(__file__).resolve().parents[2] / "backend"),
]
import fresh_cutover_docker as docker
import fresh_cutover_writes as writes
import fresh_monetary_release as fresh
import rolling_native as native
import rolling_release as rolling

from scripts import browser_schema19_contract as contract
from scripts import browser_schema19_step as step

release = rolling.release
CHAIN = (*release.CHAIN, contract.SCHEMA)
OFF = (
    *rolling.OFF,
    "BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED",
    "BROWSER_ASSISTANCE_ATTACHMENT_ENABLED",
    "BROWSER_ASSISTANCE_SUBMIT_ENABLED",
    "BROWSER_ASSISTANCE_NATIVE_UPLOAD_ENABLED",
    "BROWSER_ASSISTANCE_NATIVE_PAIRING_ENABLED",
    "BROWSER_PAIRING_NATIVE_ENABLED",
    "BROWSER_SUBMIT_NATIVE_ENABLED",
    "CANDIDATE_INGRESS_NATIVE_ENABLED",
)


@dataclass(frozen=True)
class PriorSource:
    release: str
    image_digest: str
    build_id: str
    ci_run_id: int


@dataclass(frozen=True)
class Schema19Plan(rolling.RollingPlan):
    schema_contract: Literal["hirewiz_browser_schema19_v1"] = contract.CONTRACT
    database_schema: Literal["20261015_0018", "20261015_0019"] = contract.SCHEMA
    prior_sources: tuple[PriorSource, ...] = ()

    def validate(self) -> None:
        super().validate()
        rolling.need(
            type(self) is Schema19Plan
            and self.schema_contract == contract.CONTRACT
            and self.database_schema in {step.BEFORE, contract.SCHEMA},
            "schema19_release_contract_refused",
        )
        rolling.need(
            type(self.prior_sources) is tuple
            and len(self.prior_sources) <= 3
            and len({pin.release for pin in self.prior_sources})
            == len(self.prior_sources),
            "schema19_prior_sources_invalid",
        )
        for pin in self.prior_sources:
            rolling.need(
                type(pin) is PriorSource
                and re.fullmatch(r"[a-f0-9]{40}", pin.release)
                and re.fullmatch(r"sha256:[a-f0-9]{64}", pin.image_digest)
                and re.fullmatch(r"[a-f0-9-]{36}", pin.build_id)
                and type(pin.ci_run_id) is int
                and pin.ci_run_id > 0
                and pin.release in {service.prior_release for service in self.services},
                "schema19_prior_source_pin_invalid",
            )

    def disabled_flags(self) -> tuple[str, ...]:
        return OFF


class Source19Boundary(fresh.FreshNativeBoundary):
    def verify_source_image(
        self, *, release: str, image_digest: str, build_id: str, ci_run_id: int
    ) -> None:
        tracked = rolling.release.source_guard.checked_files(self.root, release)
        rolling.need(
            rolling.release.source_guard.migration_chain(
                self.root, tracked, allow_literal_sql=True
            )
            == CHAIN,
            "schema19_exact_source_chain_required",
        )
        self._verify_source_image_provenance(
            release=release,
            image_digest=image_digest,
            build_id=build_id,
            ci_run_id=ci_run_id,
        )


def verify_permissions(
    connection, namespace: str, runtime: str, migrator: str, observer: str
) -> None:
    """Retain legacy permissions and the new Google history's append-only policy."""
    from sqlalchemy import text

    rows = (
        connection.execute(
            text(
                "SELECT c.relname,has_table_privilege(:runtime,c.oid,'SELECT') AS s,"
                "has_table_privilege(:runtime,c.oid,'INSERT') AS i,has_table_privilege(:runtime,c.oid,'UPDATE') AS u,"
                "has_table_privilege(:runtime,c.oid,'DELETE') AS d,has_table_privilege(:runtime,c.oid,'TRUNCATE') AS truncates,pg_has_role(:migration,c.relowner,'USAGE') AS owned,"
                "has_table_privilege(:observer,c.oid,'SELECT') AS observed,c.relowner FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:namespace AND c.relkind IN ('r','p') "
                "ORDER BY c.oid LIMIT 257"
            ),
            {
                "namespace": namespace,
                "runtime": runtime,
                "migration": migrator,
                "observer": observer,
            },
        )
        .mappings()
        .all()
    )
    rolling.need(1 <= len(rows) <= 256, "schema19_runtime_table_inventory_bound")
    for row in rows:
        rolling.need(
            row["s"]
            and row["owned"]
            and (row["relname"] == "alembic_version" or row["i"]),
            "schema19_runtime_or_owner_permissions_missing",
        )
        if row["relname"] == "candidate_identity_history":
            rolling.need(
                row["u"] is False
                and row["d"] is False
                and row["truncates"] is False
                and row["observed"],
                "schema19_identity_history_must_be_append_only",
            )
        else:
            rolling.need(
                row["relname"] == "alembic_version" or row["u"],
                "schema19_runtime_update_missing",
            )
            if (
                row["relname"] in release.ERASABLE_TABLES
                or row["relname"] in step.NEW_TABLES
            ):
                rolling.need(row["d"], "schema19_runtime_erasure_permission_missing")
        if row["relname"] in step.NEW_TABLES:
            rolling.need(
                row["observed"]
                and connection.execute(
                    text("SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:role"),
                    {"role": migrator},
                ).scalar_one()
                == row["relowner"],
                "schema19_google_exact_owner_or_observer_missing",
            )
        if row["relname"] == "employer_host_pacing":
            rolling.need(row["d"] is False, "schema19_global_pacing_delete_forbidden")
    sequences = (
        connection.execute(
            text(
                "SELECT has_sequence_privilege(:runtime,c.oid,'USAGE') FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:namespace AND c.relkind='S' LIMIT 257"
            ),
            {"namespace": namespace, "runtime": runtime},
        )
        .scalars()
        .all()
    )
    rolling.need(
        len(sequences) <= 256 and all(value is True for value in sequences),
        "schema19_sequence_usage_missing",
    )


class NativeSchema19Boundary(native.NativeRollingBoundary):
    def __init__(
        self,
        root: Path,
        schema: str = contract.SCHEMA,
        prior_sources: tuple[PriorSource, ...] = (),
    ):
        rolling.need(
            schema in {step.BEFORE, contract.SCHEMA}, "schema19_native_phase_refused"
        )
        super().__init__(root)
        self.observer = Source19Boundary(self.root)
        self.schema: Literal["20261015_0018", "20261015_0019"] = (
            "20261015_0018" if schema == step.BEFORE else "20261015_0019"
        )
        self.prior_sources = prior_sources

    def make_plan(self, *args, **kwargs):
        return Schema19Plan(
            *args,
            **kwargs,
            database_schema=self.schema,
            prior_sources=self.prior_sources,
        )

    def _target(self, runtime_secret, observer_secret, retired_migration_secret) -> str:
        module = self.observer._module(
            "backend/scripts/monetary_cutover_preflight.py",
            "hirewiz_schema19_database_observer",
        )
        neon = module.neon_identity
        before = self.observer.describe_release_secret(retired_migration_secret)
        name, version = retired_migration_secret.split(":")
        rolling.need(
            before.get("name")
            == f"projects/157225724590/secrets/{name}/versions/{version}"
            and before.get("state") == "DISABLED",
            "schema19_retired_migrator_secret_changed",
        )
        financial = None
        collector = (
            neon.collect_schema19_release_target
            if self.schema == contract.SCHEMA
            else neon.collect_fresh_release_target
        )
        for alias, reference in (
            ("observer", observer_secret),
            ("replacement_runtime", runtime_secret),
        ):
            binding, target = collector(
                self.observer,
                reference,
                alias=alias,
                database_owner_oid=neon.FRESH_DATABASE_OWNER_OID,
                expected_schema=self.schema,
            )
            engine = target.engine()
            try:
                with engine.connect() as connection, connection.begin():
                    connection.exec_driver_sql(
                        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                    )
                    connection.exec_driver_sql("SET LOCAL search_path=pg_catalog")
                    connection.exec_driver_sql("SET LOCAL statement_timeout='1000ms'")
                    connection.exec_driver_sql("SET LOCAL lock_timeout='1000ms'")
                    connection.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
                    neon.verify_transport(connection, binding)
                    neon.verify_native_target(
                        connection, binding, neon.FRESH_DATABASE_OID
                    )
                    sessions, _ = module._native_session_ids(
                        connection, neon.FRESH_DATABASE_OID, neon_binding=binding
                    )
                    if alias == "observer":
                        release.verify_fresh_role_phase(
                            connection, neon.FRESH_RELEASE_ACTORS, "serving", sessions
                        )
                        # Financial semantics are unchanged in 19. The independent
                        # marker/native contract and all new DDL are checked too.
                        fresh.verify_financial_state(connection, module, step.BEFORE)
                        if self.schema == contract.SCHEMA:
                            contract.verify_shape(connection, 2200)
                            verify_permissions(
                                connection,
                                "public",
                                neon.FRESH_RELEASE_ACTORS["replacement_runtime"][0],
                                neon.FRESH_RELEASE_ACTORS["replacement_migration"][0],
                                neon.FRESH_RELEASE_ACTORS["observer"][0],
                            )
                        else:
                            release.verify_runtime_permissions(
                                connection,
                                "public",
                                neon.FRESH_RELEASE_ACTORS["replacement_runtime"][0],
                                neon.FRESH_RELEASE_ACTORS["replacement_migration"][0],
                            )
                        financial = native.financial_digest(connection)
            finally:
                engine.dispose()
        rolling.need(
            financial is not None
            and self.observer.describe_release_secret(retired_migration_secret)
            == before,
            "schema19_native_target_or_retired_secret_changed",
        )
        return cast(str, financial)

    def verify_global(self, plan):
        rolling.need(
            type(plan) is Schema19Plan and plan.database_schema == self.schema,
            "schema19_native_plan_phase_mismatch",
        )
        super().verify_global(plan)
        for pin in plan.services:
            current = self.service(pin.service)
            prior = self.revision(pin.service, pin.prior_revision)
            rolling.need(prior is not None, "schema19_prior_revision_missing")
            for template in (current["template"], prior):
                rows = rolling.environment(template)
                rolling.need(
                    all(
                        rows.get(name, {"value": "false"}).get("value") == "false"
                        for name in OFF
                    ),
                    "schema19_all_candidate_admission_must_stay_closed",
                )


def load_plan(raw: bytes) -> Schema19Plan:
    value = release._json(raw)
    rolling.need(
        value.get("schema_contract") == contract.CONTRACT
        and "database_schema" in value,
        "schema19_distinct_plan_required",
    )
    value["policies"] = rolling.PolicyPins(**value["policies"])
    services = []
    for item in value["services"]:
        item["secrets"] = tuple(rolling.SecretPin(**row) for row in item["secrets"])
        item["revision_keys"], item["traffic"] = (
            tuple(item["revision_keys"]),
            tuple(item["traffic"]),
        )
        services.append(rolling.ServicePin(**item))
    value["services"] = tuple(services)
    value["prior_sources"] = tuple(PriorSource(**row) for row in value["prior_sources"])
    plan = Schema19Plan(**value)
    plan.validate()
    return plan


def bootstrap_identity() -> step.MigrationIdentity:
    return step.MigrationIdentity(
        writes.TARGET_CLUSTER,
        16395,
        16391,
        "public",
        2200,
        6171,
        "hirewiz_bootstrap_20261013",
        16391,
        "hirewiz_migrator_20261013_r1",
        24578,
        "hirewiz_runtime_20261013_r1",
        24576,
        "hirewiz_observer_20261013_r1",
        24580,
    )


def migration(
    plan: Schema19Plan,
    boundary: NativeSchema19Boundary,
    journal: rolling.Journal,
    *,
    reconcile: bool = False,
) -> dict:
    """A lost transaction acknowledgement is inspected; the DDL is never rerun."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    plan.validate()
    rolling.need(
        type(boundary) is NativeSchema19Boundary
        and type(plan) is Schema19Plan
        and plan.database_schema == step.BEFORE,
        "schema19_native_migration_contract_required",
    )
    identity = bootstrap_identity()
    with journal.locked():
        started = journal.read("schema19-migration.started")
        checked = journal.read("schema19-migration.checked")
        rolling.need(
            checked is None, "schema19_migration_already_observed_never_replay"
        )
        if started is not None:
            rolling.need(reconcile, "schema19_unknown_migration_reconcile_only")
        else:
            rolling.need(not reconcile, "schema19_reconcile_never_begins_migration")
            boundary.verify_global(plan)
        # This is the original normal Neon manager, not a Secret Manager read or
        # a revived migration credential. The BoundDatabase reauthenticates TLS,
        # cluster/role/database identity before the first protected SQL command.
        password = docker.bootstrap_password(time.monotonic() + 60)
        bootstrap = writes.transfer.BoundDatabase(
            writes.transfer.NativeDatabaseIdentity(
                writes.TARGET_HOST,
                5432,
                "neondb",
                writes.TARGET_CLUSTER,
                16395,
                identity.bootstrap,
                identity.bootstrap_oid,
                str(docker.CA),
            ),
            password,
        )
        engine = create_engine(
            "postgresql+psycopg://",
            creator=bootstrap.connect,
            poolclass=NullPool,
            echo=False,
            hide_parameters=True,
        )
        try:
            if started is None:
                with engine.connect() as connection, connection.begin():
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                    step.verify_identity(connection, identity)
                    rolling.need(
                        step.marker(connection, identity) == step.BEFORE,
                        "schema19_before_schema_mismatch",
                    )
                    connection.exec_driver_sql(f'SET LOCAL ROLE "{identity.migrator}"')
                    before_hash = step.retained_digest(connection, identity)
                    connection.exec_driver_sql("RESET ROLE")
                    before_acl = step.schema_acl(connection, identity)
                journal.record(
                    "schema19-migration.started",
                    {
                        "expected": step.BEFORE,
                        "target": contract.SCHEMA,
                        "retained_sha256": before_hash,
                        "schema_acl": before_acl,
                        "replay_authorized": False,
                    },
                )
                started = journal.read("schema19-migration.started")
                with engine.connect() as connection, connection.begin():
                    result = step.upgrade_transaction(
                        connection, identity, root=boundary.root
                    )
                    rolling.need(
                        result["retained_sha256"] == before_hash
                        and result["schema_acl"] == before_acl,
                        "schema19_transaction_preimage_changed",
                    )
            assert started is not None
            with engine.connect() as connection, connection.begin():
                connection.exec_driver_sql(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                step.verify_identity(connection, identity)
                observed = step.marker(connection, identity)
                if observed == step.BEFORE:
                    return {
                        "status": "schema18_observed_reconcile_only",
                        "schema": observed,
                        "replay_authorized": False,
                    }
                contract.verify_shape(connection, identity.namespace_oid)
                rolling.need(
                    [list(row) for row in step.schema_acl(connection, identity)]
                    == started["schema_acl"],
                    "schema19_committed_acl_changed",
                )
                connection.exec_driver_sql(f'SET LOCAL ROLE "{identity.migrator}"')
                step.drained(connection, identity)
                rolling.need(
                    step.retained_digest(connection, identity)
                    == started["retained_sha256"],
                    "schema19_committed_history_changed",
                )
                verify_permissions(
                    connection,
                    "public",
                    identity.runtime,
                    identity.migrator,
                    identity.observer,
                )
            observed_boundary = NativeSchema19Boundary(
                boundary.root, contract.SCHEMA, plan.prior_sources
            )
            observed_boundary.verify_global(
                replace(plan, database_schema=contract.SCHEMA)
            )
            journal.record(
                "schema19-migration.checked",
                {
                    "schema": contract.SCHEMA,
                    "retained_sha256": started["retained_sha256"],
                    "retired_migration_credential_revived": False,
                    "replay_authorized": False,
                },
            )
            return {
                "status": "schema19_committed_and_freshly_observed",
                "schema": contract.SCHEMA,
                "queue_admission": "closed",
                "replay_authorized": False,
                "goal_complete": False,
            }
        finally:
            engine.dispose()


def verify_schema19_rollback_sources(
    plan: Schema19Plan, boundary: NativeSchema19Boundary
) -> None:
    """Only an independently proved, byte-compatible backend can receive rollback.

    Wider binary compatibility is never inferred from an Alembic revision label.
    A future backend change needs its own reviewed compatibility evidence.
    """
    root = boundary.root
    scopes = [
        "backend/app",
        "backend/scripts/browser_schema19_contract.py",
        "backend/alembic/versions/20261015_0019_browser_typed_controls.py",
    ]
    proofs = {proof.release: proof for proof in plan.prior_sources}
    for prior in {pin.prior_release for pin in plan.services}:
        rolling.need(
            re.fullmatch(r"[a-f0-9]{40}", prior), "schema19_prior_source_pin_invalid"
        )
        rolling.need(prior in proofs, "schema19_prior_native_source_proof_required")
        proof = proofs[prior]
        for pin in (pin for pin in plan.services if pin.prior_release == prior):
            revision = boundary.revision(pin.service, pin.prior_revision)
            rolling.need(
                revision is not None
                and revision.get("containers", [{}])[0].get("image")
                == plan.image.split("@")[0] + "@" + proof.image_digest,
                "schema19_prior_native_image_changed",
            )
        boundary.observer._verify_source_image_provenance(
            release=prior,
            image_digest=proof.image_digest,
            build_id=proof.build_id,
            ci_run_id=proof.ci_run_id,
        )
        try:

            def tracked(commit):
                raw = subprocess.run(
                    ["git", "ls-tree", "-r", "--name-only", commit, "--", *scopes],
                    cwd=root,
                    capture_output=True,
                    check=True,
                    timeout=10,
                ).stdout
                rolling.need(len(raw) <= 65536, "schema19_rollback_source_bound")
                return set(raw.decode("utf8").splitlines())

            current, previous = tracked(plan.release), tracked(prior)
            rolling.need(
                current == previous
                and 3 <= len(current) <= 512
                and set(scopes[1:]) <= current,
                "schema19_rollback_prior_binary_compatibility_unproved",
            )
            for path in current:
                observed = subprocess.run(
                    ["git", "show", prior + ":" + path],
                    cwd=root,
                    capture_output=True,
                    check=True,
                    timeout=10,
                ).stdout
                rolling.need(
                    0 <= len(observed) <= 1048576
                    and (root / path).read_bytes() == observed,
                    "schema19_rollback_prior_binary_compatibility_unproved",
                )
            tree = ast.parse((root / scopes[-1]).read_bytes())
            values = {
                node.targets[0].id: ast.literal_eval(node.value)
                for node in tree.body
                if isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in {"revision", "down_revision"}
            }
            rolling.need(
                values == {"revision": contract.SCHEMA, "down_revision": step.BEFORE},
                "schema19_rollback_prior_binary_compatibility_unproved",
            )
        except (subprocess.SubprocessError, OSError, SyntaxError, ValueError):
            raise rolling.RollingDenied(
                "schema19_rollback_prior_binary_compatibility_unproved"
            ) from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=(
            "capture",
            "upgrade",
            "migration-reconcile",
            "observe",
            "advance",
            "reconcile",
            "rollback",
        ),
    )
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--journal", type=Path)
    parser.add_argument(
        "--schema", choices=(step.BEFORE, contract.SCHEMA), default=contract.SCHEMA
    )
    for name in (
        "commit",
        "image-digest",
        "build-id",
        "nonce",
        "observer-secret",
        "retired-migration-secret",
        "workers-release",
    ):
        parser.add_argument("--" + name)
    parser.add_argument("--ci-run-id", type=int)
    parser.add_argument("--prior-sources", type=Path)
    parser.add_argument("--prior-sources-sha256")
    args = parser.parse_args()
    if args.mode == "capture":
        prior_sources: tuple[PriorSource, ...] = ()
        if args.prior_sources is not None:
            prior_raw = args.prior_sources.read_bytes()
            rolling.need(
                hashlib.sha256(prior_raw).hexdigest() == args.prior_sources_sha256,
                "schema19_prior_sources_exact_pin_required",
            )
            prior_sources = tuple(
                PriorSource(**row) for row in release._json(prior_raw)
            )
        boundary = NativeSchema19Boundary(args.root, args.schema, prior_sources)
        plan = boundary.capture(
            commit=args.commit,
            image_digest=args.image_digest,
            build_id=args.build_id,
            ci_run_id=args.ci_run_id,
            nonce=args.nonce,
            observer_secret=args.observer_secret,
            retired_migration_secret=args.retired_migration_secret,
            workers_release=args.workers_release,
        )
        raw = rolling.canonical(asdict(plan)) + b"\n"
        fd = os.open(
            args.plan, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "wb") as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        print(
            json.dumps(
                {
                    "status": "native_schema19_contract_plan_captured",
                    "plan_sha256": hashlib.sha256(raw).hexdigest(),
                    "database_schema": plan.database_schema,
                    "provider_mutations": 0,
                    "goal_complete": False,
                }
            )
        )
        return
    raw = args.plan.read_bytes()
    rolling.need(
        args.plan_sha256 is not None
        and hashlib.sha256(raw).hexdigest() == args.plan_sha256,
        "schema19_exact_plan_pin_required",
    )
    plan = load_plan(raw)
    boundary = NativeSchema19Boundary(
        args.root, plan.database_schema, plan.prior_sources
    )
    if args.mode == "observe":
        boundary.verify_global(plan)
        print(
            json.dumps(
                {
                    "status": "schema19_contract_readonly_observed",
                    "schema": plan.database_schema,
                    "provider_mutations": 0,
                }
            )
        )
        return
    rolling.need(args.journal is not None, "schema19_owned_journal_required")
    journal = rolling.Journal(args.journal, plan)
    if args.mode in {"upgrade", "migration-reconcile"}:
        result = migration(
            plan, boundary, journal, reconcile=args.mode == "migration-reconcile"
        )
    else:
        rolling.need(
            plan.database_schema == contract.SCHEMA,
            "schema19_rolling_requires_observed19",
        )
        if args.mode == "rollback":
            boundary.verify_global(plan)
            verify_schema19_rollback_sources(plan, boundary)
        result = {
            "advance": rolling.advance,
            "reconcile": rolling.reconcile,
            "rollback": rolling.rollback,
        }[args.mode](plan, boundary, journal)
        if result.get("schema") == rolling.SCHEMA:
            result.update(
                status="joined_schema19_release_observed", schema=contract.SCHEMA
            )
        elif result.get("status") == "prior_traffic_restored_schema18":
            result["status"] = "prior_traffic_restored_schema19"
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        print(
            '{"status":"schema19_stopped_reconcile_only","replay_authorized":false,"goal_complete":false}'
        )
        sys.exit(2)
