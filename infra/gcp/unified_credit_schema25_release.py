"""Additive schema21→25 unified-credit release/migration ports.

No schema18/19/20/21 contract is changed. The existing one-shot journal, native
bootstrap identity, source provenance and rolling guards remain authoritative.
These callable ports do nothing at import time; only the original explicit
migration phase obtains its native bootstrap session.
"""

# ruff: noqa: E402 -- standalone native operator has explicit local module roots
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal, cast

sys.path[:0] = [
    str(Path(__file__).resolve().parent),
    str(Path(__file__).resolve().parents[2] / "backend"),
]
import email_password_schema21_release as previous
import fresh_cutover_docker as docker
import fresh_cutover_writes as writes
import rolling_native as native
import rolling_release as rolling
import schema25_coordinator_pause as coordinator
from browser_schema19_release import PriorSource, bootstrap_identity, verify_permissions

from scripts import unified_credit_schema25_contract as contract
from scripts import unified_credit_schema25_step as step

release = rolling.release
OFF = (*previous.OFF, *contract.DISABLED_FLAGS)
CHAIN = (*previous.CHAIN, *contract.REVISIONS)
identity_step = step.previous
# Rehearsed old app code, not a demand that old and new feature code be identical.
# This first additive release permits rollback only to this known schema21 app
# snapshot (including commits with identical app bytes), before new credit use.
ROLLBACK_SOURCE_RELEASE = "f0f973c9c6d989553b8f58b275fc06fd5685497a"
ROLLBACK_APP_SHA256 = "9bcae584bb090a15df4119f059fc67f01a72fe62cd8f4e40a82af7fdb6ce66ff"
ROLLBACK_WORKER_SOURCE_RELEASE = "7a00839dc90a93caebb0b3b5f2e9e03c51e941e9"
ROLLBACK_WORKER_APP_SHA256 = "2a573762fd261f43eaa3defff4a31a7762f83e564bc82d3be3ba192eb6ce2a66"
ROLLBACK_API_HOTFIX_SOURCE_RELEASE = "d25811bf9aab85687d9786bdf6a92918826783d3"
ROLLBACK_API_HOTFIX_APP_SHA256 = "b69ae75c53d92737745bd7685e9373a84bf39c451780381f4ccb7e54ba1eabcb"
QUALIFIED_ROLLBACK_APP_SHA256 = frozenset({
    ROLLBACK_APP_SHA256,
    ROLLBACK_WORKER_APP_SHA256,
    ROLLBACK_API_HOTFIX_APP_SHA256,
})


@dataclass(frozen=True)
class Schema25Plan(rolling.RollingPlan):
    schema_contract: Literal["hirewiz_unified_credit_schema25_v1"] = contract.CONTRACT
    database_schema: Literal["20261023_0021", "20261028_0025"] = contract.SCHEMA
    prior_sources: tuple[PriorSource, ...] = ()

    def validate(self) -> None:
        super().validate()
        rolling.need(
            type(self) is Schema25Plan
            and self.schema_contract == contract.CONTRACT
            and self.database_schema in {contract.BEFORE, contract.SCHEMA},
            "schema25_release_contract_refused",
        )
        rolling.need(
            type(self.prior_sources) is tuple
            and len(self.prior_sources) <= 3
            and len({pin.release for pin in self.prior_sources}) == len(self.prior_sources),
            "schema25_prior_sources_invalid",
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
                "schema25_prior_source_pin_invalid",
            )

    def disabled_flags(self) -> tuple[str, ...]:
        return OFF


class Source25Boundary(previous.Source21Boundary):
    def verify_source_image(
        self, *, release: str, image_digest: str, build_id: str, ci_run_id: int
    ) -> None:
        tracked = rolling.release.source_guard.checked_files(self.root, release)
        rolling.need(
            rolling.release.source_guard.migration_chain(self.root, tracked, allow_literal_sql=True)
            == CHAIN,
            "schema25_exact_source_chain_required",
        )
        contract.verify_source(self.root)
        self._verify_source_image_provenance(
            release=release,
            image_digest=image_digest,
            build_id=build_id,
            ci_run_id=ci_run_id,
        )


