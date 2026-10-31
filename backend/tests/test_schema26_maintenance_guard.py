"""Pure finite controls; no native metadata, SQL, DDL or credentials exercised."""

import ast
import copy
import importlib
import sys
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.recovery.gcp_contracts import RegistryPin
from app.domains.recovery.gcp_partitioned_contracts import PartitionedPin, PartitionedResource
from app.domains.recovery.gcp_publication_contracts import PublicationPin
from app.domains.recovery.store import GuardUnavailable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
v3 = importlib.import_module("schema26_app_rolling_v3")
m = importlib.import_module("schema26_maintenance_guard")

# Observed public condition SHAPE only; not a native observation/receipt. The
# actual generated SDK tests and3-GET cloud observation are retained separately.
ACTUAL_RETIRED_CONDITIONS = [
    {
        "type": "Ready",
        "state": "CONDITION_SUCCEEDED",
        "revisionReason": "RETIRED",
        "lastTransitionTime": "2026-10-30T21:34:54.787968Z",
    },
    {
        "type": "Active",
        "state": "CONDITION_FAILED",
        "revisionReason": "RETIRED",
        "lastTransitionTime": "2026-10-30T21:34:54.787968Z",
    },
    {
        "type": "ResourcesAvailable",
        "state": "CONDITION_RECONCILING",
        "revisionReason": "RETIRED",
        "lastTransitionTime": "2026-10-30T21:34:54.787968Z",
    },
    {
        "type": "ContainerReady",
        "state": "CONDITION_SUCCEEDED",
        "revisionReason": None,
        "lastTransitionTime": "2026-10-30T20:05:22.904349Z",
    },
    {
        "type": "ContainerHealthy",
        "state": "CONDITION_SUCCEEDED",
        "revisionReason": None,
        "lastTransitionTime": "2026-10-30T20:06:03.122369Z",
    },
    {
        "type": "MinInstancesProvisioned",
        "state": "CONDITION_SUCCEEDED",
        "revisionReason": None,
        "lastTransitionTime": "2026-10-30T20:06:08.893323Z",
    },
]


def data():
    service = {
        "name": m.metadata.API,
        "uid": "00000000-0000-0000-0000-000000000001",
        "generation": "1",
        "observedGeneration": "1",
        "etag": "synthetic",
        "reconciling": False,
        "terminalCondition": {"type": "Ready", "state": "CONDITION_SUCCEEDED"},
        "latestCreatedRevision": m.metadata.API + "/revisions/current",
        "latestReadyRevision": m.metadata.API + "/revisions/current",
        "traffic": [
            {
                "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                "revision": "current",
                "percent": 100,
            }
        ],
        "trafficStatuses": [
            {
                "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                "revision": "current",
                "percent": 100,
            }
        ],
    }
    row = {
        "name": m.metadata.API + "/revisions/old",
        "uid": "00000000-0000-0000-0000-000000000002",
        "service": m.metadata.SERVICE_ID,
        "conditions": copy.deepcopy(ACTUAL_RETIRED_CONDITIONS),
    }
    return service, {"revisions": [row]}


class Stock:
    """Pure finite metadata port; never native transport/SDK qualification."""

    def __init__(self, replies=None):
        self.replies = replies or [data()[0], data()[1], data()[0]]
        self.calls = []


class FakeMetadata:
    def __init__(self, stock):
        self.stock, self.request_count, self.closed = stock, 0, False

    def observe(self):
        assert self.request_count == 0
        self.request_count = 3
        self.stock.calls.extend(["fake-service-before", "fake-list", "fake-service-after"])
        before, listing, after = self.stock.replies
        assert before == after
        return {
            "service": after,
            "revisions": listing["revisions"],
            "elapsed_ms": 1,
            "resource_gets": 3,
            "physical_resource_gets": 3,
            "resource_write_sends": 0,
            "maintenance_admission_proved": False,
        }

    def close(self):
        self.closed = True


def build(stock):
    return FakeMetadata(stock)


def baseline():
    return v3.ClosedApiBaseline(
        *(["a" * 64] * 4),
        "00000000-0000-0000-0000-000000000001",
        20,
        "closed-etag",
        "ai-resume-parser-m25-aaaaaaaaaaaa-close",
        "00000000-0000-0000-0000-000000000003",
        "b" * 64,
        tuple((flag, "true") for flag in sorted(v3.historical.PILOT_FLAGS)),
    )


