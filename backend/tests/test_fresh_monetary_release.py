"""Ordinary synthetic sequencing tests; no server, credential or cloud calls."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
fresh = importlib.import_module("fresh_monetary_release")

release = fresh.release
STAMP = datetime(2026, 10, 13, 18, 0, tzinfo=UTC)


@pytest.fixture
def source(tmp_path):
    return release.Plan(
        release="a" * 40,
        image_digest="sha256:" + "b" * 64,
        build_id="11111111-1111-1111-1111-111111111111",
        ci_run_id=7,
        runtime_secret="inert-old-runtime:2",
        migration_secret="inert-old-migration:3",
        observer_secret="inert-old-observer:4",
        database_owner_oid=16387,
        expense_secret="inert-expense-policy:5",
        expense_sha256="c" * 64,
        model_policy_secret="inert-model-policy:6",
        model_policy_sha256="d" * 64,
        model_key_secret="inert-model-key:7",
        model_primary="gemini-inert-model",
        model_api_base=release.MODEL_API_BASE,
        runtime_service_account=f"inert-runtime@{release.PROJECT}.iam.gserviceaccount.com",
        migration_service_account=f"inert-migration@{release.PROJECT}.iam.gserviceaccount.com",
        backup_uri=f"gs://{release.PROJECT}-hirewiz-application-artifacts/releases/inert.dump",
        inventory_path=tmp_path / "inert.json",
        inventory_sha256="e" * 64,
    )


@pytest.fixture
def target():
    return release.FreshTargetPlan(
        "inert-fresh-runtime:1", "inert-fresh-migrator:2", "inert-fresh-observer:3"
    )


@pytest.fixture
def transfer(tmp_path):
    return fresh.TransferPlan(
        tmp_path / "pg_dump", tmp_path / "pg_restore", "f" * 64, "0" * 64, tmp_path
    )


class Boundary:
    def __init__(self, source):
        self.source, self.events = source, []
        self.schema, self.finalized = release.CUTOVER_CHAIN[0], False
        self.failure = None
        self.fence = release.Fence(
            STAMP, STAMP - timedelta(minutes=5), True, True, True
        )

    def event(self, name, *args):
        self.events.append((name, *args))
        if self.failure == name or self.failure == (name, *args):
            raise release.ReleaseDenied("synthetic_boundary_refusal")

    def verify_identity_and_policy(self, plan):
        assert plan is self.source
        self.event("identity")

    def verify_admission_closed(self):
        self.event("closed")

    def verify_fences(self, plan):
        assert plan is self.source
        self.event("fence")
        return self.fence

    def verify_database(self, plan, schema):
        assert plan is self.source and schema == release.CUTOVER_CHAIN[0]
        self.event("source-native", schema)

    def create_backup(self, plan, fence):
        assert plan is self.source
        self.event("source-backup")

    def verify_document_configuration(self, source):
        assert source is self.source
        self.event("document-configuration")
        return {"configuration_matches": True, "runtime_isolation_proven": False}

    def verify_backup(self, plan, fence):
        assert plan is self.source
        self.event("source-backup-verified")

    def transfer_fresh(self, source, target, transfer, fence):
        assert source is self.source
        self.event("final-transfer")

    def grant_fresh_runtime(self, source, target, schema):
        assert schema == self.schema
        self.event("target-grants", schema)

    def verify_fresh_database_phase(self, source, target, schema, *, phase):
        assert schema == self.schema
        self.event("target-phase", schema, phase)
        if phase == "serving" and not self.finalized:
            raise release.ReleaseDenied("synthetic_finalization_required")

    def verify_fresh_finances(self, source, target, schema):
        assert schema == self.schema
        self.event("target-finances", schema)

    def migrate_fresh(self, source, target, expected, next_schema):
        assert expected == self.schema
        self.event("target-migrate", expected, next_schema)
        self.schema = next_schema

    def stage_fresh(self, source, target, service):
        self.event("zero-traffic-stage", service)
        return service + "-00042-inert"

    def verify_fresh_revision(self, source, target, service, revision):
        assert revision == service + "-00042-inert"
        self.event("target-revision", service)

    def promote(self, service, revision):
        self.event("promote", service)

    def verify_fresh_serving(self, source, target, service, revision):
        self.event("target-serving", service)


def stage(source, target, transfer, boundary, *, execute=True):
    return fresh.stage_fresh_release(
        source, target, transfer, boundary, execute=execute, clock=lambda: STAMP
    )


def test_fresh_stage_preserves_old_source_and_stops_before_finalization(
    source, target, transfer
):
    boundary = Boundary(source)
    result = stage(source, target, transfer, boundary)
    assert result["staging_complete"] is True
    assert result["production_released"] is result["traffic_promoted"] is False
    assert result["target_finalization_required"] is True
    assert boundary.events.index(("source-backup-verified",)) < boundary.events.index(
        ("final-transfer",)
    )
    assert boundary.events.index(("final-transfer",)) < boundary.events.index(
        ("target-grants", release.CUTOVER_CHAIN[0])
    )
    migrations = [event for event in boundary.events if event[0] == "target-migrate"]
    assert migrations == [
        ("target-migrate", old, new)
        for old, new in zip(
            release.CUTOVER_CHAIN[:-1], release.CUTOVER_CHAIN[1:], strict=True
        )
    ]
    assert [
        event[1] for event in boundary.events if event[0] == "zero-traffic-stage"
    ] == list(release.SERVICES)
    assert not any(
        event[0] in {"promote", "finalize", "source-migrate", "drop", "rollback"}
        for event in boundary.events
    )
    assert all(
        event[1] == release.CUTOVER_CHAIN[0]
        for event in boundary.events
        if event[0] == "source-native"
    )


@pytest.mark.parametrize(
    "failure",
    [
        "fence",
        "source-native",
        "source-backup-verified",
        "final-transfer",
        ("target-migrate", "20261009_0011", "20261009_0012"),
        ("zero-traffic-stage", release.SERVICES[1]),
    ],
)
def test_lost_or_refused_actions_stop_without_replay(source, target, transfer, failure):
    boundary = Boundary(source)
    boundary.failure = failure
    with pytest.raises(release.ReleaseDenied, match="synthetic_boundary_refusal"):
        stage(source, target, transfer, boundary)
    mutations = [
        event
        for event in boundary.events
        if event[0]
        in {
            "source-backup",
            "final-transfer",
            "target-grants",
            "target-migrate",
            "zero-traffic-stage",
        }
    ]
    assert len(mutations) == len(set(mutations))
    assert not any(event[0] == "promote" for event in boundary.events)
    if failure == "fence" or failure == "source-native":
        assert not mutations


def test_observation_and_bad_destination_do_not_mutate(source, target, transfer):
    boundary = Boundary(source)
    result = stage(source, target, transfer, boundary, execute=False)
    assert result["mutation_count"] == 0 and result["production_released"] is False
    assert not any(
        event[0] in {"source-backup", "final-transfer"} for event in boundary.events
    )
    boundary = Boundary(source)
    with pytest.raises(release.ReleaseDenied):
        stage(
            source,
            replace(target, migration_secret=source.runtime_secret),
            transfer,
            boundary,
        )
    assert not boundary.events


def test_promotion_requires_native_finalization_and_all_joined_revisions(
    source, target, transfer
):
    boundary = Boundary(source)
    staged = stage(source, target, transfer, boundary)
    with pytest.raises(release.ReleaseDenied, match="synthetic_finalization_required"):
        fresh.promote_fresh_release(
            source, target, staged["joined_revisions"], boundary, clock=lambda: STAMP
        )
    assert not any(event[0] == "promote" for event in boundary.events)
    boundary.finalized = True
    result = fresh.promote_fresh_release(
        source, target, staged["joined_revisions"], boundary, clock=lambda: STAMP
    )
    assert (
        result["gcp_traffic_promoted"] is True
        and result["production_released"] is False
    )
    assert result["frontend_deployment_verified"] is result["goal_complete"] is False
    assert [event[1] for event in boundary.events if event[0] == "promote"] == [
        *release.SERVICES[1:],
        release.SERVICES[0],
    ]
    boundary = Boundary(source)
    with pytest.raises(
        release.ReleaseDenied, match="fresh_joined_revision_identity_invalid"
    ):
        fresh.promote_fresh_release(
            source,
            target,
            {release.SERVICES[0]: "unknown"},
            boundary,
            clock=lambda: STAMP,
        )
    assert not boundary.events


def test_native_fence_remains_unimplemented_not_an_inventory_override(
    source, target, transfer
):
    boundary = fresh.FreshNativeBoundary(ROOT)
    source.inventory_path.write_text('{"native_cut":true,"cutover_ready":true}')
    with pytest.raises(
        release.ReleaseDenied,
        match="native_external_consumer_and_provider_fence_verifier_missing",
    ):
        boundary.verify_fences(source)


def test_runtime_copy_uses_target_secrets_without_changing_source(source, target):
    plan = fresh.runtime_plan(source, target)
    for service in release.SERVICES:
        env, secrets, _ = release.runtime_environment(plan, service)
        assert secrets["DATABASE_URL"] == target.runtime_secret
        assert env["RAZORPAY_CHECKOUT_ENABLED"] == "false"
    assert (
        source.runtime_secret == "inert-old-runtime:2"
        and source.database_owner_oid == 16387
    )
    assert (
        plan.expense_secret == source.expense_secret
        and plan.model_key_secret == source.model_key_secret
    )


class FinancialConnection:
    def __init__(self, tables, counts):
        self.tables, self.counts, self.queries = tables, iter(counts), []

    def execute(self, query):
        sql = str(query)
        self.queries.append(sql)
        if "c.relname" in sql:
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: self.tables)
            )
        return SimpleNamespace(scalar_one=lambda: next(self.counts))


def financial_module(*, unresolved=0):
    return SimpleNamespace(
        TABLES={
            "analysis_runs",
            "model_call_events",
            "employer_applications",
            "employer_application_attempts",
            "service_credit_reservations",
        },
        FINANCIAL_SCHEMAS=set(release.CUTOVER_CHAIN[1:]),
        CANDIDATE_SCHEMAS=set(release.CUTOVER_CHAIN[2:]),
        CANDIDATE_TABLES={"candidate_password_accounts", "candidate_lifetime_history"},
        _candidate_projection_shape=lambda *args: None,
        _extension_shape=lambda *args: None,
        _liabilities=lambda *args: {"unresolved_count": unresolved},
    )


@pytest.mark.parametrize("bad", [1, True, -1, None])
def test_native_financial_counts_cannot_be_asserted_clean(bad):
    module = financial_module()
    tables = sorted(module.TABLES | {"alembic_version"})
    connection = FinancialConnection(tables, [0, 0, bad, 0, 0, 0])
    with pytest.raises(
        release.ReleaseDenied, match="fresh_unresolved_financial_or_application_state"
    ):
        fresh.verify_financial_state(connection, module, release.CUTOVER_CHAIN[0])
    assert all(
        "UPDATE " not in query and "DELETE " not in query
        for query in connection.queries
    )


def test_schema_extensions_and_liabilities_are_required_after_migration():
    module = financial_module(unresolved=1)
    tables = sorted(
        module.TABLES
        | module.CANDIDATE_TABLES
        | {"alembic_version", "model_cost_liabilities"}
    )
    with pytest.raises(
        release.ReleaseDenied, match="fresh_unresolved_model_liabilities"
    ):
        fresh.verify_financial_state(
            FinancialConnection(tables, [0] * 8), module, release.CHAIN[-1]
        )
    module = financial_module()
    fresh.verify_financial_state(
        FinancialConnection(tables, [0] * 8), module, release.CHAIN[-1]
    )
    with pytest.raises(
        release.ReleaseDenied, match="fresh_financial_schema_shape_invalid"
    ):
        fresh.verify_financial_state(
            FinancialConnection(tables, [0] * 8), module, release.CUTOVER_CHAIN[0]
        )


def test_source_adapter_retirement_does_not_require_old_runtime_login(
    source, monkeypatch
):
    boundary = fresh.FreshNativeBoundary(ROOT)
    rows = {
        alias: {
            "login_enabled": False,
            "schema_create": False,
            "active_session_count": 0,
            "elevated": False,
            "elevated_role_membership": False,
            "membership_administration": False,
            "writable_table_count": 0,
            "executable_security_definer_count": 0,
        }
        for alias in ("replacement_runtime", "replacement_migration")
    }
    disposed = []
    module = SimpleNamespace(
        observe=lambda *args, **kwargs: {"roles": rows},
        evaluate=lambda *args: {"database_gate_reasons": []},
    )
    engine = SimpleNamespace(dispose=lambda: disposed.append(True))
    monkeypatch.setattr(
        boundary,
        "_database_context",
        lambda *args: (module, (engine, None, None, None), {}, ()),
    )
    boundary.verify_database(source, release.CUTOVER_CHAIN[0])
    rows["replacement_runtime"]["login_enabled"] = True
    with pytest.raises(
        release.ReleaseDenied, match="fresh_source_automated_writer_not_retired"
    ):
        boundary.verify_database(source, release.CUTOVER_CHAIN[0])
    assert disposed == [True, True]
    with pytest.raises(
        release.ReleaseDenied, match="fresh_source_schema_must_remain_nine"
    ):
        boundary.verify_database(source, release.CHAIN[-1])


def transfer_library():
    spec = importlib.util.spec_from_file_location(
        "fresh_transfer_pure_fixture", ROOT / "backend/scripts/database_transfer.py"
    )
    assert spec and spec.loader
    value = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = value
    spec.loader.exec_module(value)
    return value


def test_real_transfer_adapter_does_not_require_destination_marker_before_copy(
    source, target, transfer, monkeypatch
):
    library = transfer_library()
    boundary = fresh.FreshNativeBoundary(ROOT)
    events = []
    original = SimpleNamespace(
        host="source.example", actor="source_observer", actor_oid=1884182
    )
    destination = SimpleNamespace(
        host="destination.example", actor="fresh_migrator", actor_oid=24578
    )

    def transient(identity):
        # The real owned transfer, not the profile's post-restore engine, owns
        # initial native auth and exact empty-catalog admission.
        def forbidden():
            pytest.fail("An empty destination has no schema marker yet")

        return SimpleNamespace(
            engine=forbidden,
            dump_environment=lambda: {
                "PGCHANNELBINDING": "prefer",
                "PGSSLROOTCERT": "/inert/ca.pem",
                "PGPASSWORD": "inert-synthetic-password",
            },
        )

    def collect(reader, reference, **kwargs):
        assert (
            reference == target.migration_secret
            and kwargs["alias"] == "replacement_migration"
        )
        events.append("numeric-target-binding")
        return destination, transient(destination)

    neon = SimpleNamespace(
        DATABASE="neondb",
        ROOT_CERT="/inert/ca.pem",
        FRESH_DATABASE_OWNER_OID=16391,
        FRESH_NAMESPACE_OWNER_OID=6171,
        FRESH_CLUSTER="f" * 64,
        FRESH_DATABASE_OID=16395,
        collect_fresh_release_target=collect,
    )
    module = SimpleNamespace(neon_identity=neon)
    inventory = SimpleNamespace(
        expected_system_identifier_sha256="c" * 64, expected_database_oid=16391
    )
    monkeypatch.setattr(
        boundary, "verify_database", lambda *args: events.append("native-source-check")
    )
    monkeypatch.setattr(
        boundary,
        "_database_context",
        lambda *args: (
            module,
            (
                SimpleNamespace(dispose=lambda: None),
                inventory,
                original,
                transient(original),
            ),
            {},
            (),
        ),
    )
    monkeypatch.setattr(boundary, "_module", lambda *args: library)
    now = datetime.now(UTC)
    fence = release.Fence(now, now - timedelta(minutes=5), True, True, True)
    monkeypatch.setattr(
        boundary,
        "verify_fences",
        lambda *args: replace(fence, observed_at=datetime.now(UTC)),
    )

    native_owner = [(16391, 2200, 6171)]

    class NativeDestination:
        autocommit = False

        def execute(self, query):
            assert "pg_catalog.pg_database" in query and "alembic_version" not in query
            events.append("native-empty-owner-read")
            return SimpleNamespace(fetchone=lambda: native_owner[0])

        def close(self):
            events.append("native-destination-close")

    def native_connect(database):
        assert database.identity.actor_oid == 24578
        events.append("existing-native-tls-actor-cluster-guard")
        return NativeDestination()

    monkeypatch.setattr(library.BoundDatabase, "connect", native_connect)

    def owned(old, new, tools, workdir, *, purpose, native_cut):
        assert (old.identity.database_oid, new.identity.database_oid) == (16391, 16395)
        assert (old.identity.actor_oid, new.identity.actor_oid) == (1884182, 24578)
        assert old.identity.cluster_sha256 != new.identity.cluster_sha256
        assert (
            new.restore_role is None
            and purpose == "final"
            and workdir == transfer.workdir
        )
        assert tools.dump_sha256 == transfer.dump_sha256
        observation = native_cut.verify_native_cut(old.identity, new.identity)
        assert (
            type(observation) is library.NativeCutObservation
            and observation.retired_since == fence.retired_since
        )
        connection = new.connect()
        assert connection.autocommit is False
        connection.close()
        events.append("owned-final-transfer")
        return {
            "status": "verified",
            "purpose": "final",
            "schema": release.CUTOVER_CHAIN[0],
            "native_cut_observed": True,
            "attempt_started": True,
            "cleanup": "group_absent_anchor_reaped",
            "production_released": False,
        }

    monkeypatch.setattr(library, "owned_transfer", owned)
    boundary.transfer_fresh(source, target, transfer, fence)
    assert events[-1] == "owned-final-transfer"
    assert (
        events.index("existing-native-tls-actor-cluster-guard")
        < events.index("native-empty-owner-read")
        < events.index("owned-final-transfer")
    )
    for refused_owner in (
        (777, 2200, 6171),
        (16391, 777, 6171),
        (16391, 2200, 777),
        (True, 2200, 6171),
        None,
    ):
        native_owner[0] = refused_owner
        events.clear()
        with pytest.raises(
            release.ReleaseDenied,
            match="fresh_empty_destination_owner_identity_changed",
        ):
            boundary.transfer_fresh(source, target, transfer, fence)
        assert "owned-final-transfer" not in events
        assert events[-1] == "native-destination-close"
    native_owner[0] = (16391, 2200, 6171)
    monkeypatch.setattr(
        boundary,
        "verify_fences",
        lambda *args: release.NativeBoundary.verify_fences(boundary, source),
    )
    with pytest.raises(
        release.ReleaseDenied,
        match="native_external_consumer_and_provider_fence_verifier_missing",
    ):
        boundary.transfer_fresh(source, target, transfer, fence)


class GrantConnection:
    def __init__(self, rows):
        self.rows, self.events = rows, []
        self.dialect = SimpleNamespace(
            identifier_preparer=SimpleNamespace(
                quote=lambda name: '"' + name.replace('"', '""') + '"'
            )
        )

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return None

    def begin(self):
        return self

    def exec_driver_sql(self, sql):
        self.events.append(sql)

    def execute(self, query):
        return SimpleNamespace(all=lambda: self.rows)


def test_grants_are_owned_bounded_and_never_delete_financial_history(
    source, target, monkeypatch
):
    connection = GrantConnection(
        [
            ("alembic_version", "r", 24578),
            ("users", "r", 24578),
            ("payment_orders", "r", 24578),
            ("users_id_seq", "S", 24578),
        ]
    )
    boundary = fresh.FreshNativeBoundary(ROOT)
    neon = SimpleNamespace(
        FRESH_DATABASE_OID=16395,
        FRESH_RELEASE_ACTORS={
            "replacement_runtime": ("fresh_runtime", 24576),
            "replacement_migration": ("fresh_migrator", 24578),
            "observer": ("fresh_observer", 24580),
        },
        verify_transport=lambda *args: connection.events.append("native-transport"),
        verify_native_target=lambda *args: connection.events.append(
            "native-cluster-schema"
        ),
    )
    disposed = []
    engine = SimpleNamespace(
        connect=lambda: connection, dispose=lambda: disposed.append(True)
    )
    monkeypatch.setattr(
        boundary,
        "_fresh_connection",
        lambda *args: (SimpleNamespace(neon_identity=neon), None, engine),
    )
    monkeypatch.setattr(
        release,
        "verify_runtime_permissions",
        lambda *args: connection.events.append("complete-permission-check"),
    )
    boundary.grant_fresh_runtime(source, target, release.CUTOVER_CHAIN[0])
    grants = [event for event in connection.events if event.startswith("GRANT")]
    assert 'GRANT SELECT ON TABLE public."alembic_version" TO "fresh_runtime"' in grants
    assert (
        'GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE public."users" TO "fresh_runtime"'
        in grants
    )
    assert (
        'GRANT SELECT,INSERT,UPDATE ON TABLE public."payment_orders" TO "fresh_runtime"'
        in grants
    )
    assert not any(
        "TRUNCATE" in event or " WITH GRANT OPTION" in event for event in grants
    )
    assert connection.events.index("native-cluster-schema") < connection.events.index(
        grants[0]
    )
    connection.events.clear()
    connection.rows = [("unrelated", "r", 999)]
    with pytest.raises(
        release.ReleaseDenied, match="fresh_owned_grant_inventory_invalid"
    ):
        boundary.grant_fresh_runtime(source, target, release.CUTOVER_CHAIN[0])
    assert not any(event.startswith("GRANT") for event in connection.events)
    assert disposed == [True, True]


def test_native_migration_configuration_targets_only_the_fresh_role_and_secret(
    source, target, monkeypatch
):
    boundary = fresh.FreshNativeBoundary(ROOT)
    calls = []
    monkeypatch.setattr(
        boundary, "verify_fresh_database_phase", lambda *args, **kwargs: None
    )
    neon = SimpleNamespace(
        FRESH_CLUSTER="f" * 64, FRESH_DATABASE_OID=16395, NAMESPACE="public"
    )
    monkeypatch.setattr(
        boundary,
        "_fresh_connection",
        lambda *args: (
            SimpleNamespace(neon_identity=neon),
            SimpleNamespace(actor="hirewiz_migrator_20261013_r1", actor_oid=24578),
            SimpleNamespace(dispose=lambda: None),
        ),
    )

    def cloud(*args):
        calls.append(args)
        return (
            {"status": {"succeededCount": 1, "completionTime": "inert"}}
            if "execute" in args
            else {}
        )

    monkeypatch.setattr(boundary, "_cloud", cloud)
    boundary.migrate_fresh(
        source, target, release.CUTOVER_CHAIN[0], release.CUTOVER_CHAIN[1]
    )
    deploy, execute = calls
    assert (
        deploy[deploy.index("--set-secrets") + 1]
        == "DATABASE_URL=" + target.migration_secret
    )
    assert (
        "HIREWIZ_CUTOVER_DATABASE_OID=16395"
        in deploy[deploy.index("--set-env-vars") + 1]
    )
    assert (
        "HIREWIZ_CUTOVER_MIGRATION_ROLE_OID=24578"
        in deploy[deploy.index("--set-env-vars") + 1]
    )
    assert source.migration_secret not in repr(calls)
    assert (
        deploy[deploy.index("--max-retries") + 1] == "0"
        and execute.count("execute") == 1
    )
    calls.clear()
    monkeypatch.setattr(boundary, "_cloud", lambda *args: calls.append(args) or {})
    with pytest.raises(
        release.ReleaseDenied, match="fresh_migration_outcome_unknown_reconcile_only"
    ):
        boundary.migrate_fresh(
            source, target, release.CUTOVER_CHAIN[0], release.CUTOVER_CHAIN[1]
        )
    assert len(calls) == 2


def test_existing_real_empty_guard_admits_no_marker_and_refuses_any_user_object():
    library = transfer_library()

    class Empty:
        def __init__(self, count):
            self.count = count
            self.queries = []

        def execute(self, sql):
            self.queries.append(sql)
            return SimpleNamespace(
                fetchone=lambda: (self.count, 0, 0, 0, 0, 0),
                fetchall=lambda: [("public",)],
            )

    empty = Empty(0)
    library._empty(empty)
    assert len(empty.queries) == 2 and all(
        "alembic_version" not in query for query in empty.queries
    )
    # A preexisting alembic marker is a user relation and therefore refuses.
    with pytest.raises(library.TransferDenied, match="destination_not_empty"):
        library._empty(Empty(1))


def test_fresh_migration_oid_guard_refuses_recreated_actor_before_any_ddl():
    spec = importlib.util.spec_from_file_location(
        "fresh_schema_step_pure_fixture",
        ROOT / "backend/scripts/monetary_schema_step.py",
    )
    assert spec and spec.loader
    step = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(step)

    class Connection:
        def __init__(self):
            self.events = []

        def __enter__(self):
            return self

        def __exit__(self, *unused):
            return None

        def exec_driver_sql(self, sql):
            self.events.append(sql)

        def execute(self, sql):
            query = str(sql)
            self.events.append(query)
            return SimpleNamespace(
                scalar_one=lambda: (
                    "fresh_migrator" if query == "SELECT current_user" else 777
                )
            )

        def rollback(self):
            self.events.append("rollback")

    connection = Connection()
    engine = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql"), connect=lambda: connection
    )
    args = dict(
        expected=release.CUTOVER_CHAIN[0],
        target=release.CUTOVER_CHAIN[1],
        identifier_hash="f" * 64,
        database_oid=16395,
        namespace="public",
        migration_role="fresh_migrator",
        migration_role_oid=24578,
    )
    with pytest.raises(step.StepDenied, match="migration_role_oid_mismatch"):
        step.upgrade_step(engine, **args)
    assert connection.events[0] == "SET TRANSACTION READ WRITE"
    assert (
        "SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user"
        in connection.events
    )
    assert not any(
        "pg_try_advisory_lock" in event or "alembic_version" in event
        for event in connection.events
    )
    connection.events.clear()
    args["migration_role_oid"] = True
    with pytest.raises(step.StepDenied, match="migration_step_or_identity_invalid"):
        step.upgrade_step(engine, **args)
    assert not connection.events
    args["migration_role_oid"] = None
    with pytest.raises(step.StepDenied, match="migration_database_identity_mismatch"):
        step.upgrade_step(engine, **args)
    assert "SET TRANSACTION READ WRITE" not in connection.events
    assert (
        "SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user"
        not in connection.events
    )
