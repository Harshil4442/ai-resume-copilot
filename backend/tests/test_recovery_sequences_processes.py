"""Spawned-process sequence/crash proofs against a separate local authority file.

Synthetic data only: no browser, employer, cloud, authentication or payment calls.
Application-only restoration retains authority evidence; authority-file rollback,
cloud journal retention and physical old-process egress are not proven here.
"""
from __future__ import annotations

import multiprocessing
import os
import sqlite3
from pathlib import Path
from uuid import UUID

import pytest
from backend.tests.fixtures.recovery_authority import FileAuthority
from backend.tests.test_recovery_authority import NOW
from backend.tests.test_recovery_sequences import approve, observation, sequence_case

from app.domains.recovery.contracts import AuthenticatedActor
from app.domains.recovery.sequence_contracts import Manifest, Observation, StepDecision
from app.domains.recovery.sequence_service import SequenceGuard, step_key
from app.domains.recovery.store import GuardDenied, GuardUnavailable

PROCESS_TIMEOUT = 15


def _command(ctx, operation, *, permit=None, manifest=None, actor=None, continuation=None,
             index=0, observed=None, event_id="process-outcome", decision_nonce=None):
    selected = manifest or ctx.manifest
    return {
        "path": str(ctx.store.path), "authority_id": str(ctx.store.authority_id),
        "actor": (actor or ctx.device).model_dump(mode="json"),
        "manifest": selected.model_dump(mode="json"), "operation": operation,
        "permit": permit, "index": index,
        "continuation": str(continuation) if continuation is not None else None,
        "observation": observed.model_dump(mode="json") if observed is not None else None,
        "event_id": event_id,
        "decision_nonce": str(decision_nonce) if decision_nonce is not None else None,
    }


def _perform(command, store):
    guard = SequenceGuard(store, lambda: NOW)
    actor = AuthenticatedActor.model_validate(command["actor"])
    manifest = Manifest.model_validate(command["manifest"])
    continuation = UUID(command["continuation"]) if command["continuation"] else None
    result = {"pid": os.getpid(), "operation": command["operation"], "may_act": False}
    if command["operation"] == "begin":
        permit = command["permit"]
        decision = guard.begin(actor, permit["id"], manifest.context_digest,
                               manifest.step_digest(permit["index"]), continuation)
        return {**result, "status": decision.status, "may_act": decision.may_act is not None,
                "decision": decision.model_dump(mode="json")}
    if command["operation"] == "prepare":
        permit = guard.prepare(actor, manifest.attempt_id, command["index"],
                               manifest.sequence_digest, continuation)
        return {**result, "status": permit["status"], "permit": permit}
    if command["operation"] == "outcome":
        nonce = UUID(command["decision_nonce"]) if command["decision_nonce"] else None
        outcome = guard.outcome(actor, manifest.attempt_id,
            Observation.model_validate(command["observation"]), command["event_id"], nonce)
        return {**result, **outcome.model_dump(mode="json")}
    if command["operation"] == "cancel":
        return {**result, **guard.cancel(actor, manifest.attempt_id, command["event_id"])}
    raise AssertionError("Unsupported synthetic process operation")


def _worker(command, ready, gate, output, completed, wait_for):
    store = FileAuthority(Path(command["path"]), UUID(command["authority_id"]))
    ready.put(os.getpid())
    try:
        assert gate.wait(PROCESS_TIMEOUT)
        if wait_for is not None:
            assert wait_for.wait(PROCESS_TIMEOUT)
        try:
            output.put(_perform(command, store))
        except (GuardDenied, GuardUnavailable) as exc:
            output.put({"pid": os.getpid(), "operation": command["operation"],
                        "status": type(exc).__name__, "may_act": False})
    finally:
        completed.set()


def _cleanup(processes):
    for process in processes:
        if process.pid is None:
            continue
        if process.is_alive():
            process.terminate()
        process.join(timeout=3)
        if process.is_alive():
            process.kill()
            process.join(timeout=3)
        assert not process.is_alive(), "Synthetic child process did not stop"


def _race(commands, *, ordered=False):
    context = multiprocessing.get_context("spawn")
    ready, output, gate = context.Queue(), context.Queue(), context.Event()
    completed = [context.Event() for _ in commands]
    processes = [context.Process(target=_worker, args=(command, ready, gate, output,
        completed[index], completed[index - 1] if ordered and index else None))
        for index, command in enumerate(commands)]
    try:
        for process in processes:
            process.start()
        pids = {ready.get(timeout=PROCESS_TIMEOUT) for _ in processes}
        assert len(pids) == len(processes) and os.getpid() not in pids
        gate.set()
        results = [output.get(timeout=PROCESS_TIMEOUT) for _ in processes]
        for process in processes:
            process.join(timeout=PROCESS_TIMEOUT)
            assert process.exitcode == 0
        assert {result["pid"] for result in results} == pids
        return results
    finally:
        _cleanup(processes)
        for queue in (ready, output):
            queue.cancel_join_thread()
            queue.close()


