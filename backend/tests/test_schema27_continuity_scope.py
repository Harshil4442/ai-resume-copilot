"""Synthetic credential custody and the real filesystem journal."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from infra.gcp import schema27_continuity_scope as continuity

NONCE = "f" * 32
VALUES = {"observer": "a" * 56, "runtime": "b" * 56, "coordinator": "c" * 56}


@pytest.fixture
def owned_bundle(tmp_path, monkeypatch):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    bundle = directory / "synthetic-role-bundle.json"
    bundle.write_text(json.dumps(VALUES))
    bundle.chmod(0o600)
    monkeypatch.setattr(continuity, "ROLE_BUNDLE", bundle)
    monkeypatch.setattr(continuity, "_verify_private_disk", lambda: None)
    return bundle


def test_real_journal_wrong_database_scope_refuses_before_credential_access(owned_bundle, tmp_path, monkeypatch):
    pin = continuity.capture_database_pin(NONCE)
    custody = continuity.BridgeCustody(NONCE, "e" * 64, pin)
    other = replace(custody, database=replace(pin, observer_commitment_sha256="d" * 64))
    path = tmp_path / "journal"
    path.mkdir(mode=0o700)
    journal = continuity.rolling.Journal(path, custody)
    journal.record(continuity.JOURNAL_BINDING, custody.journal_binding())
    continuity.verify_journal(custody, journal)

    def unreachable():
        pytest.fail("a mismatched journal opened the role bundle")

    monkeypatch.setattr(continuity, "_observer_password", unreachable)
    with pytest.raises(continuity.rolling.RollingDenied, match="journal_owner_changed"):
        continuity.observe_journal(other, journal, continuity.neon.Purpose.BEFORE_SCHEMA27)
    relabeled = continuity.rolling.Journal(path, other)
    with pytest.raises(continuity.rolling.RollingDenied):
        continuity.observe_journal(other, relabeled, continuity.neon.Purpose.BEFORE_SCHEMA27)
    with pytest.raises(continuity.rolling.RollingDenied, match="database_pin_changed"):
        replace(pin, runtime_reference="hirewiz-fresh-runtime-db-20261013:2").validate()
    with pytest.raises(continuity.rolling.RollingDenied, match="database_pin_changed"):
        replace(pin, observer_source="hirewiz-fresh-observer-db-20261013:2").validate()


def test_exact_observer_credential_and_change_detection(owned_bundle, tmp_path, monkeypatch):
    pin = continuity.capture_database_pin(NONCE)
    custody = continuity.BridgeCustody(NONCE, "e" * 64, pin)
    path = tmp_path / "journal"
    path.mkdir(mode=0o700)
    journal = continuity.rolling.Journal(path, custody)
    journal.record(continuity.JOURNAL_BINDING, custody.journal_binding())
    observed = []

    def observe(purpose, *, observer_password):
        assert observer_password == VALUES["observer"]
        observed.append(purpose)
        return "owned-observation"

    monkeypatch.setattr(continuity.neon, "observe", observe)
    purpose = continuity.neon.Purpose.BEFORE_SCHEMA27
    assert continuity.observe_journal(custody, journal, purpose) == "owned-observation"
    assert observed == [purpose]
    owned_bundle.write_text(json.dumps(VALUES | {"observer": "f" * 56}))
    with pytest.raises(continuity.rolling.RollingDenied, match="observer_commitment_changed"):
        continuity.observe_journal(custody, journal, purpose)
    assert observed == [purpose]


def test_role_file_never_created_or_followed(owned_bundle, tmp_path):
    owned_bundle.chmod(0o644)
    with pytest.raises(continuity.rolling.RollingDenied, match="private_bundle_metadata_invalid"):
        continuity.capture_database_pin(NONCE)
    owned_bundle.unlink()
    with pytest.raises(continuity.rolling.RollingDenied, match="private_bundle_unavailable"):
        continuity.capture_database_pin(NONCE)
    assert not owned_bundle.exists()
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps(VALUES))
    owned_bundle.symlink_to(target)
    with pytest.raises(continuity.rolling.RollingDenied, match="private_bundle_unavailable"):
        continuity.capture_database_pin(NONCE)