class NativeSchema25Boundary(native.NativeRollingBoundary):
    def __init__(
        self,
        root: Path,
        schema: str = contract.SCHEMA,
        prior_sources: tuple[PriorSource, ...] = (),
    ):
        rolling.need(schema in {contract.BEFORE, contract.SCHEMA}, "schema25_native_phase_refused")
        super().__init__(root)
        self.observer = Source25Boundary(self.root)
        self.schema: Literal["20261023_0021", "20261028_0025"] = (
            "20261023_0021" if schema == contract.BEFORE else "20261028_0025"
        )
        self.prior_sources = prior_sources

    def make_plan(self, *args, **kwargs):
        return Schema25Plan(
            *args,
            **kwargs,
            database_schema=self.schema,
            prior_sources=self.prior_sources,
        )

    def _target(self, runtime_secret, observer_secret, retired_migration_secret) -> str:
        module = self.observer._module(
            "backend/scripts/monetary_cutover_preflight.py",
            "hirewiz_schema25_database_observer",
        )
        neon = module.neon_identity
        before = self.observer.describe_release_secret(retired_migration_secret)
        name, version = retired_migration_secret.split(":")
        rolling.need(
            before.get("name") == f"projects/157225724590/secrets/{name}/versions/{version}"
            and before.get("state") == "DISABLED",
            "schema25_retired_migrator_secret_changed",
        )
        financial = None
        collector = (
            neon.collect_schema25_release_target
            if self.schema == contract.SCHEMA
            else neon.collect_schema21_release_target
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
                    neon.verify_native_target(connection, binding, neon.FRESH_DATABASE_OID)
                    sessions, _ = module._native_session_ids(
                        connection, neon.FRESH_DATABASE_OID, neon_binding=binding
                    )
                    if alias == "observer":
                        release.verify_fresh_role_phase(
                            connection, neon.FRESH_RELEASE_ACTORS, "serving", sessions
                        )
                        # In-place additive release preserves coherent terminal
                        # uncertainty; it never settles unknown provider costs.
                        identity_step.verify_financial_catalog(connection, module)
                        if self.schema == contract.SCHEMA:
                            contract.verify_shape(connection, 2200)
                        else:
                            contract.verify_before_shape(connection, 2200)
                        verify_permissions(
                            connection,
                            "public",
                            neon.FRESH_RELEASE_ACTORS["replacement_runtime"][0],
                            neon.FRESH_RELEASE_ACTORS["replacement_migration"][0],
                            neon.FRESH_RELEASE_ACTORS["observer"][0],
                        )
                        previous.contract.verify_reset_permissions(
                            connection,
                            "public",
                            neon.FRESH_RELEASE_ACTORS["replacement_runtime"][0],
                            neon.FRESH_RELEASE_ACTORS["replacement_migration"][0],
                            neon.FRESH_RELEASE_ACTORS["observer"][0],
                        )
                        if self.schema == contract.SCHEMA:
                            step.verify_new_permissions(connection, bootstrap_identity())
                            contract.verify_empty_new_tables(connection, "public")
                        identity_step.drained(connection, bootstrap_identity())
                        financial = native.financial_digest(connection)
            finally:
                engine.dispose()
        rolling.need(
            financial is not None
            and self.observer.describe_release_secret(retired_migration_secret) == before,
            "schema25_native_target_or_retired_secret_changed",
        )
        return cast(str, financial)

    def verify_global(self, plan):
        rolling.need(
            type(plan) is Schema25Plan and plan.database_schema == self.schema,
            "schema25_native_plan_phase_mismatch",
        )
        self.verify_coordinator_closed()
        super().verify_global(plan)
        self.observed_disabled_flags(plan)

    def verify_coordinator_closed(self):
        # Independent fixed-service reader: the three-service mutation plan and
        # its native transport remain unable to mutate the coordinator.
        if not hasattr(self, "_coordinator_reader"):
            self._coordinator_reader = coordinator.NativeCoordinatorRun()
        rolling.need(type(self._coordinator_reader) is coordinator.NativeCoordinatorRun,
                     "schema25_coordinator_native_reader_required")
        return coordinator.observe_closed(self._coordinator_reader)

    def observed_disabled_flags(self, plan):
        """Fresh joined flags, including the existing rollback revisions."""
        rolling.need(type(plan) is Schema25Plan, "schema25_distinct_plan_required")
        for pin in plan.services:
            current = self.service(pin.service)
            prior = self.revision(pin.service, pin.prior_revision)
            rolling.need(prior is not None, "schema25_prior_revision_missing")
            for template in (current["template"], prior):
                rows = rolling.environment(template)
                rolling.need(
                    all(rows.get(name, {"value": "false"}).get("value") == "false" for name in OFF),
                    "schema25_all_candidate_admission_must_stay_closed",
                )
        return {name: "false" for name in contract.DISABLED_FLAGS}


def load_plan(raw: bytes) -> Schema25Plan:
    value = release._json(raw)
    rolling.need(
        value.get("schema_contract") == contract.CONTRACT and "database_schema" in value,
        "schema25_distinct_plan_required",
    )
    value["policies"] = rolling.PolicyPins(**value["policies"])
    if value.get("candidate_policies") is not None:
        value["candidate_policies"] = rolling.PolicyPins(**value["candidate_policies"])
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
    plan = Schema25Plan(**value)
    plan.validate()
    return plan


def migration(
    plan: Schema25Plan,
    boundary: NativeSchema25Boundary,
    journal: rolling.Journal,
    *,
    reconcile: bool = False,
) -> dict:
    """A lost transaction acknowledgement is inspected; the DDL is never rerun."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    plan.validate()
    rolling.need(
        type(boundary) is NativeSchema25Boundary
        and type(plan) is Schema25Plan
        and plan.database_schema == contract.BEFORE,
        "schema25_native_migration_contract_required",
    )
    rolling.need(
        type(journal) is rolling.Journal and journal.plan_hash == rolling.sha(asdict(plan)),
        "schema25_exact_original_plan_journal_required",
    )
    identity = bootstrap_identity()
    with journal.locked():
        started = journal.read("schema25-migration.started")
        checked = journal.read("schema25-migration.checked")
        rolling.need(checked is None, "schema25_migration_already_observed_never_replay")
        if started is not None:
            rolling.need(reconcile, "schema25_unknown_migration_reconcile_only")
        else:
            rolling.need(not reconcile, "schema25_reconcile_never_begins_migration")
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
                    identity_step.verify_identity(connection, identity)
                    rolling.need(
                        contract.marker(connection, identity.namespace) == contract.BEFORE,
                        "schema25_before_schema_mismatch",
                    )
                    connection.exec_driver_sql(f'SET LOCAL ROLE "{identity.migrator}"')
                    before_hash = step.retained_digest(
                        connection, identity.namespace_oid, identity.namespace
                    )
                    connection.exec_driver_sql("RESET ROLE")
                    before_acl = identity_step.schema_acl(connection, identity)
                flags = boundary.observed_disabled_flags(plan)
                boundary.verify_coordinator_closed()
                journal.record(
                    "schema25-migration.started",
                    {
                        "expected": contract.BEFORE,
                        "target": contract.SCHEMA,
                        "retained_sha256": before_hash,
                        "schema_acl": before_acl,
                        "replay_authorized": False,
                    },
                )
                started = journal.read("schema25-migration.started")
                with engine.connect() as connection, connection.begin():
                    result = step.upgrade_transaction(
                        connection,
                        identity,
                        root=boundary.root,
                        flags=flags,
                    )
                    rolling.need(
                        result["retained_sha256"] == before_hash
                        and result["schema_acl"] == before_acl,
                        "schema25_transaction_preimage_changed",
                    )
            assert started is not None
            with engine.connect() as connection, connection.begin():
                connection.exec_driver_sql(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                identity_step.verify_identity(connection, identity)
                observed = contract.marker(connection, identity.namespace)
                if observed == contract.BEFORE:
                    return {
                        "status": "schema21_observed_reconcile_only",
                        "schema": observed,
                        "replay_authorized": False,
                    }
                contract.verify_shape(connection, identity.namespace_oid)
                rolling.need(
                    [list(row) for row in identity_step.schema_acl(connection, identity)]
                    == started["schema_acl"],
                    "schema25_committed_acl_changed",
                )
                connection.exec_driver_sql(f'SET LOCAL ROLE "{identity.migrator}"')
                identity_step.drained(connection, identity)
                rolling.need(
                    step.retained_digest(connection, identity.namespace_oid, identity.namespace)
                    == started["retained_sha256"],
                    "schema25_committed_history_changed",
                )
                verify_permissions(
                    connection,
                    "public",
                    identity.runtime,
                    identity.migrator,
                    identity.observer,
                )
                previous.contract.verify_reset_permissions(
                    connection, "public", identity.runtime, identity.migrator, identity.observer
                )
                step.verify_new_permissions(connection, identity)
            observed_boundary = NativeSchema25Boundary(
                boundary.root, contract.SCHEMA, plan.prior_sources
            )
            observed_boundary.verify_global(replace(plan, database_schema=contract.SCHEMA))
            journal.record(
                "schema25-migration.checked",
                {
                    "schema": contract.SCHEMA,
                    "retained_sha256": started["retained_sha256"],
                    "retired_migration_credential_revived": False,
                    "replay_authorized": False,
                },
            )
            return {
                "status": "schema25_committed_and_freshly_observed",
                "schema": contract.SCHEMA,
                "queue_admission": "closed",
                "replay_authorized": False,
                "goal_complete": False,
            }
        finally:
            engine.dispose()


def advance(plan: Schema25Plan, boundary: NativeSchema25Boundary, journal: rolling.Journal) -> dict:
    """Use the existing journaled rolling action engine after exact25 observation.

    Migration and deployment retain different original plans/journals. Capture a
    fresh exact25 plan after migration; do not relabel a21 plan or migrate here.
    """
    _release_phase(plan, boundary, journal)
    result = rolling.advance(plan, boundary, journal)
    if result.get("rolling_release_complete"):
        result = result | {"status": "joined_schema25_release_observed", "schema": contract.SCHEMA}
    return result


def reconcile(
    plan: Schema25Plan, boundary: NativeSchema25Boundary, journal: rolling.Journal
) -> dict:
    """Observe the original deployment operations without beginning new writes."""
    _release_phase(plan, boundary, journal)
    return rolling.reconcile(plan, boundary, journal)


def _release_phase(plan, boundary, journal):
    plan.validate()
    rolling.need(
        type(plan) is Schema25Plan
        and type(boundary) is NativeSchema25Boundary
        and plan.database_schema == contract.SCHEMA
        and boundary.schema == contract.SCHEMA,
        "schema25_migration_must_be_observed_before_release",
    )
    rolling.need(
        type(journal) is rolling.Journal and journal.plan_hash == rolling.sha(asdict(plan)),
        "schema25_exact_original_plan_journal_required",
    )


def prior_app_files(root: Path, release_id: str) -> dict[str, bytes]:
    """Bounded immutable Git app snapshot; never extracts or executes archive paths."""
    rolling.need(
        bool(re.fullmatch(r"[a-f0-9]{40}", release_id)), "schema25_prior_source_pin_invalid"
    )
    try:
        raw = subprocess.run(
            ["git", "archive", "--format=tar", release_id, "--", "backend/app"],
            cwd=root,
            check=True,
            capture_output=True,
            timeout=10,
        ).stdout
        rolling.need(0 < len(raw) <= 8388608, "schema25_prior_source_bound")
        result = {}
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for member in archive.getmembers():
                if member.isdir():
                    continue
                path = Path(member.name)
                rolling.need(
                    member.isfile()
                    and path.parts[:2] == ("backend", "app")
                    and not path.is_absolute()
                    and ".." not in path.parts
                    and 0 <= member.size <= 1048576
                    and member.name not in result,
                    "schema25_prior_source_bound",
                )
                stream = archive.extractfile(member)
                rolling.need(stream is not None, "schema25_prior_source_bound")
                assert stream is not None
                result[member.name] = stream.read()
        rolling.need(3 <= len(result) <= 512, "schema25_prior_source_bound")
        return result
    except (subprocess.SubprocessError, OSError, tarfile.TarError):
        raise rolling.RollingDenied("schema25_prior_source_unavailable") from None


def prior_app_digest(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path, raw in sorted(files.items()):
        digest.update(path.encode() + b"\0" + hashlib.sha256(raw).digest())
    return digest.hexdigest()


def verify_rollback_sources(plan: Schema25Plan, boundary: NativeSchema25Boundary) -> None:
    """Exact prior image proof and a rehearsed old app on unchanged legacy tables.

    verify_global additionally requires every new credit table to remain empty.
    After new credit activity, a separate forward-compatible release is needed.
    """
    contract.verify_source(boundary.root)
    proofs = {proof.release: proof for proof in plan.prior_sources}
    for prior in {pin.prior_release for pin in plan.services}:
        rolling.need(prior in proofs, "schema25_prior_native_source_proof_required")
        proof = proofs[prior]
        for pin in (item for item in plan.services if item.prior_release == prior):
            revision = boundary.revision(pin.service, pin.prior_revision)
            rolling.need(
                revision is not None
                and revision.get("containers", [{}])[0].get("image")
                == plan.image.split("@")[0] + "@" + proof.image_digest,
                "schema25_prior_native_image_changed",
            )
        boundary.observer._verify_source_image_provenance(
            release=prior,
            image_digest=proof.image_digest,
            build_id=proof.build_id,
            ci_run_id=proof.ci_run_id,
        )
        rolling.need(
            prior_app_digest(prior_app_files(boundary.root, prior))
            in QUALIFIED_ROLLBACK_APP_SHA256,
            "schema25_prior_app_compatibility_unqualified",
        )


def rollback(
    plan: Schema25Plan, boundary: NativeSchema25Boundary, journal: rolling.Journal
) -> dict:
    """Traffic-only rollback to the rehearsed21 app; never downgrades the schema."""
    _release_phase(plan, boundary, journal)
    boundary.verify_global(plan)
    verify_rollback_sources(plan, boundary)
    result = rolling.rollback(plan, boundary, journal)
    if result.get("status") == "prior_traffic_restored_schema18":
        result = result | {"status": "prior_traffic_restored_schema25", "schema": contract.SCHEMA}
    return result


def main() -> None:
    """Explicit native phases; help/import perform no provider IO."""
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
            "coordinator-capture",
            "coordinator-pause",
            "coordinator-reconcile",
            "coordinator-restore",
        ),
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--journal", type=Path)
    parser.add_argument(
        "--schema", choices=(contract.BEFORE, contract.SCHEMA), default=contract.SCHEMA
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
    parser.add_argument("--candidate-expense-secret")
    parser.add_argument("--candidate-model-policy-secret")
    parser.add_argument("--prior-sources", type=Path)
    parser.add_argument("--prior-sources-sha256")
    args = parser.parse_args()
    if args.mode.startswith("coordinator-"):
        coordinator_main(args)
        return
    if args.mode == "capture":
        rolling.need(
            all(
                getattr(args, key) is not None
                for key in (
                    "commit",
                    "image_digest",
                    "build_id",
                    "nonce",
                    "observer_secret",
                    "retired_migration_secret",
                    "workers_release",
                    "ci_run_id",
                )
            ),
            "schema25_capture_pins_required",
        )
        rolling.need(
            args.plan.is_absolute()
            and args.plan.parent.resolve() == args.plan.parent
            and args.plan.parent.stat().st_uid == os.getuid()
            and args.plan.parent.stat().st_mode & 0o077 == 0,
            "schema25_owned_private_plan_directory_required",
        )
        prior_sources: tuple[PriorSource, ...] = ()
        if args.prior_sources is not None:
            prior_raw = _read_pinned(args.prior_sources, args.prior_sources_sha256)
            prior_sources = tuple(PriorSource(**row) for row in release._json(prior_raw))
        boundary = NativeSchema25Boundary(args.root, args.schema, prior_sources)
        plan = boundary.capture(
            commit=args.commit,
            image_digest=args.image_digest,
            build_id=args.build_id,
            ci_run_id=args.ci_run_id,
            nonce=args.nonce,
            observer_secret=args.observer_secret,
            retired_migration_secret=args.retired_migration_secret,
            workers_release=args.workers_release,
            candidate_expense_secret=args.candidate_expense_secret,
            candidate_model_policy_secret=args.candidate_model_policy_secret,
        )
        raw = rolling.canonical(asdict(plan)) + b"\n"
        fd = os.open(args.plan, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        print(
            json.dumps(
                {
                    "status": "native_schema25_contract_plan_captured",
                    "plan_sha256": hashlib.sha256(raw).hexdigest(),
                    "database_schema": plan.database_schema,
                    "provider_mutations": 0,
                    "goal_complete": False,
                }
            )
        )
        return
    plan = load_plan(_read_pinned(args.plan, args.plan_sha256))
    boundary = NativeSchema25Boundary(args.root, plan.database_schema, plan.prior_sources)
    if args.mode == "observe":
        boundary.verify_global(plan)
        print(
            json.dumps(
                {
                    "status": "schema25_contract_readonly_observed",
                    "schema": plan.database_schema,
                    "provider_mutations": 0,
                    "goal_complete": False,
                }
            )
        )
        return
    rolling.need(args.journal is not None, "schema25_owned_journal_required")
    journal = rolling.Journal(args.journal, plan)
    if args.mode in {"upgrade", "migration-reconcile"}:
        result = migration(plan, boundary, journal, reconcile=args.mode == "migration-reconcile")
    else:
        result = {"advance": advance, "reconcile": reconcile, "rollback": rollback}[args.mode](
            plan, boundary, journal
        )
    print(json.dumps(result, sort_keys=True))


def _read_pinned(path: Path, expected_sha256: str | None) -> bytes:
    rolling.need(
        path.is_file()
        and not path.is_symlink()
        and path.stat().st_size <= 131072
        and path.stat().st_uid == os.getuid()
        and path.stat().st_mode & 0o077 == 0
        and rolling.HASH.fullmatch(expected_sha256 or ""),
        "schema25_exact_owned_plan_pin_required",
    )
    raw = path.read_bytes()
    rolling.need(
        hashlib.sha256(raw).hexdigest() == expected_sha256, "schema25_exact_plan_pin_required"
    )
    return raw


def coordinator_main(args) -> None:
    """Separate pinned coordinator record, using the same private journal format."""
    if args.mode == "coordinator-capture":
        rolling.need(
            args.nonce is not None
            and args.plan.is_absolute()
            and args.plan.parent.resolve() == args.plan.parent
            and args.plan.parent.stat().st_uid == os.getuid()
            and args.plan.parent.stat().st_mode & 0o077 == 0,
            "schema25_owned_private_plan_directory_required",
        )
        reader = coordinator.NativeCoordinatorRun()
        plan = coordinator.capture(reader, args.nonce)
        raw = rolling.canonical(asdict(plan)) + b"\n"
        fd = os.open(args.plan, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        print(json.dumps({"status": "coordinator_plan_captured",
                          "plan_sha256": hashlib.sha256(raw).hexdigest(),
                          "provider_mutations": 0, "goal_complete": False}, sort_keys=True))
        return
    plan = coordinator.load_plan(_read_pinned(args.plan, args.plan_sha256))
    rolling.need(args.journal is not None, "schema25_owned_journal_required")
    journal = rolling.Journal(args.journal, plan)
    reader = coordinator.NativeCoordinatorRun()
    reconcile_only = args.mode == "coordinator-reconcile"
    phase = "restore" if args.mode == "coordinator-restore" else "pause"
    if reconcile_only:
        # Only inspect the existing original phase. This command cannot create a
        # missing pause or restoration, even following an unacknowledged write.
        phase = "restore" if journal.read("coordinator.restore.started") is not None else "pause"
    result = coordinator.action(plan, reader, journal, phase, reconcile=reconcile_only)
    print(json.dumps(result, sort_keys=True))


# Reviewed literal refusal identifiers only. A vendor/SQL error, subclass or
# arbitrary message never becomes an operator-visible diagnostic.
SAFE_REFUSAL_CODES = frozenset(
    {
        "rolling_active_traffic_not_prior_revision",
        "rolling_candidate_policy_numeric_refs_required",
        "rolling_candidate_policy_pair_invalid",
        "rolling_candidate_policy_pair_required",
        "rolling_candidate_policy_version_unverified",
        "rolling_candidate_revision_already_exists",
        "rolling_candidate_template_changed",
        "rolling_candidate_template_pin_required",
        "rolling_current_policy_phase_unverified",
        "rolling_explicit_rollback_in_progress",
        "rolling_financial_snapshot_bound",
        "rolling_financial_state_changed_reconcile_only",
        "rolling_joined_stage_must_be_reconciled_first",
        "rolling_maintenance_authority_unverified",
        "rolling_maintenance_binding_unverified",
        "rolling_migrator_must_remain_retired",
        "rolling_native_preimage_changed",
        "rolling_numeric_secret_required",
        "rolling_operation_ack_unmapped",
        "rolling_operation_failed_reconcile_only",
        "rolling_pinned_traffic_invalid",
        "rolling_plan_identity_invalid",
        "rolling_prior_admission_not_closed",
        "rolling_product_admission_not_closed",
        "rolling_reconciliation_never_starts_a_mutation",
        "rolling_rollback_configuration_changed",
        "rolling_rollback_revision_missing",
        "rolling_runtime_identity_drift",
        "rolling_schema18_only",
        "rolling_secret_native_version_unverified",
        "rolling_secret_reference_drift",
        "rolling_service_database_binding_invalid",
        "rolling_service_model_binding_invalid",
        "rolling_service_model_fields_forbidden",
        "rolling_service_policy_binding_invalid",
        "rolling_tag_collision",
        "rolling_unknown_mutation_reconcile_only",
        "rolling_worker_invokers_unverified",
        "schema25_add_only_ddl_required",
        "schema25_all_candidate_admission_must_stay_closed",
        "schema25_before_new_tables_present",
        "schema25_before_schema_mismatch",
        "schema25_capture_pins_required",
        "schema25_catalog_inventory_bound",
        "schema25_committed_acl_changed",
        "schema25_committed_history_changed",
        "schema25_coordinator_admission_not_closed",
        "schema25_coordinator_native_reader_required",
        "schema25_coordinator_observation_changed",
        "schema25_coordinator_operation_failed_reconcile_only",
        "schema25_coordinator_original_journal_required",
        "schema25_coordinator_pause_not_observed",
        "schema25_coordinator_phase_invalid",
        "schema25_coordinator_plan_invalid",
        "schema25_coordinator_preimage_changed",
        "schema25_coordinator_prior_revision_changed",
        "schema25_coordinator_prior_template_changed",
        "schema25_coordinator_reachable_source_changed",
        "schema25_coordinator_reachable_configuration_differs",
        "schema25_coordinator_readback_changed",
        "schema25_coordinator_reconcile_never_starts_write",
        "schema25_coordinator_restore_already_started",
        "schema25_coordinator_revision_already_exists",
        "schema25_coordinator_revision_not_ready",
        "schema25_coordinator_service_not_ready",
        "schema25_coordinator_serving_template_changed",
        "schema25_coordinator_source_or_identity_invalid",
        "schema25_coordinator_stale_ready_revision_serving",
        "schema25_coordinator_unknown_acknowledgement",
        "schema25_coordinator_unknown_write_reconcile_only",
        "schema25_distinct_plan_required",
        "schema25_exact_21_required_never_replay",
        "schema25_exact_marker_required",
        "schema25_exact_migration_source_required",
        "schema25_exact_original_plan_journal_required",
        "schema25_exact_owned_plan_pin_required",
        "schema25_exact_plan_pin_required",
        "schema25_exact_source_chain_required",
        "schema25_existing_table_owner_changed",
        "schema25_external_transaction_lost",
        "schema25_external_transaction_required",
        "schema25_migration_already_observed_never_replay",
        "schema25_migration_already_running",
        "schema25_migration_must_be_observed_before_release",
        "schema25_migration_sources_changed",
        "schema25_native_migration_contract_required",
        "schema25_native_phase_refused",
        "schema25_native_plan_phase_mismatch",
        "schema25_native_target_or_retired_secret_changed",
        "schema25_new_admission_must_remain_closed",
        "schema25_new_catalog_shape_mismatch",
        "schema25_new_financial_activity_refused",
        "schema25_new_table_acl_mismatch",
        "schema25_owned_journal_required",
        "schema25_owned_private_plan_directory_required",
        "schema25_prior_app_compatibility_unqualified",
        "schema25_prior_native_image_changed",
        "schema25_prior_native_source_proof_required",
        "schema25_prior_revision_missing",
        "schema25_prior_source_bound",
        "schema25_prior_source_pin_invalid",
        "schema25_prior_source_unavailable",
        "schema25_prior_sources_invalid",
        "schema25_reconcile_never_begins_migration",
        "schema25_release_contract_refused",
        "schema25_retained_history_changed",
        "schema25_retained_rows_bound",
        "schema25_retained_table_bound",
        "schema25_retained_table_name_refused",
        "schema25_retired_migrator_secret_changed",
        "schema25_schema_acl_changed",
        "schema25_source_chain_invalid",
        "schema25_source_manifest_invalid",
        "schema25_source_pin_changed",
        "schema25_target_marker_required",
        "schema25_transaction_preimage_changed",
        "schema25_unknown_migration_reconcile_only",
        "schema25_upgrade_missing",
    }
)


def failure_response(error: BaseException) -> dict:
    code = "unclassified_failure"
    if type(error) in {rolling.RollingDenied, contract.Schema25Denied} and len(error.args) == 1:
        value = error.args[0]
        if type(value) is str and value in SAFE_REFUSAL_CODES:
            code = value
    return {
        "status": "schema25_stopped_reconcile_only",
        "code": code,
        "replay_authorized": False,
        "goal_complete": False,
    }


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        # Native responses, credentials, candidate data and vendor errors never
        # become terminal output. Uncertain work always requires reconciliation.
        print(json.dumps(failure_response(error), sort_keys=True))
        sys.exit(2)