def _kill_after_commit(command):
    store = FileAuthority(Path(command["path"]), UUID(command["authority_id"]),
                          after_commit=lambda: os._exit(73))
    _perform(command, store)
    os._exit(99)  # A computed response must never reach the caller.


def _crash_after_commit(command):
    process = multiprocessing.get_context("spawn").Process(
        target=_kill_after_commit, args=(command,))
    try:
        process.start()
        process.join(timeout=PROCESS_TIMEOUT)
        assert process.exitcode == 73
    finally:
        _cleanup([process])


def _another_attempt(ctx, *, another_device=False):
    device = ctx.device
    updates = {"approval_id": "approval-two", "application_id": "application-two",
               "package_digest": "f" * 64}
    if another_device:
        device = ctx.device.model_copy(update={"actor_id": "device-actor-two",
            "device_id": "device-two", "key_sha256": "f" * 64})
        updates.update(device_id=device.device_id, device_key_sha256=device.key_sha256)
        binding = ctx.manifest.binding().model_copy(update={
            "device_id": device.device_id, "device_key_sha256": device.key_sha256})
        ctx.store.provision_fixture([device], binding)
    manifest = ctx.guard.seal(ctx.candidate, ctx.manifest.base.model_copy(update=updates),
                              ctx.manifest.steps)
    approve(ctx, manifest)
    permit = ctx.guard.prepare(device, manifest.attempt_id, 0, manifest.sequence_digest)
    return manifest, device, permit


def test_spawned_same_permit_has_one_begin_response(tmp_path):
    ctx = sequence_case(tmp_path / "authority.sqlite")
    permit = ctx.prepared()
    results = _race([_command(ctx, "begin", permit=permit) for _ in range(4)])
    assert sum(result["may_act"] for result in results) == 1
    assert sorted(result["status"] for result in results) == ["ALREADY_BEGUN"] * 3 + ["BEGUN"]
    status = ctx.guard.status(ctx.device, ctx.manifest.attempt_id)
    assert status["next_index"] == 0 and status["in_flight"] == 0
    assert status["steps"][0]["status"] == "BEGUN"


def test_spawned_separate_permits_for_one_step_have_one_winner(tmp_path):
    ctx = sequence_case(tmp_path / "authority.sqlite")
    permits = [ctx.prepared(), ctx.prepared()]
    assert permits[0]["id"] != permits[1]["id"]
    results = _race([_command(ctx, "begin", permit=permit) for permit in permits])
    assert sum(result["may_act"] for result in results) == 1
    assert {result["status"] for result in results} == {"BEGUN", "GuardDenied"}
    claim = ctx.store.transact(lambda tx: tx.get("claims", ctx.manifest.opening_claim_key))
    assert claim["attempt_id"] == str(ctx.manifest.attempt_id)


@pytest.mark.parametrize("another_device", [False, True])
def test_spawned_attempts_cannot_namespace_away_one_opening(tmp_path, another_device):
    ctx = sequence_case(tmp_path / "authority.sqlite")
    first = ctx.prepared()
    other, device, second = _another_attempt(ctx, another_device=another_device)
    assert other.attempt_id != ctx.manifest.attempt_id
    assert other.opening_claim_key == ctx.manifest.opening_claim_key
    results = _race([_command(ctx, "begin", permit=first),
                    _command(ctx, "begin", permit=second, manifest=other, actor=device)])
    assert sum(result["may_act"] for result in results) == 1
    assert {result["status"] for result in results} == {"BEGUN", "GuardDenied"}
    winner = next(result for result in results if result["may_act"])
    decision = StepDecision.model_validate(winner["decision"])
    assert decision.may_act is not None
    claim = ctx.store.transact(lambda tx: tx.get("claims", ctx.manifest.opening_claim_key))
    assert claim["attempt_id"] == str(decision.may_act.attempt_id)
    assert claim["context_digest"] == decision.may_act.context_digest
    for selected, actor in ((ctx.manifest, ctx.device), (other, device)):
        denied = _race([_command(ctx, "prepare", manifest=selected, actor=actor)])[0]
        assert denied["status"] == "GuardDenied" and not denied["may_act"]
    assert ctx.store.transact(lambda tx: tx.get("claims", ctx.manifest.opening_claim_key)) == claim


