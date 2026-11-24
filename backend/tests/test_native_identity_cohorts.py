"""Pre-send-only installed-SDK metadata candidate; no native/cloud authority proof."""
from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from threading import Barrier, Event, Lock
from uuid import uuid4

import pytest
from google.api_core.exceptions import ServiceUnavailable

from app.domains.candidate_accounts.production import ConfiguredCandidateAccounts
from app.domains.recovery import native_identity_observation as observations
from app.domains.recovery.gcp_rpc import FirestoreIdentityList
from app.domains.recovery.native_identity_cohorts import (
    MAX_COHORT_SIZE,
    MAX_COHORTS,
    MAX_PENDING,
    WINDOW_SECONDS,
    PreSendIdentityCohorts,
)
from app.domains.recovery.native_identity_observation import (
    identity_observation_scope,
    invalidate_identity_observations,
)
from app.domains.recovery.native_metadata_admission import MetadataCapacityUnavailable
from app.domains.recovery.store import GuardDenied, GuardUnavailable
from tests.test_candidate_production_factory import configured  # noqa: F401
from tests.test_candidate_validation_identity_list import enabled
from tests.test_firestore_identity_list import response, setup
from tests.test_gcp_rpc import sdk  # noqa: F401
from tests.test_native_metadata_admission import inert_shared_redis


@pytest.fixture
def candidate(sdk, monkeypatch):  # noqa: F811
    admission = inert_shared_redis(monkeypatch)
    monkeypatch.setenv("CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED", "true")
    rpcs, bindings, _ = setup(sdk)
    cohorts = PreSendIdentityCohorts(bindings)
    batch = FirestoreIdentityList(rpcs, cohorts=cohorts, pins=tuple(pin for _, pin in bindings))
    calls = []
    lock = Lock()

    def listed(request, **kwargs):
        assert request.parent == "projects/local-proof" and request.show_deleted is False
        assert kwargs["timeout"] == 2
        assert ("x-goog-request-params", "parent=projects/local-proof") in kwargs["metadata"]
        with lock:
            calls.append(request)
        return response()

    sdk.admin.transport.list_databases.side_effect = listed
    return rpcs, bindings, cohorts, batch, calls, admission


def settled(cohorts):
    assert cohorts.open is None and cohorts.pending == cohorts.active == 0


def test_sequential_operations_keep_separate_entry_and_exit_reads(candidate):
    rpcs, bindings, cohorts, batch, calls, admission = candidate
    for _ in range(2):
        with identity_observation_scope(bindings, batch=batch):
            assert rpcs[0].identity()["uid"] == str(bindings[0][1].database_uid)
    assert len(calls) == admission.grants == 4
    settled(cohorts)


def test_bounded_eight_waiter_workload_saves_actual_sdk_calls_without_sharing_scopes(candidate, record_property):
    rpcs, bindings, cohorts, batch, calls, admission = candidate
    assert (WINDOW_SECONDS, MAX_PENDING, MAX_COHORT_SIZE, MAX_COHORTS) == (0.002, 32, 8, 4)
    entry = Barrier(8)
    dependent_reads_complete = Barrier(8)
    control_reads = []

    def validate(index):
        entry.wait(timeout=5)
        with identity_observation_scope(bindings, batch=batch):
            # Each request performs its own dependent control read and receives
            # a separate mutable copy, never another request's Context/scope.
            value = rpcs[0].identity()
            value["uid"] = "caller-local-change"
            assert rpcs[0].identity()["uid"] == str(bindings[0][1].database_uid)
            control_reads.append(index)
            dependent_reads_complete.wait(timeout=5)
        return index

    with ThreadPoolExecutor(max_workers=8) as workers:
        assert sorted(workers.map(validate, range(8))) == list(range(8))
    assert sorted(control_reads) == list(range(8))
    assert 2 <= len(calls) < 16  # Baseline is 8 independent entry/exit Lists.
    assert admission.grants == len(calls)
    record_property("baseline_metadata_rpcs", 16)
    record_property("actual_metadata_rpcs", len(calls))
    record_property("independent_dependent_reads", len(control_reads))
    settled(cohorts)


def test_one_callers_denial_is_not_a_shared_authorization_result(candidate):
    _, bindings, cohorts, batch, _, _ = candidate
    entry, checked = Barrier(8), Barrier(8)

    def validate(index):
        entry.wait(timeout=5)
        try:
            with identity_observation_scope(bindings, batch=batch):
                checked.wait(timeout=5)
                if index == 0:
                    raise GuardDenied("This caller's independent session is revoked")
            return "allowed"
        except GuardDenied:
            return "denied"

    with ThreadPoolExecutor(max_workers=8) as workers:
        outcomes = list(workers.map(validate, range(8)))
    assert outcomes == ["denied"] + ["allowed"] * 7
    settled(cohorts)


