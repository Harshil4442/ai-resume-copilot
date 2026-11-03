"""Pure finite controls; no native metadata, SQL, DDL or credentials exercised."""

import ast
import copy
import importlib
import json
import signal
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import pytest
import requests
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


def _owned_metadata_sdk():
    """Portable message port, not installed-SDK or native acceptance evidence."""
    class Client:
        def __init__(self, *, url, get_credentials, http, log_request, log_response):
            assert url == "https://run.googleapis.com/" and not get_credentials
            assert not log_request and not log_response
            self.http = http
            self.num_retries = -1
            self.projects_locations_services = SimpleNamespace(Get=self.get)
            self.projects_locations_services_revisions = SimpleNamespace(List=self.list)

        def call(self, path, parameters, global_params):
            assert self.num_retries == 0
            query = {"alt": "json", "prettyPrint": "0", "fields": global_params.fields,
                     **parameters}
            info, raw = self.http.request("https://run.googleapis.com/v2/" + path + "?"
                + urlencode(query), method="GET", body=None, headers={})
            if info.status != 200:
                raise ValueError("owned SDK port non-success status")
            return json.loads(raw)

        def get(self, request, *, global_params):
            return self.call(request.name, {}, global_params)

        def list(self, request, *, global_params):
            return self.call(request.parent + "/revisions", {
                "pageSize": str(request.pageSize), "showDeleted": str(request.showDeleted)}, global_params)

    return (SimpleNamespace(MessageToDict=lambda reply: reply), SimpleNamespace(RunV2=Client),
        SimpleNamespace(StandardQueryParameters=SimpleNamespace,
            RunProjectsLocationsServicesGetRequest=SimpleNamespace,
            RunProjectsLocationsServicesRevisionsListRequest=SimpleNamespace))


@pytest.fixture
def metadata_wire(monkeypatch):
    """Real requests send/cleanup with owned HTTP responses and no credentials.

    The optional SDK tuple is used by a separate local installed-SDK proof;
    portable CI controls never claim that this small message port is the SDK.
    """
    def build_wire(*, sdk=None, delays=(0.1, 2.5, 0.1), fault=None):
        metadata = m.metadata
        state = SimpleNamespace(clock=100.0, sends=[], responses=[], response_closes=[], closed=False)
        expected = observation()
        replies = [copy.deepcopy(expected["service"]),
                   {"revisions": copy.deepcopy(expected["revisions"])},
                   copy.deepcopy(expected["service"])]
        if fault == "service_drift":
            replies[2]["etag"] = "changed"
        elif fault == "next_page":
            replies[1]["nextPageToken"] = "owned-continuation"
        elif fault == "257_revisions":
            row = replies[1]["revisions"][0]
            replies[1]["revisions"] = [{**copy.deepcopy(row),
                "name": metadata.API + "/revisions/old-" + str(index),
                "uid": str(UUID(int=index + 100))} for index in range(257)]
        elif fault == "not_retired":
            replies[1]["revisions"][0]["conditions"][0].pop("revisionReason")
        elif fault == "foreign_uid":
            replies[1]["revisions"][0]["uid"] = "not-a-native-uid"

        class Info(dict):
            @property
            def status(self):
                return int(self["status"])

        class Adapter(requests.adapters.HTTPAdapter):
            def send(self, request, **kwargs):
                index = len(state.sends)
                assert index < 3 and request.method == "GET"
                assert urlsplit(request.url).hostname == "run.googleapis.com"
                # Session.send consumes allow_redirects before the physical
                # adapter; a redirect response below must still stop at one send.
                assert kwargs["stream"] is True
                assert self.max_retries.total == 0 and not kwargs.get("proxies")
                state.sends.append((request.url, kwargs["timeout"]))
                delay = delays[index]
                state.clock += min(delay, kwargs["timeout"])
                if delay > kwargs["timeout"]:
                    raise requests.ReadTimeout("owned response-header timeout")
                if fault == "wall_alarm" and index == 1:
                    signal.raise_signal(signal.SIGALRM)
                class Response(requests.Response):
                    @property
                    def content(self):
                        if fault == "body_deadline" and index == 1:
                            state.clock = 106.0
                        return super().content

                response = Response()
                state.responses.append(index)
                response.request, response.url = request, request.url
                response.status_code = (307 if fault == "redirect" else
                    401 if fault == "unauthorized" else 503 if fault == "server_error" else 200) if index == 1 else 200
                response.headers["content-type"] = "application/json"
                response._content = json.dumps(replies[index]).encode()
                response._content_consumed = True
                close = response.close

                def close_owned():
                    state.response_closes.append(index)
                    close()
                response.close = close_owned
                return response

        class Stock:
            def __init__(self):
                self.session = requests.Session()
                self.session.mount("https://", Adapter(max_retries=0))
                # Native manager stores its request wrapper as an owned stable
                # callable; preserve that identity for the adapter's guard.
                self.session.request = self.session.request
                close = self.session.close

                def close_owned():
                    state.closed = True
                    close()
                self.session.close = close_owned

            def request(self, uri, *, method, body, headers, **kwargs):
                assert kwargs.get("redirections") == 0
                response = self.session.request(method, uri, data=body, headers=headers)
                return Info(status=str(response.status_code), **dict(response.headers)), response.content

        stock = Stock()
        monkeypatch.setenv("CLOUDSDK_CORE_DISABLE_FILE_LOGGING", "true")
        monkeypatch.setenv("CLOUDSDK_CORE_LOG_HTTP", "false")
        monkeypatch.setattr(metadata, "_normal_http", lambda: stock)
        monkeypatch.setattr(metadata, "_sdk", lambda: sdk or _owned_metadata_sdk())
        monkeypatch.setattr(metadata.time, "monotonic", lambda: state.clock)
        reader = metadata.ReadOnlyMaintenanceMetadata()
        return reader, state

    return build_wire