def observation():
    pin = baseline()
    service, listing = data()
    service.update(
        generation="20",
        observedGeneration="20",
        etag=pin.api_etag,
        latestCreatedRevision=m.metadata.API + "/revisions/" + pin.closed_revision,
        latestReadyRevision=m.metadata.API + "/revisions/" + pin.closed_revision,
    )
    for field in ("traffic", "trafficStatuses"):
        service[field] = [
            {
                "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                "revision": pin.closed_revision,
                "percent": 100,
            },
            {
                "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                "revision": pin.closed_revision,
                "tag": "prior",
            },
        ]
    listing["revisions"].append(
        {
            "name": m.metadata.API + "/revisions/" + pin.closed_revision,
            "uid": pin.closed_revision_uid,
            "service": m.metadata.SERVICE_ID,
            "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}],
        }
    )
    return {
        "service": service,
        "revisions": listing["revisions"],
        "elapsed_ms": 40,
        "resource_gets": 3,
        "physical_resource_gets": 3,
        "resource_write_sends": 0,
        "maintenance_admission_proved": False,
    }


def test_actual_retired_triple_and_exact_stock_closed_current_only_can_be_excluded():
    assert len(m.retirement_fingerprint(observation(), baseline())) == 64
    assert ACTUAL_RETIRED_CONDITIONS


@pytest.mark.parametrize(
    "mutation",
    [
        "uid",
        "generation",
        "etag",
        "created",
        "ready",
        "open_route",
        "old_tag",
        "latest_type",
        "missing_closed",
        "unretired",
        "physical_count",
        "logical_count",
        "pretend_approval",
    ],
)
def test_current_open_unsettled_alias_or_incomplete_census_cannot_admit(mutation):
    result = observation()
    row = result["service"]
    if mutation == "uid":
        row["uid"] = str(UUID(int=50))
    if mutation == "generation":
        row["generation"] = row["observedGeneration"] = "21"
    if mutation == "etag":
        row["etag"] = "changed"
    if mutation == "created":
        row["latestCreatedRevision"] += "-candidate"
    if mutation == "ready":
        row["latestReadyRevision"] += "-candidate"
    if mutation == "open_route":
        row["traffic"][0]["revision"] = "old"
    if mutation == "old_tag":
        row["trafficStatuses"][1]["revision"] = "old"
    if mutation == "latest_type":
        row["traffic"][0]["type"] = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    if mutation == "missing_closed":
        result["revisions"].pop()
    if mutation == "unretired":
        result["revisions"][0]["conditions"][0]["revisionReason"] = "RETIRING"
    if mutation == "physical_count":
        result["physical_resource_gets"] = 2
    if mutation == "logical_count":
        result["resource_gets"] = 2
    if mutation == "pretend_approval":
        result["maintenance_admission_proved"] = True
    with pytest.raises((m.MaintenanceDenied, m.metadata.MetadataUnavailable)):
        m.retirement_fingerprint(result, baseline())


def resource():
    execution = RegistryPin(
        database="projects/hirewiz-test/databases/execution",
        database_uid=UUID(int=1),
        authority_id=UUID(int=2),
        incarnation=1,
        epoch_id=UUID(int=3),
    )
    protected = RegistryPin(
        database="projects/hirewiz-test/databases/protected",
        database_uid=UUID(int=4),
        authority_id=UUID(int=5),
        incarnation=1,
        epoch_id=UUID(int=6),
    )
    pin = PartitionedPin(
        authority=PublicationPin(
            registry=protected, target=execution, journal_bucket="synthetic-only-bucket"
        ),
        lanes=2,
        directory_shards=2,
        segment_size=4,
    )
    return PartitionedResource(
        pin=pin,
        bucket="synthetic-only-bucket",
        generation="1",
        sha256=m.fingerprint(pin.model_dump(mode="json")),
    )


def empty_publication():
    pin = resource().pin
    from app.domains.recovery.gcp_partitioned_contracts import (
        DirectoryHead,
        LaneHead,
        PartitionedRoot,
    )

    return {
        ("v3_root", "current"): PartitionedRoot(pin=pin, phase="OPEN").model_dump(mode="json"),
        **{
            ("v3_lanes", str(n)): LaneHead(lane=n, segment_count=0, segment_id=None).model_dump(
                mode="json"
            )
            for n in range(2)
        },
        **{
            ("v3_directory", str(n)): DirectoryHead(shard=n, count=0, digest="0" * 64).model_dump(
                mode="json"
            )
            for n in range(2)
        },
    }


