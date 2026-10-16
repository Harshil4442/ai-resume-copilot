"""Production composition over actual SDKs with local intercepted transports.

Only synthetic operator baselines are seeded. Candidates, protected subjects,
credential bindings and sessions must be created by genuine enrollment ACKs.
This is not production IAM, provider durability or public device-pairing proof.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import UUID, uuid4

import google.auth
import pytest
import requests
from google.auth.credentials import AnonymousCredentials
from google.cloud import storage
from google.cloud.firestore_admin_v1.services import firestore_admin
from google.cloud.firestore_v1.services import firestore
from sqlalchemy import Engine, create_engine, event
from test_browser_submit_native import InterceptedNative
from test_gcp_journal_sdk import response

from app import database
from app.database import Base
from app.domains.candidate_accounts import production
from app.domains.candidate_accounts.production import CandidateNativeConfiguration
from app.domains.candidate_accounts.service import (
    credential_digest,
    production_candidate_accounts,
    revision_from_row,
)
from app.domains.recovery.contracts import canonical, fingerprint
from app.domains.recovery.gcp_buffer import BufferedRegistry
from app.domains.recovery.gcp_closure_contracts import closure_path
from app.domains.recovery.gcp_contracts import RegistryPin, WitnessBody, WitnessPin
from app.domains.recovery.gcp_pairing import pairing_control_record
from app.domains.recovery.gcp_partitioned_contracts import (
    DirectoryHead,
    LaneHead,
    PartitionedPin,
    PartitionedResource,
    PartitionedRoot,
)
from app.domains.recovery.gcp_publication_contracts import PublicationPin
from app.domains.recovery.gcp_rpc import FirestoreRpc
from app.domains.recovery.gcp_scoped_census import validate_scoped_rows
from app.domains.recovery.gcp_service import control_record
from app.domains.recovery.password_reauth import CandidateWebSession, RetainedWebSession
from app.domains.recovery.store import GuardDenied, GuardUnavailable
from app.models import CandidatePasswordAccount, User

NOW = 1_800_000_000_000
PASSWORD = "synthetic-candidate-password-2026"
REPLACEMENT = "synthetic-replacement-password-2026"  # gitleaks:allow -- synthetic only


@pytest.fixture
def configured(monkeypatch, tmp_path):
    monkeypatch.setenv("CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED", "true")
    monkeypatch.setenv("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA", "true")
    monkeypatch.delenv("BROWSER_SUBMIT_NATIVE_CONFIG", raising=False)
    monkeypatch.setattr(google.auth, "default", lambda *a, **k: pytest.fail("ADC is forbidden"))
    c = SimpleNamespace(native=InterceptedNative(), objects={}, http_calls=[], sql_active=set())
    execution = RegistryPin(
        database="projects/hirewiz-local-authority/databases/candidate-execution",
        database_uid=uuid4(),
        authority_id=uuid4(),
        incarnation=1,
        epoch_id=uuid4(),
    )
    protected = RegistryPin(
        database="projects/hirewiz-local-authority/databases/candidate-protected",
        database_uid=uuid4(),
        authority_id=uuid4(),
        incarnation=1,
        epoch_id=uuid4(),
    )
    c.native.identities = {
        execution.database: str(execution.database_uid),
        protected.database: str(protected.database_uid),
    }
    pin = PartitionedPin(
        authority=PublicationPin(
            registry=protected, target=execution, journal_bucket="synthetic-candidate-journal"
        ),
        lanes=2,
        directory_shards=2,
    )
    resource = PartitionedResource(
        pin=pin,
        bucket=pin.authority.journal_bucket,
        generation="101",
        sha256=fingerprint(pin.model_dump(mode="json")),
    )
    body = WitnessBody(
        registry=execution,
        state="OPEN",
        manifest_generation="1",
        manifest_sha256="a" * 64,
        partitions_sha256="b" * 64,
    )
    witness = WitnessPin(
        registry=execution,
        bucket=resource.bucket,
        path="candidate/witness",
        generation="102",
        sha256=fingerprint(body.model_dump(mode="json")),
    )
    c.config = CandidateNativeConfiguration(
        execution_witness_pin=witness,
        execution_witness_body=body,
        publication_resource=resource,
        epoch_generation=1,
    )
    c.execution, c.protected = execution, protected
    c.objects[resource.path] = canonical(pin.model_dump(mode="json")).encode(), "101"
    c.objects[witness.path] = canonical(body.model_dump(mode="json")).encode(), "102"
    monkeypatch.setenv(
        "CANDIDATE_ACCOUNT_NATIVE_CONFIG", canonical(c.config.model_dump(mode="json"))
    )
    monkeypatch.setattr(production.time, "time_ns", lambda: NOW * 1_000_000)
    c.source = create_engine(
        f"sqlite:///{tmp_path / 'owned-candidate.sqlite'}", hide_parameters=False
    )
    Base.metadata.create_all(c.source)
    monkeypatch.setattr(database, "engine", c.source)

    def opened(connection):
        if connection.engine.pool is c.source.pool:
            c.sql_active.add(id(connection))

    def ended(connection):
        c.sql_active.discard(id(connection))

    event.listen(Engine, "begin", opened)
    event.listen(Engine, "commit", ended)
    event.listen(Engine, "rollback", ended)
    original_begin, original_identity = c.native.begin, c.native.identity

    def begin(request, **kwargs):
        assert not c.sql_active, "Native transaction began under SQL locks"
        return original_begin(request, **kwargs)

    def identity(request, **kwargs):
        assert not c.sql_active, "Metadata IO entered SQL transaction"
        return original_identity(request, **kwargs)

    c.native.sdk.client.transport.begin_transaction.side_effect = begin
    c.native.sdk.admin.transport.get_database.side_effect = identity

    def http(method, url, **kwargs):
        assert not c.sql_active and not c.native.views, "Storage IO entered SQL/native transaction"
        assert kwargs["timeout"] == 2.0
        c.http_calls.append((method, url))
        query = parse_qs(urlsplit(url).query)
        if method == "POST":
            assert query["ifGenerationMatch"] == ["0"]
            data = kwargs["data"]
            path = json.loads(data.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0])["name"]
            if path in c.objects:
                return response(b'{"error":{"code":412}}', status=412)
            c.objects[path] = (
                data.split(b"\r\n\r\n")[2].rsplit(b"\r\n--", 1)[0],
                str(200 + len(c.objects)),
            )
        else:
            path = unquote(urlsplit(url).path.split("/o/", 1)[1])
        if path not in c.objects:
            return response(b'{"error":{"code":404}}', status=404)
        raw, generation = c.objects[path]
        if query.get("alt") == ["media"]:
            return response(raw)
        return response(
            json.dumps(
                {
                    "name": path,
                    "bucket": resource.bucket,
                    "generation": generation,
                    "size": str(len(raw)),
                }
            ).encode()
        )

    session = MagicMock(spec=requests.Session)
    session.is_mtls = False
    session.request.side_effect = http
    c.storage = storage.Client(
        project="hirewiz-local-authority", credentials=AnonymousCredentials(), _http=session
    )
    c.clients = [
        MagicMock(return_value=c.native.sdk.client),
        MagicMock(return_value=c.native.sdk.admin),
        MagicMock(return_value=c.storage),
    ]
    monkeypatch.setattr(firestore, "FirestoreClient", c.clients[0])
    monkeypatch.setattr(firestore_admin, "FirestoreAdminClient", c.clients[1])
    monkeypatch.setattr(storage, "Client", c.clients[2])
    c.execution_registry = BufferedRegistry(
        FirestoreRpc(c.native.sdk.client, c.native.sdk.admin, database_resource=execution.database)
    )
    c.protected_registry = BufferedRegistry(
        FirestoreRpc(c.native.sdk.client, c.native.sdk.admin, database_resource=protected.database)
    )
    # Synthetic operator baseline only. No candidate identity or lifetime seed.
    for namespace, key, value in [
        ("control", "meta", control_record(execution)),
        ("pairing_control", "current", pairing_control_record(execution, 1)),
        ("pairing_clock", "observed", {"now_ms": NOW}),
        ("head", "global", {"sequence": 0, "digest": "0" * 64}),
    ]:
        c.native.seed(c.execution_registry, namespace, key, value)
    c.native.seed(
        c.protected_registry,
        "v3_root",
        "current",
        PartitionedRoot(pin=pin, phase="OPEN").model_dump(mode="json"),
    )
    for index in range(2):
        c.native.seed(
            c.protected_registry,
            "v3_lanes",
            str(index),
            LaneHead(lane=index, segment_count=0, segment_id=None).model_dump(mode="json"),
        )
        c.native.seed(
            c.protected_registry,
            "v3_directory",
            str(index),
            DirectoryHead(shard=index, count=0, digest="0" * 64).model_dump(mode="json"),
        )
    c.accounts = production_candidate_accounts()
    try:
        yield c
    finally:
        event.remove(Engine, "begin", opened)
        event.remove(Engine, "commit", ended)
        event.remove(Engine, "rollback", ended)
        c.storage.close()
        c.source.dispose()


def register(c):
    return c.accounts.register(
        email="synthetic@example.test", password=PASSWORD, policy_version="2026-10-08"
    )


def test_actual_factory_fresh_enrollment_and_session_have_original_protected_provenance(configured):
    c = configured
    for client in c.clients:
        client.assert_not_called()
    assert not any(namespace in {"subjects", "v3_subjects"} for namespace, _ in c.native.values())
    uid = register(c)
    result = c.accounts.login(email="synthetic@example.test", password=PASSWORD)
    c.accounts.validate(result.context, auth_generation=1)
    with c.accounts.sessions() as db:
        user, account = db.get(User, uid), db.get(CandidatePasswordAccount, uid)
        assert account.state == "ACTIVE" and account.credential_sha256 == credential_digest(
            user.password_hash
        )
        subject = c.native.values()["v3_subjects", account.subject_uuid]
        assert (
            subject["candidate_id"] == uid
            and subject["credential_sha256"] == account.credential_sha256
        )
    engine = c.accounts.sessions.kw["bind"]
    assert engine.hide_parameters is True and engine.pool is c.source.pool
    assert c.source.hide_parameters is False
    for client in c.clients:
        assert client.call_count == 1
    validate_scoped_rows(c.native.values())
    assert not c.sql_active and not c.native.views
    c.accounts.logout(result.context)
    with pytest.raises(GuardDenied):
        c.accounts.validate(result.context, auth_generation=1)
    for rpc in (
        c.native.sdk.client.begin_transaction,
        c.native.sdk.client.batch_get_documents,
        c.native.sdk.client.commit,
        c.native.sdk.client.rollback,
        c.native.sdk.admin.get_database,
    ):
        for call in rpc.call_args_list:
            assert call.kwargs["retry"] is None and call.kwargs["timeout"] == 2.0


def test_actual_reset_cookie_logout_and_delete_ports_are_composed(configured):
    c = configured
    uid = register(c)
    first = c.accounts.login(email="synthetic@example.test", password=PASSWORD)
    second = c.accounts.login(email="synthetic@example.test", password=PASSWORD)
    c.accounts.change_password(first.context, current_password=PASSWORD, new_password=REPLACEMENT)
    for old in (first, second):
        with pytest.raises(GuardDenied):
            c.accounts.validate(old.context, auth_generation=1)
    current = c.accounts.login(email="synthetic@example.test", password=REPLACEMENT)
    assert current.auth_generation == 2
    operation = uuid4()
    c.accounts.logout_cookie(current.context, auth_generation=2, operation_id=operation)
    c.accounts.logout_cookie(current.context, auth_generation=2, operation_id=operation)
    with pytest.raises(GuardDenied):
        c.accounts.validate(current.context, auth_generation=2)
    final = c.accounts.login(email="synthetic@example.test", password=REPLACEMENT)
    c.accounts.prepare_delete(final.context)
    c.accounts.logout_cookie(final.context, auth_generation=2, operation_id=uuid4())
    with c.accounts.sessions() as db:
        assert db.get(CandidatePasswordAccount, uid).state == "DELETE_DENIED"
    with pytest.raises(GuardDenied):
        c.accounts.login(email="synthetic@example.test", password=REPLACEMENT)
    validate_scoped_rows(c.native.values())


@pytest.mark.parametrize("stage", ["projection", "completion"])
@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_reset_cannot_update_sql_credentials_or_reactivate_old_scope(
    configured, stage, persisted
):
    c = configured
    uid = register(c)
    current = c.accounts.login(email="synthetic@example.test", password=PASSWORD)
    with c.accounts.sessions() as db:
        before_hash = db.get(User, uid).password_hash
    namespace = "password_credential_revisions" if stage == "projection" else "v3_reset_completions"
    c.native.fault = lambda names: (
        ("persist_loss" if persisted else "no_persist_loss") if namespace in names else None
    )
    with pytest.raises(GuardUnavailable):
        c.accounts.change_password(
            current.context, current_password=PASSWORD, new_password=REPLACEMENT
        )
    c.native.fault = None
    with c.accounts.sessions() as db:
        assert db.get(User, uid).password_hash == before_hash
        assert db.get(CandidatePasswordAccount, uid).auth_generation == 1
    with pytest.raises(GuardDenied):
        c.accounts.validate(current.context, auth_generation=1)
    with pytest.raises(GuardDenied):
        c.accounts.login(email="synthetic@example.test", password=PASSWORD)


@pytest.mark.parametrize("operation", ["logout", "delete"])
@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_final_denial_still_blocks_scope_without_sql_completion(
    configured, operation, persisted
):
    c = configured
    uid = register(c)
    current = c.accounts.login(email="synthetic@example.test", password=PASSWORD)
    namespace = "v3_session_tombstones" if operation == "logout" else "v3_subject_tombstones"
    c.native.fault = lambda names: (
        ("persist_loss" if persisted else "no_persist_loss") if namespace in names else None
    )
    with pytest.raises(GuardUnavailable):
        if operation == "logout":
            c.accounts.logout(current.context)
        else:
            c.accounts.prepare_delete(current.context)
    c.native.fault = None
    with c.accounts.sessions() as db:
        assert db.get(CandidatePasswordAccount, uid).state == "ACTIVE"
    with pytest.raises(GuardDenied):
        c.accounts.validate(current.context, auth_generation=1)


@pytest.mark.parametrize("stage", ["native", "activation"])
@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_enrollment_or_activation_keeps_pending_without_login_or_adoption(
    configured, stage, persisted
):
    c = configured
    namespace = "password_credential_revisions" if stage == "native" else "v3_activations"
    c.native.fault = lambda names: (
        ("persist_loss" if persisted else "no_persist_loss") if namespace in names else None
    )
    with pytest.raises(GuardUnavailable):
        register(c)
    c.native.fault = None
    with c.accounts.sessions() as db:
        assert db.query(CandidatePasswordAccount).one().state == "PENDING"
    before = len(c.native.calls)
    with pytest.raises(GuardDenied):
        c.accounts.login(email="synthetic@example.test", password=PASSWORD)
    with pytest.raises(GuardDenied):
        register(c)
    assert len(c.native.calls) == before


@pytest.mark.parametrize(
    "fault", ["execution_uid", "protected_uid", "closure", "resource", "configuration", "flag"]
)
def test_fresh_factory_epoch_and_configuration_checks_deny_existing_session(
    configured, monkeypatch, fault
):
    c = configured
    register(c)
    current = c.accounts.login(email="synthetic@example.test", password=PASSWORD)
    if fault == "execution_uid":
        c.native.identities[c.execution.database] = str(uuid4())
    if fault == "protected_uid":
        c.native.identities[c.protected.database] = str(uuid4())
    if fault == "closure":
        c.objects[closure_path(c.execution)] = b"closed", "999"
    if fault == "resource":
        c.objects[c.config.publication_resource.path] = b"{}", "999"
    if fault == "configuration":
        value = c.config.model_dump(mode="json")
        value["epoch_generation"] = 2
        monkeypatch.setenv("CANDIDATE_ACCOUNT_NATIVE_CONFIG", canonical(value))
    if fault == "flag":
        monkeypatch.setenv("CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED", "false")
    before = len(c.native.calls)
    with pytest.raises((GuardDenied, GuardUnavailable)):
        c.accounts.validate(current.context, auth_generation=1)
    assert len(c.native.calls) == before


def test_structural_factory_is_default_closed_and_never_constructs_clients(configured, monkeypatch):
    c = configured
    monkeypatch.setenv("CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED", "false")
    with pytest.raises(GuardUnavailable):
        production_candidate_accounts()
    monkeypatch.setenv("CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED", "true")
    production_candidate_accounts()
    for client in c.clients:
        client.assert_not_called()
    raw = canonical(c.config.model_dump(mode="json"))
    monkeypatch.setenv("CANDIDATE_ACCOUNT_NATIVE_CONFIG", raw[:-1] + ',"epoch_generation":1}')
    with pytest.raises(GuardUnavailable):
        production_candidate_accounts()
    for client in c.clients:
        client.assert_not_called()


def test_optional_submit_configuration_must_share_exact_authority_pins(configured, monkeypatch):
    c = configured
    shared = {
        **c.config.model_dump(mode="json"),
        "executor_revision": "companion_0_6_0",
        "device_grant_pin": {
            "bucket": c.config.publication_resource.bucket,
            "path": f"authority-submit-device-grants/{UUID(int=7)}.json",
            "generation": "1",
            "sha256": "a" * 64,
        },
    }
    monkeypatch.setenv("BROWSER_SUBMIT_NATIVE_CONFIG", canonical(shared))
    production_candidate_accounts()
    shared["epoch_generation"] = 2
    monkeypatch.setenv("BROWSER_SUBMIT_NATIVE_CONFIG", canonical(shared))
    with pytest.raises(GuardUnavailable):
        production_candidate_accounts()
    for client in c.clients:
        client.assert_not_called()


def oauth_revision(c):
    uid = register(c)
    with c.accounts.sessions() as db:
        return revision_from_row(
            db.get(CandidatePasswordAccount, uid), db.get(User, uid).password_hash
        )


def issue_oauth(c, revision, proof="9" * 64):
    return c.accounts.retention.issue_oauth(
        revision,
        principal_sha256=revision.principal_sha256,
        verified_proof_sha256=proof,
        expires_at_ms=NOW + 60_000,
    )


def test_distinct_oauth_original_ack_binds_owner_proof_method_and_prevents_token_replay(configured):
    c = configured
    revision = oauth_revision(c)
    context = issue_oauth(c, revision)
    assert context.authentication_method == "google"
    assert context.model_dump(mode="json") == {
        "candidate_id": revision.candidate_id,
        "account_binding_id": str(revision.account_binding_id),
        "session_id": str(context.session_id),
        "authentication_method": "google",
    }
    c.accounts.validate(context, auth_generation=1)
    rows = c.native.values()
    retained = rows["pairing_web_sessions", str(context.session_id)]
    protected = rows["v3_sessions", str(context.session_id)]
    assert retained["authentication_method"] == protected["authentication_method"] == "google"
    assert retained["verified_proof_sha256"] == protected["verified_proof_sha256"] == "9" * 64
    proof = rows["oauth_web_session_proofs", "9" * 64]
    assert (
        proof["session_id"] == str(context.session_id)
        and proof["principal_sha256"] == revision.principal_sha256
    )
    event = rows["events", retained["registration_event_id"]]
    assert (
        event["kind"] == "OAUTH_WEB_SESSION_CREATED"
        and event["payload"]["verified_proof_sha256"] == "9" * 64
    )
    with pytest.raises(GuardDenied):
        issue_oauth(c, revision)
    with pytest.raises(GuardDenied):
        c.accounts.validate(
            context.model_copy(update={"authentication_method": "password"}), auth_generation=1
        )
    with pytest.raises(GuardUnavailable):
        c.accounts.retention.issue_oauth(
            revision,
            principal_sha256="0" * 64,
            verified_proof_sha256="8" * 64,
            expires_at_ms=NOW + 60_000,
        )
    validate_scoped_rows(c.native.values())


def test_google_context_retains_separate_password_change_and_cookie_deletion_guards(configured):
    c = configured
    revision = oauth_revision(c)
    context = issue_oauth(c, revision)
    with pytest.raises(GuardDenied):
        c.accounts.change_password(context, current_password="incorrect", new_password=REPLACEMENT)
    c.accounts.change_password(context, current_password=PASSWORD, new_password=REPLACEMENT)
    with pytest.raises(GuardDenied):
        c.accounts.validate(context, auth_generation=1)
    with c.accounts.sessions() as db:
        revised = revision_from_row(
            db.get(CandidatePasswordAccount, revision.candidate_id),
            db.get(User, revision.candidate_id).password_hash,
        )
    current = issue_oauth(c, revised, "8" * 64)
    c.accounts.logout_cookie(current, auth_generation=2, operation_id=uuid4())
    with pytest.raises(GuardDenied):
        c.accounts.validate(current, auth_generation=2)
    final = issue_oauth(c, revised, "7" * 64)
    c.accounts.prepare_delete(final)
    c.accounts.logout_cookie(final, auth_generation=2, operation_id=uuid4())
    validate_scoped_rows(c.native.values())


@pytest.mark.parametrize("stage", ["native", "activation"])
@pytest.mark.parametrize("persisted", [False, True])
def test_oauth_unknown_commit_exports_no_context_and_cannot_adopt_retained_proof(
    configured, stage, persisted
):
    c = configured
    revision = oauth_revision(c)
    namespace = "oauth_web_session_proofs" if stage == "native" else "v3_activations"
    c.native.fault = lambda names: (
        ("persist_loss" if persisted else "no_persist_loss") if namespace in names else None
    )
    try:
        assert issue_oauth(c, revision) is None
    except GuardUnavailable:
        pass
    c.native.fault = None
    if ("oauth_web_session_proofs", "9" * 64) in c.native.values():
        with pytest.raises(GuardDenied):
            issue_oauth(c, revision)


def test_password_context_and_retained_record_keep_original_serialized_bytes(configured):
    c = configured
    register(c)
    current = c.accounts.login(email="synthetic@example.test", password=PASSWORD)
    assert set(current.context.model_dump(mode="json")) == {
        "candidate_id",
        "account_binding_id",
        "session_id",
    }
    raw = c.native.values()["pairing_web_sessions", str(current.context.session_id)]
    assert "authentication_method" not in raw and "verified_proof_sha256" not in raw
    assert canonical(RetainedWebSession.model_validate(raw).model_dump(mode="json")) == canonical(
        raw
    )
    assert canonical(
        CandidateWebSession.model_validate(current.context.model_dump(mode="json")).model_dump(
            mode="json"
        )
    ) == canonical(current.context.model_dump(mode="json"))
