"""Fresh production factories over intercepted SDKs; no ADC/provider/IAM proof."""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event, Lock
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
import requests
from google.auth.compute_engine import Credentials as ComputeCredentials
from google.auth.credentials import AnonymousCredentials
from google.cloud import firestore_admin_v1
from google.cloud.firestore_admin_v1.types import Database, ListDatabasesResponse
from google.cloud.storage import Client as StorageClient

from app.domains.candidate_accounts import metadata_transport as metadata
from app.domains.candidate_accounts.production import production_candidate_accounts
from app.domains.recovery.gcp_rpc import FirestoreIdentityList
from app.domains.recovery.native_metadata_admission import MetadataCapacityUnavailable
from app.domains.recovery.store import GuardDenied, GuardUnavailable
from tests.test_candidate_production_factory import PASSWORD, configured, register  # noqa: F401
from tests.test_gcp_rpc import ScriptedSdk
from tests.test_native_metadata_admission import inert_shared_redis

PRINCIPAL = "synthetic-metadata@hirewiz-local-authority.iam.gserviceaccount.com"
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


@pytest.fixture(autouse=True)
def isolated_pool(monkeypatch):
    monkeypatch.setattr(metadata, "_state", None)
    monkeypatch.setattr(metadata, "_PID", os.getpid())
    monkeypatch.delenv(metadata.FLAG, raising=False)


@pytest.fixture
def fresh_factories(configured, monkeypatch):  # noqa: F811
    c = configured
    register(c)
    session = c.accounts.login(email="synthetic@example.test", password=PASSWORD)
    admission = inert_shared_redis(monkeypatch, project="hirewiz-local-authority")
    monkeypatch.setenv("CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED", "true")
    sdks, storage_clients, data_clients, admin_clients, listed_calls = [], [], [], [], []
    lock = Lock()

    def listed(request, **kwargs):
        assert request.parent == "projects/hirewiz-local-authority" and request.show_deleted is False
        assert kwargs["timeout"] == 2
        with lock:
            listed_calls.append(request)
        return ListDatabasesResponse(databases=[Database(name=name, uid=uid, type_=1)
                                               for name, uid in c.native.identities.items()])

    def data_client():
        sdk = ScriptedSdk()
        sdks.append(sdk)
        for name in ("begin_transaction", "batch_get_documents", "commit", "rollback"):
            getattr(sdk.client.transport, name).side_effect = getattr(c.native.sdk.client.transport, name).side_effect
        data_clients.append(sdk.client)
        return sdk.client

    def admin_client():
        sdk = ScriptedSdk()
        sdks.append(sdk)
        sdk.admin.transport._credentials = ComputeCredentials(service_account_email=PRINCIPAL, scopes=SCOPES)
        sdk.admin.transport.get_database.side_effect = c.native.sdk.admin.transport.get_database.side_effect
        sdk.admin.transport.list_databases.side_effect = listed
        admin_clients.append(sdk.admin)
        return sdk.admin

    def storage_client(**kwargs):
        http = MagicMock(spec=requests.Session)
        http.is_mtls = False
        http.request.side_effect = c.storage._http.request.side_effect
        client = StorageClient(**kwargs, credentials=AnonymousCredentials(), _http=http)
        storage_clients.append(client)
        return client

    c.clients[0].reset_mock()
    c.clients[0].side_effect = data_client
    c.clients[1].reset_mock()
    c.clients[1].side_effect = admin_client
    c.clients[2].reset_mock()
    c.clients[2].side_effect = storage_client
    monkeypatch.setenv(metadata.FLAG, "true")
    try:
        yield SimpleNamespace(c=c, session=session, admission=admission, sdks=sdks,
                              data=data_clients, admins=admin_clients, storage=storage_clients, listed=listed_calls)
    finally:
        for client in storage_clients:
            client.close()


def bindings(accounts):
    current = accounts._composition(ordinary_validation=True)
    return ((current.lifetime.registry.rpc, current.lifetime.pin),
            (current.scoped.publication.registry.rpc, current.scoped.publication.resource.pin.authority.registry))


def batch(accounts):
    fixed = bindings(accounts)
    owner = accounts._metadata_transport
    cohorts = owner.for_bindings(fixed)
    return FirestoreIdentityList(tuple(rpc for rpc, _ in fixed), cohorts=cohorts, pins=tuple(pin for _, pin in fixed))