def test_readonly_genesis_full_membership_without_forging_closed_capability():
    m.validate_publication(empty_publication(), resource())


@pytest.mark.parametrize(
    "mutation", ["orphan", "missing_lane", "rollback_head", "closed_root", "unknown", "record"]
)
def test_full_native_membership_preserves_original_orphan_and_chain_refusals(mutation):
    values = empty_publication()
    if mutation == "orphan":
        values["v3_subject_tombstones", str(UUID(int=7))] = {}
    if mutation == "missing_lane":
        del values["v3_lanes", "1"]
    if mutation == "rollback_head":
        values["v3_directory", "0"]["count"] = 1
    if mutation == "closed_root":
        values["v3_root", "current"].update(phase="CLOSED", close_id=str(UUID(int=8)))
    if mutation == "unknown":
        values["unknown", "row"] = {}
    if mutation == "record":
        values["v3_records", str(UUID(int=9))] = {"intent": {}}
    with pytest.raises((m.MaintenanceDenied, GuardUnavailable, ValidationError)):
        m.validate_publication(values, resource())


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return iter(self.rows)

    def __iter__(self):
        return iter(self.rows)


class Sql:
    def __init__(self, rows=(), absence=()):
        self.rows, self.absence, self.calls = list(rows), list(absence), []

    def exec_driver_sql(self, command):
        assert command in {
            "SET LOCAL statement_timeout='2000ms'",
            "SET LOCAL statement_timeout='10s'",
        }

    def execute(self, query, params=None):
        self.calls.append((str(query), params))
        assert str(query) in {m.ACCOUNT_SQL, m.ABSENCE_SQL}
        return Result(self.rows if str(query) == m.ACCOUNT_SQL else self.absence)


def test_sql_projection_uses_two_constant_bounded_reads_only_and_no_raw_credentials():
    db = Sql(absence=[(101, *([0] * 16))])
    assert m.sql_projection(db, (101,)) == ([], {101: dict.fromkeys(m.SQL_ENTITIES, 0)})
    assert len(db.calls) == 2 and db.calls[1][1] == {"owners": [101]}
    assert "LIMIT 1001" in m.ACCOUNT_SQL and "pg_catalog.sha256" in m.ACCOUNT_SQL
    assert "u.password_hash AS" not in m.ACCOUNT_SQL
    assert not any(
        word in "\n".join(q for q, _ in db.calls).upper().split()
        for word in ("INSERT", "UPDATE", "DELETE", "GRANT", "COMMIT")
    )


def test_sql_1001_sentinel_refuses_before_treating_prefix_as_ready():
    db = Sql(rows=[{}] * 1001)
    with pytest.raises(m.MaintenanceDenied, match="account_overflow"):
        m.sql_projection(db, ())
    assert len(db.calls) == 1


@pytest.mark.parametrize(
    "absence",
    [[], [(101, *([False] * 16))], [(101, *([2] * 16))], [(101, *([0] * 16)), (101, *([0] * 16))]],
)
def test_sql_absence_missing_bool_unknown_or_duplicate_cannot_be_zero(absence):
    with pytest.raises(m.MaintenanceDenied):
        m.sql_projection(Sql(absence=absence), (101,))


def prepared_guard(reader):
    # Explicit fake preflight for this unit scheduling control, not native authority.
    value = object.__new__(m.NativeMaintenanceDdlGuard)
    value.started, value.closed, value.prepared = False, False, True
    value.owners, value.baseline = (), baseline()
    value.sql_sha256 = m.fingerprint({"rows": [], "absence": {}})
    value.retired_sha256 = m.retirement_fingerprint(observation(), value.baseline)
    value.final_reader = reader
    return value


def test_final_guard_real_sdk_three_gets_then_ddl_and_no_replay():
    expected = observation()
    stock = Stock([expected["service"], {"revisions": expected["revisions"]}, expected["service"]])
    guard = prepared_guard(build(stock))
    ddl = []
    guard.verify_pre_ddl(Sql())
    ddl.append("unit-only-ddl-after-success")
    assert len(stock.calls) == 3 and guard.started and guard.closed and len(ddl) == 1
    with pytest.raises(m.MaintenanceDenied):
        guard.verify_pre_ddl(Sql())
    assert len(stock.calls) == 3


