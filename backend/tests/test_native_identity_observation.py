"""Local immutable-identity observations never share request authority."""
from __future__ import annotations

import asyncio
from contextvars import copy_context
from threading import Thread
from uuid import uuid4

import pytest

from app.domains.recovery import native_identity_observation as observation
from app.domains.recovery.gcp_contracts import RegistryPin
from app.domains.recovery.native_identity_observation import (
    identity_barrier,
    identity_observation_scope,
    invalidate_identity_observations,
    observe_identity,
    quarantine_on_failure,
)
from app.domains.recovery.store import GuardDenied, GuardUnavailable


class Rpc:
    def __init__(self, name):
        self.database_resource = f"projects/project-one/databases/{name}"
        self.uid = str(uuid4())
        self.calls = 0
        self.fail = False

    def fresh(self):
        self.calls += 1
        if self.fail:
            raise GuardUnavailable("synthetic unavailable")
        return {"name": self.database_resource, "uid": self.uid, "type": "FIRESTORE_NATIVE"}

    def identity(self):
        return observe_identity(self, self.fresh)


def bindings():
    rpcs = (Rpc("execution"), Rpc("protected"))
    pins = tuple(RegistryPin(database=rpc.database_resource, database_uid=rpc.uid,
        authority_id=uuid4(), incarnation=1, epoch_id=uuid4()) for rpc in rpcs)
    return rpcs, tuple(zip(rpcs, pins, strict=True))


def counts(rpcs):
    return [rpc.calls for rpc in rpcs]


def test_outside_scope_is_always_fresh_and_helpers_are_noops():
    rpcs, _ = bindings()
    identity_barrier()
    invalidate_identity_observations()
    quarantine_on_failure(lambda: pytest.fail("no scope"))
    rpcs[0].identity()
    rpcs[0].identity()
    assert counts(rpcs) == [2, 0]


def test_entry_exit_and_barriers_refresh_both_but_reuse_copies():
    rpcs, fixed = bindings()
    with identity_observation_scope(fixed):
        assert counts(rpcs) == [1, 1]
        result = rpcs[0].identity()
        result["uid"] = "untrusted mutable copy"
        assert rpcs[0].identity()["uid"] == rpcs[0].uid
        assert counts(rpcs) == [1, 1]
        identity_barrier()
        assert counts(rpcs) == [2, 2]
    assert counts(rpcs) == [3, 3]
    rpcs[0].identity()
    assert counts(rpcs) == [4, 3]


def test_abort_invalidation_revalidates_all_before_any_reuse():
    rpcs, fixed = bindings()
    with identity_observation_scope(fixed):
        invalidate_identity_observations()
        rpcs[1].identity()
        assert counts(rpcs) == [2, 2]
    assert counts(rpcs) == [3, 3]


@pytest.mark.parametrize("kind", ["uid", "failure", "database"])
def test_caught_failure_is_sticky_and_exit_refreshes_both_and_quarantines(kind):
    rpcs, fixed = bindings()
    cleaned = []
    with pytest.raises(GuardUnavailable):
        with identity_observation_scope(fixed):
            quarantine_on_failure(lambda: cleaned.append("first"))
            quarantine_on_failure(lambda: cleaned.append("second"))
            if kind == "uid":
                rpcs[0].uid = str(uuid4())
            elif kind == "database":
                rpcs[0].database_resource += "changed"
            else:
                rpcs[0].fail = True
            with pytest.raises(GuardUnavailable):
                identity_barrier()
            # Restoring metadata cannot revive a poisoned scope.
            rpcs[0].uid = str(fixed[0][1].database_uid)
            rpcs[0].database_resource = fixed[0][1].database
            rpcs[0].fail = False
            with pytest.raises(GuardUnavailable):
                rpcs[1].identity()
    assert counts(rpcs) == [3, 3]
    assert cleaned == ["second", "first"]


def test_exit_replacement_never_exports_and_next_request_is_fresh():
    rpcs, fixed = bindings()
    with pytest.raises(GuardUnavailable):
        with identity_observation_scope(fixed):
            rpcs[1].uid = str(uuid4())
    assert counts(rpcs) == [2, 2]
    with pytest.raises(GuardUnavailable):
        with identity_observation_scope(fixed):
            pytest.fail("changed identity entered")
    assert counts(rpcs) == [3, 3]