def test_default_and_snapshot_are_lazy_and_unknown_flag_refuses(monkeypatch):
    calls = []
    monkeypatch.setattr(firestore_admin_v1.services.firestore_admin, "FirestoreAdminClient", lambda: calls.append("forbidden"))
    assert metadata.enabled() is False
    assert metadata.shared_metadata_snapshot() == {} and calls == []
    monkeypatch.setenv(metadata.FLAG, "TRUE")
    with pytest.raises(GuardUnavailable):
        metadata.enabled()
    assert calls == []


def test_fresh_factories_share_only_metadata_while_data_storage_and_services_remain_private(fresh_factories):
    f = fresh_factories
    first, second = production_candidate_accounts(), production_candidate_accounts()
    assert first is not second and first._instance is second._instance is None
    assert metadata.shared_metadata_snapshot() == {} and f.admins == f.data == f.storage == []
    a, b = first._composition(ordinary_validation=True), second._composition(ordinary_validation=True)
    assert a is not b and a.service is not b.service and a.scoped is not b.scoped and a.lifetime is not b.lifetime
    assert a.fence is not b.fence and a.fence.bucket.client is not b.fence.bucket.client
    assert a.service.sessions.kw["bind"] is not b.service.sessions.kw["bind"]
    arpc, brpc = a.lifetime.registry.rpc, b.lifetime.registry.rpc
    assert arpc is not brpc and arpc.client is not brpc.client
    assert arpc.client.transport is not brpc.client.transport
    assert first._metadata_transport is second._metadata_transport
    assert arpc.admin is brpc.admin is first._metadata_transport.admin
    assert len(f.admins) == 1 and len(f.data) == len(f.storage) == 2
    assert f.storage[0]._http is not f.storage[1]._http
    assert first._metadata_transport.cohorts is None  # Construction itself observes no UID or auth rows.


def test_separate_factories_keep_fresh_session_journal_and_storage_reads_after_revocation(fresh_factories):
    f = fresh_factories
    first, second = production_candidate_accounts(), production_candidate_accounts()
    before_http = len(f.c.http_calls)
    first.validate(f.session.context, auth_generation=1)
    first_http = len(f.c.http_calls) - before_http
    first_reads = f.data[-1].transport.batch_get_documents.call_count
    second.validate(f.session.context, auth_generation=1)
    assert first_http > 0 and len(f.c.http_calls) - before_http == 2 * first_http
    assert f.data[-1].transport.batch_get_documents.call_count == first_reads > 0
    assert len(f.listed) == f.admission.grants == 4
    assert first._metadata_transport is second._metadata_transport
    lifecycle = production_candidate_accounts()
    lifecycle.logout(f.session.context)
    assert lifecycle._metadata_transport is None
    assert len(f.listed) == 4  # Lifecycle retains its uncached Get path.
    lifecycle_grants = f.admission.grants
    assert lifecycle_grants > 4
    third = production_candidate_accounts()
    with pytest.raises(GuardDenied, match="revoked"):
        third.validate(f.session.context, auth_generation=1)
    assert len(f.listed) == 6 and f.admission.grants == lifecycle_grants + 2
    assert f.data[-1].transport.batch_get_documents.call_count > 0
    cohorts = third._metadata_transport.cohorts
    assert cohorts.rpcs == cohorts.clients == ()
    assert cohorts.pending == cohorts.active == 0 and cohorts.open is None


def test_eight_fresh_factory_bindings_share_presend_metadata_but_receive_only_their_own_rpc_keys(fresh_factories):
    f = fresh_factories
    accounts = [production_candidate_accounts() for _ in range(8)]
    batches = [batch(account) for account in accounts]
    rendezvous = Barrier(8)

    def read(index):
        rendezvous.wait(timeout=5)
        value = batches[index].identities()
        assert set(value) == {id(rpc) for rpc in batches[index].rpcs}
        return value

    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(read, range(8)))
    assert 1 <= len(f.listed) < 8 and f.admission.grants == len(f.listed)
    assert all(set(result).isdisjoint(results[0]) for result in results[1:])
    owner = accounts[0]._metadata_transport
    assert all(account._metadata_transport is owner for account in accounts)
    assert owner.cohorts.rpcs == owner.cohorts.clients == ()
    assert owner.cohorts.pending == owner.cohorts.active == 0 and owner.cohorts.open is None


