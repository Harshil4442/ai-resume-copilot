"""Bounded native admission inventory; stock CLI execution is an offline fake."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from backend.tests.test_monetary_release_entrypoint import ROOT, release

PROJECTED_FORMAT = "--format=json(metadata.name,status.completionTime)"


def observed_inventory(monkeypatch, executions):
    calls = []
    encoded = executions if type(executions) is bytes else json.dumps(executions).encode()

    def command(arguments, **kwargs):
        calls.append(arguments)
        assert arguments[0] == "gcloud"
        assert kwargs["check"] is True and kwargs["timeout"] == 600
        if arguments[1:4] == ["tasks", "queues", "describe"]:
            value = {"state": "PAUSED"}
        elif arguments[1:3] == ["tasks", "list"]:
            value = []
        elif arguments[1:4] == ["scheduler", "jobs", "list"]:
            value = [{"state": "PAUSED"}]
        elif arguments[1:5] == ["run", "jobs", "executions", "list"]:
            assert arguments == ["gcloud", "run", "jobs", "executions", "list", "--region",
                release.REGION, "--limit", "257", "--project", release.PROJECT,
                PROJECTED_FORMAT, "--quiet"]
            return SimpleNamespace(stdout=encoded)
        else:
            pytest.fail("Only the existing read-only admission commands may run")
        assert "--format=json" in arguments and PROJECTED_FORMAT not in arguments
        return SimpleNamespace(stdout=json.dumps(value).encode())

    monkeypatch.setattr(release.subprocess, "run", command)
    return release.NativeBoundary(ROOT), calls


def completed(count):
    return [{"metadata": {"name": f"reviewed-job-{index}-execution"},
             "status": {"completionTime": "2026-10-21T19:42:29Z"}} for index in range(count)]


@pytest.mark.parametrize("count", [0, 39, 256])
def test_exact_cli_projection_preserves_completed_inventory_and_other_reads(monkeypatch, count):
    rows = completed(count)
    assert len(json.dumps(rows).encode()) < release.MAX_JSON == 131072
    boundary, calls = observed_inventory(monkeypatch, rows)
    boundary.verify_admission_closed()
    assert len(calls) == len(release.QUEUES) * 2 + 2
    assert len([call for call in calls if PROJECTED_FORMAT in call]) == 1
    assert calls[-1][1:5] == ["run", "jobs", "executions", "list"]


@pytest.mark.parametrize("executions", [
    None, {}, {"items": []}, [None], [True], ["malformed"], [[]],
    [{}], [{"status": None}], [{"status": False}], [{"status": []}],
    [{"status": {}}], [{"status": {"completionTime": ""}}],
    [{"status": {"completionTime": None}}],
    [{"status": {"completionTime": False}}],
    [{"metadata": {"name": "still-running"}, "status": {"runningCount": 1}}],
    completed(257),
])
def test_pending_malformed_or_overcount_inventory_stays_closed(monkeypatch, executions):
    boundary, calls = observed_inventory(monkeypatch, executions)
    with pytest.raises(release.ReleaseDenied, match="^job_execution_inventory_unresolved$"):
        boundary.verify_admission_closed()
    assert len([call for call in calls if PROJECTED_FORMAT in call]) == 1
    assert len(calls) == len(release.QUEUES) * 2 + 2


def test_completed_rows_cannot_hide_a_later_pending_execution(monkeypatch):
    rows = completed(39) + [{"metadata": {"name": "pending"}, "status": {}}]
    boundary, _ = observed_inventory(monkeypatch, rows)
    with pytest.raises(release.ReleaseDenied, match="^job_execution_inventory_unresolved$"):
        boundary.verify_admission_closed()


def test_projected_response_still_obeys_original_raw_size_bound_without_retry(monkeypatch):
    encoded = json.dumps([{"metadata": {"name": "x" * release.MAX_JSON},
                           "status": {"completionTime": "2026-10-21T19:42:29Z"}}]).encode()
    assert len(encoded) > release.MAX_JSON
    boundary, calls = observed_inventory(monkeypatch, encoded)
    with pytest.raises(release.ReleaseDenied, match="^observation_size_bound$"):
        boundary.verify_admission_closed()
    assert len([call for call in calls if PROJECTED_FORMAT in call]) == 1


def test_duplicate_projected_status_key_stays_refused(monkeypatch):
    boundary, calls = observed_inventory(monkeypatch,
        b'[{"metadata":{"name":"job"},"status":{},"status":{"completionTime":"done"}}]')
    with pytest.raises(release.ReleaseDenied, match="^duplicate_input_key$"):
        boundary.verify_admission_closed()
    assert len([call for call in calls if PROJECTED_FORMAT in call]) == 1


@pytest.mark.parametrize("arguments", [
    ("run", "jobs", "executions", "describe", "execution"),
    ("run", "jobs", "list"),
    ("run", "jobs", "executions", "list-unreviewed"),
    ("builds", "describe", "build"),
])
def test_projection_is_exclusive_to_exact_execution_list_command(monkeypatch, arguments):
    calls = []

    def command(argv, **_kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout=b"{}")

    monkeypatch.setattr(release.subprocess, "run", command)
    assert release.NativeBoundary(ROOT)._cloud(*arguments) == {}
    assert calls == [["gcloud", *arguments, "--project", release.PROJECT, "--format=json", "--quiet"]]