def test_metadata_list_borrows_only_remaining_six_second_window(metadata_wire):
    reader, state = metadata_wire()
    previous = signal.getsignal(signal.SIGALRM)
    observed = reader.observe()
    assert len(m.retirement_fingerprint(observed, baseline())) == 64
    assert m.metadata.TOTAL_SECONDS == 6.0 and m.metadata.SOCKET_SECONDS == 2.0
    assert observed["elapsed_ms"] >= 2600 and observed["elapsed_ms"] < 6000
    assert len(state.sends) == reader.request_count == reader.resource_get_sends == 3
    assert state.sends[0][1] <= 2 and 5.8 < state.sends[1][1] < 6
    assert state.sends[2][1] <= 2
    assert state.response_closes == [0, 1, 2] and state.closed
    assert reader.resource_write_sends == 0 and reader.closed
    assert signal.getsignal(signal.SIGALRM) == previous
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    with pytest.raises(m.metadata.MetadataUnavailable, match="one_observation_only"):
        reader.observe()
    assert len(state.sends) == 3
    # A direct fourth physical resource GET is also denied before the adapter.
    with pytest.raises(m.metadata.MetadataUnavailable, match="resource_resend"):
        reader.stock.session.get(state.sends[0][0])
    assert len(state.sends) == 3


@pytest.mark.parametrize("fault,delays,expected_sends", [
    (None, (0.1, 6.0, 0.1), 2),
    (None, (0.1, 5.85, 0.1), 3),
    ("wall_alarm", (0.1, 0.1, 0.1), 2),
    ("body_deadline", (0.1, 0.1, 0.1), 2),
    ("redirect", (0.1, 0.1, 0.1), 2),
    ("unauthorized", (0.1, 0.1, 0.1), 2),
    ("server_error", (0.1, 0.1, 0.1), 2),
    ("next_page", (0.1, 0.1, 0.1), 2),
    ("257_revisions", (0.1, 0.1, 0.1), 2),
    ("foreign_uid", (0.1, 0.1, 0.1), 2),
    ("service_drift", (0.1, 0.1, 0.1), 3),
])
def test_metadata_residual_list_budget_preserves_refusal_cleanup_and_no_replay(
    metadata_wire, fault, delays, expected_sends,
):
    reader, state = metadata_wire(fault=fault, delays=delays)
    previous = signal.getsignal(signal.SIGALRM)
    with pytest.raises(m.metadata.MetadataUnavailable):
        reader.observe()
    assert len(state.sends) == expected_sends
    assert reader.resource_get_sends == expected_sends and reader.resource_write_sends == 0
    assert reader.closed and state.closed
    assert state.response_closes == state.responses
    assert signal.getsignal(signal.SIGALRM) == previous
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    with pytest.raises(m.metadata.MetadataUnavailable, match="one_observation_only"):
        reader.observe()
    assert len(state.sends) == expected_sends