def test_after_send_new_factory_cannot_join_an_older_inflight_metadata_observation(fresh_factories):
    f = fresh_factories
    first, second = batch(production_candidate_accounts()), batch(production_candidate_accounts())
    sent, later, release = Event(), Event(), Event()
    original = first.admin.transport.list_databases.side_effect

    def held(request, **kwargs):
        value = original(request, **kwargs)
        (sent if len(f.listed) == 1 else later).set()
        assert release.wait(timeout=5)
        return value

    first.admin.transport.list_databases.side_effect = held
    with ThreadPoolExecutor(max_workers=2) as workers:
        a = workers.submit(first.identities)
        assert sent.wait(timeout=5)
        b = workers.submit(second.identities)
        assert later.wait(timeout=5)
        release.set()
        assert set(a.result(timeout=5)) == {id(rpc) for rpc in first.rpcs}
        assert set(b.result(timeout=5)) == {id(rpc) for rpc in second.rpcs}
    assert len(f.listed) == f.admission.grants == 2


@pytest.mark.parametrize("change", ["principal", "scopes", "quota", "credentials", "host", "environment", "configuration", "pin"])
def test_changed_process_binding_refuses_before_another_metadata_rpc(fresh_factories, monkeypatch, change):
    f = fresh_factories
    original = production_candidate_accounts()
    current = batch(original)
    owner = original._metadata_transport
    if change == "principal":
        monkeypatch.setattr(owner.credentials, "_service_account_email", "other@hirewiz-local-authority.iam.gserviceaccount.com")
    elif change == "scopes":
        monkeypatch.setattr(owner.credentials, "_scopes", ["https://www.googleapis.com/auth/firestore"])
    elif change == "quota":
        monkeypatch.setattr(owner.credentials, "_quota_project_id", "another-project")
    elif change == "credentials":
        monkeypatch.setattr(owner.transport, "_credentials", ComputeCredentials(service_account_email=PRINCIPAL, scopes=SCOPES))
    elif change == "host":
        monkeypatch.setattr(owner.transport, "host", "other.invalid:443")
    elif change == "environment":
        monkeypatch.setenv("GOOGLE_CLOUD_QUOTA_PROJECT", "another-project")
    elif change == "configuration":
        different = f.c.config.model_copy(update={"epoch_generation": 2})
        with pytest.raises(GuardUnavailable) as failure:
            metadata.process_metadata_transport(different)
        assert type(failure.value) is GuardUnavailable
        assert f.listed == []
        return
    else:
        current.pins = (current.pins[0].model_copy(update={"database_uid": uuid4()}), current.pins[1])
    with pytest.raises(GuardUnavailable) as failure:
        current.identities()
    assert type(failure.value) is GuardUnavailable
    assert f.listed == [] and f.admission.grants == 0


def test_same_credentials_normal_token_refresh_is_not_an_identity_change(fresh_factories):
    f = fresh_factories
    owner = metadata.process_metadata_transport(f.c.config)
    owner.credentials.token = "synthetic-token-never-provider-access"
    owner.credentials.expiry = datetime.now(UTC) + timedelta(minutes=30)
    owner.guard()
    assert metadata.process_metadata_transport(f.c.config) is owner
    assert f.listed == []


@pytest.mark.parametrize("wrong", [{"candidate_id": 2}, {"account_binding_id": uuid4()}])
def test_wrong_context_cannot_borrow_a_shared_metadata_success(fresh_factories, wrong):
    f = fresh_factories
    denied = production_candidate_accounts()
    context = f.session.context.model_copy(update=wrong)
    with pytest.raises(GuardDenied):
        denied.validate(context, auth_generation=1)
    valid = production_candidate_accounts()
    valid.validate(f.session.context, auth_generation=1)
    assert denied is not valid and denied._instance is not valid._instance
    assert denied._metadata_transport is valid._metadata_transport
    assert len(f.listed) == f.admission.grants == 4


