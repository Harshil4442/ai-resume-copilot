"""Explicit loopback socket fixture: actual PG, native issuance and V3 activation.

No candidate, subject event or session is seeded. Only synthetic operator pins,
controls and an exact test extension/release are installed in unique emulator
databases. Storage HTTP and admin UID custody remain synthetic, as labelled in
the metadata. Import requires explicit test-only configuration; shipping
production factories are unchanged.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import urlsplit
from uuid import uuid4

import google.auth
import grpc
import httpx
import requests
from cryptography.hazmat.primitives.asymmetric import ec
from google.auth.credentials import AnonymousCredentials
from google.cloud.firestore_v1.services.firestore import FirestoreClient
from google.cloud.firestore_v1.services.firestore.transports.grpc import FirestoreGrpcTransport
from google.cloud.firestore_v1.types import Document, Write
from google.cloud.storage import Client
from sqlalchemy.engine import make_url

if os.getenv("HIREWIZ_CANDIDATE_SOCKET_FIXTURE") != "1" or os.getenv("APP_ENV") != "test":
    raise RuntimeError("Explicit local candidate socket fixture configuration required")
os.environ["DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA"] = "true"

def forbidden_custody(*args, **kwargs):
    raise AssertionError("Ambient credentials or secure cloud transport are forbidden in this local fixture")
google.auth.default = forbidden_custody
grpc.secure_channel = forbidden_custody
# Explicit constructor custody remains loopback-only. Synthetic Storage HTTP is
# separately mounted, never an authenticated external HTTP client.
original_requests = requests.Session.request
original_httpx = httpx.Client.send
original_async_httpx = httpx.AsyncClient.send

def local_requests(self, method, url, *args, **kwargs):
    if urlsplit(str(url)).hostname != "127.0.0.1":
        raise AssertionError("External HTTP is forbidden in the candidate socket fixture")
    return original_requests(self, method, url, *args, **kwargs)

def local_httpx(self, request, *args, **kwargs):
    if request.url.host != "127.0.0.1":
        raise AssertionError("External HTTP is forbidden in the candidate socket fixture")
    return original_httpx(self, request, *args, **kwargs)

async def local_async_httpx(self, request, *args, **kwargs):
    if request.url.host != "127.0.0.1":
        raise AssertionError("External HTTP is forbidden in the candidate socket fixture")
    return await original_async_httpx(self, request, *args, **kwargs)
requests.Session.request = local_requests
httpx.Client.send = local_httpx
httpx.AsyncClient.send = local_async_httpx
tests = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(tests))
from backend.tests.fixtures.firestore_emulator import local_firestore_endpoint  # noqa: E402
from backend.tests.fixtures.gcp_partitioned_publication import partitioned_for  # noqa: E402
from backend.tests.fixtures.pairing_authority import jwk, sign  # noqa: E402
from test_gcp_pairing_emulator import (  # noqa: E402
    FixtureClaims,
    LocalTransactions,
    SyntheticFence,
    SyntheticJournalHttp,
)

from app import database, security  # noqa: E402
from app.domains.candidate_accounts import service as candidate_service  # noqa: E402
from app.domains.candidate_ingress import replay as ingress_replay  # noqa: E402
from backend.tests.fixtures.candidate_ingress import create as create_ingress  # noqa: E402
from app.domains.candidate_accounts.retention import NativeCandidateLifetimeRetention  # noqa: E402
from app.domains.candidate_accounts.service import CandidateAccountService  # noqa: E402
from app.domains.recovery.browser_pairing import GatewayVerifier, NativeBrowserPairing  # noqa: E402
from app.domains.recovery.contracts import canonical  # noqa: E402
from app.domains.recovery.gcp_buffer import BufferedRegistry, BufferedTransaction  # noqa: E402
from app.domains.recovery.gcp_candidate_lifetimes import (  # noqa: E402
    GcpProtectedCandidateLifetimes,  # noqa: E402
)
from app.domains.recovery.gcp_candidate_logout import GcpCandidateCookieLogout  # noqa: E402
from app.domains.recovery.gcp_contracts import AmbiguousCommit, RegistryPin  # noqa: E402
from app.domains.recovery.gcp_journal import GcsJournal  # noqa: E402
from app.domains.recovery.gcp_pairing import (  # noqa: E402
    GcpPairingCoordinator,
    pairing_control_record,
)
from app.domains.recovery.gcp_pairing_attempts import GcsPairingAttempts  # noqa: E402
from app.domains.recovery.gcp_password import GcpPasswordCandidateBoundary  # noqa: E402
from app.domains.recovery.gcp_password_lifetime import GcpPasswordLifetimeCoordinator  # noqa: E402
from app.domains.recovery.gcp_password_lifetime_journal import (  # noqa: E402
    GcsPasswordLifetimeJournal,  # noqa: E402
)
from app.domains.recovery.gcp_rpc import FirestoreRpc  # noqa: E402
from app.domains.recovery.gcp_scoped_authority import ScopedCandidateAuthority  # noqa: E402
from app.domains.recovery.gcp_service import control_record  # noqa: E402
from app.domains.recovery.pairing_auth import PinnedAssertions  # noqa: E402
from app.domains.recovery.password_reauth import SqlPasswordCredentials  # noqa: E402
from app.main import app  # noqa: E402
from app.routers import auth, candidate_accounts  # noqa: E402
from app.routers.v1 import browser_pairing  # noqa: E402

configured = make_url(database.DATABASE_URL)
if (configured.host != "127.0.0.1" or configured.port != 55433 or configured.database != "hirewiz_admission_test"
        or re.fullmatch(r"-csearch_path=candidate_web_[a-f0-9]{32} -clock_timeout=4000 -cstatement_timeout=10000", configured.query.get("options", "")) is None):
    raise RuntimeError("Only the uniquely owned local candidate PostgreSQL schema is permitted")
origin = os.environ["BROWSER_PAIRING_WEBSITE_ORIGIN"]
if urlsplit(origin).hostname != "127.0.0.1" or not origin.startswith("https://"):
    raise RuntimeError("Exact loopback HTTPS website origin required")
extension, release = os.environ["HIREWIZ_TEST_EXTENSION_ID"], os.environ["HIREWIZ_TEST_EXTENSION_REVISION"]
directory = Path(os.environ["HIREWIZ_CANDIDATE_FIXTURE_DIRECTORY"])
if not directory.is_dir() or directory.is_symlink() or directory.stat().st_uid != os.getuid():
    raise RuntimeError("An existing owned fixture directory is required")
database.engine.hide_parameters = True
def clock():
    return time.time_ns() // 1_000_000
endpoint = local_firestore_endpoint()
channel = grpc.insecure_channel(endpoint)
grpc.channel_ready_future(channel).result(timeout=3)
atexit.register(channel.close)
client = FirestoreClient(transport=FirestoreGrpcTransport(host=endpoint, channel=channel, credentials=AnonymousCredentials()))
pin = RegistryPin(database=f"projects/hirewiz-local-authority/databases/candidate-web-{uuid4().hex[:12]}",
    database_uid=uuid4(), authority_id=uuid4(), incarnation=1, epoch_id=uuid4())
rpc = FirestoreRpc(client, SimpleNamespace(), database_resource=pin.database, rpc_timeout=5)
registry = BufferedRegistry(rpc, deadline_seconds=10)
active = LocalTransactions()
real_begin, real_commit, real_rollback = rpc.begin, rpc.commit, rpc.rollback
def begin():
    tx = real_begin()
    active.add(tx)
    return tx
def commit(tx, writes):
    try:
        return real_commit(tx, writes)
    finally:
        active.discard(tx)
def rollback(tx):
    try:
        return real_rollback(tx)
    finally:
        active.discard(tx)
rpc.begin, rpc.commit, rpc.rollback = begin, commit, rollback
controls = [
    ("control", "meta", control_record(pin)),
    ("pairing_control", "current", pairing_control_record(pin, 1)),
    ("pairing_clock", "observed", {"now_ms": clock()}),
    ("head", "global", {"sequence": 0, "digest": "0" * 64}),
    ("pairing_extensions", f"{extension}:{release}", {
        "extension_id": extension, "executor_revision": release, "protocol_version": 2, "active": True}),
]
writes = [Write(update=Document(name=BufferedTransaction(rpc, b"path-only").path(namespace, key),
    fields={"body": {"bytes_value": canonical({"namespace": namespace, "key": key, "value": value}).encode()}}),
    current_document={"exists": False}) for namespace, key, value in controls]
reply = client.commit(request={"database": pin.database, "writes": writes}, retry=None, timeout=5)
if len(reply.write_results) != len(writes):
    raise RuntimeError("Owned synthetic operator controls lack their actual Commit acknowledgement")
http = SyntheticJournalHttp(active)
http_session = MagicMock(spec=requests.Session)
http_session.is_mtls = False
http_session.request.side_effect = http.request
storage = Client(project="hirewiz-local-authority", credentials=AnonymousCredentials(), _http=http_session)
atexit.register(storage.close)
# partitioned_for requires only this actual ordinary pin/bucket, no subject.
journal = GcsJournal(storage.bucket("synthetic-pairing-journal"))
case = SimpleNamespace(pin=pin, journal=journal, active=active, http=http)
publication = partitioned_for(case)
journal.publication = publication
lifetime_journal = GcsPasswordLifetimeJournal(journal.bucket, publication=publication)
create = lifetime_journal.create
def allow_lifetime(intent):
    path, raw = lifetime_journal._bytes(intent)
    http.allowed[path] = raw
    return create(intent)
lifetime_journal.create = allow_lifetime
fence = SyntheticFence(active, pin)
lifetime = GcpPasswordLifetimeCoordinator(registry, pin, epoch_generation=1,
    journal=lifetime_journal, fence=fence, now_ms=clock)
scoped = ScopedCandidateAuthority(publication, now_ms=clock)
ingress_case = create_ingress(origin=origin, action_pins=(pin, publication.resource.pin.authority.registry), clock=clock)
atexit.register(ingress_case.channel.close)
ingress_replay.production_candidate_ingress = lambda: ingress_case.ingress
accounts = CandidateAccountService(database.engine,
    NativeCandidateLifetimeRetention(lifetime, GcpProtectedCandidateLifetimes(scoped)),
    now_ms=clock, cookie_logout=GcpCandidateCookieLogout(scoped))

# Private metadata-only instrumentation around the actual service methods. No
# argument, result, credential or session is retained in this local evidence.
lifecycle_counts = {}
def counted(name, method):
    def call(*args, **kwargs):
        lifecycle_counts[name] = lifecycle_counts.get(name, 0) + 1
        (directory / "lifecycle-counts.json").write_text(json.dumps(lifecycle_counts, sort_keys=True))
        return method(*args, **kwargs)
    return call
for lifecycle_method in ("register", "login", "validate", "registration_status", "logout", "logout_cookie", "change_password", "prepare_delete"):
    setattr(accounts, lifecycle_method, counted(lifecycle_method, getattr(accounts, lifecycle_method)))
(directory / "lifecycle-counts.json").write_text("{}")
ingress_commit = ingress_case.rpc.commit
def ingress_fault_commit(tx, writes):
    fault = directory / "ingress-unknown"
    if fault.is_file():
        persisted = fault.read_text() == "persisted"
        fault.unlink()
        if persisted:
            ingress_commit(tx, writes)
        else:
            ingress_case.rpc.rollback(tx)
        raise AmbiguousCommit("Owned synthetic fixture lost ingress Commit acknowledgement")
    return ingress_commit(tx, writes)
ingress_case.rpc.commit = ingress_fault_commit

# Explicit fixture-only constructor injection; neither HTTP identities nor
# final activation callbacks are overridden or seeded.
auth.production_candidate_accounts = lambda: accounts
security.production_candidate_accounts = lambda: accounts
candidate_accounts.production_candidate_accounts = lambda: accounts
candidate_service.production_candidate_accounts = lambda: accounts
attempts = GcsPairingAttempts(journal.bucket, publication=publication)
write_intent = attempts.write_intent
def allow_pairing(intent):
    http.allow(intent)
    return write_intent(intent)
attempts.write_intent = allow_pairing
auth_key, claim_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
coordinator = GcpPairingCoordinator(registry, journal, pin, epoch_generation=1, fence=fence, attempts=attempts,
    assertions=PinnedAssertions(issuer="fixture_auth", public_key=jwk(auth_key)),
    claims=FixtureClaims(claim_key), now_ms=clock)
class FixtureSigner:
    def sign(self, payload):
        return {"payload": payload, "signature": sign(auth_key, "candidate-assertion", payload)}
credentials = SqlPasswordCredentials(database.engine, dummy_hash=security.hash_password("synthetic-fixture-dummy-password"))
atexit.register(credentials.close)
password = GcpPasswordCandidateBoundary(coordinator, scoped=scoped).service(
    credentials=credentials, signer=FixtureSigner(), issuer="fixture_auth")
native = NativeBrowserPairing(coordinator, password,
    GatewayVerifier(key=bytes.fromhex(os.environ["BROWSER_PAIRING_GATEWAY_SECRET"]), issuer="hirewiz_bff_v1", origin=origin))
app.dependency_overrides[browser_pairing.get_browser_pairing] = lambda: native

# Faults are one-use owned local files, never public fixture HTTP endpoints.
protected_commit = publication.registry.rpc.commit
def fault_commit(tx, writes):
    namespaces = {json.loads(write.raw)["namespace"] for write in writes}
    for name, namespace in (("activation-unknown", "v3_activations"), ("logout-unknown", "v3_pending_session_denials")):
        fault = directory / name
        if namespace in namespaces and fault.is_file():
            persisted = fault.read_text() == "persisted"
            fault.unlink()
            if persisted:
                protected_commit(tx, writes)
            else:
                publication.registry.rpc.rollback(tx)
            raise AmbiguousCommit("Owned synthetic fixture lost protected Commit acknowledgement")
    return protected_commit(tx, writes)
publication.registry.rpc.commit = fault_commit
metadata = {"ordinary_database": pin.database, "protected_database": publication.registry.rpc.database_resource,
    "private_ingress_database": ingress_case.resource.registry.database, "private_ingress_native_replay": True,
    "extension_id": extension, "executor_revision": release, "public_claim_jwk": jwk(claim_key),
    "claim_issuer": "hirewiz-pairing",
    "candidate_identities_seeded": 0, "actual_postgresql": True, "actual_native_v3": True,
    "synthetic_custody": ["operator UID witness", "Storage HTTP", "local signing custody", "private ingress operator UID/restore/clock witness"],
    "pairing_admission": "UNAVAILABLE_PENDING_REVIEWED_SAME_AUTHORITY_JOIN",
    "ambient_credentials_forbidden": True, "secure_cloud_transport_forbidden": True, "external_http_forbidden": True}
(directory / "metadata.json").write_text(json.dumps(metadata, sort_keys=True))
