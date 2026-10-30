"""Bounded local harness contracts; none of these qualify provider capacity."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.stage5_capacity import (
    PATHS,
    Plan,
    expected_capacity_refusal,
    finish_owned_http,
    release_native_context,
    scheduled_phase,
    summarize,
    workload_validation,
)
from scripts.stage5_resources import (
    EMULATOR_REPO_DIGEST,
    FORBIDDEN_PORTS,
    Resources,
    effective_publication,
    internal_endpoint,
    pinned_emulator_configuration,
    private_json,
    require_owned_internal_network,
    unused_port,
)


def configuration():
    return {**asdict(Plan()), "concurrency": [1, 10, 25]}


@pytest.mark.parametrize("key,value", [
    ("requests_per_phase", 501), ("requests_per_phase", True), ("measurement_seconds", 601),
    ("concurrency", [1, 10, 50]), ("buyers_percent", 24), ("users", 9999),
    ("arrivals_per_second", 0), ("heavy_opportunities", 501), ("measurement_seconds", 1),
])
def test_fixed_budget_and_cardinality_refuse_drift(key, value):
    with pytest.raises(ValueError):
        Plan.parse({**configuration(), key: value})


def test_both_required_buyer_scenarios_and_at_most_3000_requests():
    for percent in (20, 25):
        plan = Plan.parse({**configuration(), "buyers_percent": percent, "requests_per_phase": 499})
        assert plan.requests_per_phase * len(plan.concurrency) * 2 + 3 <= 3000
    with pytest.raises(ValueError):
        Plan.parse({**configuration(), "requests_per_phase": 500})
    with pytest.raises(ValueError):
        Plan.parse({**configuration(), "database_url": "synthetic-private-value"})


def test_pinned_oci_manifest_is_distinct_from_observed_platform_configuration():
    config = "sha256:" + "a" * 64
    assert pinned_emulator_configuration([{"Id": config, "RepoDigests": [EMULATOR_REPO_DIGEST]}]) == config
    for value in ([{"Id": config, "RepoDigests": []}],
                  [{"Id": "unknown", "RepoDigests": [EMULATOR_REPO_DIGEST]}],
                  [{"Id": config, "RepoDigests": ["foreign@sha256:" + "a" * 64]}]):
        with pytest.raises(RuntimeError):
            pinned_emulator_configuration(value)


def test_network_is_observed_internal_and_empty_before_any_emulator_start():
    network = {"Id": "owned", "Internal": True, "Containers": {},
               "Labels": {"hirewiz.stage5.owner": "nonce"}}
    require_owned_internal_network([network], identity="owned", nonce="nonce")
    for changed in ({"Internal": False}, {"Id": "foreign"}, {"Containers": {"foreign": {}}},
                    {"Labels": {"hirewiz.stage5.owner": "foreign"}}):
        with pytest.raises(RuntimeError):
            require_owned_internal_network([{**network, **changed}], identity="owned", nonce="nonce")


def test_scheduled_latency_includes_time_waiting_for_concurrency_slot():
    async def run():
        import time
        async def request(index):
            await asyncio.sleep(.04)
            return {"status": 200, "bytes": 4, "contract_valid": True}
        return await scheduled_phase(count=3, concurrency=1, arrivals_per_second=1000,
            deadline=time.perf_counter() + 1, request=request)
    rows = asyncio.run(run())
    assert rows[2]["queue_ms"] >= 40
    assert rows[2]["arrival_to_completion_ms"] > rows[2]["http_ms"] + 40
    assert summarize(rows)["successful_owned_responses"] == 3


def test_global_deadline_counts_unsent_and_inflight_work_without_replay():
    async def run():
        import time
        calls = []
        async def request(index):
            calls.append(index)
            await asyncio.sleep(.2)
            return {"status": 200}
        rows = await scheduled_phase(count=4, concurrency=1, arrivals_per_second=1000,
            deadline=time.perf_counter() + .03, request=request)
        return rows, calls
    rows, calls = asyncio.run(run())
    assert calls == [0]
    assert len(rows) == 4 and {row["status"] for row in rows} == {"deadline"}
    assert summarize(rows)["successful_owned_responses"] == 0


@pytest.mark.parametrize("expired_before_first", [False, True])
def test_actual_workload_retains_unstarted_phases_without_extra_rpc_observation(monkeypatch, expired_before_first):
    import httpx

    from scripts import run_stage5_capacity as runner

    clock, calls = [100.0], {"snapshot": 0, "redis": 0, "schedule": 0}
    class InertClient:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            if expired_before_first:
                clock[0] = 701.0
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, *args, **kwargs):
            pytest.fail("Deadline-only synthetic phase must not send HTTP")
    def snapshot():
        calls["snapshot"] += 1
        return {}
    def info(*args):
        calls["redis"] += 1
        return {"total_commands_processed": 0}
    async def phase(**kwargs):
        calls["schedule"] += 1
        clock[0] = 701.0
        return [{"index": i, "status": "deadline", "bytes": 0, "queue_ms": 0,
                 "http_ms": 0, "arrival_to_completion_ms": 0} for i in range(kwargs["count"])]
    monkeypatch.setattr(httpx, "AsyncClient", InertClient)
    monkeypatch.setattr(runner, "ssl", SimpleNamespace(create_default_context=lambda **kwargs: None))
    monkeypatch.setattr(runner, "time", SimpleNamespace(perf_counter=lambda: clock[0]))
    monkeypatch.setattr(runner, "scheduled_phase", phase)
    for name in ("CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED", "CANDIDATE_METADATA_ADMISSION_ENABLED"):
        monkeypatch.setenv(name, "false")
    results, _ = asyncio.run(runner.workload(Plan(), SimpleNamespace(api_port=59999), {},
        SimpleNamespace(storage=SimpleNamespace(info=info)), SimpleNamespace(snapshot=snapshot),
        {"acquire_ms": []}, Path("/not-read")))
    unstarted = [row for row in results if row.get("state") == "not_started_deadline"]
    assert len(results) == 6 and len(unstarted) == (6 if expired_before_first else 5)
    assert sum(row["unsent_requests"] for row in unstarted) == (300 if expired_before_first else 250)
    assert all(row["offered"] == 0 and row["requests"] == []
               and row["http_p95_ms"] is None and row["successful_owned_responses_per_second"] is None
               for row in unstarted)
    assert calls == ({"snapshot": 0, "redis": 0, "schedule": 0} if expired_before_first else
                     {"snapshot": 2, "redis": 2, "schedule": 1})


def test_refusals_and_bad_owned_contracts_are_never_successful_throughput():
    rows = [{"status": value, "bytes": 10, "queue_ms": 0, "http_ms": 1,
             "arrival_to_completion_ms": 1, "contract_valid": valid}
            for value, valid in ((200, True), (200, False), (429, False), (503, False))]
    result = summarize(rows)
    assert result["offered"] == 4 and result["successful_owned_responses"] == 1
    assert result["contract_failures"] == 1 and result["status_counts"] == {"200": 2, "429": 1, "503": 1}


PREFLIGHT = {"missing_bearer_status": 401, "foreign_resume_status": 404,
             "bootstrap_status": 200, "bootstrap_contract_valid": True}
CAPACITY_BODY = {"detail": {"code": "NATIVE_METADATA_CAPACITY",
    "message": "Request capacity is temporarily allocated. Please try again later."}}
CAPACITY_HEADERS = {"retry-after": "60", "cache-control": "private, no-store", "pragma": "no-cache"}


@pytest.mark.parametrize("change", [None, "mode", "status", "body", "retry", "cache", "pragma"])
def test_capacity_refusal_requires_actual_typed_body_and_all_response_headers(change):
    mode, status, body, headers = "list_governed_40", 429, CAPACITY_BODY, dict(CAPACITY_HEADERS)
    if change == "mode":
        mode = "get_unpaced_diagnostic"
    elif change == "status":
        status = 503
    elif change == "body":
        body = {"detail": "Rate limited"}
    elif change:
        del headers[{"retry": "retry-after", "cache": "cache-control", "pragma": "pragma"}[change]]
    assert expected_capacity_refusal(mode, status, body, headers) is (change is None)


def measured_results(count=5):
    return [{"mode": mode, "concurrency": concurrency, "offered": count,
             "requests": [{"index": i, "status": 200, "contract_valid": True} for i in range(count)]}
        for mode in ("get_unpaced_diagnostic", "list_governed_40") for concurrency in (1, 10, 25)]


@pytest.mark.parametrize("failure", [None, "contract", "503", "401", "404", "transport_error",
    "deadline", "429_unknown", "429_get", "missing_phase", "empty_phase", "wrong_owner_denial",
    "missing_profile", "unstarted"])
def test_workload_validation_preserves_failures_and_expected_refusals_without_capacity_claim(failure):
    results, preflight = measured_results(), dict(PREFLIGHT)
    # A correctly identified governed refusal is never a successful response.
    results[-1]["requests"][0] = {"index": 0, "status": 429, "contract_valid": False, "capacity_refusal_valid": True}
    if failure == "contract":
        results[0]["requests"][0]["contract_valid"] = False
    elif failure in {"503", "401", "404", "transport_error", "deadline", "429_unknown", "429_get"}:
        results[0]["requests"][0] = {"index": 0, "status": int(failure) if failure.isdigit() else
            (429 if failure.startswith("429") else failure), "capacity_refusal_valid": failure == "429_get"}
    elif failure == "missing_phase":
        results.pop()
    elif failure == "empty_phase":
        results[0].update(requests=[], offered=0)
    elif failure == "wrong_owner_denial":
        preflight["foreign_resume_status"] = 200
    elif failure == "missing_profile":
        preflight["bootstrap_contract_valid"] = False
    elif failure == "unstarted":
        results[0]["state"] = "not_started_deadline"
    validation = workload_validation(results, requests_per_phase=5, preflight=preflight)
    assert validation["passed"] is (failure is None)
    assert validation["production_capacity_qualified"] is False
    assert bool(validation["failures"]) is (failure is not None)


@pytest.mark.parametrize("indices", [
    [0, 0, 0, 0, 0], [0, 1, 2, 3, None], [False, 1, 2, 3, 4],
    [-1, 1, 2, 3, 4], [0, 1, 2, 3, 5], [0.0, 1, 2, 3, 4], ["0", 1, 2, 3, 4],
])
def test_exact_phase_denominator_refuses_missing_duplicate_noninteger_and_outside_indices(indices):
    results = measured_results()
    for phase in results:
        for row, index in zip(phase["requests"], indices, strict=True):
            if index is None:
                del row["index"]
            else:
                row["index"] = index
    validation = workload_validation(results, requests_per_phase=5, preflight=PREFLIGHT)
    assert validation["passed"] is False and validation["failures"] == {"request_indices": 6}
    assert validation["production_capacity_qualified"] is False


def test_indexed_deadline_and_unknown_rows_stay_in_complete_denominator_but_fail_status():
    results = measured_results()
    results[0]["requests"][0].update(status="deadline", contract_valid=False)
    results[0]["requests"][1].update(status="transport_error", contract_valid=False)
    validation = workload_validation(results, requests_per_phase=5, preflight=PREFLIGHT)
    assert validation["failures"] == {"unexpected_status_or_error": 2}
    assert validation["passed"] is False


@pytest.mark.parametrize("failure", [None, "contract", "preflight", "cleanup"])
def test_actual_cli_preserves_report_and_returns_nonzero_for_any_measured_failure(tmp_path, monkeypatch, capsys, failure):
    from scripts import run_stage5_capacity as runner

    output, calls = tmp_path / "owned", []
    class InertResources:
        def __init__(self, directory, *args):
            self.directory, self.cleanup = directory, []
            directory.mkdir(mode=0o700)
        def close(self):
            calls.append("cleanup")
            self.cleanup.append({"synthetic_terminal": failure != "cleanup"})
            if failure == "cleanup":
                raise RuntimeError("synthetic-private-error")
    def execute(plan, resources):
        results, preflight = measured_results(plan.requests_per_phase), dict(PREFLIGHT)
        resources.partial_report.update(results=results, preflight=preflight, synthetic_test=True)
        if failure == "preflight":
            preflight["foreign_resume_status"] = 200
            raise RuntimeError("synthetic-private-error")
        if failure == "contract":
            results[0]["requests"][0]["contract_valid"] = False
        # The actual CLI independently recomputes; a producer's stale success
        # annotation cannot hide a measured bad body or failed control.
        resources.partial_report["validation"] = {"passed": True}
        return resources.partial_report
    monkeypatch.setattr(runner, "Resources", InertResources)
    monkeypatch.setattr(runner, "execute", execute)
    monkeypatch.setattr(sys, "argv", ["runner", "--execute", "--output", str(output),
                                    "--pg-bin", "/not-opened", "--redis-server", "/not-opened"])
    assert runner.main() == (0 if failure is None else 1)
    report = json.loads((output / "report.json").read_text())
    assert report["validation"]["passed"] is (failure is None)
    assert report["validation"]["production_capacity_qualified"] is False
    assert report["synthetic_test"] and calls == ["cleanup"]
    assert report["cleanup_verified"] is (failure != "cleanup")
    assert (output / "cleanup.json").is_file()
    assert len(report["results"]) == 6
    assert "synthetic-private-error" not in capsys.readouterr().err


def test_cli_does_not_write_failure_into_preexisting_unowned_output(tmp_path, monkeypatch):
    from scripts import run_stage5_capacity as runner
    monkeypatch.setattr(sys, "argv", ["runner", "--execute", "--output", str(tmp_path),
                                    "--pg-bin", "/not-opened", "--redis-server", "/not-opened"])
    assert runner.main() == 1 and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("response_kind", ["missing_profile", "governed", "untyped_429"])
def test_actual_workload_collector_validates_refusal_wire_and_missing_profile_without_sockets(monkeypatch, response_kind):
    import httpx

    from scripts import run_stage5_capacity as runner

    class InertClient:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, path, **kwargs):
            assert path in PATHS and kwargs["headers"]["Authorization"] == "Bearer synthetic-only"
            return httpx.Response(200 if response_kind == "missing_profile" else 429,
                json={"profile": None} if response_kind == "missing_profile" else
                    CAPACITY_BODY if response_kind == "governed" else {"detail": "other"},
                headers=CAPACITY_HEADERS)
    async def phase(**kwargs):
        rows = []
        for i in range(kwargs["count"]):
            row = await kwargs["request"](i)
            rows.append({**row, "index": i, "queue_ms": 0, "http_ms": 1, "arrival_to_completion_ms": 1})
        return rows
    monkeypatch.setattr(httpx, "AsyncClient", InertClient)
    monkeypatch.setattr(runner, "ssl", SimpleNamespace(create_default_context=lambda **kwargs: None))
    monkeypatch.setattr(runner, "scheduled_phase", phase)
    for name in ("CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED", "CANDIDATE_METADATA_ADMISSION_ENABLED"):
        monkeypatch.setenv(name, "false")
    results, _ = asyncio.run(runner.workload(Plan(requests_per_phase=5), SimpleNamespace(api_port=59999),
        dict.fromkeys(range(1, 9), "synthetic-only"),
        SimpleNamespace(storage=SimpleNamespace(info=lambda *args: {"total_commands_processed": 0})),
        SimpleNamespace(snapshot=lambda: {}), {"acquire_ms": []}, Path("/not-read")))
    assert len(results) == 6
    for result in results:
        valid_refusal = response_kind == "governed" and result["mode"] == "list_governed_40"
        assert result["successful_owned_responses"] == 0
        assert result["validated_capacity_refusals"] == (5 if valid_refusal else 0)
        assert all(row["contract_valid"] is False and row["capacity_refusal_valid"] is valid_refusal
                   for row in result["requests"])


def test_completed_phase_is_retained_when_later_observation_fails_without_sockets(monkeypatch):
    import httpx

    from scripts import run_stage5_capacity as runner

    class InertClient:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
    calls = []
    def snapshot():
        calls.append("observation")
        if len(calls) == 3:
            raise RuntimeError("synthetic later observation failed")
        return {}
    async def phase(**kwargs):
        return [{"index": i, "status": 200, "contract_valid": True, "bytes": 1,
                 "queue_ms": 0, "http_ms": 1, "arrival_to_completion_ms": 1} for i in range(kwargs["count"])]
    monkeypatch.setattr(httpx, "AsyncClient", InertClient)
    monkeypatch.setattr(runner, "ssl", SimpleNamespace(create_default_context=lambda **kwargs: None))
    monkeypatch.setattr(runner, "scheduled_phase", phase)
    for name in ("CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED", "CANDIDATE_METADATA_ADMISSION_ENABLED"):
        monkeypatch.setenv(name, "false")
    resources = SimpleNamespace(api_port=59999, partial_report={})
    with pytest.raises(RuntimeError, match="later observation"):
        asyncio.run(runner.workload(Plan(requests_per_phase=5), resources, {},
            SimpleNamespace(storage=SimpleNamespace(info=lambda *args: {"total_commands_processed": 0})),
            SimpleNamespace(snapshot=snapshot), {"acquire_ms": []}, Path("/not-read")))
    assert len(resources.partial_report["results"]) == 1
    assert resources.partial_report["results"][0]["offered"] == 5


def test_no_configurable_provider_destination_and_no_payment_mutation_path():
    assert len(PATHS) == 5 and all(path.startswith("/api/") for path in PATHS)
    assert not {"POST", "pay", "submit", "search"} & set(PATHS)
    assert unused_port() not in FORBIDDEN_PORTS


def test_private_reports_create_only_and_no_secret_overwrite(tmp_path):
    path = tmp_path / "report.json"
    private_json(path, {"numeric": 1})
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        private_json(path, {"numeric": 2})
    assert json.loads(path.read_text()) == {"numeric": 1}


def test_http_shutdown_failure_keeps_egress_guard_and_still_closes_every_owned_dependency():
    calls = []
    server = SimpleNamespace(should_exit=False)
    thread = SimpleNamespace(join=lambda **kwargs: calls.append(("join", kwargs)), is_alive=lambda: True)
    guards = SimpleNamespace(undo=lambda: calls.append(("guard", None)))
    with pytest.raises(RuntimeError, match="finalization is incomplete"):
        finish_owned_http(server=server, thread=thread, guards=guards, finalizers=[
            lambda terminal: calls.append(("SQL", terminal)),
            lambda terminal: calls.append(("Redis", terminal)),
            lambda terminal: calls.append(("keys", terminal))])
    assert server.should_exit and calls == [("join", {"timeout": 15}),
        ("SQL", False), ("Redis", False), ("keys", False)]


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_http_shutdown_interruption_cannot_skip_guarded_finalizers(interruption):
    calls = []
    def interrupted(**kwargs):
        raise interruption()
    with pytest.raises(RuntimeError, match="finalization is incomplete"):
        finish_owned_http(server=SimpleNamespace(should_exit=False),
            thread=SimpleNamespace(join=interrupted, is_alive=lambda: True),
            guards=SimpleNamespace(undo=lambda: calls.append("unrestricted")),
            finalizers=[lambda terminal: calls.append(("native_held", terminal)),
                        lambda terminal: calls.append(("Redis_closed", terminal))])
    assert calls == [("native_held", False), ("Redis_closed", False)]


def test_a_failed_owned_finalizer_does_not_skip_later_cleanup_and_restores_only_terminal_guard():
    calls = []
    def failed(terminal):
        assert terminal
        calls.append("failed")
        raise RuntimeError("synthetic private exception must not escape")
    with pytest.raises(RuntimeError, match="Owned HTTP or dependency finalization is incomplete"):
        finish_owned_http(server=None, thread=None,
            guards=SimpleNamespace(undo=lambda: calls.append("guard")),
            finalizers=[failed, lambda terminal: calls.append("Redis")])
    assert calls == ["failed", "Redis", "guard"]


@pytest.mark.parametrize("terminal", [True, False])
def test_native_factory_context_is_not_unwound_until_http_is_terminal(monkeypatch, terminal):
    import scripts.stage5_capacity as module
    retained = []
    monkeypatch.setattr(module, "_RETAINED_NATIVE_CONTEXTS", retained)
    state = {"native_installed": False, "egress_blocked": True}
    events = []
    @contextmanager
    def native_fixture():
        state["native_installed"] = True
        try:
            yield
        finally:
            assert state["egress_blocked"]
            events.append("native_unwind")
            state["native_installed"] = False
    stack = ExitStack()
    stack.enter_context(native_fixture())
    def joined(**kwargs):
        assert state["native_installed"] and state["egress_blocked"]
        events.append("http_join")
    def restore_guard():
        assert not state["native_installed"]
        state["egress_blocked"] = False
        events.append("egress_restore")
    arguments = {"server": SimpleNamespace(should_exit=False),
        "thread": SimpleNamespace(join=joined, is_alive=lambda: not terminal),
        "guards": SimpleNamespace(undo=restore_guard),
        "finalizers": [lambda done: release_native_context(stack, done)]}
    if terminal:
        finish_owned_http(**arguments)
        assert events == ["http_join", "native_unwind", "egress_restore"] and not retained
    else:
        with pytest.raises(RuntimeError):
            finish_owned_http(**arguments)
        assert state == {"native_installed": True, "egress_blocked": True}
        assert events == ["http_join"] and retained == [stack]
        # Synthetic test cleanup after representing terminal workers. The CLI
        # schedules no automatic delayed unwind on its stuck-worker path.
        stack.close()


def test_new_owned_directory_required_and_existing_is_never_adopted(tmp_path):
    with pytest.raises(ValueError, match="must be new"):
        Resources(tmp_path, Path("/not-used"), Path("/not-used"))


def test_emulator_cleanup_refuses_a_different_nonce_before_any_delete():
    resource = object.__new__(Resources)
    resource.container_id, resource.network_id = "retained-id", None
    resource.nonce = "original-owner"
    resource.cleanup, resource.processes, resource.logs = [], [], []
    calls = []
    def command(arguments):
        calls.append(arguments)
        return json.dumps([{"Id": "retained-id", "Config": {"Labels": {"hirewiz.stage5.owner": "different-owner"}}}])
    resource.command = command
    with pytest.raises(RuntimeError, match="cleanup refused"):
        resource.close()
    assert calls == [["docker", "inspect", "retained-id"]]
    assert resource.cleanup == [{"component": "emulator", "cleanup": "refused_or_unknown"}]


@pytest.mark.parametrize("drift", ["nonce", "live_cluster", "symlink"])
def test_scratch_cleanup_never_deletes_drifted_or_live_cluster(tmp_path, drift):
    resource = object.__new__(Resources)
    resource.container_id = resource.network_id = None
    resource.processes, resource.logs, resource.cleanup = [], [], []
    resource.directory, resource.nonce, resource.pg_bin = tmp_path, "own", Path("/unused")
    data = tmp_path / "pg-data"
    if drift == "symlink":
        target = tmp_path / "foreign"
        target.mkdir()
        (target / "PG_VERSION").write_text("17")
        data.symlink_to(target, target_is_directory=True)
    else:
        data.mkdir()
        (data / "PG_VERSION").write_text("17")
    resource.pg_data = data
    (tmp_path / "ownership.json").write_text(json.dumps({"nonce": "foreign" if drift == "nonce" else "own"}))
    resource.command = lambda *args: "Database cluster state: in production\nDatabase system identifier: 123\n"
    with pytest.raises(RuntimeError, match="cleanup refused"):
        resource.close()
    assert data.exists() and (data / "PG_VERSION").is_file()


@pytest.mark.parametrize("observed_id,anchor,saved", [
    ("111", "111", "111"), ("222", "111", "111"), ("111", None, "111"),
    ("111", "111", "222"),
])
def test_shutdown_scratch_must_match_original_native_creation_identity(tmp_path, observed_id, anchor, saved):
    resource = object.__new__(Resources)
    resource.container_id = resource.network_id = None
    resource.processes, resource.logs, resource.cleanup = [], [], []
    resource.directory, resource.nonce, resource.pg_bin = tmp_path, "own", Path("/unused")
    resource.pg_system_identifier = anchor
    resource.pg_data = data = tmp_path / "pg-data"
    data.mkdir()
    (data / "PG_VERSION").write_text("17")
    (tmp_path / "ownership.json").write_text(json.dumps({"nonce": "own"}))
    (tmp_path / "pg-creation-identity.json").write_text(json.dumps({"nonce": "own", "system_identifier": saved}))
    resource.command = lambda *args: "Database cluster state: shut down\nDatabase system identifier: " + observed_id + "\n"
    if observed_id == anchor == saved == "111":
        resource.close()
        assert not data.exists() and resource.cleanup[-1]["local_system_identifier"] == "111"
    else:
        with pytest.raises(RuntimeError):
            resource.close()
        assert data.exists() and resource.cleanup[-1]["cleanup"] == "refused_or_unknown"


def test_unknown_docker_cleanup_does_not_prevent_stopping_other_owned_process(monkeypatch):
    import scripts.stage5_resources as module
    resource = object.__new__(Resources)
    resource.container_id, resource.network_id, resource.nonce = "own", None, "own"
    resource.processes, resource.logs, resource.cleanup = [object()], [], []
    resource.command = lambda *args: "[]"
    stopped = []
    monkeypatch.setattr(module, "_stop", lambda process: stopped.append(process) or {"verified_stopped": True})
    with pytest.raises(RuntimeError):
        resource.close()
    assert stopped == resource.processes and resource.cleanup[0]["verified_stopped"]


def test_plan_mode_has_no_database_or_application_dependency(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/run_stage5_capacity.py"
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                            timeout=10, env={"PATH": "/usr/bin:/bin"}, cwd=tmp_path)
    assert result.returncode == 0
    assert json.loads(result.stdout)["production_qualified"] is False
    assert not result.stderr


def test_linux_manual_workflow_has_no_cloud_authority_and_requires_real_dependencies():
    import yaml
    path = Path(__file__).resolve().parents[2] / ".github/workflows/stage5-owned-capacity.yml"
    source = path.read_text()
    workflow = yaml.load(source, Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch", "pull_request"}
    trigger = workflow["on"]["pull_request"]
    assert trigger["branches"] == ["main"]
    assert len(trigger["paths"]) == 11 and "backend/app/**" not in trigger["paths"]
    assert all("*" not in path and not path.startswith("docs/") for path in trigger["paths"])
    assert workflow["permissions"] == {"contents": "read"}
    assert "${{ secrets." not in source and "google-github-actions/auth" not in source
    assert "id-token" not in source and "service_account" not in source
    job = workflow["jobs"]["owned-linux"]
    assert job["steps"][0]["with"] == {
        "ref": "${{ github.event.pull_request.head.sha || github.sha }}", "persist-credentials": "false"}
    assert job["strategy"]["matrix"]["buyers"] == ["20", "25"]
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    assert "test -x \"$owned_pg_root/runtime/bin/initdb\"" in runs
    assert "postgresql-17.11.tar.bz2" in runs
    assert "dd27f2b3c59e73ed14aa3324901242bf69a032a6347805f274e6260322d42979" in runs
    assert "--pg-bin \"$OWNED_PG_BIN\"" in runs
    assert "sudo" not in runs and "apt-get" not in runs
    assert "HIREWIZ_STAGE5_PG_REQUIRED" in source and "test_stage5_seed_postgres.py" in runs
    assert "1b083a6a9647024a98d4f38c2b51906a0f61ca36adcba11672b8933544962efe" in runs
    assert "fdbdee0a19a12e61c2eca46f4162f6d3ede3a01e0baa8a12cee1f35f3391f1e9" in runs
    assert "run_stage5_capacity.py --execute" in runs
    assert "--redis-server" in runs and "--pg-bin" in runs
    artifacts = job["steps"][-1]["with"]["path"].splitlines()
    assert len(artifacts) == 4 and all(path.endswith(("report.json", "cleanup.json", "failure.json", "emulator-transport.json")) for path in artifacts)


def test_effective_publication_is_not_the_requested_host_configuration():
    requested = {"HostConfig": {"PortBindings": {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "59999"}]}},
                 "NetworkSettings": {"Ports": {"8080/tcp": None}}}
    assert effective_publication(requested, 59999) is False
    requested["NetworkSettings"]["Ports"]["8080/tcp"] = [{"HostIp": "127.0.0.1", "HostPort": "59999"}]
    assert effective_publication(requested, 59999) is True
    requested["NetworkSettings"]["Ports"]["8080/tcp"] = [{"HostIp": "0.0.0.0", "HostPort": "59999"}]
    with pytest.raises(RuntimeError):
        effective_publication(requested, 59999)


def test_relay_target_requires_exact_current_native_network_membership():
    import copy
    container = {"Id": "container", "Config": {"Labels": {"hirewiz.stage5.owner": "nonce"}},
        "NetworkSettings": {"Networks": {"own": {"NetworkID": "network", "IPAddress": "172.20.0.2"}}}}
    network = {"Id": "network", "Internal": True, "Driver": "bridge",
        "Labels": {"hirewiz.stage5.owner": "nonce"},
        "Containers": {"container": {"IPv4Address": "172.20.0.2/24"}},
        "IPAM": {"Config": [{"Subnet": "172.20.0.0/24", "Gateway": "172.20.0.1"}]}}
    assert internal_endpoint(container, network, container_id="container", network_id="network",
                             nonce="nonce") == (("172.20.0.2", 8080), "172.20.0.0/24")
    for change in ("container", "network", "subnet", "outside", "gateway", "external", "other_member"):
        c, n = copy.deepcopy(container), copy.deepcopy(network)
        if change == "container":
            c["Id"] = "foreign"
        elif change == "network":
            c["NetworkSettings"]["Networks"]["own"]["NetworkID"] = "foreign"
        elif change == "subnet":
            n["IPAM"]["Config"][0]["Subnet"] = "172.21.0.0/24"
        elif change == "outside":
            c["NetworkSettings"]["Networks"]["own"]["IPAddress"] = "8.8.8.8"
        elif change == "gateway":
            c["NetworkSettings"]["Networks"]["own"]["IPAddress"] = "172.20.0.1"
        elif change == "external":
            n["Internal"] = False
        else:
            n["Containers"]["foreign"] = {}
        with pytest.raises((RuntimeError, ValueError)):
            internal_endpoint(c, n, container_id="container", network_id="network", nonce="nonce")


@pytest.mark.parametrize("address,subnet,port", [
    ("8.8.8.8", "8.0.0.0/8", 8080), ("169.254.169.254", "169.254.0.0/16", 8080),
    ("127.0.0.1", "127.0.0.0/8", 8080), ("172.20.0.2", "172.21.0.0/24", 8080),
    ("172.20.0.2", "172.20.0.0/24", 443), ("example.invalid", "172.20.0.0/24", 8080),
    ("172.20.0.0", "172.20.0.0/24", 8080), ("::1", "::/0", 8080),
])
def test_relay_has_no_external_dns_or_alternative_port_target(address, subnet, port):
    from scripts.stage5_relay import validate_target
    with pytest.raises(ValueError):
        validate_target(address, subnet, port)


def test_relay_preserves_actual_binary_bytes_both_ways_without_network():
    import socket
    import time
    from concurrent.futures import ThreadPoolExecutor

    from scripts.stage5_relay import forward_pair

    with ExitStack() as stack:
        sockets = [stack.enter_context(value) for value in (*socket.socketpair(), *socket.socketpair())]
        client, left, right, backend = sockets
        for value in sockets:
            value.settimeout(1)
        counts = []
        first, second = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n\x00\xff" * 100, b"\x00\x00\x00\x04\x00\x00\x00\x00\x00" * 100
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(forward_pair, left, right, deadline=time.monotonic() + 2, account=counts.append)
            client.sendall(first)
            actual = b""
            while len(actual) < len(first):
                actual += backend.recv(len(first) - len(actual))
            assert actual == first
            backend.sendall(second)
            actual = b""
            while len(actual) < len(second):
                actual += client.recv(len(second) - len(actual))
            assert actual == second
            client.shutdown(socket.SHUT_WR)
            future.result(timeout=1)
        assert sum(counts) == len(first) + len(second)


def test_relay_rejects_config_hash_drift_before_any_listener(tmp_path, monkeypatch, capsys):
    import scripts.stage5_relay as relay
    config = tmp_path / "config.json"
    config.write_text('{"synthetic_private_value":"do-not-print"}')
    config.chmod(0o600)
    monkeypatch.setattr(sys, "argv", ["stage5_relay.py", str(config), "0" * 64, str(tmp_path / "ready.json")])
    monkeypatch.setattr(relay, "serve", lambda *args: pytest.fail("Unbound config reached listener"))
    assert relay.main() == 1 and "do-not-print" not in capsys.readouterr().err


def test_relay_stop_is_proved_before_docker_endpoint_can_be_released(monkeypatch):
    import scripts.stage5_resources as module
    resource = object.__new__(Resources)
    resource.container_id, resource.network_id, resource.nonce = "own", "network", "own"
    resource.processes, resource.logs, resource.cleanup = [object()], [], []
    calls = []
    resource.command = lambda *args: calls.append("docker") or "[]"
    def uncertain(process):
        raise RuntimeError("Owned process is not terminal")
    monkeypatch.setattr(module, "_stop", uncertain)
    with pytest.raises(RuntimeError):
        resource.close()
    assert not calls and resource.container_id == "own" and resource.network_id == "network"
