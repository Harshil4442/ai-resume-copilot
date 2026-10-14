"""Credential expiry and custody controls; no credential manager/API access."""
from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "infra/gcp"))
handoff = importlib.import_module("fresh_cutover_auth")
host = importlib.import_module("fresh_cutover_host_auth")


def packet(**changes):
    return json.dumps({"google_access_token": "synthetic-access", "github_token": "synthetic-github",
                       "google_expires_at": 4600, **changes}).encode()


def test_ram_custody_clears_on_unknown_execution_failure(monkeypatch, capsys):
    monkeypatch.delenv("CLOUDSDK_AUTH_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="synthetic-unknown"):
        with handoff.ram_authentication(packet(), now=1000):
            assert os.environ["CLOUDSDK_AUTH_ACCESS_TOKEN"] == "synthetic-access"
            assert os.environ["GH_TOKEN"] == "synthetic-github"
            raise RuntimeError("synthetic-unknown")
    assert "CLOUDSDK_AUTH_ACCESS_TOKEN" not in os.environ and "GH_TOKEN" not in os.environ
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("raw", [
    packet(google_expires_at=4544), packet(google_expires_at=5000),
    packet(google_expires_at=True), packet(google_expires_at=float("nan")),
    packet(google_expires_at=float("inf")), packet(google_access_token="bad\nvalue"),
    packet(extra="unapproved"), b'{"google_access_token":"first","google_access_token":"second"}',
    b"x" * 24577,
])
def test_invalid_or_insufficient_expiry_cannot_enter_operator(raw, monkeypatch):
    monkeypatch.delenv("CLOUDSDK_AUTH_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    entered = False
    with pytest.raises(ValueError):
        with handoff.ram_authentication(raw, now=1000):
            entered = True
    assert not entered and "CLOUDSDK_AUTH_ACCESS_TOKEN" not in os.environ


def test_existing_process_override_is_not_replaced(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "existing-synthetic")
    with pytest.raises(ValueError):
        with handoff.ram_authentication(packet(), now=1000):
            pytest.fail("Preexisting token override must refuse")
    assert os.environ["GH_TOKEN"] == "existing-synthetic"


def test_stock_fresh_loader_checks_actual_expiry_without_refresh_token_export():
    now = datetime(2026, 10, 14, tzinfo=UTC)
    calls = []
    store = SimpleNamespace(LoadFreshCredential=lambda **kwargs: (
        calls.append(kwargs) or SimpleNamespace(token="synthetic-access", expiry=now + timedelta(seconds=3599))))
    value = host.fresh_google_credential(store, now)
    assert calls == [{"account": host.ACCOUNT, "min_expiry_duration": "1h",
                      "allow_account_impersonation": False}]
    assert set(value) == {"google_access_token", "google_expires_at"}
    store.LoadFreshCredential = lambda **kwargs: SimpleNamespace(
        token="synthetic-access", expiry=now + timedelta(seconds=3569))
    with pytest.raises(ValueError, match="expiry_refused"):
        host.fresh_google_credential(store, now)