def test_no_unknown_rpc_or_added_dependency_can_borrow_scope():
    rpcs, fixed = bindings()
    alien = Rpc("execution")
    alien.uid = rpcs[0].uid
    with pytest.raises(GuardUnavailable):
        with identity_observation_scope(fixed):
            alien.identity()
    assert alien.calls == 0


def test_nested_scope_poison_denies_outer_success():
    _, fixed = bindings()
    with pytest.raises(GuardUnavailable):
        with identity_observation_scope(fixed):
            with pytest.raises(GuardUnavailable):
                with identity_observation_scope(fixed):
                    pytest.fail("nested")


@pytest.mark.parametrize("foreign", ["copied", "thread", "task"])
def test_copied_context_thread_or_task_cannot_borrow_observation(foreign):
    rpcs, fixed = bindings()

    def rejected():
        with pytest.raises(GuardUnavailable):
            rpcs[0].identity()

    async def scenario():
        with pytest.raises(GuardUnavailable):
            with identity_observation_scope(fixed):
                context = copy_context()
                if foreign == "copied":
                    context.run(rejected)
                elif foreign == "thread":
                    thread = Thread(target=lambda: context.run(rejected))
                    thread.start()
                    thread.join()
                else:
                    async def child():
                        rejected()
                    await asyncio.create_task(child())
    asyncio.run(scenario())
    assert counts(rpcs) == [2, 2]


def test_independent_concurrent_tasks_use_their_own_fresh_observations():
    async def request():
        rpcs, fixed = bindings()
        with identity_observation_scope(fixed):
            await asyncio.sleep(0)
            rpcs[0].identity()
            identity_barrier()
        return counts(rpcs)

    async def run():
        return await asyncio.gather(request(), request())
    assert asyncio.run(run()) == [[3, 3], [3, 3]]


def test_callback_failure_cannot_reopen_scope_or_skip_other_cleanup():
    _, fixed = bindings()
    cleaned = []
    def fail():
        raise RuntimeError("synthetic cleanup")
    with pytest.raises(ValueError, match="body denied"):
        with identity_observation_scope(fixed):
            quarantine_on_failure(lambda: cleaned.append(True))
            quarantine_on_failure(fail)
            raise ValueError("body denied")
    assert cleaned == [True]


def test_scope_does_not_retain_success_callbacks_or_identity_between_requests():
    rpcs, fixed = bindings()
    cleaned = []
    with identity_observation_scope(fixed):
        quarantine_on_failure(lambda: cleaned.append(True))
    with identity_observation_scope(fixed):
        pass
    assert cleaned == []
    assert counts(rpcs) == [4, 4]


