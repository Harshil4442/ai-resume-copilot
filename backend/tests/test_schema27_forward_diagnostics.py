"""Typed exact-read retries and metadata-only actionable failure output."""
from __future__ import annotations

import json

import pytest
from infra.gcp import schema27_forward_diagnostics as d


class BadStatusCodeError(Exception):
    def __init__(self, status):
        super().__init__("secret URL https://private.invalid/?token=synthetic-secret")
        self.status_code = status
        self.content = json.dumps({"error": {"message": "synthetic-secret", "details": [
            {"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "metadata": {"private": "synthetic-secret"}}]}})


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_only_proven_transient_status_retries_same_read_with_one_deadline(monkeypatch, status):
    clock, resources = [0.0], []
    monkeypatch.setattr(d.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(d.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    budget = d.ReadBudget()
    def read():
        resources.append("exact-original-project-policy")
        if len(resources) < 3:
            raise BadStatusCodeError(status)
        return {"etag": "same-native-etag"}
    assert budget.read("crm_project_policy_read", read, BadStatusCodeError) == {"etag": "same-native-etag"}
    assert resources == ["exact-original-project-policy"] * 3 and budget.deadline == 120


@pytest.mark.parametrize("status", [400, 401, 403, 404, "503"])
def test_denial_or_untyped_status_never_retries_and_diagnostic_keeps_safe_context(status):
    calls = []
    def read():
        calls.append(True)
        raise BadStatusCodeError(status)
    with pytest.raises(d.ForwardDenied) as failed:
        d.ReadBudget().read("crm_project_policy_read", read, BadStatusCodeError)
    result = d.diagnostic(failed.value, "stage")
    assert len(calls) == 1
    assert result["operation"] == "crm_project_policy_read"
    assert result["exception_class"] == "BadStatusCodeError"
    assert result["provider_reason"] == "ACCESS_TOKEN_SCOPE_INSUFFICIENT"
    assert "synthetic-secret" not in json.dumps(result) and "https://" not in json.dumps(result)
    assert result["http_status"] == (status if type(status) is int else None)


def test_unknown_transport_failure_and_mutation_label_never_authorize_retry():
    calls = []
    def read():
        calls.append(True)
        raise TimeoutError("synthetic-secret")
    with pytest.raises(d.ForwardDenied):
        d.ReadBudget().read("run_service_read", read, BadStatusCodeError)
    assert calls == [True]
    with pytest.raises(d.ForwardDenied, match="read_route_refused"):
        d.ReadBudget().read("promote", read, BadStatusCodeError)
    assert calls == [True]


def test_deadline_exhaustion_never_restarts_budget(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(d.time, "monotonic", lambda: clock[0])
    budget = d.ReadBudget(seconds=1)
    clock[0] = 2
    with pytest.raises(d.ForwardDenied, match="read_deadline"):
        budget.read("run_service_read", lambda: pytest.fail("expired read"), BadStatusCodeError)
    assert budget.deadline == 1