def test_metadata_slow_list_still_requires_every_old_revision_retired(metadata_wire):
    reader, state = metadata_wire(fault="not_retired")
    result = reader.observe()
    with pytest.raises(m.metadata.MetadataUnavailable, match="not_retired"):
        m.retirement_fingerprint(result, baseline())
    assert len(state.sends) == 3 and state.response_closes == [0, 1, 2] and state.closed


@pytest.mark.parametrize("mutation", ["fields", "page_size", "page_token", "parent", "write"])
def test_metadata_census_route_projection_and_page_envelope_remain_exact(metadata_wire, mutation):
    reader, state = metadata_wire()
    query = {"prettyPrint": "0", "fields": m.metadata.REVISION_FIELDS,
             "pageSize": "257", "showDeleted": "False"}
    path = m.metadata.API + "/revisions"
    method = "GET"
    if mutation == "fields":
        query["fields"] = "revisions(name)"
    elif mutation == "page_size":
        query["pageSize"] = "256"
    elif mutation == "page_token":
        query["pageToken"] = "owned-continuation"
    elif mutation == "parent":
        path = path.replace("services/ai-resume-parser", "services/foreign-service")
    else:
        method = "POST"
    try:
        with pytest.raises(m.metadata.MetadataUnavailable):
            reader.request("https://run.googleapis.com/v2/" + path + "?" + urlencode(query), method=method)
        assert not state.sends and reader.resource_get_sends == reader.resource_write_sends == 0
    finally:
        reader.close()
    assert state.closed