def test_spawned_duplicate_outcomes_return_one_continuation_acknowledgement(tmp_path):
    ctx = sequence_case(tmp_path / "authority.sqlite")
    begun = ctx.begun(ctx.prepared())
    proof = begun.may_act
    assert proof is not None
    command = _command(ctx, "outcome", observed=observation(ctx.manifest, 0,
        proof.permit_id, proof.begun_sequence), decision_nonce=proof.decision_nonce)
    results = _race([command for _ in range(4)])
    assert {result["status"] for result in results} == {"LOCAL_FILLED"}
    assert sum(result["continuation_nonce"] is not None for result in results) == 1
    assert {result["next_index"] for result in results} == {1}
    continuation = UUID(next(result["continuation_nonce"] for result in results
                             if result["continuation_nonce"] is not None))
    next_permits = [ctx.prepared(1, continuation), ctx.prepared(1, continuation)]
    next_results = _race([_command(ctx, "begin", permit=permit, continuation=continuation)
                          for permit in next_permits])
    assert sum(result["may_act"] for result in next_results) == 1
    assert {result["status"] for result in next_results} == {"BEGUN", "GuardDenied"}
    prior = ctx.store.transact(lambda tx: tx.get("sequence_steps", step_key(ctx.manifest.attempt_id, 0)))
    assert prior["ack_consumed_by"] == 1


@pytest.mark.parametrize("order", ["cancel-first", "begin-first", "compete"])
def test_spawned_cancel_and_second_begin_share_a_serialized_boundary(tmp_path, order):
    ctx = sequence_case(tmp_path / "authority.sqlite")
    continuation = ctx.filled(ctx.begun(ctx.prepared())).continuation_nonce
    assert continuation is not None
    permit = ctx.prepared(1, continuation)
    begin = _command(ctx, "begin", permit=permit, continuation=continuation)
    cancel = _command(ctx, "cancel", actor=ctx.candidate, event_id="process-cancel")
    commands = [cancel, begin] if order == "cancel-first" else [begin, cancel]
    results = _race(commands, ordered=order != "compete")
    begun = next(result for result in results if result["operation"] == "begin")
    assert next(result for result in results if result["operation"] == "cancel")["status"] == "CANCELLED"
    if order == "cancel-first":
        assert begun["status"] == "GuardDenied" and not begun["may_act"]
    if order == "begin-first":
        assert begun["status"] == "BEGUN" and begun["may_act"]
    assert begun["status"] in {"BEGUN", "GuardDenied"}
    status = ctx.guard.status(ctx.device, ctx.manifest.attempt_id)
    assert status["state"] == "CANCELLED"
    assert (status["steps"][1]["status"] == "BEGUN") == begun["may_act"]
    if begun["may_act"]:
        late = ctx.filled(StepDecision.model_validate(begun["decision"]))
        assert late.continuation_nonce is None
    denied = _race([_command(ctx, "prepare", index=1, continuation=continuation)])[0]
    assert denied["status"] == "GuardDenied"
    assert ctx.guard.status(ctx.device, ctx.manifest.attempt_id)["state"] == "CANCELLED"
    assert ctx.store.transact(lambda tx: tx.get("claims", ctx.manifest.opening_claim_key)) is not None


def test_spawned_unknown_outcome_blocks_sequence_and_replacement_attempt(tmp_path):
    ctx = sequence_case(tmp_path / "authority.sqlite")
    continuation = ctx.filled(ctx.begun(ctx.prepared())).continuation_nonce
    permit = ctx.prepared(1, continuation)
    proof = ctx.begun(permit, continuation).may_act
    assert proof is not None
    result = _race([_command(ctx, "outcome", observed=observation(ctx.manifest, 1,
        proof.permit_id, proof.begun_sequence, "UNKNOWN"))])[0]
    assert result["status"] == "UNKNOWN" and result["continuation_nonce"] is None
    status = ctx.guard.status(ctx.device, ctx.manifest.attempt_id)
    assert status["state"] == "BLOCKED_UNKNOWN" and status["next_index"] == 1
    commands = [_command(ctx, "prepare", index=index, continuation=continuation) for index in (1, 2)]
    assert {result["status"] for result in _race(commands)} == {"GuardDenied"}
    old = _race([_command(ctx, "begin", permit=permit, continuation=continuation)])[0]
    assert old["status"] == "UNKNOWN" and not old["may_act"]
    other = ctx.guard.seal(ctx.candidate,
        ctx.manifest.base.model_copy(update={"approval_id": "replacement-approval"}), ctx.manifest.steps)
    approve(ctx, other)
    denied = _race([_command(ctx, "prepare", manifest=other)])[0]
    assert denied["status"] == "GuardDenied"


