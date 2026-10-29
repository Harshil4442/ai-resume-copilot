"""Bounded local harness contracts; none of these qualify provider capacity."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.stage5_capacity import PATHS, Plan, finish_owned_http, scheduled_phase, summarize
from scripts.stage5_resources import (
    EMULATOR_REPO_DIGEST,
    FORBIDDEN_PORTS,
    Resources,
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


def test_refusals_and_bad_owned_contracts_are_never_successful_throughput():
    rows = [{"status": value, "bytes": 10, "queue_ms": 0, "http_ms": 1,
             "arrival_to_completion_ms": 1, "contract_valid": valid}
            for value, valid in ((200, True), (200, False), (429, False), (503, False))]
    result = summarize(rows)
    assert result["offered"] == 4 and result["successful_owned_responses"] == 1
    assert result["contract_failures"] == 1 and result["status_counts"] == {"200": 2, "429": 1, "503": 1}


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
    assert stopped == resource.processes and resource.cleanup[-1]["verified_stopped"]


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
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    assert "${{ secrets." not in source and "google-github-actions/auth" not in source
    assert "id-token" not in source and "service_account" not in source
    job = workflow["jobs"]["owned-linux"]
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
    assert len(artifacts) == 3 and all(path.endswith(("report.json", "cleanup.json", "failure.json")) for path in artifacts)