@pytest.mark.parametrize("fault", ["uid", "unknown"])
def test_unknown_or_replaced_metadata_remains_unavailable_not_a_capacity_refusal(fresh_factories, fault):
    current = batch(production_candidate_accounts())
    original = current.admin.transport.list_databases.side_effect

    def changed(request, **kwargs):
        value = original(request, **kwargs)
        if fault == "unknown":
            raise RuntimeError("synthetic-unknown")
        value.databases[0].uid = str(uuid4())
        return value

    current.admin.transport.list_databases.side_effect = changed
    with pytest.raises(GuardUnavailable) as failure:
        current.identities()
    assert type(failure.value) is GuardUnavailable
    assert current.cohorts.snapshot()["candidate_admission_rejections"] == 0


def test_fork_change_refuses_before_touching_an_inherited_lock(fresh_factories, monkeypatch):
    f = fresh_factories
    owner = metadata.process_metadata_transport(f.c.config)

    class NoInheritedLock:
        def __enter__(self):
            pytest.fail("Fork must be refused before acquiring the inherited pool lock")

    monkeypatch.setattr(metadata, "_lock", NoInheritedLock())
    monkeypatch.setattr(metadata.os, "getpid", lambda: owner.pid + 1)
    with pytest.raises(GuardUnavailable):
        metadata.process_metadata_transport(f.c.config)
    with pytest.raises(GuardUnavailable):
        owner.guard()
    assert f.listed == []


@pytest.mark.parametrize("resolve", [True, False])
def test_unresolved_default_principal_is_resolved_on_same_credentials_before_publish(fresh_factories, monkeypatch, resolve):
    import google.auth.transport.requests

    f = fresh_factories
    original = f.c.clients[1].side_effect
    refreshes, requests_seen = [], []

    def default_admin():
        admin = original()
        admin.transport._credentials = ComputeCredentials(service_account_email="default", scopes=SCOPES)
        return admin

    class InterceptedRequest:
        def __init__(self):
            self.session = SimpleNamespace(trust_env=True, close=lambda: None)

        def __call__(self, *_args, **kwargs):
            requests_seen.append(kwargs)
            assert self.session.trust_env is False
            return SimpleNamespace(status=200)

    def refresh(credentials, request):
        assert metadata._state is None
        refreshes.append(credentials)
        request("http://metadata.google.internal/synthetic-only", timeout=99, allow_redirects=True)
        if resolve:
            credentials._service_account_email = PRINCIPAL

    f.c.clients[1].side_effect = default_admin
    monkeypatch.setattr(google.auth.transport.requests, "Request", InterceptedRequest)
    monkeypatch.setattr(ComputeCredentials, "refresh", refresh)
    if resolve:
        owner = metadata.process_metadata_transport(f.c.config)
        assert owner.credentials is refreshes[0] and owner.credentials.service_account_email == PRINCIPAL
        assert metadata._state is owner
    else:
        with pytest.raises(GuardUnavailable):
            metadata.process_metadata_transport(f.c.config)
        assert metadata._state is None
        f.admins[0].transport.close.assert_called_once()
    assert len(requests_seen) == 1
    assert 0 < requests_seen[0]["timeout"] <= 2.0
    assert requests_seen[0]["allow_redirects"] is False
    assert len(refreshes) == 1 and f.listed == []


def initial_resolution_transport(monkeypatch, *, fault=None, ticks=None):
    """Run installed ComputeCredentials.refresh with every Request intercepted."""
    import google.auth._agent_identity_utils
    import google.auth._exponential_backoff
    import google.auth.transport.requests
    from google.auth.exceptions import TransportError

    calls, closed = [], []
    if ticks is None:
        now = [0.0]
        monkeypatch.setattr(metadata, "monotonic", lambda: now[0])
    else:
        clock = iter(ticks)
        monkeypatch.setattr(metadata, "monotonic", lambda: next(clock))
    monkeypatch.setattr(google.auth._agent_identity_utils, "get_and_parse_agent_identity_certificate", lambda: None)

    def no_backoff(_seconds):
        pytest.fail("Initial metadata resolution must not enter installed SDK backoff")

    monkeypatch.setattr(google.auth._exponential_backoff.time, "sleep", no_backoff)

    class InterceptedRequest:
        def __init__(self):
            self.session = SimpleNamespace(trust_env=True, close=lambda: closed.append(True))

        def __call__(self, **kwargs):
            assert self.session.trust_env is False
            assert kwargs["method"] == "GET" and kwargs["allow_redirects"] is False
            assert 0 < kwargs["timeout"] <= 2.0
            calls.append(kwargs)
            if fault == "transport":
                raise TransportError("synthetic-initial-resolution-only")
            if fault == "non200":
                return SimpleNamespace(status=503, data=b"synthetic", headers={})
            value = ({"access_token": "synthetic-token-never-provider-access", "expires_in": 3600}
                     if "/token" in kwargs["url"] else {"email": PRINCIPAL, "scopes": SCOPES})
            if ticks is None:
                now[0] += 0.25
            return SimpleNamespace(status=200, data=json.dumps(value).encode(),
                                   headers={"content-type": "application/json"})

    monkeypatch.setattr(google.auth.transport.requests, "Request", InterceptedRequest)
    credential = ComputeCredentials(service_account_email="default", scopes=SCOPES,
                                    universe_domain="googleapis.com")
    return credential, calls, closed


