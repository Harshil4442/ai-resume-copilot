"""Authenticated all-off26 bridge and one-shot additive27 migration.

The old successor owns bridge staging/promotion while SQL is26. This controller
never calls that26 SQL verifier after DDL. Reconciliation is read-only, requires
our detached receipt in the SAME DDL transaction, and never retries migration.
No SDK, credential, database or provider action occurs on import/help.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "infra/gcp"), str(ROOT / "backend"), str(ROOT)]

import rolling_release as rolling  # noqa: E402
import schema26_app_rolling_v3 as app26  # noqa: E402
from sqlalchemy import text  # noqa: E402

from scripts import notification_schema27_contract as contract  # noqa: E402
from scripts import notification_schema27_step as step  # noqa: E402
from scripts import schema26_app_successor as successor  # noqa: E402

KIND = "hirewiz_schema27_native_additive_v1"
MARKER = "schema27-migration"
ACTION = "schema27_additive_migration"
F07_FLAGS = ("NOTIFICATION_DIGESTS_ENABLED", "LIFECYCLE_EMAILS_ENABLED")
BRIDGE_DOCKERFILE_SHA256 = "e02fce326f540cb09b6e5e9390923605371da4f84b40ccbd21b999251a4b060f"


def need(value, code):
    rolling.need(value, "schema27_native_" + code)


class Source27BridgeBoundary(app26.Source26Boundary):
    """Exact reviewed27 source, genuine unchanged image/Build/CI provenance."""

    def verify_source_image(self, *, release, image_digest, build_id, ci_run_id):
        tracked = rolling.release.source_guard.checked_files(self.root, release)
        need(rolling.release.source_guard.migration_chain(
            self.root, tracked, allow_literal_sql=True
        ) == (*app26.initial.CHAIN, contract.BEFORE, contract.SCHEMA), "exact_bridge_chain")
        contract.verify_source(self.root)
        dockerfile = self.root / "Dockerfile"
        need(dockerfile.is_file() and not dockerfile.is_symlink()
             and hashlib.sha256(dockerfile.read_bytes()).hexdigest() == BRIDGE_DOCKERFILE_SHA256,
             "baked_off_image_source_changed")
        self._verify_source_image_provenance(
            release=release, image_digest=image_digest, build_id=build_id, ci_run_id=ci_run_id
        )


def native_bridge(root, target_root, prior_root):
    """Keep exact stock boundary/plan types; change only the explicit source port.

    Existing successor.execute/_sources/native IAM/image/SQL26 gates stay intact.
    No caller transport, verifier, source label or acknowledgement is accepted.
    """
    need(Path(__file__).resolve() == root.resolve() / "infra/gcp/schema27_native_migration.py",
         "controller_module_origin")
    bridge = successor.NativeSuccessorBoundary(root, target_root, prior_root)
    bridge.observer = Source27BridgeBoundary(bridge.target_root)
    return bridge


def capture_bridge(root, target_root, prior_root, source, prior, **options):
    """Normal stock native capture, including actual SQL26/source/CI gates."""
    boundary = native_bridge(root, target_root, prior_root)
    try:
        return boundary.capture(source, prior, **options)
    finally:
        boundary.close()


@dataclass(frozen=True)
class Migration27Plan:
    nonce: str
    bridge: successor.SuccessorPlan
    bridge_journal_sha256: str
    source_sha256: str
    services: tuple[rolling.ServicePin, ...]
    catalog26_sha256: str
    retained_acl_sha256: str
    schema_contract: str = KIND
    database_schema: str = contract.BEFORE
    target_schema: str = contract.SCHEMA

    def validate(self):
        need(type(self) is Migration27Plan and self.schema_contract == KIND
             and self.database_schema == contract.BEFORE and self.target_schema == contract.SCHEMA,
             "exact_plan_required")
        need(type(self.bridge) is successor.SuccessorPlan, "exact_bridge_plan_required")
        self.bridge.validate()
        need(type(self.nonce) is str and re.fullmatch(r"[a-f0-9]{32}", self.nonce), "nonce")
        need(all(type(value) is str and rolling.HASH.fullmatch(value) for value in (
            self.bridge_journal_sha256, self.source_sha256, self.catalog26_sha256,
            self.retained_acl_sha256)), "snapshot_hashes")
        need(tuple(pin.service for pin in self.services) == rolling.release.SERVICES
             and all(type(pin) is rolling.ServicePin for pin in self.services), "service_scope")
        need(all(pin.prior_release == self.bridge.release for pin in self.services), "bridge_only")


def bridge_receipts(plan, journal):
    need(type(plan) is successor.SuccessorPlan and type(journal) is rolling.Journal
         and journal.plan_hash == rolling.sha(asdict(plan)), "bridge_custody")
    need(journal.read(app26.historical.ROLLBACK_INTENT) is None, "bridge_rollback_started")
    result, operations = {}, set()
    for pin in plan.services:
        rows = {}
        for phase in ("stage", "promote"):
            prefix = phase + "." + pin.service
            record = {suffix: journal.read(prefix + "." + suffix) for suffix in ("started", "ack", "checked")}
            need(all(row is not None for row in record.values()), "bridge_not_promoted")
            started, ack, checked = record["started"], record["ack"], record["checked"]
            # Exact original stock action schema; missing==missing is never
            # custody. These are refusal checks, not reconstructed ACKs.
            need(set(started) == {"phase", "request_sha256", "mask", "template_sha256", "traffic",
                 "expected_generation", "service_uid", "previous_etag", "replay_authorized", "plan_sha256"}
                 and started["phase"] == phase and started["mask"] == ("template,traffic" if phase == "stage" else "traffic")
                 and all(type(started[key]) is str and rolling.HASH.fullmatch(started[key])
                         for key in ("request_sha256", "template_sha256"))
                 and type(started["expected_generation"]) is int
                 and started["expected_generation"] == pin.generation + (1 if phase == "stage" else 2)
                 and started["service_uid"] == pin.uid
                 and type(started["previous_etag"]) is str and 1 <= len(started["previous_etag"]) <= 256
                 and started["replay_authorized"] is False
                 and started["traffic"] == rolling._traffic_for(plan, pin, phase == "promote"),
                 "bridge_started_shape")
            need(set(ack) == {"operation", "request_sha256", "plan_sha256"}
                 and type(ack["operation"]) is str and rolling.OPERATION.fullmatch(ack["operation"])
                 and ack["request_sha256"] == started["request_sha256"]
                 and ack["operation"] not in operations, "bridge_receipt_lineage")
            operations.add(ack["operation"])
            need(checked == {"service_uid": pin.uid, "revision": plan.revision(pin.service),
                 "generation": started["expected_generation"], "native_metadata_reobserved": True,
                 "replay_authorized": False, "operation_ack_retained": True, "plan_sha256": journal.plan_hash}
                 and type(checked.get("generation")) is int
                 and checked.get("native_metadata_reobserved") is True
                 and checked.get("replay_authorized") is False and checked.get("operation_ack_retained") is True,
                 "bridge_checked_shape")
            rows[phase] = record
        need(rows["stage"]["started"]["template_sha256"] == rows["promote"]["started"]["template_sha256"],
             "bridge_template_lineage")
        need(journal.read("rollback." + pin.service + ".started") is None, "bridge_rollback_started")
        result[pin.service] = rows
    return rolling.sha(result)


def require_closed(template, flags):
    env = rolling.environment(template)
    need(all(env.get(name, {"value": "false"}).get("value") == "false" for name in flags),
         "admission_not_closed")
    # Called ONLY after exact bridge source/Build/image proof. Absent entries
    # inherit the pinned final-image ENV false; secret refs/other values deny.
    need(all(name not in env or env[name] == {"name": name, "value": "false"} for name in F07_FLAGS),
         "f07_not_closed")
    need(env.get("AUTO_DB_MIGRATE") == {"name": "AUTO_DB_MIGRATE", "value": "false"},
         "automatic_migration_forbidden")


class NativeMigration27Boundary:
    """Fixed normal native transports and physical SQL identity; no plugins."""

    def __init__(self, root, target_root, prior_root, bridge_plan, bridge_journal):
        self.root = root.resolve()
        self.bridge = native_bridge(self.root, target_root, prior_root)
        self.bridge_plan, self.bridge_journal = bridge_plan, bridge_journal
        need(type(bridge_plan) is successor.SuccessorPlan
             and type(bridge_journal) is rolling.Journal
             and bridge_journal.plan_hash == rolling.sha(asdict(bridge_plan)), "bridge_custody")
        self.bridge.journal = bridge_journal

    def bootstrap_engine(self):
        """Fixed existing normal Neon manager and verified BoundDatabase TLS.

        Kept here rather than importing the old migration's lifecycle guard,
        which imports application metadata before its authentic runtime URL.
        No caller DSN, password, target, actor or transport can be supplied.
        """
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool

        original = app26.initial
        identity = app26.bootstrap_identity()
        password = original.docker.bootstrap_password(time.monotonic() + 60)
        bound = original.writes.transfer.BoundDatabase(
            original.writes.transfer.NativeDatabaseIdentity(
                original.writes.TARGET_HOST, 5432, "neondb", original.writes.TARGET_CLUSTER,
                16395, identity.bootstrap, identity.bootstrap_oid, str(original.docker.CA)
            ), password
        )
        return create_engine("postgresql+psycopg://", creator=bound.connect,
                             poolclass=NullPool, echo=False, hide_parameters=True)

    def bridge_phase(self, mode):
        need(mode in {"stage", "advance", "observe", "reconcile"}, "bridge_phase")
        # The unchanged controller verifies exact SQL26 before any stage or
        # promotion; this method must never be used as a27 observer.
        return successor.execute(mode, self.bridge_plan, self.bridge, self.bridge_journal)

    def sources(self, plan):
        need(plan.bridge == self.bridge_plan, "bridge_plan_changed")
        need(Path(__file__).resolve() == self.root / "infra/gcp/schema27_native_migration.py"
             and Path(step.__file__).resolve() == self.root / "backend/scripts/notification_schema27_step.py"
             and type(self.bridge) is successor.NativeSuccessorBoundary
             and type(self.bridge.observer) is Source27BridgeBoundary,
             "source_modules")
        self.bridge._sources(plan.bridge)
        need(contract.verify_source(self.root) == plan.source_sha256, "migration_source_changed")

    def _current_pins(self):
        source = successor.SourcePin(self.bridge_plan.release, self.bridge_plan.image_digest,
                                     self.bridge_plan.build_id, self.bridge_plan.ci_run_id)
        return tuple(self.bridge._pin(name, source) for name in rolling.release.SERVICES)

    def verify_live(self, plan):
        """Fresh source/native checks independent of the current SQL marker."""
        plan.validate()
        self.sources(plan)
        need(bridge_receipts(plan.bridge, self.bridge_journal) == plan.bridge_journal_sha256,
             "bridge_journal_changed")
        b = self.bridge
        b.observer.verify_policy_configuration(plan.bridge.target_policies())
        b._identity(plan.bridge.runtime_uid)
        need(b._resources(plan.bridge.workers_release) == plan.bridge.resources, "resources_changed")
        need(self._current_pins() == plan.services, "bridge_service_changed")
        for pin in plan.services:
            original = plan.bridge.pin(pin.service)
            for phase in ("stage", "promote"):
                ack = self.bridge_journal.read(phase + "." + pin.service + ".ack")
                operation = b.operation(ack["operation"])
                need(operation.get("name") == ack["operation"] and operation.get("done") is True
                     and "error" not in operation, "bridge_operation_not_done")
            # Reuse native metadata+HTTP-health only, never the old SQL26 gate.
            rolling._readback(plan.bridge, b, original,
                              self.bridge_journal.read("promote." + pin.service + ".started"))
            current = b.service(pin.service)
            rolling._ready(current, pin)
            rolling.worker_iam(pin.service, b.iam(pin.service))
            need(rolling.static_sha(current) == pin.static_sha256
                 and rolling.sha(b.iam(pin.service)) == pin.iam_sha256, "static_or_iam_changed")
            require_closed(current["template"], plan.bridge.disabled_flags())
            revisions = {rolling.short_revision(current[key], pin.service)
                         for key in ("latestCreatedRevision", "latestReadyRevision")}
            revisions |= {row["revision"] for row in rolling.pinned_traffic(pin.service, current)}
            need(revisions == {plan.bridge.revision(pin.service)}, "non_bridge_reachable")
            for revision in revisions:
                row = b.revision(pin.service, revision)
                need(row is not None and row["containers"][0]["image"] == plan.bridge.image,
                     "reachable_image_changed")
                require_closed(row, plan.bridge.disabled_flags())
                need(rolling.environment(row).get("APP_RELEASE")
                     == {"name": "APP_RELEASE", "value": plan.bridge.release}, "reachable_source")
            env = rolling.environment(current["template"])
            rolling.verify_policy_bindings(plan.bridge.target_policies(), pin, current["template"])
            for secret in pin.secrets:
                need(env.get(secret.environment_name) == {"name": secret.environment_name,
                     "valueSource": {"secretKeyRef": {"secret": secret.secret, "version": secret.version}}}
                     and b._reference(secret.secret, secret.version) == secret.version,
                     "numeric_secret_changed")
        refs = {s.environment_name: s.secret + ":" + s.version for s in plan.services[0].secrets}
        need(refs.get("DATABASE_URL") == plan.bridge.runtime_secret, "runtime_secret_changed")

    def _neon(self):
        return self.bridge.observer._module("backend/scripts/monetary_cutover_preflight.py",
                                           "hirewiz_schema27_native_observer").neon_identity

    @contextmanager
    def readonly(self, schema):
        """Independently issued exact26/27 binding, not a relabelled old handle."""
        need(schema in {contract.BEFORE, contract.SCHEMA}, "observer_schema")
        neon = self._neon()
        retired = self.bridge.observer.describe_release_secret(self.bridge_plan.retired_migration_secret)
        name, version = self.bridge_plan.retired_migration_secret.split(":")
        need(retired.get("name") == f"projects/157225724590/secrets/{name}/versions/{version}"
             and retired.get("state") == "DISABLED", "retired_migrator_changed")
        collector = neon.collect_schema26_release_target if schema == contract.BEFORE else neon.collect_schema27_release_target
        observer_engine = None
        try:
            for alias, ref in (("replacement_runtime", self.bridge_plan.runtime_secret),
                               ("observer", self.bridge_plan.observer_secret)):
                binding, target = collector(self.bridge.observer, ref, alias=alias,
                    database_owner_oid=neon.FRESH_DATABASE_OWNER_OID, expected_schema=schema)
                need(binding.purpose == ("schema26_fresh_release_capture" if schema == contract.BEFORE
                     else "schema27_fresh_release_capture"), "observer_purpose")
                engine = target.engine()
                try:
                    with engine.connect() as db, db.begin():
                        db.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                        db.exec_driver_sql("SET LOCAL search_path=pg_catalog")
                        db.exec_driver_sql("SET LOCAL statement_timeout='3000ms'")
                        db.exec_driver_sql("SET LOCAL lock_timeout='1000ms'")
                        db.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
                        neon.verify_transport(db, binding)
                        neon.verify_native_target(db, binding, neon.FRESH_DATABASE_OID)
                        neon.verify_release_schema(db, binding)
                        if alias == "observer":
                            observer_engine = engine
                            yield db
                finally:
                    engine.dispose()
            need(observer_engine is not None
                 and self.bridge.observer.describe_release_secret(self.bridge_plan.retired_migration_secret) == retired,
                 "observer_or_retired_changed")
        finally:
            # Each acquired Engine was disposed in its own finally, including
            # exceptions from the caller's observation.
            pass

    def observe(self, schema):
        identity = app26.bootstrap_identity()
        with self.readonly(schema) as db:
            if schema == contract.BEFORE:
                signature = contract.before.observe(db)
                contract.before.verify_writer_permissions(db, runtime=identity.runtime, migrator=identity.migrator)
                need(db.execute(text("SELECT to_regclass('public.notification_preferences')")).scalar_one() is None,
                     "projection_must_be_absent")
            else:
                signature = contract.observe(db, identity)
            return signature, rolling.sha(step.old_acls(db, identity))

    def capture(self, *, nonce):
        need(type(nonce) is str and re.fullmatch(r"[a-f0-9]{32}", nonce), "nonce")
        with successor._original_journal_lock(self.bridge_journal):
            # Full immutable26 verification remains mandatory before capture.
            self.bridge.verify_global(self.bridge_plan)
            digest = bridge_receipts(self.bridge_plan, self.bridge_journal)
            signature, acls = self.observe(contract.BEFORE)
            plan = Migration27Plan(nonce, self.bridge_plan, digest, contract.verify_source(self.root),
                                   self._current_pins(), signature, acls)
            self.verify_live(plan)
            return plan

    @contextmanager
    def metadata(self):
        """Fresh process only; real collector26 runtime URL stays in RAM.

        Alembic needs application metadata, but its unused app Engine can never
        connect. The only write connection is the existing bootstrap manager.
        """
        from sqlalchemy import event

        need(not any(name in sys.modules for name in ("app.database", "app.models", "backend.app.database",
             "backend.app.models")), "metadata_fresh_process_required")
        neon = self._neon()
        _, target = neon.collect_schema26_release_target(self.bridge.observer,
            self.bridge_plan.runtime_secret, alias="replacement_runtime",
            database_owner_oid=neon.FRESH_DATABASE_OWNER_OID, expected_schema=contract.BEFORE)
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


def intent(plan):
    return {"expected": contract.BEFORE, "target": contract.SCHEMA,
            "plan_sha256": rolling.sha(asdict(plan)), "source_sha256": plan.source_sha256,
            "bridge_journal_sha256": plan.bridge_journal_sha256, "replay_authorized": False}


def receipt(plan, result):
    return {"kind": KIND, "intent_sha256": rolling.sha(intent(plan)),
            "plan_sha256": rolling.sha(asdict(plan)), "source_sha256": plan.source_sha256,
            "schema": contract.SCHEMA, "catalog_sha256": result["catalog_sha256"],
            "retained_acl_sha256": result["retained_acl_sha256"], "replay_authorized": False}


def write_receipt(db, plan, result):
    """Detached operator audit, atomically committed with DDL, no customer FK."""
    value = receipt(plan, result)
    need(result["source_sha256"] == plan.source_sha256
         and result["retained_acl_sha256"] == plan.retained_acl_sha256,
         "transaction_result_changed")
    db.execute(text("INSERT INTO public.admin_audit_events "
        "(id,actor_user_id,actor_email,action,target_type,target_id,reason,before_state,after_state,correlation_id,created_at) "
        "VALUES (:id,NULL,:actor,:action,'database_schema',:target,:reason,CAST(:before AS json),"
        "CAST(:after AS json),:correlation,timezone('UTC',clock_timestamp()))"),
        {"id": MARKER + "-" + plan.nonce, "actor": "harshilyogeshbhai886@gmail.com", "action": ACTION,
         "target": contract.SCHEMA, "reason": "One-use additive migration; no replay or activation",
         "before": rolling.canonical(intent(plan)).decode(), "after": rolling.canonical(value).decode(),
         "correlation": plan.nonce})
    return value


def reconcile_sql(db, plan):
    """A27 marker without THIS same-transaction audit is never our success."""
    identity = app26.bootstrap_identity()
    need(db.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": step.LOCK_ID}).scalar_one() is True,
         "original_transaction_pending")
    rows = db.execute(text("SELECT version_num FROM public.alembic_version LIMIT 2")).scalars().all()
    need(len(rows) == 1 and rows[0] in {contract.BEFORE, contract.SCHEMA}, "unexpected_schema")
    events = db.execute(text("SELECT actor_user_id,actor_email,action,target_type,target_id,before_state,"
        "after_state,correlation_id FROM public.admin_audit_events WHERE id=:id LIMIT 2"),
        {"id": MARKER + "-" + plan.nonce}).all()
    if rows == [contract.BEFORE]:
        need(not events, "receipt_without_migration")
        need(contract.before.observe(db) == plan.catalog26_sha256, "before_catalog_changed")
        need(db.execute(text("SELECT to_regclass('public.notification_preferences')")).scalar_one() is None,
             "unexpected_projection")
        return {"status": "schema26_observed_reconcile_only", "migration_committed": False}
    signature = contract.observe(db, identity)
    acls = rolling.sha(step.old_acls(db, identity))
    expected = receipt(plan, {"catalog_sha256": signature, "retained_acl_sha256": acls})
    need(len(events) == 1 and tuple(events[0]) == (None, "harshilyogeshbhai886@gmail.com", ACTION,
         "database_schema", contract.SCHEMA, intent(plan), expected, plan.nonce)
         and acls == plan.retained_acl_sha256, "same_transaction_receipt_required")
    return {"status": "schema27_same_transaction_observed", "migration_committed": True,
            "receipt_sha256": rolling.sha(expected)}


def migration(plan, boundary, journal, *, reconcile=False):
    """One durable owned intent, one write transaction; unknowns never replay."""
    plan.validate()
    need(type(boundary) is NativeMigration27Boundary and type(journal) is rolling.Journal
         and journal.plan_hash == rolling.sha(asdict(plan)), "exact_owned_plan")
    with successor._original_journal_lock(boundary.bridge_journal), journal.locked():
        started = journal.read(MARKER + ".started")
        ack = journal.read(MARKER + ".commit-acknowledged")
        checked = journal.read(MARKER + ".checked")
        need(started is not None or ack is checked is None, "orphan_transaction_record")
        need(ack is None or (set(ack) == {"receipt_sha256", "plan_sha256"}
             and rolling.HASH.fullmatch(ack.get("receipt_sha256", ""))), "commit_ack_changed")
        need((started is not None) == reconcile, "started_reconcile_only")
        need(started is None or started == intent(plan), "intent_changed")
        boundary.verify_live(plan)
        if started is None:
            signature, acls = boundary.observe(contract.BEFORE)
            need((signature, acls) == (plan.catalog26_sha256, plan.retained_acl_sha256), "before_catalog_or_acl_changed")
            # Finish native credentials/metadata initialization OUTSIDE SQL.
            with boundary.metadata():
                engine = boundary.bootstrap_engine()
                try:
                    # O_EXCL/file+directory fsync must ACK before ANY DDL send.
                    journal.record(MARKER + ".started", intent(plan))
                    with engine.connect() as db, db.begin():
                        result = step.upgrade_transaction(db, app26.bootstrap_identity(), root=boundary.root,
                            flags=dict.fromkeys(contract.DISABLED_FLAGS, "false"))
                        need(result["committed"] is False and result["transaction_ready_to_commit"] is True,
                             "transaction_not_ready")
                        committed_receipt = write_receipt(db, plan, result)
                    expected_ack = {"receipt_sha256": rolling.sha(committed_receipt),
                                    "plan_sha256": journal.plan_hash}
                    journal.record(MARKER + ".commit-acknowledged", expected_ack)
                    # Bind THIS invocation's known committed ACK too; a later
                    # SQL26 restoration cannot erase that historical fact.
                    need(journal.read(MARKER + ".commit-acknowledged") == expected_ack, "commit_ack_changed")
                    ack = expected_ack
                finally:
                    engine.dispose()
        # Unknown commits have no trusted marker guess. Probe26 through a NEW
        # exact26 target, or27 through a separately issued exact27 target. The
        # collector's marker refusal is the sole allowed branch discriminator.
        neon = boundary._neon()
        try:
            with boundary.readonly(contract.BEFORE) as db:
                observed = reconcile_sql(db, plan)
        except neon.NeonIdentityDenied as error:
            need(str(error) == "neon_release_exact_schema_mismatch", "observer_unavailable")
            with boundary.readonly(contract.SCHEMA) as db:
                observed = reconcile_sql(db, plan)
        boundary.verify_live(plan)
        if ack is not None:
            need(observed.get("migration_committed") is True
                 and ack == {"plan_sha256": journal.plan_hash,
                             "receipt_sha256": observed.get("receipt_sha256")}, "commit_ack_changed")
        need(checked is None or checked == observed | {"plan_sha256": journal.plan_hash},
             "checked_observation_changed")
        if observed["migration_committed"] and journal.read(MARKER + ".checked") is None:
            journal.record(MARKER + ".checked", observed)
        return observed | {"replay_authorized": False, "migration_replayed": False,
                           "new_admission_enabled": False, "cloud_resource_mutations": 0}


def load(raw):
    value = rolling.release._json(raw)
    value["bridge"] = successor.load(rolling.canonical(value["bridge"]))
    value["services"] = tuple(rolling.ServicePin(**(row | {
        "secrets": tuple(rolling.SecretPin(**secret) for secret in row["secrets"]),
        "traffic": tuple(row["traffic"]), "revision_keys": tuple(row["revision_keys"]),
    })) for row in value["services"])
    result = Migration27Plan(**value)
    result.validate()
    return result


def load_capture_config(raw):
    """Bounded source/ref metadata only; no credential/plugin/transport slots."""
    value = rolling.release._json(raw)
    need(type(value) is dict and set(value) == {"source", "prior", "options"}, "capture_config")
    pins = []
    for name in ("source", "prior"):
        item = value[name]
        need(type(item) is dict and set(item) == {"release", "image_digest", "build_id", "ci_run_id"},
             "capture_source_pin")
        pin = successor.SourcePin(**item)
        pin.validate()
        pins.append(pin)
    options = value["options"]
    required = {"controller_ci_run_id", "nonce", "observer_secret", "retired_migration_secret", "workers_release"}
    optional = {"employer_cache_secret", "candidate_expense_secret", "candidate_model_policy_secret"}
    need(type(options) is dict and required <= set(options) <= required | optional
         and type(options["controller_ci_run_id"]) is int and options["controller_ci_run_id"] > 0
         and type(options["nonce"]) is str and re.fullmatch(r"[a-f0-9]{32}", options["nonce"])
         and type(options["workers_release"]) is str and re.fullmatch(r"[a-f0-9]{40}", options["workers_release"]),
         "capture_options")
    for key in ("observer_secret", "retired_migration_secret", "candidate_expense_secret", "candidate_model_policy_secret"):
        need(key not in options or (type(options[key]) is str and rolling.REF.fullmatch(options[key])),
             "numeric_capture_reference")
    need("employer_cache_secret" not in options or (type(options["employer_cache_secret"]) is str
         and re.fullmatch(r"[a-zA-Z0-9_-]{1,255}", options["employer_cache_secret"])), "capture_cache_name")
    return pins[0], pins[1], options


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("bridge-capture", "bridge-stage", "bridge-advance", "bridge-observe", "bridge-reconcile",
                                          "capture", "upgrade", "reconcile"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--prior-root", type=Path, required=True)
    parser.add_argument("--capture-config", type=Path)
    parser.add_argument("--capture-config-sha256")
    parser.add_argument("--bridge-plan", type=Path)
    parser.add_argument("--bridge-plan-sha256")
    parser.add_argument("--bridge-journal", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--nonce")
    args = parser.parse_args()
    if args.phase == "bridge-capture":
        need(args.capture_config is not None and args.capture_config_sha256 is not None,
             "pinned_capture_config_required")
        source, prior, options = load_capture_config(app26.initial._read_pinned(
            args.capture_config, args.capture_config_sha256))
        print(json.dumps(asdict(capture_bridge(args.root, args.target_root, args.prior_root,
                                             source, prior, **options)), sort_keys=True))
        return
    need(args.bridge_plan is not None and args.bridge_plan_sha256 is not None
         and args.bridge_journal is not None, "pinned_bridge_plan_required")
    bridge = successor.load(app26.initial._read_pinned(args.bridge_plan, args.bridge_plan_sha256))
    owner = rolling.Journal(args.bridge_journal, bridge)
    boundary = NativeMigration27Boundary(args.root, args.target_root, args.prior_root, bridge, owner)
    try:
        if args.phase.startswith("bridge-"):
            result = boundary.bridge_phase(args.phase.removeprefix("bridge-"))
        elif args.phase == "capture":
            result = asdict(boundary.capture(nonce=args.nonce))
        else:
            need(args.plan is not None and args.journal is not None and args.plan_sha256 is not None,
                 "pinned_plan_required")
            plan = load(app26.initial._read_pinned(args.plan, args.plan_sha256))
            result = migration(plan, boundary, rolling.Journal(args.journal, plan), reconcile=args.phase == "reconcile")
        print(json.dumps(result, sort_keys=True))
    finally:
        boundary.bridge.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # No raw provider/credential/SQL exception can escape the CLI.
        raise SystemExit("schema27_native_unavailable_or_outcome_unknown") from None
