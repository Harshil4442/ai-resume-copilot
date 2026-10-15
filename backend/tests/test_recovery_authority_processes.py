"""Independent-process and crash proofs for the separate *local* authority file.

No employer network, production authentication, cloud service or real data is used.
Application-only restoration keeps the separate authority; rolling that authority
back itself, physical executor egress and retained cloud journal/IAM are NOT proven.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import time
from pathlib import Path
from uuid import UUID

import pytest
from backend.tests.fixtures.recovery_authority import FileAuthority
from backend.tests.test_recovery_authority import NOW, case

from app.domains.recovery.contracts import AuthenticatedActor, Binding, Revocation
from app.domains.recovery.service import RecoveryGuard
from app.domains.recovery.store import GuardDenied, GuardUnavailable


def _command(ctx, permit_id, *, device=None, binding=None, operation="begin", scope=None,
             challenge_id=None):
    return {"path": str(ctx.store.path), "authority_id": str(ctx.store.authority_id),
            "actor": (device or ctx.device).model_dump(mode="json"),
            "binding": (binding or ctx.binding).model_dump(mode="json"),
            "permit_id": permit_id, "operation": operation,
            "scope": scope.model_dump(mode="json") if scope else None, "challenge_id": challenge_id}


def _worker(command, ready, gate, output, completed, wait_for):
    store = FileAuthority(Path(command["path"]), UUID(command["authority_id"]))
    guard = RecoveryGuard(store, lambda: NOW)
    ready.put(os.getpid())
    assert gate.wait(15)
    if wait_for is not None:
        assert wait_for.wait(15)
    try:
        actor = AuthenticatedActor.model_validate(command["actor"])
        if command["operation"] == "revoke":
            result = guard.revoke(actor, Revocation.model_validate(command["scope"]), "process-revoke")
            output.put({"pid": os.getpid(), "operation": "revoke", "status": result["status"]})
        elif command["operation"] == "register":
            result = guard.register_approval(actor, command["challenge_id"],
                                            Binding.model_validate(command["binding"]))
            output.put({"pid": os.getpid(), "operation": "register", "status": result["status"],
                        "may_act": False})
        else:
            result = guard.begin(actor, command["permit_id"], Binding.model_validate(command["binding"]))
            output.put({"pid": os.getpid(), "operation": "begin", "status": result.status,
                        "may_act": result.may_act is not None})
    except (GuardDenied, GuardUnavailable) as exc:
        output.put({"pid": os.getpid(), "operation": command["operation"],
                    "status": type(exc).__name__, "may_act": False})
    finally:
        completed.set()


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
        pids = {ready.get(timeout=15) for _ in processes}
        assert len(pids) == len(processes) and os.getpid() not in pids
        gate.set()
        results = [output.get(timeout=15) for _ in processes]
        for process in processes:
            process.join(timeout=15)
            assert process.exitcode == 0
        return results
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=3)
        ready.close()
        output.close()


def test_independent_processes_same_permit_have_exactly_one_permission(tmp_path):
    ctx = case(tmp_path / "authority.sqlite")
    prepared = ctx.prepared()
    results = _race([_command(ctx, prepared["permit_id"]) for _ in range(4)])
    assert sum(result["may_act"] for result in results) == 1
    assert sorted(result["status"] for result in results) == ["ALREADY_BEGUN"] * 3 + ["BEGUN"]
    events = ctx.store.transact(lambda tx: tx.head())
    assert events[0] > 1


def test_independent_processes_cannot_consume_one_candidate_challenge_twice(tmp_path):
    ctx = case(tmp_path / "authority.sqlite")
    challenge = ctx.guard.challenge(ctx.candidate)
    other = ctx.binding.model_copy(update={"approval_id": "approval-two"})
    commands = [_command(ctx, "unused", device=ctx.candidate, binding=binding,
                operation="register", challenge_id=challenge["id"])
                for binding in (ctx.binding, other)]
    results = _race(commands)
    assert sorted(result["status"] for result in results) == ["GuardDenied", "REGISTERED"]
    assert not any(result["may_act"] for result in results)


def test_independent_devices_and_packages_share_one_stable_opening_claim(tmp_path):
    ctx = case(tmp_path / "authority.sqlite")
    first = ctx.prepared()
    other_device = ctx.device.model_copy(update={"actor_id": "device-actor-two",
        "device_id": "device-two", "key_sha256": "f" * 64})
    other_binding = ctx.binding.model_copy(update={"device_id": other_device.device_id,
        "device_key_sha256": other_device.key_sha256, "approval_id": "approval-two",
        "application_id": "application-two", "package_digest": "7" * 64,
        "field_id": "second-field", "value_sha256": "8" * 64})
    ctx.store.provision_fixture([other_device], other_binding)
    challenge = ctx.guard.challenge(ctx.candidate)
    ctx.guard.register_approval(ctx.candidate, challenge["id"], other_binding)
    second = ctx.guard.prepare(other_device, other_binding.approval_id)
    assert other_binding.opening_claim_key == ctx.binding.opening_claim_key
    results = _race([_command(ctx, first["permit_id"]), _command(ctx, second["permit_id"],
                     device=other_device, binding=other_binding)])
    assert sum(result["may_act"] for result in results) == 1
    assert {result["status"] for result in results} == {"BEGUN", "GuardDenied"}
    with pytest.raises(GuardDenied, match="possible disclosure"):
        ctx.guard.prepare(other_device, other_binding.approval_id)


@pytest.mark.parametrize("order", ["cancel-first", "begin-first", "compete"])
def test_process_cancellation_and_begin_have_a_serialized_boundary(tmp_path, order):
    ctx = case(tmp_path / "authority.sqlite")
    prepared = ctx.prepared()
    scope = Revocation(subject_uuid=ctx.binding.subject_uuid, kind="approval",
                       target=ctx.binding.approval_id, revision=1)
    begin = _command(ctx, prepared["permit_id"])
    cancel = _command(ctx, prepared["permit_id"], device=ctx.candidate,
                      operation="revoke", scope=scope)
    commands = [cancel, begin] if order == "cancel-first" else [begin, cancel]
    results = _race(commands, ordered=order != "compete")
    begun = next(result for result in results if result["operation"] == "begin")
    assert next(result for result in results if result["operation"] == "revoke")["status"] == "REVOKED"
    assert ctx.store.transact(lambda tx: tx.get("revocations", scope.key)) is not None
    claim = ctx.store.transact(lambda tx: tx.get("claims", ctx.binding.opening_claim_key))
    assert (claim is not None) == begun["may_act"]
    if order == "cancel-first":
        assert begun["status"] == "GuardDenied" and not begun["may_act"]
    if order == "begin-first":
        assert begun["status"] == "BEGUN" and begun["may_act"]
        # Cancellation after possible disclosure cannot undo it or grant a retry.
        assert ctx.guard.begin(ctx.device, prepared["permit_id"], ctx.binding).may_act is None


def _kill_after_commit(command):
    store = FileAuthority(Path(command["path"]), UUID(command["authority_id"]),
                          after_commit=lambda: os._exit(73))
    RecoveryGuard(store, lambda: NOW).begin(AuthenticatedActor.model_validate(command["actor"]),
        command["permit_id"], Binding.model_validate(command["binding"]))
    os._exit(99)  # must never receive/act on the computed permission


def test_process_death_after_durable_begin_before_reply_cannot_replay(tmp_path):
    ctx = case(tmp_path / "authority.sqlite")
    prepared = ctx.prepared()
    process = multiprocessing.get_context("spawn").Process(target=_kill_after_commit,
                args=(_command(ctx, prepared["permit_id"]),))
    try:
        process.start()
        process.join(timeout=15)
        assert process.exitcode == 73
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=3)
    restarted = RecoveryGuard(FileAuthority(ctx.store.path, ctx.store.authority_id), lambda: NOW)
    result = restarted.begin(ctx.device, prepared["permit_id"], ctx.binding)
    assert result.status == "ALREADY_BEGUN" and result.may_act is None
    claim = ctx.store.transact(lambda tx: tx.get("claims", ctx.binding.opening_claim_key))
    assert claim is not None
    with pytest.raises(GuardDenied, match="possible disclosure"):
        restarted.prepare(ctx.device, ctx.binding.approval_id)
    assert restarted.outcome(ctx.device, prepared["permit_id"], "UNKNOWN").may_act is None


def _hold_writer(path, ready, release):
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        ready.set()
        assert release.wait(15)
        connection.rollback()
    finally:
        connection.close()


def test_independent_process_contention_has_a_bounded_fail_closed_timeout(tmp_path):
    ctx = case(tmp_path / "authority.sqlite")
    prepared = ctx.prepared()
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = context.Process(target=_hold_writer, args=(str(ctx.store.path), ready, release))
    try:
        process.start()
        assert ready.wait(15)
        blocked = RecoveryGuard(FileAuthority(ctx.store.path, ctx.store.authority_id,
            timeout_seconds=0.15), lambda: NOW)
        started = time.monotonic()
        with pytest.raises(GuardUnavailable, match="cannot confirm"):
            blocked.begin(ctx.device, prepared["permit_id"], ctx.binding)
        assert 0.1 <= time.monotonic() - started < 1.5
    finally:
        release.set()
        process.join(timeout=15)
        if process.is_alive():
            process.terminate()
            process.join(timeout=3)
    assert process.exitcode == 0
    assert ctx.store.transact(lambda tx: tx.get("claims", ctx.binding.opening_claim_key)) is None


@pytest.mark.parametrize("retained", ["possible-disclosure", "subject-deletion"])
def test_application_only_backup_restore_preserves_independent_denials(tmp_path, retained):
    ctx = case(tmp_path / "separate-authority.sqlite")
    prepared = ctx.prepared()
    application_path, backup_path = tmp_path / "application.sqlite", tmp_path / "application-backup.sqlite"
    with sqlite3.connect(application_path) as application, sqlite3.connect(backup_path) as backup:
        application.execute("CREATE TABLE applications(id TEXT,approval_id TEXT,status TEXT)")
        application.execute("INSERT INTO applications VALUES (?,?,?)",
            (ctx.binding.application_id, ctx.binding.approval_id, "approved"))
        application.commit()
        application.backup(backup)  # WAL-safe logical backup; never copy authority files
    if retained == "possible-disclosure":
        ctx.guard.begin(ctx.device, prepared["permit_id"], ctx.binding)
    else:
        ctx.guard.revoke(ctx.candidate, Revocation(subject_uuid=ctx.binding.subject_uuid,
            kind="subject", target=str(ctx.binding.subject_uuid), revision=0), "delete-subject")
    with sqlite3.connect(application_path) as application:
        application.execute("DELETE FROM applications")
    authority_head = ctx.store.transact(lambda tx: tx.head())
    with sqlite3.connect(backup_path) as backup, sqlite3.connect(application_path) as application:
        backup.backup(application)  # restore only the application database
        assert application.execute("SELECT status FROM applications").fetchone()[0] == "approved"
    assert ctx.store.transact(lambda tx: tx.head()) == authority_head
    results = _race([_command(ctx, prepared["permit_id"])])  # new process, no cached state/checkpoint
    assert not results[0]["may_act"]
    assert results[0]["status"] == ("ALREADY_BEGUN" if retained == "possible-disclosure" else "GuardDenied")
    assert application_path != ctx.store.path


@pytest.mark.parametrize("projection", ["claim", "begun-permit", "tombstone", "approval", "epoch"])
def test_partial_authority_projection_loss_fails_closed_with_intact_journal(tmp_path, projection):
    ctx = case(tmp_path / "authority.sqlite")
    prepared = ctx.prepared()
    ctx.guard.begin(ctx.device, prepared["permit_id"], ctx.binding)
    scope = Revocation(subject_uuid=ctx.binding.subject_uuid, kind="artifact",
        target=ctx.binding.artifact_sha256, revision=int(ctx.binding.artifact_generation))
    ctx.guard.revoke(ctx.candidate, scope, "artifact-revoked")
    old_control = ctx.store.transact(lambda tx: tx.get("control", "current"))
    ctx.guard.close(ctx.operator, "projection-test-close")
    targets = {"claim": ("claims", ctx.binding.opening_claim_key),
        "begun-permit": ("permits", prepared["permit_id"]),
        "tombstone": ("revocations", scope.key), "approval": ("approvals", ctx.binding.approval_id)}
    with sqlite3.connect(ctx.store.path) as connection:
        if projection == "epoch":
            connection.execute("UPDATE records SET payload=? WHERE namespace='control' AND key='current'",
                               (json.dumps(old_control),))
        else:
            namespace, key = targets[projection]
            connection.execute("DELETE FROM records WHERE namespace=? AND key=?", (namespace, key))
    # A retained journal never permits replay when its deny/ownership projection is missing.
    with pytest.raises(GuardUnavailable, match="evidence is incomplete"):
        ctx.guard.prepare(ctx.device, ctx.binding.approval_id)
    with pytest.raises(GuardUnavailable, match="evidence is incomplete"):
        RecoveryGuard(FileAuthority(ctx.store.path, ctx.store.authority_id), lambda: NOW).begin(
            ctx.device, prepared["permit_id"], ctx.binding)
