"""PRIVATE V3 maintenance proposal. Reads only; never grants DDL or commits.

The existing qualified native migration owner still owns source/CI/image,
physical SQL identity, the one-use migration intent and commit reconciliation.
This finite guard adds closed stock lineage, actual revision retirement and
typed retained lifecycle/SQL joins. It does not close native authority roots.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import v3_readonly_adapter as metadata
from sqlalchemy import text

from app.domains.recovery.contracts import canonical, fingerprint
from app.domains.recovery.gcp_partitioned_contracts import (
    DirectoryHead,
    LaneHead,
    PartitionedRecord,
    PartitionedRoot,
    ScopedDenial,
    ScopedJournalIntent,
    ScopedPasswordReset,
    ScopedSubject,
    SegmentState,
)
from app.domains.recovery.gcp_partitioned_publication import partitioned_intent
from app.domains.recovery.gcp_scoped_census import SCOPED_NAMESPACES, validate_scoped_rows

MAX_DOCUMENTS = 4096
MAX_ACCOUNTS = 1000
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
OWNER_TABLES = (
    "users",
    "candidate_password_accounts",
    "user_profiles",
    "resumes",
    "job_matches",
    "opportunities",
    "application_events",
    "resume_versions",
    "evidence_items",
    "reminders",
    "opportunity_contacts",
    "career_memory_entries",
    "analysis_runs",
    "usage_events",
    "model_call_events",
    "notification_outbox",
)
ACCOUNT_SQL = """SELECT c.user_id AS candidate_id,c.subject_uuid,c.account_binding_id,
    c.principal_sha256,c.auth_generation,c.credential_sha256,c.state,u.id AS user_id,
    CASE WHEN octet_length(u.password_hash) BETWEEN 1 AND 1024 THEN
      encode(pg_catalog.sha256(pg_catalog.convert_to(u.password_hash,'UTF8')),'hex')
    ELSE NULL END AS password_sha256
    FROM public.candidate_password_accounts c LEFT JOIN public.users u ON u.id=c.user_id
    ORDER BY c.user_id LIMIT 1001"""
ABSENCE_SQL = (
    "SELECT owner_id,"
    + ",".join(
        '(SELECT count(*) FROM (SELECT 1 FROM public."'
        + table
        + '" WHERE '
        + ("id" if table == "users" else "user_id")
        + "=owner_id LIMIT 1) AS bounded_"
        + str(i)
        + ")"
        for i, table in enumerate(OWNER_TABLES)
    )
    + " FROM unnest(CAST(:owners AS integer[])) AS owner_id ORDER BY owner_id"
)


class MaintenanceDenied(RuntimeError):
    probe_projection: dict | None = None
    native_subphase: str | None = None
    native_failure: list | None = None


def need(value, reason):
    if not value:
        raise MaintenanceDenied("schema26_maintenance_" + reason)


def retirement_fingerprint(observation, baseline):
    """All old revisions, including tags/zero-traffic revisions, must be retired.

    The full closed template/source/flags are established by the unchanged
    stock reader in capture_closed_api_baseline. Partial metadata is never
    substituted for that check or used to inspect/persist environment values.
    """
    from schema26_app_rolling_v3 import ClosedApiBaseline

    need(type(baseline) is ClosedApiBaseline, "stock_baseline_required")
    baseline.validate()
    need(
        type(observation) is dict
        and set(observation)
        == {
            "service",
            "revisions",
            "elapsed_ms",
            "resource_gets",
            "resource_write_sends",
            "physical_resource_gets",
            "maintenance_admission_proved",
        }
        and observation["resource_gets"] == observation["physical_resource_gets"] == 3
        and observation["resource_write_sends"] == 0
        and observation["maintenance_admission_proved"] is False,
        "metadata_observation_required",
    )
    service = metadata.project_service(observation["service"])
    rows = metadata.project_revisions({"revisions": observation["revisions"]})
    closed = metadata.API + "/revisions/" + baseline.closed_revision
    need(
        service["uid"] == baseline.api_service_uid
        and str(service.get("generation"))
        == str(service.get("observedGeneration"))
        == str(baseline.api_generation)
        and service["etag"] == baseline.api_etag
        and service.get("reconciling", False) is False
        and service["terminalCondition"] == {"type": "Ready", "state": "CONDITION_SUCCEEDED"}
        and service.get("latestCreatedRevision") == service.get("latestReadyRevision") == closed,
        "closed_service_changed",
    )
    for field in ("traffic", "trafficStatuses"):
        targets = service[field]
        need(
            all(
                row.get("revision") == baseline.closed_revision
                and row.get("type") == "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION"
                and (
                    type(row.get("percent")) is int
                    and 0 <= row["percent"] <= 100
                    or "percent" not in row
                    and type(row.get("tag")) is str
                )
                for row in targets
            )
            and sum(row.get("percent", 0) for row in targets) == 100,
            "old_route_or_alias",
        )
    found = [row for row in rows if row["name"] == closed]
    need(
        len(found) == 1 and found[0]["uid"] == baseline.closed_revision_uid,
        "closed_revision_missing",
    )
    for row in rows:
        if row["name"] != closed:
            metadata.require_retired(row)
    return fingerprint({"service": service, "revisions": rows, "baseline": asdict(baseline)})


def validate_publication(values, resource):
    """Read-only counterpart of the original full census membership checks.

    No forged ClosedPartitionedRoot, CLOSE, seal or receipt is constructed.
    The original typed scoped validator still checks all scoped rows. This
    additionally verifies the existing complete directory/lane/slot chains.
    """
    need(type(values) is dict and len(values) <= MAX_DOCUMENTS, "authority_bound")
    allowed = {
        "v3_root",
        "v3_lanes",
        "v3_directory",
        "v3_registration_slots",
        "v3_segments",
        "v3_records",
        "v3_record_slots",
    } | SCOPED_NAMESPACES
    need(
        all(
            type(k) is tuple and len(k) == 2 and k[0] in allowed and type(v) is dict
            for k, v in values.items()
        ),
        "authority_shape",
    )
    config = resource.pin
    root = PartitionedRoot.model_validate(values.get(("v3_root", "current")))
    need(root.pin == config and root.phase == "OPEN", "protected_root_changed")
    directories = tuple(
        DirectoryHead.model_validate(values.get(("v3_directory", str(s))))
        for s in range(config.directory_shards)
    )
    lanes = tuple(
        LaneHead.model_validate(values.get(("v3_lanes", str(n)))) for n in range(config.lanes)
    )
    segments = tuple(
        SegmentState.model_validate(v) for (n, _), v in values.items() if n == "v3_segments"
    )
    records = tuple(
        PartitionedRecord.model_validate(v) for (n, _), v in values.items() if n == "v3_records"
    )
    registrations = {s.registration.segment_id: s.registration for s in segments}
    need(
        len(registrations)
        == len(segments)
        == sum(h.count for h in directories)
        == sum(lane.segment_count for lane in lanes)
        and len(records) == sum(s.count for s in segments),
        "authority_cardinality",
    )
    consumed = {("v3_root", "current")}
    for head in directories:
        consumed.add(("v3_directory", str(head.shard)))
        ordered = sorted(
            (r for r in registrations.values() if r.directory_shard == head.shard),
            key=lambda r: r.directory_ordinal,
        )
        previous = "0" * 64
        for ordinal, registration in enumerate(ordered, 1):
            identity = "v3_registration_slots", f"{head.shard}:{ordinal}"
            need(
                registration.directory_ordinal == ordinal
                and registration.previous == previous
                and values.get(identity) == registration.model_dump(mode="json"),
                "registration_chain",
            )
            consumed.add(identity)
            previous = registration.digest
        need(head.count == len(ordered) and head.digest == previous, "directory_head")
    for lane in lanes:
        consumed.add(("v3_lanes", str(lane.lane)))
        ordered = sorted(
            (r for r in registrations.values() if r.lane == lane.lane), key=lambda r: r.lane_ordinal
        )
        need(
            [r.lane_ordinal for r in ordered] == list(range(1, lane.segment_count + 1))
            and (ordered[-1].segment_id if ordered else None) == lane.segment_id,
            "lane_chain",
        )
    for segment in segments:
        registration = segment.registration
        consumed.add(("v3_segments", registration.segment_id))
        ordered = sorted(
            (r for r in records if r.segment_id == registration.segment_id), key=lambda r: r.ordinal
        )
        previous = "0" * 64
        for ordinal, full in enumerate(ordered, 1):
            reference, _ = partitioned_intent(canonical(full.intent).encode(), resource)
            operation = str(full.reference.operation_id)
            lane = int(hashlib.sha256(operation.encode()).hexdigest(), 16) % config.lanes
            slot = "v3_record_slots", f"{registration.segment_id}:{ordinal}"
            need(
                full.ordinal == ordinal
                and full.previous == previous
                and lane == registration.lane
                and full.reference == reference
                and values.get(("v3_records", operation)) == full.model_dump(mode="json")
                and values.get(slot)
                == {
                    "operation_id": operation,
                    "record_sha256": fingerprint(full.model_dump(mode="json")),
                },
                "record_chain",
            )
            consumed.update({("v3_records", operation), slot})
            previous = full.chain_digest
        need(
            segment.count == len(ordered)
            and segment.count <= config.segment_size
            and segment.digest == previous,
            "segment_head",
        )
    consumed.update(validate_scoped_rows(values))
    need(consumed == set(values), "unregistered_authority_rows")


def sql_projection(connection, owner_ids):
    """Constant bounded queries; hashes/counts only, never password/source bytes."""
    need(
        type(owner_ids) is tuple
        and len(owner_ids) <= MAX_ACCOUNTS
        and tuple(sorted(set(owner_ids))) == owner_ids
        and all(type(v) is int and 0 < v < 2**31 for v in owner_ids),
        "owner_bound",
    )
    rows = [dict(row) for row in connection.execute(text(ACCOUNT_SQL)).mappings()]
    need(len(rows) <= MAX_ACCOUNTS, "account_overflow")
    absence = {}
    if owner_ids:
        for row in connection.execute(text(ABSENCE_SQL), {"owners": list(owner_ids)}):
            need(
                len(row) == len(SQL_ENTITIES) + 1
                and row[0] not in absence
                and type(row[0]) is int
                and all(type(v) is int and v in (0, 1) for v in row[1:]),
                "absence_projection",
            )
            absence[row[0]] = dict(zip(SQL_ENTITIES, row[1:], strict=True))
        need(set(absence) == set(owner_ids), "absence_incomplete")
    return rows, absence


def _native_client_types():
    """Import application GAPICs before any normal SDK path injection; no IO."""
    from google.auth.credentials import Credentials
    from google.auth.transport.requests import AuthorizedSession
    from google.cloud.firestore_admin_v1.services.firestore_admin import FirestoreAdminClient
    from google.cloud.firestore_admin_v1.services.firestore_admin.transports.grpc import (
        FirestoreAdminGrpcTransport,
    )
    from google.cloud.firestore_v1.services.firestore import FirestoreClient
    from google.cloud.firestore_v1.services.firestore.transports.grpc import FirestoreGrpcTransport
    from google.cloud.storage import Client

    return (
        Credentials,
        AuthorizedSession,
        FirestoreAdminClient,
        FirestoreAdminGrpcTransport,
        FirestoreClient,
        FirestoreGrpcTransport,
        Client,
    )


def _close_clients(clients):
    failed = False
    for client in clients:
        try:
            client.close() if hasattr(client, "close") else client.transport.close()
        except Exception:
            failed = True
    need(not failed, "native_client_close_unavailable")


def _read_channel(channel, config, counters):
    """Actual gRPC interception before credential metadata/resource sends."""
    import grpc
    from google.protobuf.json_format import MessageToDict

    execution = config.execution_witness_pin.registry.database
    protected = config.publication_resource.pin.authority.registry.database
    databases = {execution, protected}

    class Gate(
        grpc.UnaryUnaryClientInterceptor,
        grpc.UnaryStreamClientInterceptor,
        grpc.StreamUnaryClientInterceptor,
        grpc.StreamStreamClientInterceptor,
    ):
        def check(self, details, request):
            method = details.method
            if method == "/google.firestore.admin.v1.FirestoreAdmin/GetDatabase":
                need(request.name in databases, "native_admin_route")
            elif method == "/google.firestore.v1.Firestore/BeginTransaction":
                need(request.database in databases, "native_transaction_route")
            elif method == "/google.firestore.v1.Firestore/BatchGetDocuments":
                prefix = request.database + "/documents/authority_records/"
                need(
                    request.database in databases
                    and 0 < len(request.documents) <= 64
                    and all(
                        name.startswith(prefix)
                        and re.fullmatch(r"[a-f0-9]{64}", name[len(prefix) :])
                        for name in request.documents
                    )
                    and 0 < len(request.transaction) <= 4096,
                    "native_batch_route",
                )
            elif method == "/google.firestore.v1.Firestore/Rollback":
                need(
                    request.database in databases and 0 < len(request.transaction) <= 4096,
                    "native_rollback_route",
                )
            elif method == "/google.firestore.v1.Firestore/RunQuery":
                from google.cloud.firestore_v1.types import RunQueryRequest

                # The installed GAPIC passes proto-plus here before gRPC's
                # serializer; direct raw-protobuf calls use the exact same type.
                need(
                    type(request) in {RunQueryRequest, RunQueryRequest.pb()},
                    "native_census_request_type",
                )
                protobuf = (
                    RunQueryRequest.pb(request) if type(request) is RunQueryRequest else request
                )
                value = MessageToDict(protobuf, preserving_proto_field_name=True)
                query = value.get("structured_query", {})
                need(
                    set(value) == {"parent", "structured_query"}
                    and request.parent == protected + "/documents"
                    and set(query) <= {"from_", "order_by", "limit", "start_at"}
                    and query.get("from_") == [{"collection_id": "authority_records"}]
                    and query.get("order_by")
                    == [{"field": {"field_path": "__name__"}, "direction": "ASCENDING"}]
                    and query.get("limit") == 32,
                    "native_census_route",
                )
                if "start_at" in query:
                    cursor = query["start_at"]
                    need(
                        set(cursor) <= {"values", "before"}
                        and cursor.get("before", False) is False
                        and type(cursor.get("values")) is list
                        and len(cursor["values"]) == 1
                        and set(cursor["values"][0]) == {"reference_value"}
                        and cursor["values"][0]["reference_value"].startswith(
                            protected + "/documents/authority_records/"
                        ),
                        "native_census_cursor",
                    )
            else:
                # Includes Commit, Write, Create/Update/DeleteDocument and every
                # admin mutation. No mutation can reach continuation/send.
                counters["denied_mutations"] += 1
                raise MaintenanceDenied("schema26_maintenance_native_rpc_refused")
            name = method.rsplit("/", 1)[-1]
            counters[name] = counters.get(name, 0) + 1

        def intercept_unary_unary(self, continuation, details, request):
            self.check(details, request)
            return continuation(details, request)

        def intercept_unary_stream(self, continuation, details, request):
            self.check(details, request)
            return continuation(details, request)

        def intercept_stream_unary(self, *_):
            counters["denied_mutations"] += 1
            raise MaintenanceDenied("schema26_maintenance_native_stream_refused")

        def intercept_stream_stream(self, *_):
            counters["denied_mutations"] += 1
            raise MaintenanceDenied("schema26_maintenance_native_stream_refused")

    return grpc.intercept_channel(channel, Gate())


def _read_storage(http, config, counters):
    from app.domains.recovery.gcp_closure_contracts import closure_path

    resource, witness = config.publication_resource, config.execution_witness_pin
    buckets = {resource.bucket, witness.bucket}
    objects = {
        (resource.bucket, resource.path),
        (resource.bucket, resource.close_path),
        (witness.bucket, witness.path),
        (witness.bucket, closure_path(witness.registry)),
    }
    send = http.send

    def bounded(request, **kwargs):
        target = urlsplit(request.url)
        need(
            target.scheme == "https"
            and target.username is None
            and target.password is None
            and target.port is None
            and not target.fragment,
            "native_storage_origin",
        )
        if request.method != "GET":
            counters["denied_mutations"] += 1
            raise MaintenanceDenied("schema26_maintenance_native_storage_mutation_refused")
        need(
            request.method == "GET"
            and request.body in (None, "", b"")
            and target.hostname == "storage.googleapis.com",
            "native_storage_read_only",
        )
        match = re.fullmatch(r"/(?:download/)?storage/v1/b/([^/]+)/o/(.+)", target.path)
        need(match is not None and unquote(match[1]) in buckets, "native_storage_scope")
        assert match is not None
        name = unquote(match[2])
        need(
            0 < len(name) <= 2048
            and not any(part in {"", ".", ".."} for part in name.split("/"))
            and (
                (unquote(match[1]), name) in objects
                or unquote(match[1]) == resource.bucket
                and re.fullmatch(
                    r"authority-intents/[a-f0-9]{64}/[a-f0-9]{64}/[a-f0-9-]{36}\.json", name
                )
            ),
            "native_storage_object",
        )
        query = parse_qs(target.query, strict_parsing=True)
        need(
            set(query) <= {"alt", "projection", "generation", "ifGenerationMatch", "prettyPrint"}
            and all(len(values) == 1 for values in query.values())
            and query.get("alt", ["json"])[0] in {"json", "media"}
            and query.get("projection", ["noAcl"]) == ["noAcl"]
            and query.get("prettyPrint", ["false"]) == ["false"]
            and all(
                re.fullmatch(r"[1-9][0-9]{0,18}", query[key][0])
                for key in ("generation", "ifGenerationMatch")
                if key in query
            ),
            "native_storage_query",
        )
        counters["storage_gets"] += 1
        kwargs["timeout"], kwargs["allow_redirects"] = 2.0, False
        return send(request, **kwargs)

    http.send = bounded
    for adapter in http.adapters.values():
        adapter.max_retries = adapter.max_retries.__class__(
            total=0, connect=0, read=0, redirect=0, status=0
        )


def _normal_clients(config):
    """Existing normal manager identity; no ADC, bootstrap, grant or KMS client."""
    (
        Credentials,
        AuthorizedSession,
        FirestoreAdminClient,
        FirestoreAdminGrpcTransport,
        FirestoreClient,
        FirestoreGrpcTransport,
        Client,
    ) = _native_client_types()
    # Application/GAPIC imports precede SDK third_party injection.
    need(
        not any(
            os.getenv(name)
            for name in (
                "GOOGLE_APPLICATION_CREDENTIALS",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
                "CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT",
                "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
                "CLOUDSDK_AUTH_ACCESS_TOKEN_FILE",
                "REQUESTS_CA_BUNDLE",
                "CURL_CA_BUNDLE",
                "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH",
                "GRPC_TRACE",
                "GRPC_VERBOSITY",
                "STORAGE_EMULATOR_HOST",
                "FIRESTORE_EMULATOR_HOST",
            )
        )
        and os.getenv("GOOGLE_API_USE_MTLS_ENDPOINT", "never") == "never"
        and os.getenv("GOOGLE_API_USE_CLIENT_CERTIFICATE", "false") == "false"
        and os.getenv("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA") == "true",
        "normal_manager_transport_override",
    )
    check = metadata._normal_http()
    check.session.close()
    from googlecloudsdk.core import config as manager_config
    from googlecloudsdk.core.credentials import store

    need(
        manager_config.CLOUD_SDK_VERSION == "578.0.0"
        and Path(manager_config.__file__).resolve()
        == (metadata.SDK / "googlecloudsdk/core/config.py").resolve()
        and Path(manager_config.Paths().global_config_dir).resolve()
        == Path("/Users/harshil/.config/gcloud"),
        "normal_manager_location_or_version",
    )
    credentials = store.Load(account=metadata.ACCOUNT, allow_account_impersonation=False)
    need(isinstance(credentials, Credentials), "normal_manager_credentials_required")
    options = (("grpc.enable_retries", 0), ("grpc.enable_http_proxy", 0))

    counters = {"denied_mutations": 0, "storage_gets": 0}

    def channel(transport):
        native = transport.create_channel(
            "firestore.googleapis.com:443", credentials=credentials, options=options
        )
        return _read_channel(native, config, counters)

    owned = []
    try:
        admin_channel = channel(FirestoreAdminGrpcTransport)
        owned.append(admin_channel)
        admin = FirestoreAdminClient(transport=FirestoreAdminGrpcTransport(channel=admin_channel))
        native_channel = channel(FirestoreGrpcTransport)
        owned.append(native_channel)
        client = FirestoreClient(transport=FirestoreGrpcTransport(channel=native_channel))
        http = AuthorizedSession(credentials, max_refresh_attempts=0)
        owned.append(http)
        http.max_redirects, http.trust_env = 0, False
        _read_storage(http, config, counters)
        storage = Client(
            project="ai-resume-parser-482412",
            credentials=credentials,
            _http=http,
            client_options={"api_endpoint": "https://storage.googleapis.com"},
        )
        return (admin, client, storage), counters
    except Exception:
        _close_clients(owned)
        raise MaintenanceDenied(
            "schema26_maintenance_native_client_construction_unavailable"
        ) from None


def _readonly_registry(rpc):
    from app.domains.recovery.gcp_buffer import BufferedRegistry

    # Only this administrative graph uses the class's existing upper bound.
    # Its GAPIC channels reject every mutation before send; definite aborts
    # are not retried, and all original external witness checks remain.
    return BufferedRegistry(rpc, max_attempts=1, deadline_seconds=10.0)


def _native_graph(config, clients):
    from app.domains.recovery.gcp_journal import GcsWitnessFence
    from app.domains.recovery.gcp_partitioned_publication import PartitionedPublicationCoordinator
    from app.domains.recovery.gcp_password_lifetime import GcpPasswordLifetimeCoordinator
    from app.domains.recovery.gcp_password_lifetime_journal import GcsPasswordLifetimeJournal
    from app.domains.recovery.gcp_password_reset import GcpPasswordCredentialProjection
    from app.domains.recovery.gcp_rpc import FirestoreRpc
    from app.domains.recovery.gcp_scoped_authority import ScopedCandidateAuthority

    admin, client, storage = clients
    resource, witness = config.publication_resource, config.execution_witness_pin
    execution = FirestoreRpc(client, admin, database_resource=witness.registry.database)
    protected = FirestoreRpc(
        client, admin, database_resource=resource.pin.authority.registry.database
    )
    publication = PartitionedPublicationCoordinator(
        _readonly_registry(protected), resource, storage.bucket(resource.bucket)
    )
    fence = GcsWitnessFence(
        storage.bucket(witness.bucket), execution, witness, config.execution_witness_body
    )
    journal = GcsPasswordLifetimeJournal(storage.bucket(resource.bucket), publication=publication)

    def clock():
        return time.time_ns() // 1_000_000

    lifetime = GcpPasswordLifetimeCoordinator(
        _readonly_registry(execution),
        witness.registry,
        epoch_generation=config.epoch_generation,
        journal=journal,
        fence=fence,
        now_ms=clock,
    )
    scoped = ScopedCandidateAuthority(publication, now_ms=clock)
    return publication, lifetime, GcpPasswordCredentialProjection(lifetime, scoped)


def _configuration(boundary):
    plan, _ = boundary._maintenance_custody()
    pin = plan.services[0]
    return _parse_configuration(boundary.run.revision(pin.name, pin.prior_revision))


def _parse_configuration(template):
    import rolling_release

    from app.domains.candidate_accounts.production import CandidateNativeConfiguration, _pairs
    from app.domains.recovery.browser_submit import SubmitNativeConfiguration

    # Use the existing exact environment parser; no secret/config dump leaves RAM.
    env = rolling_release.environment(template)
    raw = env.get("CANDIDATE_ACCOUNT_NATIVE_CONFIG", {}).get("value")
    need(type(raw) is str and 0 < len(raw.encode()) <= 16_384, "native_configuration_required")
    config = CandidateNativeConfiguration.model_validate(json.loads(raw, object_pairs_hook=_pairs))
    shared = env.get("BROWSER_SUBMIT_NATIVE_CONFIG", {}).get("value", "")
    need(
        type(shared) is str and len(shared.encode()) <= 16_384,
        "shared_native_configuration_required",
    )
    if shared:
        submit = SubmitNativeConfiguration.model_validate(
            json.loads(shared, object_pairs_hook=_pairs)
        )
        need(
            all(
                getattr(config, key) == getattr(submit, key)
                for key in CandidateNativeConfiguration.model_fields
            ),
            "shared_native_configuration_changed",
        )
    return config


def safe_native_failure(error):
    """Finite class/static-code projection; never return str/repr/provider bodies."""
    from app.domains.recovery.store import GuardDenied, GuardUnavailable

    known = {
        "Transaction retry deadline exceeded": "registry_total_deadline",
        "Firestore transaction could not begin": "firestore_begin_unavailable",
        "Complete transactional document evidence is unavailable": "firestore_batch_unavailable",
        "Lifetime writer control is absent or closed": "lifetime_control_unavailable",
        "Complete current native census is unavailable": "native_census_unavailable",
        "Fresh protected V3 identity/resource is unavailable": "publication_identity_resource_unavailable",
        "Protected V3 close absence cannot be established": "publication_close_absence_unavailable",
        "Protected close absence cannot be established": "execution_close_absence_unavailable",
        "Current database incarnation disagrees": "execution_identity_changed",
        "Protected V3 emergency closure denies new admission": "protected_closed",
        "The pinned authority has a protected closure": "execution_closed",
        "Dedicated Storage SDK metadata suppression is required": "storage_configuration_required",
        "A fresh dedicated pinned Storage SDK client is required": "storage_configuration_changed",
        "Generation-bound object bytes disagree": "gcs_exact_bytes_changed",
        "Live witness generation changed": "gcs_generation_changed",
        "Exact-generation metadata disagrees": "gcs_metadata_changed",
        "Object metadata lacks the exact bounded byte size": "gcs_size_unavailable",
        "SDK media request is outside its exact bounded generation": "gcs_media_scope",
        "Media response must be fresh, raw and successful": "gcs_media_response",
    }
    for suffix in (
        "native_admin_route",
        "native_transaction_route",
        "native_batch_route",
        "native_rollback_route",
        "native_census_route",
        "native_census_cursor",
        "native_rpc_refused",
        "native_stream_refused",
        "native_storage_origin",
        "native_storage_read_only",
        "native_storage_mutation_refused",
        "native_storage_scope",
        "native_storage_object",
        "native_storage_query",
    ):
        known["schema26_maintenance_" + suffix] = suffix
    classes = {
        "GuardUnavailable",
        "GuardDenied",
        "MaintenanceDenied",
        "MetadataUnavailable",
        "Aborted",
        "DeadlineExceeded",
        "InvalidArgument",
        "FailedPrecondition",
        "ResourceExhausted",
        "Cancelled",
        "InternalServerError",
        "ServiceUnavailable",
        "PermissionDenied",
        "Forbidden",
        "NotFound",
        "Unauthenticated",
        "RefreshError",
        "TransportError",
        "_InactiveRpcError",
        "TypeError",
        "ValueError",
        "AttributeError",
        "ValidationError",
        "TimeoutError",
        "OSError",
        "RuntimeError",
    }
    chain: list[dict[str, str]] = []
    seen: set[int] = set()
    current = error
    while isinstance(current, BaseException) and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        name = type(current).__name__
        message = (
            current.args[0] if len(current.args) == 1 and type(current.args[0]) is str else None
        )
        code = (
            known.get(message, "unclassified")
            if (
                type(current) in {GuardUnavailable, GuardDenied, MaintenanceDenied}
                and message is not None
                and len(message) <= 200
            )
            else "unclassified"
        )
        chain.append(
            {"exception_class": name if name in classes else "unclassified", "known_code": code}
        )
        current = current.__cause__ or current.__context__
    return chain


def _native_snapshot(graph):
    phase = {"value": "snapshot_start"}
    try:
        return _snapshot_reads(graph, phase)
    except Exception as error:
        failure = MaintenanceDenied("schema26_maintenance_native_snapshot_unavailable")
        failure.native_subphase = phase["value"]
        failure.native_failure = safe_native_failure(error)
        raise failure from None


def _snapshot_reads(graph, phase):
    from app.domains.recovery.contracts import Revocation
    from app.domains.recovery.gcp_password_lifetime import _Snapshot
    from app.domains.recovery.gcp_publication_scan import NativeAuthorityScanner

    publication, lifetime, projector = graph
    phase["value"] = "publication_open"
    publication._fresh_open()
    phase["value"] = "execution_witness"
    lifetime.fence.check(lifetime.pin)

    def check_control(tx):
        lifetime._control(_Snapshot(tx))

    def before_control():
        phase["value"] = "execution_control_witness"
        lifetime.fence.check(lifetime.pin)
        phase["value"] = "execution_control_after_witness"

    lifetime.registry.run(check_control, before_attempt=before_control)

    def scan():
        rows = NativeAuthorityScanner(publication.registry.rpc).scan(max_documents=MAX_DOCUMENTS)
        values = {}
        for row in rows:
            envelope = row.envelope
            key = envelope["namespace"], envelope["key"]
            need(key not in values, "duplicate_native_row")
            values[key] = envelope["value"]
        validate_publication(values, publication.resource)
        return values

    phase["value"] = "protected_census_first"
    before = scan()
    statuses = {}
    for (namespace, key), raw in before.items():
        if namespace != "v3_records":
            continue
        full = PartitionedRecord.model_validate(raw)
        if full.reference.kind == "scoped_admission":
            intent = ScopedJournalIntent.model_validate(full.intent)
            if isinstance(intent.command, ScopedPasswordReset):
                phase["value"] = "reset_status"
                statuses[key] = projector.status(intent)
    subjects = [
        ScopedSubject.model_validate(raw)
        for (namespace, _), raw in before.items()
        if namespace == "v3_subjects"
    ]
    need(len(subjects) <= MAX_ACCOUNTS, "subject_bound")
    keys = (
        tuple(
            (namespace, str(subject.subject_uuid))
            for subject in subjects
            for namespace in (
                "subjects",
                "password_credential_revisions",
                "pairing_auth_high_water",
            )
        )
        + tuple(
            ("pairing_account_bindings", str(subject.account_binding_id)) for subject in subjects
        )
        + tuple(
            (namespace, key)
            for subject in subjects
            for namespace, key in (
                ("pairing_subject_accounts", str(subject.subject_uuid)),
                ("pairing_sql_accounts", fingerprint({"candidate_id": subject.candidate_id})),
                ("pairing_account_tombstones", str(subject.account_binding_id)),
                (
                    "revocations",
                    Revocation(
                        subject_uuid=subject.subject_uuid,
                        kind="subject",
                        target=str(subject.subject_uuid),
                        revision=0,
                    ).key,
                ),
            )
        )
    )
    phase["value"] = "execution_rows_first"
    execution = {}
    for offset in range(0, len(keys), 32):
        execution.update(lifetime.registry.read_many(keys[offset : offset + 32]))
    phase["value"] = "protected_census_second"
    after = scan()
    phase["value"] = "execution_rows_second"
    execution_after = {}
    for offset in range(0, len(keys), 32):
        execution_after.update(lifetime.registry.read_many(keys[offset : offset + 32]))
    need(execution == execution_after, "execution_lifecycle_changed")
    phase["value"] = "publication_recheck"
    publication._fresh_open()
    phase["value"] = "execution_witness_recheck"
    lifetime.fence.check(lifetime.pin)
    lifetime.registry.run(check_control, before_attempt=before_control)
    need(before == after, "native_lifecycle_changed")
    return before, execution, statuses


def readonly_producer_drain(boundary):
    import rolling_release as rolling
    import schema25_coordinator_pause as coordinator
    import schema25_maintenance as maintenance

    reader = coordinator.NativeCoordinatorRun()
    try:
        coordinator.observe_closed(reader)
    finally:
        reader.stock.session.close()
    for queue in maintenance.QUEUES:
        need(
            boundary.observer._cloud(
                "tasks",
                "list",
                "--queue",
                queue,
                "--location",
                rolling.release.REGION,
                "--limit",
                "257",
            )
            == [],
            "queue_not_empty",
        )
    executions = boundary.observer._cloud(
        "run", "jobs", "executions", "list", "--region", rolling.release.REGION, "--limit", "257"
    )
    need(
        type(executions) is list
        and len(executions) <= 256
        and all(
            type(row) is dict and row.get("status", {}).get("completionTime") for row in executions
        ),
        "job_execution_not_drained",
    )


def probe_current_native_graph(root):
    """Read-shape probe on genuine current e23, including while it is OPEN.

    It constructs no maintenance baseline/DDL guard and performs no SQL. Output
    is counts/hashes only. Provider admission and migration acceptance remain
    false regardless of successful reads. The owner runs this only after review.
    """
    import rolling_native
    import rolling_release as rolling
    from schema26_app_rolling_v3 import PRIOR, Source26Boundary

    clients, run = (), None
    counters = {"denied_mutations": 0, "storage_gets": 0}
    phase = "source"
    try:
        _native_client_types()
        observer = Source26Boundary(root)
        observer._verify_source_image_provenance(
            **{
                "release": PRIOR.release,
                "image_digest": PRIOR.image_digest,
                "build_id": PRIOR.build_id,
                "ci_run_id": PRIOR.ci_run_id,
            }
        )
        run = rolling_native.NativeRun()
        current = run.service("ai-resume-parser")
        revision = rolling.short_revision(current["latestCreatedRevision"], "ai-resume-parser")
        native = run.revision("ai-resume-parser", revision)
        env = rolling.environment(current["template"])
        need(
            env.get("APP_RELEASE") == {"name": "APP_RELEASE", "value": PRIOR.release}
            and current["template"]["containers"][0]["image"].endswith("@" + PRIOR.image_digest)
            and native is not None
            and native["name"] == metadata.API + "/revisions/" + revision
            and metadata.UUID.fullmatch(native.get("uid", ""))
            and rolling.template_sha(current["template"], "ai-resume-parser")
            == rolling.template_sha(
                {
                    **{key: native.get(key) for key in current["template"] if key != "revision"},
                    **({"revision": revision} if "revision" in current["template"] else {}),
                },
                "ai-resume-parser",
            ),
            "probe_current_source_changed",
        )
        config = _parse_configuration(current["template"])
        phase = "clients"
        clients, counters = _normal_clients(config)
        phase = "native_snapshot"
        values, execution, statuses = _native_snapshot(_native_graph(config, clients))
        need(
            all(status.status == "COMMITTED" for status in statuses.values()),
            "probe_reset_status_unconfirmed",
        )
        phase = "source_recheck"
        need(run.service("ai-resume-parser") == current, "probe_service_changed")
        need(
            counters["denied_mutations"] == 0 and run.armed is None and run.sent == 0,
            "probe_unexpected_write_attempt",
        )
        namespaces: dict[str, int] = {}
        for namespace, _ in values:
            namespaces[namespace] = namespaces.get(namespace, 0) + 1
        return {
            "kind": "schema26_native_graph_read_shape_v1",
            "source": asdict(PRIOR),
            "api_service_uid": current["uid"],
            "api_generation": int(current["generation"]),
            "api_revision_uid": native["uid"],
            "configuration_sha256": fingerprint(config.model_dump(mode="json")),
            "protected_rows": len(values),
            "execution_rows": len(execution),
            "namespaces": namespaces,
            "reset_statuses": len(statuses),
            "snapshot_sha256": fingerprint(
                {
                    "protected": [
                        {"namespace": n, "key": k, "value": v}
                        for (n, k), v in sorted(values.items())
                    ],
                    "execution": [
                        {"namespace": n, "key": k, "value": v}
                        for (n, k), v in sorted(execution.items())
                    ],
                }
            ),
            "read_counters": dict(counters),
            "maintenance_admission_proved": False,
            "sql_authority": False,
            "ddl_authority": False,
        }
    except Exception as error:
        failure = MaintenanceDenied("schema26_maintenance_native_graph_probe_refused")
        failure.probe_projection = {
            "phase": phase,
            "native_subphase": getattr(error, "native_subphase", None),
            "failure_classes": getattr(error, "native_failure", None) or safe_native_failure(error),
            "read_counters": dict(counters),
            "maintenance_admission_proved": False,
            "sql_authority": False,
            "ddl_authority": False,
        }
        raise failure from None
    finally:
        _close_clients(clients)
        if run is not None:
            run.stock.session.close()


class NativeMaintenanceDdlGuard:
    """Fixed source-owned observer constructed BEFORE the supplied SQL interval.

    No caller callback or accepted boolean. Exactly one final metadata reader
    is constructed/authenticated here and consumed just before original DDL.
    This object does not acquire migration credentials or authorize commit.
    """

    def __init__(self, boundary, *, observer_secret):
        self.started, self.closed, self.prepared = False, False, False
        try:
            self._prepare(boundary, observer_secret=observer_secret)
        except MaintenanceDenied:
            self.close()
            raise
        except Exception:
            self.close()
            raise MaintenanceDenied("schema26_maintenance_preflight_unavailable") from None

    def _prepare(self, boundary, *, observer_secret):
        import schema26_app_rolling_v3 as v3

        need(type(boundary) is v3.NativeApp26Boundary, "fixed_controller_required")
        from schema26_lifecycle_join import join_lifecycle

        self.boundary = boundary
        _native_client_types()
        self.baseline = boundary._capture_maintenance_baseline()
        readonly_producer_drain(boundary)
        reader = metadata.ReadOnlyMaintenanceMetadata()
        self.retired_sha256 = retirement_fingerprint(reader.observe(), self.baseline)
        config = _configuration(boundary)
        self.clients, self.read_counters = _normal_clients(config)
        try:
            self.graph = _native_graph(config, self.clients)
            self.values, self.execution, self.statuses = _native_snapshot(self.graph)
            denied_owners = []
            for (namespace, _), raw in self.values.items():
                if namespace != "v3_records":
                    continue
                full = PartitionedRecord.model_validate(raw)
                if full.reference.kind != "scoped_admission":
                    continue
                command = ScopedJournalIntent.model_validate(full.intent).command
                if isinstance(command, ScopedDenial) and command.scope == "subject":
                    denied_owners.append(command.subject.candidate_id)
            self.owners = tuple(sorted(denied_owners))
            need(len(set(self.owners)) == len(self.owners) <= MAX_ACCOUNTS, "owner_bound")
            module = boundary.observer._module(
                "backend/scripts/monetary_cutover_preflight.py",
                "hirewiz_schema26_maintenance_observer",
            )
            neon = module.neon_identity
            binding, target = neon.collect_schema25_release_target(
                boundary.observer,
                observer_secret,
                alias="observer",
                database_owner_oid=neon.FRESH_DATABASE_OWNER_OID,
            )
            engine = target.engine()
            try:
                with engine.connect() as connection, connection.begin():
                    connection.exec_driver_sql(
                        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                    )
                    connection.exec_driver_sql("SET LOCAL statement_timeout='2000ms'")
                    neon.verify_transport(connection, binding)
                    neon.verify_native_target(connection, binding, neon.FRESH_DATABASE_OID)
                    rows, absence = sql_projection(connection, self.owners)
                    self.sql_sha256 = fingerprint({"rows": rows, "absence": absence})
                    self.lifecycle_sha256 = join_lifecycle(
                        self.values, self.execution, self.statuses, rows, absence, self.graph[1].pin
                    )
            finally:
                engine.dispose()
            # Recheck full immutable close lineage, then prepare final normal
            # authenticated transport outside all caller-owned SQL locks.
            boundary._verify_maintenance_baseline(self.baseline)
            self.final_reader = metadata.ReadOnlyMaintenanceMetadata()
            self.prepared = True
        finally:
            _close_clients(self.clients)

    def verify_pre_ddl(self, connection):
        need(self.prepared and not self.started and not self.closed, "one_final_observation_only")
        self.started = True
        try:
            connection.exec_driver_sql("SET LOCAL statement_timeout='2000ms'")
            rows, absence = sql_projection(connection, self.owners)
            need(
                fingerprint({"rows": rows, "absence": absence}) == self.sql_sha256,
                "sql_lifecycle_changed",
            )
            connection.exec_driver_sql("SET LOCAL statement_timeout='10s'")
            current = self.final_reader.observe()
            need(
                retirement_fingerprint(current, self.baseline) == self.retired_sha256,
                "retirement_census_changed",
            )
        except MaintenanceDenied:
            raise
        except Exception:
            raise MaintenanceDenied("schema26_maintenance_final_observation_unavailable") from None
        finally:
            self.close()

    def close(self):
        if not self.closed:
            self.closed = True
            reader = getattr(self, "final_reader", None)
            if reader is not None:
                reader.close()

    def __enter__(self):
        need(self.prepared and not self.closed, "prepared_guard_required")
        return self

    def __exit__(self, *_):
        self.close()
