"""Finite public-source/marker refusals; no native or release qualification."""
from pathlib import Path

import pytest

from scripts import notification_schema27_contract as contract

ROOT = Path(__file__).resolve().parents[2]


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class MarkerPort:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql):
        assert str(sql).startswith('SELECT version_num FROM "public".alembic_version LIMIT 2')
        return Rows(self.rows)


def test_exact_reviewed_source_preserves_old26_migration_and_definition():
    assert contract.verify_source(ROOT)


def test_changed27_source_refuses_without_changing_old26_pins(monkeypatch):
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda path: original(path)+b"\n# changed\n"
                        if path == ROOT/contract.MIGRATION else original(path))
    with pytest.raises(contract.Schema27Denied, match="source_changed"):
        contract.verify_source(ROOT)


@pytest.mark.parametrize("rows", [[contract.BEFORE], [], [contract.SCHEMA, contract.SCHEMA]])
def test_only_one_exact27_marker_can_represent27(rows):
    with pytest.raises(contract.Schema27Denied, match="exact_marker"):
        contract.marker(MarkerPort(rows))


def test26_historical_marker_reader_does_not_accept27():
    assert contract.marker(MarkerPort([contract.SCHEMA])) == contract.SCHEMA
    with pytest.raises(contract.before.Schema26Denied, match="exact_marker"):
        contract.before.marker(MarkerPort([contract.SCHEMA]))


@pytest.mark.parametrize("name", contract.DISABLED_FLAGS)
def test_each_proposal_flag_remains_strict_false_and_is_not_native_evidence(name):
    flags = dict.fromkeys(contract.DISABLED_FLAGS, "false")
    contract.require_disabled(flags)
    for value in (True, "true", None, "False"):
        flags[name] = value
        with pytest.raises(contract.Schema27Denied, match="admission"):
            contract.require_disabled(flags)