@pytest.fixture
def _census_budget_context(monkeypatch):
    """Real installed GAPIC and original intercepted route over an owned stream."""
    import grpc
    from google.api_core.exceptions import DeadlineExceeded
    from google.cloud.firestore_admin_v1.services.firestore_admin import FirestoreAdminClient
    from google.cloud.firestore_admin_v1.services.firestore_admin.transports.grpc import (
        FirestoreAdminGrpcTransport,
    )
    from google.cloud.firestore_v1.services.firestore import FirestoreClient
    from google.cloud.firestore_v1.services.firestore.transports.grpc import FirestoreGrpcTransport

    from app.domains.recovery.gcp_contracts import WitnessPin
    from app.domains.recovery.gcp_rpc import FirestoreRpc

    now = [0.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: now[0])
    bound = resource()
    cfg = SimpleNamespace(publication_resource=bound, execution_witness_pin=WitnessPin(
        registry=bound.pin.authority.target, bucket=bound.bucket, path="owned/witness",
        generation="1", sha256="a" * 64))

    class Stream:
        def __init__(self, steps, timeout, fail_close):
            self.steps, self.deadline = iter(steps), now[0] + timeout
            self.cancelled, self.native_expired, self.fail_close = 0, False, fail_close

        def __iter__(self):
            return self

        def __next__(self):
            value, delay = next(self.steps)
            if now[0] + delay > self.deadline:
                now[0] = self.deadline
                self.native_expired = True
                raise DeadlineExceeded("owned native RPC deadline")
            now[0] += delay
            if value == "alarm":
                signal.raise_signal(signal.SIGALRM)
            if isinstance(value, BaseException):
                raise value
            if value is None:
                raise StopIteration
            return value

        def cancel(self):
            self.cancelled += 1
            if self.fail_close:
                raise RuntimeError("owned cancel failure")
            return True

    class Channel(grpc.Channel):
        def __init__(self):
            self.pages, self.streams, self.queries, self.other = [], [], [], []
            self.closed, self.fail_close = 0, False

        def close(self):
            self.closed += 1

        def subscribe(self, *_a, **_k):
            pass

        def unsubscribe(self, *_a, **_k):
            pass

        def unary_stream(self, method, *_a, **_k):
            def call(request, **kwargs):
                assert method.endswith("/RunQuery")
                self.queries.append((request, kwargs["timeout"]))
                stream = Stream(self.pages.pop(0) if self.pages else [], kwargs["timeout"], self.fail_close)
                self.streams.append(stream)
                return stream
            return call

        def unary_unary(self, method, *_a, **_k):
            owner = self

            class Call:
                def __call__(self, request, **kwargs):
                    owner.other.append((method, kwargs["timeout"]))
                    raise RuntimeError("owned unary response unavailable")

                with_call = __call__

            return Call()

        stream_unary = unary_unary
        stream_stream = unary_unary

    raw, counters = Channel(), {"denied_mutations": 0}
    client = FirestoreClient(transport=FirestoreGrpcTransport(channel=m._read_channel(raw, cfg, counters)))
    admin = FirestoreAdminClient(transport=FirestoreAdminGrpcTransport(channel=m._read_channel(raw, cfg, counters)))
    rpc = FirestoreRpc(client, admin, database_resource=bound.pin.authority.registry.database)
    original_handler = signal.getsignal(signal.SIGALRM)
    case = SimpleNamespace(raw=raw, counters=counters, now=now, rpc=rpc, client=client,
        admin=admin, original_handler=original_handler)
    try:
        yield case
    finally:
        assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
        assert signal.getsignal(signal.SIGALRM) == original_handler
        client.transport.close()
        admin.transport.close()


def _census_budget_documents(context, count):
    from google.cloud.firestore_v1.types import RunQueryResponse

    from app.domains.recovery.contracts import canonical, fingerprint

    values = [{"namespace": "owned_budget", "key": str(i), "value": {"n": i}} for i in range(count)]
    return sorted((RunQueryResponse(document={
        "name": context.rpc.database_resource + "/documents/authority_records/" + fingerprint(
            {"namespace": value["namespace"], "key": value["key"]}),
        "fields": {"body": {"bytes_value": canonical(value).encode()}},
        "update_time": "2026-10-31T00:00:00Z"}) for value in values), key=lambda response: response.document.name)


def test_census_budget_actual_gapic_allows_slow_page_without_changing_regular_rpc(_census_budget_context):
    c = _census_budget_context
    documents = _census_budget_documents(c, 2)
    c.raw.pages = [[(documents[0], 3.0), (documents[1], 0.0)]]
    rows = m._protected_census(c.rpc)
    assert len(rows) == 2 and c.now[0] == 3.0
    assert [timeout for _, timeout in c.raw.queries] == [10.0]
    assert c.raw.queries[0][0].structured_query.limit == 32
    assert c.counters == {"denied_mutations": 0, "RunQuery": 1}
    assert c.rpc.client is c.client and c.rpc.admin is c.admin and c.rpc.rpc_timeout == 2.0
    with pytest.raises(GuardUnavailable):
        c.rpc.begin()
    assert c.raw.other[-1][1] == 2.0 and c.raw.closed == 0
    assert c.raw.streams[0].cancelled == 1


@pytest.mark.parametrize("late", [False, True])
def test_census_budget_later_page_is_clamped_to_same_whole_deadline(_census_budget_context, late):
    c = _census_budget_context
    documents = _census_budget_documents(c, 97)
    c.raw.pages = [[(row, 9.0 if index == 0 else 0.0) for index, row in enumerate(documents[start:start + 32])]
        for start in (0, 32, 64)]
    c.raw.pages.append([(documents[96], 4.0 if late else 2.0)])
    if late:
        with pytest.raises(GuardUnavailable):
            m._protected_census(c.rpc)
        assert c.now[0] == 30.0 and c.raw.streams[-1].native_expired
    else:
        assert len(m._protected_census(c.rpc)) == 97 and c.now[0] == 29.0
    assert [timeout for _, timeout in c.raw.queries] == [10.0, 10.0, 10.0, 3.0]
    assert len(c.raw.queries) == 4 and c.raw.closed == 0