def test_final_sql_drift_fails_before_metadata_and_zero_ddl():
    reader = build(Stock())
    guard = prepared_guard(reader)
    ddl = []
    try:
        with pytest.raises(m.MaintenanceDenied, match="sql_lifecycle_changed"):
            guard.verify_pre_ddl(Sql(rows=[{"different": True}]))
            ddl.append("must-not-run")
        assert reader.request_count == 0 and guard.closed and ddl == []
    finally:
        guard.close()


def test_final_native_drift_refuses_zero_ddl_and_consumes_original_reader():
    expected = observation()
    changed = copy.deepcopy(expected["service"])
    changed["etag"] = "changed"
    reader = build(Stock([changed, {"revisions": expected["revisions"]}, changed]))
    guard = prepared_guard(reader)
    ddl = []
    with pytest.raises(m.MaintenanceDenied):
        guard.verify_pre_ddl(Sql())
        ddl.append("must-not-run")
    assert reader.request_count == 3 and guard.started and guard.closed and ddl == []


def test_fixed_guard_rejects_caller_namespace_before_any_native_or_sql_construction():
    with pytest.raises(m.MaintenanceDenied, match="fixed_controller_required"):
        m.NativeMaintenanceDdlGuard(object(), observer_secret="not-an-authority")


def test_private_sql_patch_last_external_seam_precedes_unchanged_alembic_upgrade():
    source = (ROOT / "backend/scripts/evidence_writer_schema26_step.py").read_text()
    tree = ast.parse(source)
    upgrade = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade_transaction"
    )
    calls = [n for n in ast.walk(upgrade) if isinstance(n, ast.Call)]
    guard = next(n for n in calls if ast.unparse(n.func) == "maintenance_guard.verify_pre_ddl")
    ddl = next(n for n in calls if ast.unparse(n.func) == "command.upgrade")
    assert guard.lineno + 1 == ddl.lineno
    assert "type(maintenance_guard) is NativeMaintenanceDdlGuard" in source
    assert "maintenance_guard" in [n.arg for n in upgrade.args.kwonlyargs]


def test_exact_absence_scope_matches_original_erasure_audit_models():
    from app import models

    assert tuple(getattr(models, name).__tablename__ for name in m.SQL_ENTITIES) == m.OWNER_TABLES


def complete_case(scenario):
    """Synthetic typed chains, not native enrollment/ACK or an OPEN capability."""
    import hashlib

    import test_schema26_lifecycle_join as fixtures

    from app.domains.recovery.gcp_partitioned_contracts import (
        DirectoryHead,
        LaneHead,
        PartitionedRecord,
        PartitionedRoot,
        SegmentRegistration,
        SegmentState,
    )

    c = fixtures.fixture()
    if scenario == "subject_denial":
        fixtures.denial(c, "subject")
    if scenario in {"reset", "two_rotations"}:
        fixtures.reset(c)
    if scenario == "two_rotations":
        fixtures.add_session(c)
        fixtures.reset(c)
    native = RegistryPin(
        database="projects/hirewiz-local-authority/databases/protected",
        database_uid=UUID(int=101),
        authority_id=UUID(int=102),
        incarnation=1,
        epoch_id=UUID(int=103),
    )
    pin = PartitionedPin(
        authority=PublicationPin(
            registry=native, target=c["pin"], journal_bucket="synthetic-candidate-journal"
        ),
        lanes=2,
        directory_shards=2,
        segment_size=128,
    )
    bound = PartitionedResource(
        pin=pin,
        bucket="synthetic-candidate-journal",
        generation="1",
        sha256=m.fingerprint(pin.model_dump(mode="json")),
    )
    values = c["values"]
    values["v3_root", "current"] = PartitionedRoot(pin=pin, phase="OPEN").model_dump(mode="json")
    for lane in range(2):
        records = [
            r
            for r in c["records"]
            if int(hashlib.sha256(str(r.reference.operation_id).encode()).hexdigest(), 16) % 2
            == lane
        ]
        segment_id = m.fingerprint({"synthetic-lane": lane})
        previous = "0" * 64
        for ordinal, original in enumerate(records, 1):
            parts = dict(
                reference=original.reference.model_dump(mode="json"),
                segment_id=segment_id,
                ordinal=ordinal,
                previous=previous,
            )
            full = PartitionedRecord(
                **parts, chain_digest=m.fingerprint(parts), intent=original.intent
            )
            operation = str(full.reference.operation_id)
            values["v3_records", operation] = full.model_dump(mode="json")
            values["v3_record_slots", f"{segment_id}:{ordinal}"] = {
                "operation_id": operation,
                "record_sha256": m.fingerprint(full.model_dump(mode="json")),
            }
            previous = full.chain_digest
        if records:
            parts = dict(
                segment_id=segment_id,
                lane=lane,
                lane_ordinal=1,
                directory_shard=lane,
                directory_ordinal=1,
                previous="0" * 64,
            )
            registration = SegmentRegistration(**parts, digest=m.fingerprint(parts))
            values["v3_segments", segment_id] = SegmentState(
                registration=registration, phase="OPEN", count=len(records), digest=previous
            ).model_dump(mode="json")
            values["v3_registration_slots", f"{lane}:1"] = registration.model_dump(mode="json")
            directory_digest = registration.digest
        else:
            directory_digest = "0" * 64
        values["v3_lanes", str(lane)] = LaneHead(
            lane=lane, segment_count=int(bool(records)), segment_id=segment_id if records else None
        ).model_dump(mode="json")
        values["v3_directory", str(lane)] = DirectoryHead(
            shard=lane, count=int(bool(records)), digest=directory_digest
        ).model_dump(mode="json")
    return c, bound