def test_spawned_death_after_begin_commit_never_reissues_a_decision(tmp_path):
    ctx = sequence_case(tmp_path / "authority.sqlite")
    permit = ctx.prepared()
    command = _command(ctx, "begin", permit=permit)
    _crash_after_commit(command)
    retry = _race([command])[0]
    assert retry["status"] == "ALREADY_BEGUN" and not retry["may_act"]
    status = ctx.guard.status(ctx.device, ctx.manifest.attempt_id)
    assert status["state"] == "ACTIVE" and status["in_flight"] == 0
    assert {result["status"] for result in _race([
        _command(ctx, "prepare", index=index) for index in (0, 1)])} == {"GuardDenied"}
    step = ctx.store.transact(lambda tx: tx.get("sequence_steps", step_key(ctx.manifest.attempt_id, 0)))
    unknown = _race([_command(ctx, "outcome", observed=observation(ctx.manifest, 0,
        permit["id"], step["begun_sequence"], "UNKNOWN"))])[0]
    assert unknown["status"] == "UNKNOWN" and unknown["continuation_nonce"] is None
    assert ctx.store.transact(lambda tx: tx.get("claims", ctx.manifest.opening_claim_key)) is not None


def test_spawned_death_after_outcome_commit_cannot_reconstruct_acknowledgement(tmp_path):
    ctx = sequence_case(tmp_path / "authority.sqlite")
    permit = ctx.prepared()
    proof = ctx.begun(permit).may_act
    assert proof is not None
    command = _command(ctx, "outcome", observed=observation(ctx.manifest, 0,
        proof.permit_id, proof.begun_sequence), decision_nonce=proof.decision_nonce)
    _crash_after_commit(command)
    status = ctx.guard.status(ctx.device, ctx.manifest.attempt_id)
    assert status["next_index"] == 1 and status["steps"][0]["status"] == "LOCAL_FILLED"
    retried = _race([command])[0]
    assert retried["status"] == "LOCAL_FILLED" and retried["continuation_nonce"] is None
    denied = _race([_command(ctx, "prepare", index=1)])[0]
    assert denied["status"] == "GuardDenied"
    replay = _race([_command(ctx, "begin", permit=permit)])[0]
    assert replay["status"] == "LOCAL_FILLED" and not replay["may_act"]


@pytest.mark.parametrize("retained", ["in-flight", "unknown", "cancelled", "completed-prefix"])
def test_application_only_restore_keeps_sequence_progress_and_denials(tmp_path, retained):
    ctx = sequence_case(tmp_path / "separate-authority.sqlite")
    permit = ctx.prepared()
    application_path = tmp_path / "application.sqlite"
    backup_path = tmp_path / "application-backup.sqlite"
    with sqlite3.connect(application_path) as application, sqlite3.connect(backup_path) as backup:
        application.execute("CREATE TABLE applications(id TEXT,status TEXT,filled_count INTEGER)")
        application.execute("INSERT INTO applications VALUES (?,?,?)",
                            (ctx.manifest.base.application_id, "approved", 0))
        application.commit()
        application.backup(backup)
    begun = ctx.begun(permit)
    proof = begun.may_act
    assert proof is not None
    if retained in {"cancelled", "completed-prefix"}:
        ctx.filled(begun)  # This acknowledgement is deliberately unavailable after restart.
    if retained == "cancelled":
        ctx.guard.cancel(ctx.candidate, ctx.manifest.attempt_id, "restored-cancel")
    if retained == "unknown":
        ctx.guard.outcome(ctx.device, ctx.manifest.attempt_id, observation(ctx.manifest, 0,
            proof.permit_id, proof.begun_sequence, "UNKNOWN"), "restored-unknown")
    expected_status = ctx.guard.status(ctx.device, ctx.manifest.attempt_id)
    head = ctx.store.transact(lambda tx: tx.head())
    claim = ctx.store.transact(lambda tx: tx.get("claims", ctx.manifest.opening_claim_key))
    with sqlite3.connect(application_path) as application:
        application.execute("DELETE FROM applications")
    with sqlite3.connect(backup_path) as backup, sqlite3.connect(application_path) as application:
        backup.backup(application)
        assert application.execute("SELECT status,filled_count FROM applications").fetchone() == ("approved", 0)
    assert application_path != ctx.store.path
    assert ctx.store.transact(lambda tx: tx.head()) == head
    assert ctx.guard.status(ctx.device, ctx.manifest.attempt_id) == expected_status
    commands = [_command(ctx, "begin", permit=permit), _command(ctx, "prepare", index=0),
                _command(ctx, "prepare", index=1)]
    results = _race(commands)
    assert not any(result["may_act"] for result in results)
    begun_result = next(result for result in results if result["operation"] == "begin")
    assert begun_result["status"] in {"ALREADY_BEGUN", "UNKNOWN", "LOCAL_FILLED", "GuardDenied"}
    assert [result["status"] for result in results if result["operation"] == "prepare"] == ["GuardDenied"] * 2
    assert ctx.store.transact(lambda tx: tx.get("claims", ctx.manifest.opening_claim_key)) == claim
    assert ctx.guard.status(ctx.device, ctx.manifest.attempt_id) == expected_status