@pytest.mark.parametrize("fault", ["partial", "malformed", "body", "alarm", "cancel", "late_terminal"])
def test_census_budget_partial_validation_alarm_or_cleanup_never_returns_inventory(_census_budget_context, fault):
    from google.cloud.firestore_v1.types import RunQueryResponse

    c = _census_budget_context
    document = _census_budget_documents(c, 1)[0]
    if fault == "malformed":
        document.document.fields["extra"] = {"string_value": "no mask"}
    elif fault == "body":
        document.document.fields["body"] = {"bytes_value": b"x" * (128 * 1024 + 1)}
    c.raw.pages = [[(document, 0.0)]]
    if fault == "partial":
        c.raw.pages[0].append((RuntimeError("owned partial stream"), 0.0))
    elif fault == "alarm":
        c.raw.pages[0].append(("alarm", 0.0))
    elif fault == "cancel":
        c.raw.fail_close = True
    elif fault == "late_terminal":
        c.raw.pages[0].append((RunQueryResponse(), 11.0))
    with pytest.raises(GuardUnavailable):
        m._protected_census(c.rpc)
    assert len(c.raw.queries) == 1 and c.raw.streams[0].cancelled == 1
    assert c.raw.closed == 0 and signal.getsignal(signal.SIGALRM) == c.original_handler
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_census_budget_prefetch_failure_has_no_public_handle_and_remains_bounded_unknown(_census_budget_context):
    c = _census_budget_context
    c.raw.pages = [[(RuntimeError("owned pre-return failure"), 0.0)]]
    with pytest.raises(GuardUnavailable):
        m._protected_census(c.rpc)
    assert len(c.raw.queries) == 1 and c.raw.queries[0][1] == 10.0
    # Installed grpc_helpers prefetches before returning the public iterator;
    # no explicit-cancel claim is made for an unavailable handle. The original
    # graph owner still closes its clients on failure, before any SQL collection.
    assert c.raw.streams[0].cancelled == 0 and c.raw.closed == 0
    m._close_clients((c.admin, c.client))
    assert c.raw.closed == 2


@pytest.mark.parametrize("fault", ["foreign_timer", "thread", "handler_none", "replaced_method"])
def test_census_budget_refuses_foreign_owner_or_noncanonical_client_before_query(_census_budget_context, monkeypatch, fault):
    c = _census_budget_context
    if fault == "foreign_timer":
        monkeypatch.setattr(signal, "getitimer", lambda _kind: (1.0, 0.0))
    elif fault == "thread":
        monkeypatch.setattr(m.threading, "current_thread", lambda: object())
    elif fault == "handler_none":
        monkeypatch.setattr(signal, "getsignal", lambda _kind: None)
    else:
        c.client.run_query = lambda **_kwargs: pytest.fail("replacement must never run")
    with pytest.raises(m.MaintenanceDenied):
        m._protected_census(c.rpc)
    assert not c.raw.queries and c.raw.closed == 0
    monkeypatch.undo()


def test_census_budget_integration_keeps_full_original_scanner_and_no_sql_before_failure(monkeypatch):
    import inspect

    source = inspect.getsource(m._protected_census)
    assert "page_size=32, max_documents=MAX_DOCUMENTS, max_pages=256, deadline_seconds=30.0" in source
    assert m.MAX_DOCUMENTS == 4096
    assert "rows = _protected_census(publication.registry.rpc)" in inspect.getsource(m._snapshot_reads)
    assert "statement_timeout='10s'" in inspect.getsource(m.NativeMaintenanceDdlGuard.verify_pre_ddl)
    assert "idle_in_transaction_session_timeout" not in source