@pytest.mark.parametrize("fault", ["rpc", "capacity", "uid"])
def test_each_waiter_receives_a_shared_transport_failure_without_dependent_work(candidate, fault):
    _, bindings, cohorts, batch, calls, admission = candidate
    entry = Barrier(8)
    original = batch.admin.transport.list_databases.side_effect

    def failed(request, **kwargs):
        value = original(request, **kwargs)
        if fault == "rpc":
            raise ServiceUnavailable("synthetic-only")
        value.databases[0].uid = str(uuid4())
        return value

    batch.admin.transport.list_databases.side_effect = failed
    if fault == "capacity":
        admission.entries["LIMITS:hirewiz-native-metadata-v1/local-proof"] = 40

    def validate(_):
        entry.wait(timeout=5)
        failure = MetadataCapacityUnavailable if fault == "capacity" else GuardUnavailable
        with pytest.raises(failure), identity_observation_scope(bindings, batch=batch):
            pytest.fail("Failed cohort must not authorize dependent reads")

    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(validate, range(8)))
    assert len(calls) == (0 if fault == "capacity" else admission.grants)
    settled(cohorts)


def test_a_waiter_arriving_after_send_never_joins_the_inflight_response(candidate, monkeypatch):
    _, _, cohorts, batch, calls, admission = candidate
    first_sent, second_sent, release = Event(), Event(), Event()
    original = batch.admin.transport.list_databases.side_effect

    def held(request, **kwargs):
        value = original(request, **kwargs)
        (first_sent if len(calls) == 1 else second_sent).set()
        assert release.wait(timeout=5)
        return value

    monkeypatch.setattr(batch.admin.transport.list_databases, "side_effect", held)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(batch.identities)
        assert first_sent.wait(timeout=5)
        second = workers.submit(batch.identities)
        assert second_sent.wait(timeout=5)
        release.set()
        assert first.result(timeout=5) == second.result(timeout=5)
    assert len(calls) == admission.grants == 2
    # A later operation cannot discover either completed response.
    batch.admin.transport.list_databases.side_effect = original
    batch.identities()
    assert len(calls) == admission.grants == 3
    settled(cohorts)


@pytest.mark.parametrize("change", ["client", "transport", "resource", "pin", "governor"])
def test_changed_binding_is_not_admitted_to_a_cohort(candidate, monkeypatch, change):
    rpcs, _, cohorts, batch, calls, _ = candidate
    if change == "client":
        monkeypatch.setattr(rpcs[0], "client", object())
    elif change == "transport":
        monkeypatch.setattr(cohorts, "transport", object())
    elif change == "resource":
        monkeypatch.setattr(rpcs[0], "database_resource", "projects/local-proof/databases/other-proof")
    elif change == "pin":
        batch.pins = (batch.pins[0].model_copy(update={"epoch_id": uuid4()}), batch.pins[1])
    else:
        monkeypatch.setenv("CANDIDATE_METADATA_CALLS_PER_MINUTE", "39")
    with pytest.raises(GuardUnavailable):
        batch.identities()
    assert calls == []
    settled(cohorts)


@pytest.mark.parametrize("fault", ["uid", "partial", "rpc", "capacity"])
def test_failed_fresh_cohort_never_reuses_prior_values(candidate, monkeypatch, fault):
    _, bindings, cohorts, batch, calls, admission = candidate
    batch.identities()
    original = batch.admin.transport.list_databases.side_effect

    def changed(request, **kwargs):
        value = original(request, **kwargs)
        if fault == "uid":
            value.databases[0].uid = str(uuid4())
        elif fault == "partial":
            del value.databases[1]
        elif fault == "rpc":
            raise ServiceUnavailable("synthetic-only")
        return value

    batch.admin.transport.list_databases.side_effect = changed
    if fault == "capacity":
        admission.entries["LIMITS:hirewiz-native-metadata-v1/local-proof"] = 40
    failure = MetadataCapacityUnavailable if fault == "capacity" else GuardUnavailable
    with pytest.raises(failure), identity_observation_scope(bindings, batch=batch):
        pytest.fail("Failed fresh metadata must not authorize dependent reads")
    assert len(calls) == (1 if fault == "capacity" else 2)
    settled(cohorts)


def test_slow_metadata_cannot_extend_an_individual_observation_deadline(candidate, monkeypatch):
    _, bindings, cohorts, batch, _, _ = candidate
    now = [0.0]
    monkeypatch.setattr(observations, "monotonic", lambda: now[0])
    original = batch.admin.transport.list_databases.side_effect

    def expires(request, **kwargs):
        result = original(request, **kwargs)
        now[0] = 31
        return result

    batch.admin.transport.list_databases.side_effect = expires
    with pytest.raises(GuardUnavailable), identity_observation_scope(bindings, batch=batch):
        pytest.fail("Expired caller scope must not enter dependent reads")
    settled(cohorts)