@pytest.mark.parametrize("scenario", ["live", "subject_denial", "reset", "two_rotations"])
def test_full_publication_slots_original_scoped_validator_and_sql_join_together(scenario):
    import schema26_lifecycle_join as core

    c, bound = complete_case(scenario)
    m.validate_publication(c["values"], bound)
    owners = tuple(sorted(c["absent"]))
    counts = [(owner, *(c["absent"][owner][name] for name in m.SQL_ENTITIES)) for owner in owners]
    rows, absence = m.sql_projection(Sql(rows=c["sql"], absence=counts), owners)
    assert (
        len(
            core.join_lifecycle(c["values"], c["execution"], c["statuses"], rows, absence, c["pin"])
        )
        == 64
    )


@pytest.mark.parametrize(
    "fault", ["record_slot", "registration_slot", "directory_head", "lane_head", "record_chain"]
)
def test_real_nonempty_publication_cannot_omit_or_forge_base_provenance(fault):
    c, bound = complete_case("two_rotations")
    namespace = {
        "record_slot": "v3_record_slots",
        "registration_slot": "v3_registration_slots",
        "directory_head": "v3_directory",
        "lane_head": "v3_lanes",
        "record_chain": "v3_records",
    }[fault]
    key = next(key for key in c["values"] if key[0] == namespace)
    if fault in {"record_slot", "registration_slot"}:
        del c["values"][key]
    elif fault == "directory_head":
        c["values"][key]["digest"] = "f" * 64
    elif fault == "lane_head":
        c["values"][key]["segment_id"] = "f" * 64
    else:
        c["values"][key]["previous"] = "f" * 64
    with pytest.raises((m.MaintenanceDenied, GuardUnavailable, ValidationError)):
        m.validate_publication(c["values"], bound)


def test_public_guard_redacts_unexpected_preflight_failure_and_closes(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic private source and credential material")

    monkeypatch.setattr(m.NativeMaintenanceDdlGuard, "_prepare", fail)
    with pytest.raises(m.MaintenanceDenied, match="preflight_unavailable") as error:
        m.NativeMaintenanceDdlGuard(object(), observer_secret="synthetic-only")
    assert "private" not in str(error.value) and error.value.__cause__ is None


def test_owned_native_client_cleanup_attempts_all_and_redacts_errors():
    seen = []

    class Client:
        def __init__(self, identity):
            self.identity = identity

        def close(self):
            seen.append(self.identity)
            if self.identity == 1:
                raise RuntimeError("synthetic private provider body")

    with pytest.raises(m.MaintenanceDenied, match="native_client_close_unavailable") as error:
        m._close_clients((Client(1), Client(2)))
    assert seen == [1, 2] and "private" not in str(error.value)
