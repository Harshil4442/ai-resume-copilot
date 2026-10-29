"""Explicit synthetic public-release authority for financial adapter unit tests.

These tests exercise wallet/delivery state machines, not canary consent. Actual
canary audience, denominator and consent behavior has separate SQL/PG tests.
No production configuration, human qualification or invoice is provided here.
"""
from types import SimpleNamespace

from backend.app.domains.common import payload_fingerprint
from backend.app.domains.credits import release


def reviewed_runtime(monkeypatch, config):
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_AUDIENCE", "reviewed_public")
    monkeypatch.setattr(release, "current_review", lambda **_: SimpleNamespace(
        policy=config.policy, evidence={"delivery_routes": payload_fingerprint(config.model_dump(mode="json"))}))