def test_caught_cohort_failure_poison_is_not_revived_by_a_later_good_response(candidate):
    rpcs, bindings, cohorts, batch, calls, _ = candidate
    original = batch.admin.transport.list_databases.side_effect

    def failed(request, **kwargs):
        original(request, **kwargs)
        raise ServiceUnavailable("synthetic-only")

    with pytest.raises(GuardUnavailable), identity_observation_scope(bindings, batch=batch):
        invalidate_identity_observations()
        batch.admin.transport.list_databases.side_effect = failed
        with pytest.raises(GuardUnavailable):
            rpcs[0].identity()
        batch.admin.transport.list_databases.side_effect = original
        with pytest.raises(GuardUnavailable):
            rpcs[0].identity()
        assert len(calls) == 2
    assert len(calls) == 3
    with identity_observation_scope(bindings, batch=batch):
        rpcs[0].identity()
    assert len(calls) == 5
    settled(cohorts)


def test_copied_context_still_poisons_only_its_own_scope(candidate):
    rpcs, bindings, cohorts, batch, calls, _ = candidate
    with pytest.raises(GuardUnavailable), identity_observation_scope(bindings, batch=batch):
        with pytest.raises(GuardUnavailable):
            copy_context().run(rpcs[0].identity)
        with pytest.raises(GuardUnavailable):
            rpcs[1].identity()
    assert len(calls) == 2
    settled(cohorts)


def test_four_inflight_cohorts_are_bounded_and_no_fifth_sdk_call_is_sent(candidate):
    _, _, cohorts, batch, calls, _ = candidate
    sent = [Event() for _ in range(4)]
    release = Event()
    original = batch.admin.transport.list_databases.side_effect

    def held(request, **kwargs):
        value = original(request, **kwargs)
        sent[len(calls) - 1].set()
        assert release.wait(timeout=5)
        return value

    batch.admin.transport.list_databases.side_effect = held
    with ThreadPoolExecutor(max_workers=4) as workers:
        futures = []
        for index in range(4):
            futures.append(workers.submit(batch.identities))
            assert sent[index].wait(timeout=5)
        with pytest.raises(GuardUnavailable):
            batch.identities()
        assert len(calls) == 4
        release.set()
        for future in futures:
            future.result(timeout=5)
    settled(cohorts)


def test_actual_factory_default_is_not_shared_and_candidate_keeps_fresh_revocation(configured, monkeypatch):  # noqa: F811
    from app.domains.candidate_accounts.production import production_candidate_accounts

    c = configured
    another = production_candidate_accounts()
    assert another is not c.accounts and not another._pre_send_identity_batching
    assert c.accounts._composition() is c.accounts._composition()
    c.accounts = ConfiguredCandidateAccounts(c.config, pre_send_identity_batching=True)
    session, calls = enabled(c, monkeypatch)
    c.accounts.validate(session.context, auth_generation=1)
    assert len(calls) == 2
    c.accounts.logout(session.context)
    with pytest.raises(GuardDenied, match="revoked"):
        c.accounts.validate(session.context, auth_generation=1)
    assert len(calls) == 4
    settled(c.accounts._identity_cohorts)
    counts = c.accounts._identity_cohorts.snapshot()
    assert counts["candidate_validation_started"] == counts["candidate_validation_entered"] == 2
    assert counts["candidate_validation_succeeded"] == counts["candidate_validation_failed"] == 1
    assert counts["candidate_barriers_started"] == counts["candidate_barriers_succeeded"] == 4
    assert counts["candidate_barriers_failed"] == counts["candidate_pending"] == counts["candidate_active"] == 0


def test_runner_opt_in_is_explicit_bounded_local_and_cannot_select_another_workload(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/run_stage5_capacity.py"
    def plan(*flags):
        return subprocess.run([sys.executable, "-I", "-B", str(script), *flags], cwd=tmp_path,
                              env={"PATH": "/usr/bin:/bin"}, capture_output=True, timeout=10, check=False)
    ordinary = plan("--billing-journey")
    assert ordinary.returncode == 0
    assert "pre_send_identity_batching_candidate" not in json.loads(ordinary.stdout)
    candidate = plan("--billing-journey", "--pre-send-identity-batching")
    assert candidate.returncode == 0
    value = json.loads(candidate.stdout)
    assert value["pre_send_identity_batching_candidate"] is True
    assert value["cohort_window_seconds"] == 0.002 and value["maximum_waiters"] == 32
    assert value["shared_admin_limit"] == 40 and value["maximum_requests_including_controls"] == 79
    assert value["external_calls"] == 0
    assert value["production_transport_sharing_proven"] is value["production_qualified"] is False
    assert plan("--pre-send-identity-batching").returncode != 0
    assert plan("--jobs-journey", "--pre-send-identity-batching").returncode != 0