def test_installed_compute_refresh_resolves_info_and_token_once_with_one_total_deadline(monkeypatch):
    credential, calls, closed = initial_resolution_transport(monkeypatch)
    metadata._resolve_principal(credential)
    assert credential.service_account_email == PRINCIPAL
    assert credential.token == "synthetic-token-never-provider-access"
    assert len(calls) == 2 and closed == [True]
    assert "/instance/service-accounts/default/" in calls[0]["url"]
    assert "/instance/service-accounts/default/token" in calls[1]["url"]
    assert [call["timeout"] for call in calls] == [2.0, 1.75]


@pytest.mark.parametrize("fault", ["non200", "transport"])
def test_installed_compute_failure_cannot_retry_or_sleep_after_first_send(monkeypatch, fault):
    credential, calls, closed = initial_resolution_transport(monkeypatch, fault=fault)
    with pytest.raises(GuardUnavailable) as failure:
        metadata._resolve_principal(credential)
    assert type(failure.value) is GuardUnavailable
    assert len(calls) == 1 and closed == [True]
    assert metadata._state is None


@pytest.mark.parametrize("ticks,expected_sends", [
    ([0.0, 0.0, 0.5, 2.0], 1),  # Fresh info; deadline is exhausted before token send.
    ([0.0, 0.0, 2.0], 1),  # A late HTTP200 cannot authorize a later send.
    ([0.0, 0.0, 0.25, 0.25, 0.5, 2.0], 2),  # Post-refresh deadline check also refuses.
])
def test_installed_refresh_exhausted_deadline_never_sends_later_work(monkeypatch, ticks, expected_sends):
    credential, calls, closed = initial_resolution_transport(monkeypatch, ticks=ticks)
    with pytest.raises(GuardUnavailable) as failure:
        metadata._resolve_principal(credential)
    assert type(failure.value) is GuardUnavailable
    assert len(calls) == expected_sends and closed == [True]
    assert metadata._state is None


def test_third_callback_after_installed_info_and_token_refresh_is_refused_before_request(monkeypatch):
    credential, calls, closed = initial_resolution_transport(monkeypatch)
    installed_refresh = ComputeCredentials.refresh

    def extra_callback(self, request):
        installed_refresh(self, request)
        request(url="http://metadata.google.internal/synthetic-third-callback", method="GET")

    monkeypatch.setattr(ComputeCredentials, "refresh", extra_callback)
    with pytest.raises(GuardUnavailable) as failure:
        metadata._resolve_principal(credential)
    assert type(failure.value) is GuardUnavailable
    assert len(calls) == 2 and closed == [True]
    assert metadata._state is None


@pytest.mark.parametrize("bound", ["MAX_PENDING", "MAX_COHORTS", "MAX_COHORT_SIZE"])
def test_known_local_capacity_rejection_has_429_class_and_only_admission_counter(fresh_factories, monkeypatch, bound):
    from app.domains.recovery import native_identity_cohorts

    f = fresh_factories
    current = batch(production_candidate_accounts())
    cohorts = current.cohorts
    monkeypatch.setattr(native_identity_cohorts, bound, 0)
    if bound == "MAX_COHORT_SIZE":
        cohorts.open = native_identity_cohorts._Cohort(0.0)
    with pytest.raises(MetadataCapacityUnavailable):
        current.identities()
    measured = cohorts.snapshot()
    assert measured["candidate_admission_rejections"] == 1
    assert measured["candidate_barriers_failed"] == 1
    assert f.listed == [] and f.admission.grants == 0