def test_deadline_is_fixed_and_barrier_does_not_renew_it(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(observation, "monotonic", lambda: clock[0])
    rpcs, fixed = bindings()
    cleaned = []
    with pytest.raises(GuardUnavailable):
        with identity_observation_scope(fixed):
            quarantine_on_failure(lambda: cleaned.append(True))
            clock[0] += 20.0
            identity_barrier()
            assert counts(rpcs) == [2, 2]
            clock[0] += 10.0
            rpcs[0].identity()
    assert cleaned == [True]


def test_timeout_during_entry_cannot_allow_body(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(observation, "monotonic", lambda: clock[0])
    rpcs, fixed = bindings()
    before = Rpc.fresh
    def delayed(self):
        reply = before(self)
        if self is rpcs[1]:
            clock[0] += 31.0
        return reply
    monkeypatch.setattr(Rpc, "fresh", delayed)
    with pytest.raises(GuardUnavailable):
        with identity_observation_scope(fixed):
            pytest.fail("expired entry cannot enter")


def test_original_denial_survives_fresh_exit_and_cleans_minted_token():
    rpcs, fixed = bindings()
    cleaned = []
    with pytest.raises(GuardDenied, match="wrong password"):
        with identity_observation_scope(fixed):
            quarantine_on_failure(lambda: cleaned.append(True))
            raise GuardDenied("wrong password")
    assert cleaned == [True]
    assert counts(rpcs) == [2, 2]


@pytest.mark.parametrize("reason", ["poisoned", "full"])
def test_refused_cleanup_registration_discards_just_minted_token(reason):
    rpcs, fixed = bindings()
    cleaned = []
    with pytest.raises(GuardUnavailable):
        with identity_observation_scope(fixed):
            if reason == "poisoned":
                rpcs[0].fail = True
                with pytest.raises(GuardUnavailable):
                    identity_barrier()
            else:
                for _ in range(32):
                    quarantine_on_failure(lambda: None)
            with pytest.raises(GuardUnavailable):
                quarantine_on_failure(lambda: cleaned.append(True))
            assert cleaned == [True]
    assert cleaned == [True]


def test_cached_observation_rejects_local_rpc_path_change_before_reuse():
    rpcs, fixed = bindings()
    with pytest.raises(GuardUnavailable):
        with identity_observation_scope(fixed):
            rpcs[0].database_resource += "changed"
            with pytest.raises(GuardUnavailable):
                rpcs[0].identity()
    assert counts(rpcs) == [2, 2]


def test_timeout_during_barrier_cannot_reuse_last_good_reply(monkeypatch):
    clock = [100.0]
    delaying = [False]
    monkeypatch.setattr(observation, "monotonic", lambda: clock[0])
    rpcs, fixed = bindings()
    before = Rpc.fresh
    def delayed(self):
        reply = before(self)
        if delaying[0] and self is rpcs[1]:
            clock[0] += 31.0
        return reply
    monkeypatch.setattr(Rpc, "fresh", delayed)
    with pytest.raises(GuardUnavailable):
        with identity_observation_scope(fixed):
            delaying[0] = True
            with pytest.raises(GuardUnavailable):
                identity_barrier()
            assert clock[0] == 131.0
            assert counts(rpcs) == [2, 2]
            rpcs[0].identity()


def test_poisoned_request_does_not_poison_next_independent_request():
    rpcs, fixed = bindings()
    with pytest.raises(GuardUnavailable):
        with identity_observation_scope(fixed):
            rpcs[0].fail = True
            identity_barrier()
    rpcs[0].fail = False
    with identity_observation_scope(fixed):
        assert rpcs[0].identity()["uid"] == str(fixed[0][1].database_uid)


class SyntheticCancellation(BaseException):
    pass


def test_final_refresh_cancellation_quarantines_minted_handles_and_preserves_error(monkeypatch):
    rpcs, fixed = bindings()
    handles = {}
    token = object()
    cancellation = SyntheticCancellation("synthetic final refresh cancellation")
    cancelling = [False]
    original = Rpc.fresh
    def fresh(self):
        if cancelling[0] and self is rpcs[0]:
            raise cancellation
        return original(self)
    monkeypatch.setattr(Rpc, "fresh", fresh)
    with pytest.raises(SyntheticCancellation) as caught:
        with identity_observation_scope(fixed):
            handles["minted"] = token
            quarantine_on_failure(lambda: handles.pop("minted", None))
            cancelling[0] = True
    assert caught.value is cancellation
    assert handles == {}
    cancelling[0] = False
    # The failed finalization restored its exact Context, so a new operation may enter.
    with identity_observation_scope(fixed):
        assert rpcs[0].identity()["uid"] == rpcs[0].uid


def test_caught_barrier_cancellation_still_poisoned_and_quarantines(monkeypatch):
    rpcs, fixed = bindings()
    handles = {}
    cancelling = [False]
    cancellation = SyntheticCancellation("synthetic barrier cancellation")
    original = Rpc.fresh
    def fresh(self):
        if cancelling[0] and self is rpcs[0]:
            raise cancellation
        return original(self)
    monkeypatch.setattr(Rpc, "fresh", fresh)
    with pytest.raises(GuardUnavailable):
        with identity_observation_scope(fixed):
            handles["minted"] = object()
            quarantine_on_failure(lambda: handles.pop("minted", None))
            cancelling[0] = True
            with pytest.raises(SyntheticCancellation) as caught:
                identity_barrier()
            assert caught.value is cancellation
            cancelling[0] = False
            with pytest.raises(GuardUnavailable):
                rpcs[1].identity()
    assert handles == {}


def test_cleanup_cancellation_does_not_skip_remaining_token_or_mask_primary_failure():
    _, fixed = bindings()
    handles = {}
    prior = object()
    minted = object()
    handles["prior"], handles["minted"] = prior, minted
    primary = SyntheticCancellation("primary operation cancellation")
    def interrupted_cleanup():
        raise SyntheticCancellation("cleanup cancellation")
    with pytest.raises(SyntheticCancellation) as caught:
        with identity_observation_scope(fixed):
            quarantine_on_failure(lambda: handles.pop("minted", None))
            quarantine_on_failure(interrupted_cleanup)
            raise primary
    assert caught.value is primary
    assert handles == {"prior": prior}
    with identity_observation_scope(fixed):
        pass
