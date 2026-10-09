"""Protected attempt + actual localhost native admission, synthetic identities.

Imports the explicit anonymous SDK/unique-database fixture. Storage is the real
pinned SDK against intercepted HTTP; no cloud IAM/retention/restore proof.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from backend.tests.fixtures.pairing_authority import EXTENSION, RELEASE, jwk
from google.api_core.exceptions import Aborted
from test_gcp_journal_sdk import response
from test_gcp_pairing_emulator import case as case
from test_gcp_pairing_emulator import is_admission

from app.domains.recovery.contracts import fingerprint
from app.domains.recovery.gcp_contracts import AmbiguousCommit
from app.domains.recovery.gcp_pairing import GcpPairingCoordinator
from app.domains.recovery.gcp_pairing_contracts import PairingInvocationClaim
from app.domains.recovery.store import GuardDenied, GuardUnavailable


class ProcessLoss(BaseException):
    """Simulated crash deliberately bypasses service exception recovery."""


def prepared_plan(case):
    parameters = {"key": jwk(case.device_key), "extension_id": EXTENSION, "revision": RELEASE}
    plan = case.coordinator.allocate("prepare_request", parameters)
    case.http.allow(plan.intent)
    return plan, parameters


def issued_replica(case, plan, parameters, monkeypatch):
    """Two legitimate server issuers with identical logical IDs, no plan loader.

    This synthetic allocation collision exercises the native/GCS gates rather
    than letting the process-local issuer check stand in for cross-replica safety.
    """
    coordinator = GcpPairingCoordinator(case.registry, case.journal, case.pin, epoch_generation=1,
        fence=case.fence, attempts=case.attempts, now_ms=lambda: case.now)
    sequence = iter((*plan._allocated_ids, plan.intent.operation_id))
    with monkeypatch.context() as allocation:
        allocation.setattr("app.domains.recovery.gcp_pairing.uuid4", lambda: next(sequence))
        duplicate = coordinator.allocate(plan.intent.command, parameters)
    assert duplicate.intent == plan.intent and duplicate._issuer is not plan._issuer
    return coordinator, duplicate


def marker_post(method, kwargs):
    return method == "POST" and b'"kind":"pairing_attempt_consumption"' in kwargs.get("data", b"")


def marker_objects(case):
    return {path: value for path, value in case.http.objects.items() if path.startswith("authority-pairing-attempts/")}


def test_exact_acknowledged_marker_and_native_claim_bind_core_receipt(case):
    plan, parameters = prepared_plan(case)
    result = case.coordinator.execute(plan, parameters)
    assert result.status == "COMMITTED" and result.result is not None
    claim = PairingInvocationClaim.model_validate(case.registry.read("pairing_invocations", str(plan.intent.operation_id)))
    assert claim.matches(plan.intent, result.journal)
    assert claim.attempt_id != claim.operation_id
    assert len(case.commits) == 2 and is_admission(case.commits[0]) and not is_admission(case.commits[1])
    assert case.commits[0][0].version is None  # Actual RPC exists=False precondition.
    operation = case.registry.read("pairing_operations", str(plan.intent.operation_id))
    assert operation["invocation_sha256"] == fingerprint(claim.model_dump(mode="json"))
    stored = next(iter(marker_objects(case).values()))[0]
    assert stored == case.attempts._bytes(claim.attempt_marker())[1]
    assert set(json.loads(stored)) == {"version", "kind", "operation_id", "attempt_id", "pin",
                                      "epoch_generation", "intent_sha256", "journal"}
    assert result.result["nonce"].encode() not in stored
    assert b'"signature"' not in stored and b'"public_key"' not in stored and b'"envelope"' not in stored
    assert case.coordinator.status(plan.intent).status == "COMMITTED"


def test_existing_native_claim_never_substitutes_own_admission_ack(case):
    plan, parameters = prepared_plan(case)
    assert case.coordinator.execute(plan, parameters).status == "COMMITTED"
    claim = PairingInvocationClaim.model_validate(case.registry.read("pairing_invocations", str(plan.intent.operation_id)))
    before = len(case.commits)
    assert case.coordinator._admit(plan.intent, claim) is False
    assert len(case.commits) == before
    different = claim.model_copy(update={"intent_sha256": "f" * 64})
    with pytest.raises(GuardDenied, match="exact current intent"):
        case.coordinator._admit(plan.intent, different)
    assert len(case.commits) == before


def test_concurrent_replicas_share_one_protected_attempt_and_one_core_mutation(case, monkeypatch):
    plan, parameters = prepared_plan(case)
    replica, duplicate = issued_replica(case, plan, parameters, monkeypatch)
    barrier = Barrier(2)
    original = case.http.request
    def concurrent_upload(method, url, **kwargs):
        if method == "POST" and not marker_post(method, kwargs):
            barrier.wait(timeout=4.0)
        return original(method, url, **kwargs)
    monkeypatch.setattr(case.attempts.bucket.client._http.request, "side_effect", concurrent_upload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        left = pool.submit(case.coordinator.execute, plan, parameters)
        right = pool.submit(replica.execute, duplicate, parameters)
        outcomes = (left.result(timeout=10.0), right.result(timeout=10.0))
    assert sum(outcome.result is not None for outcome in outcomes) == 1
    assert all(outcome.result is not None or outcome.journal is None or outcome.status == "COMMITTED" for outcome in outcomes)
    assert len(marker_objects(case)) == 1
    assert len(case.commits) == 2 and sum(map(is_admission, case.commits)) == 1
    assert case.claims.calls == 0 and not case.active.any()


@pytest.mark.parametrize("retained", [False, True])
def test_ambiguous_native_admission_never_dispatches_or_reclaims_even_when_row_absent(case, monkeypatch, retained):
    plan, parameters = prepared_plan(case)
    replica, duplicate = issued_replica(case, plan, parameters, monkeypatch)
    real_commit = case.rpc.commit
    attempts = []
    def lost_ack(transaction, writes):
        assert is_admission(writes), "Core must never dispatch after unknown admission"
        attempts.append(writes)
        if retained:
            real_commit(transaction, writes)
        else:
            case.active.discard(transaction)
        raise AmbiguousCommit("Synthetic admission acknowledgement loss")
    monkeypatch.setattr(case.rpc, "commit", lost_ack)
    initial = case.coordinator.execute(plan, parameters)
    assert initial.status == "UNKNOWN" and initial.result is initial.journal is None
    assert (case.registry.read("pairing_invocations", str(plan.intent.operation_id)) is not None) is retained
    assert case.registry.read("pairing_operations", str(plan.intent.operation_id)) is None
    assert len(marker_objects(case)) == 1
    before = len(case.commits)
    monkeypatch.setattr(case.rpc, "commit", real_commit)
    for coordinator, candidate in ((case.coordinator, replace(plan)), (replica, duplicate)):
        result = coordinator.execute(candidate, parameters)
        assert result.status == "UNKNOWN" and result.result is result.journal is None
    assert len(attempts) == 1 and len(case.commits) == before and case.claims.calls == 0


@pytest.mark.parametrize("fault", ["lost_ack", "missing_marker", "malformed_ack"])
def test_marker_ack_loss_or_malformed_reply_is_consumed_without_get_reconciliation(case, monkeypatch, fault):
    plan, parameters = prepared_plan(case)
    replica, duplicate = issued_replica(case, plan, parameters, monkeypatch)
    original = case.http.request
    marker_gets = []
    def lose_marker_reply(method, url, **kwargs):
        if method == "GET" and "authority-pairing-attempts" in url:
            marker_gets.append(url)
        if marker_post(method, kwargs) and fault == "missing_marker":
            raise TimeoutError("Synthetic marker timeout with no retained object")
        result = original(method, url, **kwargs)
        if marker_post(method, kwargs):
            if fault == "lost_ack":
                raise TimeoutError("Synthetic marker response loss after retained upload")
            return response(b'{}')
        return result
    monkeypatch.setattr(case.attempts.bucket.client._http.request, "side_effect", lose_marker_reply)
    result = case.coordinator.execute(plan, parameters)
    assert result.status == "UNKNOWN" and result.result is result.journal is None
    assert len(marker_objects(case)) == (0 if fault == "missing_marker" else 1) and case.commits == []
    assert marker_gets == []  # Never reconcile/adopt a marker upload error.
    monkeypatch.setattr(case.attempts.bucket.client._http.request, "side_effect", original)
    repeated = replica.execute(duplicate, parameters)
    assert repeated.status == "UNKNOWN" and repeated.result is repeated.journal is None
    assert case.commits == [] and case.claims.calls == 0


@pytest.mark.parametrize("retained", [False, True])
def test_first_original_upload_ambiguity_has_no_possible_core_and_precise_persistence_boundary(case, monkeypatch, retained):
    plan, parameters = prepared_plan(case)
    replica, duplicate = issued_replica(case, plan, parameters, monkeypatch)
    original = case.http.request
    def unknown_intent(method, url, **kwargs):
        if method == "POST" and not marker_post(method, kwargs):
            if retained:
                original(method, url, **kwargs)
            raise TimeoutError("Synthetic original-intent upload ambiguity")
        return original(method, url, **kwargs)
    monkeypatch.setattr(case.attempts.bucket.client._http.request, "side_effect", unknown_intent)
    initial = case.coordinator.execute(plan, parameters)
    assert initial.status == "UNKNOWN" and initial.result is initial.journal is None
    assert len(case.http.objects) == int(retained) and not marker_objects(case) and case.commits == []
    before = len(case.http.calls)
    assert case.coordinator.execute(replace(plan), parameters).status == "UNKNOWN"
    assert len(case.http.calls) == before  # The ambiguous local plan is never retried.
    monkeypatch.setattr(case.attempts.bucket.client._http.request, "side_effect", original)
    later = replica.execute(duplicate, parameters)
    if retained:
        assert later.status == "UNKNOWN" and later.result is None and case.commits == []
    else:
        # No durable original intent existed and no marker/native send was ever
        # possible; this proves a safe first dispatch, not permanent consumption.
        assert later.status == "COMMITTED" and later.result is not None and len(case.commits) == 2


@pytest.mark.parametrize("point", ["after_marker", "after_admission"])
def test_process_loss_after_consumption_strands_operation_and_restart_never_dispatches(case, monkeypatch, point):
    plan, parameters = prepared_plan(case)
    replica, duplicate = issued_replica(case, plan, parameters, monkeypatch)
    def crash(*args, **kwargs):
        raise ProcessLoss("Synthetic process loss")
    if point == "after_marker":
        monkeypatch.setattr(case.coordinator, "_admit", crash)
    else:
        monkeypatch.setattr(case.coordinator.core, "prepare_request", crash)
    with pytest.raises(ProcessLoss):
        case.coordinator.execute(plan, parameters)
    assert len(marker_objects(case)) == 1
    assert len(case.commits) == (1 if point == "after_admission" else 0)
    assert all(map(is_admission, case.commits))
    assert case.coordinator.status(plan.intent).status == "UNKNOWN"
    with pytest.raises(GuardDenied, match="not issued"):
        replica.execute(plan, parameters)
    before = len(case.commits)
    result = replica.execute(duplicate, parameters)
    assert result.status == "UNKNOWN" and result.result is result.journal is None
    assert len(case.commits) == before and case.claims.calls == 0


def test_crash_after_original_intent_before_attempt_has_no_admission_or_output(case, monkeypatch):
    plan, parameters = prepared_plan(case)
    replica, duplicate = issued_replica(case, plan, parameters, monkeypatch)
    def crash(marker):
        raise ProcessLoss("Synthetic process loss before attempt marker")
    monkeypatch.setattr(case.attempts, "consume", crash)
    with pytest.raises(ProcessLoss):
        case.coordinator.execute(plan, parameters)
    assert len(case.http.objects) == 1 and not marker_objects(case)
    assert case.commits == [] and case.coordinator.status(plan.intent).status == "UNKNOWN"
    restarted = GcpPairingCoordinator(case.registry, case.journal, case.pin, epoch_generation=1,
        attempts=case.attempts, fence=case.fence, now_ms=lambda: case.now)
    with pytest.raises(GuardDenied, match="not issued"):
        restarted.execute(plan, parameters)
    # The positively retained original intent consumes cross-replica dispatch
    # even if the process died before trying to create its attempt marker.
    assert replica.execute(duplicate, parameters).status == "UNKNOWN"
    assert case.commits == [] and case.claims.calls == 0


@pytest.mark.parametrize("state", ["marker_only", "core_committed"])
def test_real_process_restart_can_read_public_status_but_never_reconstruct_output(case, monkeypatch, state):
    plan, parameters = prepared_plan(case)
    if state == "marker_only":
        def crash(*args):
            raise ProcessLoss("Synthetic crash after protected marker")
        monkeypatch.setattr(case.coordinator, "_admit", crash)
        with pytest.raises(ProcessLoss):
            case.coordinator.execute(plan, parameters)
    else:
        assert case.coordinator.execute(plan, parameters).status == "COMMITTED"
    # Only public, secret-free intent/marker bytes cross the process boundary.
    payload = {"intent": plan.intent.model_dump(mode="json"), "objects": {
        path: [base64.b64encode(raw).decode(), generation] for path, (raw, generation) in case.http.objects.items()}}
    code = '''
import base64,json,sys
from types import SimpleNamespace
from unittest.mock import MagicMock
import google.auth,grpc,requests
from google.auth.credentials import AnonymousCredentials
from google.cloud.firestore_v1.services.firestore import FirestoreClient
from google.cloud.firestore_v1.services.firestore.transports.grpc import FirestoreGrpcTransport
from google.cloud.storage import Client
from test_gcp_pairing_emulator import LocalTransactions,SyntheticFence,SyntheticJournalHttp,ENDPOINT
from app.domains.recovery.gcp_pairing import GcpPairingCoordinator
from app.domains.recovery.gcp_pairing_contracts import PairingJournalIntent
from app.domains.recovery.gcp_pairing_attempts import GcsPairingAttempts
from app.domains.recovery.gcp_rpc import FirestoreRpc
from app.domains.recovery.gcp_buffer import BufferedRegistry
from app.domains.recovery.gcp_journal import GcsJournal
def forbidden(*args,**kwargs): raise AssertionError('Credentials/cloud/POST forbidden')
google.auth.default=forbidden
grpc.secure_channel=forbidden
data=json.load(sys.stdin)
intent=PairingJournalIntent.model_validate(data['intent'])
assert intent.pin.database.startswith('projects/hirewiz-local-authority/databases/pairing-bridge-')
channel=grpc.insecure_channel(ENDPOINT)
client=FirestoreClient(transport=FirestoreGrpcTransport(host=ENDPOINT,channel=channel,credentials=AnonymousCredentials()))
active=LocalTransactions()
http=SyntheticJournalHttp(active)
http.objects={path:(base64.b64decode(item[0]),item[1]) for path,item in data['objects'].items()}
original=http.request
def readonly(method,url,**kwargs):
    if method!='GET': forbidden()
    return original(method,url,**kwargs)
session=MagicMock(spec=requests.Session)
session.is_mtls=False
session.request.side_effect=readonly
storage=Client(project='hirewiz-local-authority',credentials=AnonymousCredentials(),_http=session)
try:
    registry=BufferedRegistry(FirestoreRpc(client,SimpleNamespace(),database_resource=intent.pin.database,rpc_timeout=5.0))
    bucket=storage.bucket('synthetic-pairing-journal')
    coordinator=GcpPairingCoordinator(registry,GcsJournal(bucket),intent.pin,epoch_generation=intent.epoch_generation,
        attempts=GcsPairingAttempts(bucket),fence=SyntheticFence(active,intent.pin),now_ms=lambda:intent.created_at_ms)
    status=coordinator.status(intent)
    print(json.dumps({'status':status.status,'has_result':hasattr(status,'result'),
        'storage_posts':sum(method=='POST' for method,_ in http.calls)}))
finally:
    storage.close()
    channel.close()
'''
    backend = Path(__file__).resolve().parents[1]
    child = subprocess.run([sys.executable, "-B", "-c", code], input=json.dumps(payload), text=True,
        capture_output=True, timeout=15, cwd=backend, env={"PATH": os.defpath,
        "PYTHONPATH": os.pathsep.join((str(backend.parent), str(backend / "tests"))), "PYTHONDONTWRITEBYTECODE": "1",
        "DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA": "true",
        "HIREWIZ_AUTHORITY_EMULATOR_PORT": os.environ.get("HIREWIZ_AUTHORITY_EMULATOR_PORT", "58877")})
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == {"status": "COMMITTED" if state == "core_committed" else "UNKNOWN",
                                        "has_result": False, "storage_posts": 0}


def test_native_admission_definite_abort_reuses_exact_claim_and_rechecks_fence(case, monkeypatch):
    plan, parameters = prepared_plan(case)
    real_commit, attempts = case.rpc.commit, []
    def abort_once(transaction, writes):
        if is_admission(writes):
            attempts.append((writes[0].raw, case.fence.calls))
            if len(attempts) == 1:
                raise Aborted("Synthetic definite admission abort")
        return real_commit(transaction, writes)
    monkeypatch.setattr(case.rpc, "commit", abort_once)
    result = case.coordinator.execute(plan, parameters)
    assert result.status == "COMMITTED" and result.result is not None
    assert len(attempts) == 2 and attempts[0][0] == attempts[1][0]
    assert attempts[1][1] > attempts[0][1]  # Fresh independent fence before retry Begin.
    assert len(case.commits) == 2


def test_admission_abort_budget_cannot_reclaim_marker_or_dispatch_on_new_replica(case, monkeypatch):
    plan, parameters = prepared_plan(case)
    replica, duplicate = issued_replica(case, plan, parameters, monkeypatch)
    real_commit, attempts = case.rpc.commit, []
    def abort(transaction, writes):
        assert is_admission(writes)
        attempts.append(writes[0].raw)
        raise Aborted("Synthetic definite admission abort")
    monkeypatch.setattr(case.rpc, "commit", abort)
    with pytest.raises(GuardUnavailable, match="retry budget"):
        case.coordinator.execute(plan, parameters)
    assert len(attempts) == 3 and len(set(attempts)) == 1 and case.commits == []
    monkeypatch.setattr(case.rpc, "commit", real_commit)
    result = replica.execute(duplicate, parameters)
    assert result.status == "UNKNOWN" and result.result is None and case.commits == []


@pytest.mark.parametrize("fault", ["fence", "expiry", "claim", "control"])
def test_after_admission_change_blocks_first_core_mutation_and_output(case, monkeypatch, fault):
    plan, parameters = prepared_plan(case)
    real_commit = case.rpc.commit
    def changed_after_ack(transaction, writes):
        result = real_commit(transaction, writes)
        if is_admission(writes):
            if fault == "fence":
                case.fence.closed = True
            elif fault == "expiry":
                case.now = plan.intent.deadline_ms
            elif fault == "claim":
                claim = case.registry.read("pairing_invocations", str(plan.intent.operation_id))
                case.operator_replace("pairing_invocations", str(plan.intent.operation_id), {**claim, "attempt_id": str(uuid4())})
            else:
                case.operator_replace("pairing_control", "current", {})
        return result
    monkeypatch.setattr(case.rpc, "commit", changed_after_ack)
    if fault in {"fence", "expiry"}:
        result = case.coordinator.execute(plan, parameters)
        assert result.status == "UNKNOWN" and result.result is result.journal is None
    else:
        with pytest.raises(GuardUnavailable):
            case.coordinator.execute(plan, parameters)
    assert len(case.commits) == 1 and is_admission(case.commits[0]) and case.claims.calls == 0
    assert case.registry.read("pairing_operations", str(plan.intent.operation_id)) is None


def test_receipt_status_requires_original_claim_and_marker_generation(case):
    plan, parameters = prepared_plan(case)
    result = case.coordinator.execute(plan, parameters)
    assert result.status == "COMMITTED"
    path, (raw, generation) = next(iter(marker_objects(case).items()))
    case.http.objects[path] = (raw, str(int(generation) + 1))
    assert case.coordinator.status(plan.intent).status == "UNKNOWN"
    case.http.objects[path] = (raw, generation)
    retained = case.registry.read("pairing_invocations", str(plan.intent.operation_id))
    case.operator_replace("pairing_invocations", str(plan.intent.operation_id), {**retained, "attempt_id": str(uuid4())})
    assert case.coordinator.status(plan.intent).status == "UNKNOWN"
    assert case.claims.calls == 0


def test_changed_claim_after_core_definite_abort_blocks_retry(case, monkeypatch):
    plan, parameters = prepared_plan(case)
    real_commit, core_attempts = case.rpc.commit, []
    def abort_and_change_claim(transaction, writes):
        if is_admission(writes):
            return real_commit(transaction, writes)
        core_attempts.append(writes)
        case.rpc.rollback(transaction)  # Release only this definite-abort fixture transaction.
        claim = case.registry.read("pairing_invocations", str(plan.intent.operation_id))
        case.operator_replace("pairing_invocations", str(plan.intent.operation_id), {**claim, "attempt_id": str(uuid4())})
        raise Aborted("Synthetic definite core abort with changed current claim")
    monkeypatch.setattr(case.rpc, "commit", abort_and_change_claim)
    with pytest.raises(GuardUnavailable, match="claim"):
        case.coordinator.execute(plan, parameters)
    assert len(core_attempts) == 1 and len(case.commits) == 1 and is_admission(case.commits[0])
    assert case.registry.read("pairing_operations", str(plan.intent.operation_id)) is None
    assert case.claims.calls == 0


def test_unavailable_attempt_store_never_allows_native_admission(case):
    plan, parameters = prepared_plan(case)
    unavailable = GcpPairingCoordinator(case.registry, case.journal, case.pin, epoch_generation=1,
        fence=case.fence, now_ms=lambda: case.now)
    own = unavailable.allocate("prepare_request", parameters)
    case.http.allow(own.intent)
    with pytest.raises(GuardUnavailable, match="attempt configuration"):
        unavailable.execute(own, parameters)
    assert case.commits == [] and not marker_objects(case)
