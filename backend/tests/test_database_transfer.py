"""Pure protocol/refusal checks; no PostgreSQL, provider or credential acquisition."""

from __future__ import annotations

import struct
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import database_transfer as transfer


def _wire(*rows):
    encoded = transfer.COPY_HEADER + struct.pack("!II", 0, 0)
    for row in rows:
        encoded += struct.pack("!h", len(row))
        for value in row:
            encoded += struct.pack("!i", -1 if value is None else len(value))
            if value is not None:
                encoded += value
    return encoded + struct.pack("!h", -1)


@pytest.mark.parametrize("width", [1, 2, 3, 7, 64])
def test_copy_binary_exact_rows_survive_arbitrary_short_chunks(width):
    wire = _wire((None, b"", b"\x00\xff"), (b"same",), (b"same",))
    rows = list(transfer._rows([wire[i:i + width] for i in range(0, len(wire), width)],
                              transfer.Limits(), time.monotonic() + 10))
    assert len(rows) == 3 and rows[1] == rows[2] and rows[0] != rows[1]
    assert rows[0] == struct.pack("!hiii", 3, -1, 0, 2) + b"\x00\xff"


@pytest.mark.parametrize("wire,reason", [
    (_wire((b"abc",))[:-1], "copy_incomplete"),
    (_wire((b"abc",)) + b"tail", "copy_after_trailer"),
    (transfer.COPY_HEADER + struct.pack("!II", 1, 0), "copy_header_refused"),
    (transfer.COPY_HEADER + struct.pack("!IIh", 0, 0, -2), "copy_column_bound"),
    (transfer.COPY_HEADER + struct.pack("!IIhi", 0, 0, 1, -2), "copy_field_bound"),
])
def test_partial_malformed_or_trailing_binary_copy_is_refused(wire, reason):
    with pytest.raises(transfer.TransferDenied, match="^" + reason + "$"):
        list(transfer._rows([wire], transfer.Limits(), time.monotonic() + 10))


def test_global_content_budget_does_not_reset_for_the_next_relation():
    wire = _wire((b"example",))
    budget = transfer._ContentBudget(len(wire) * 2, 1)
    assert len(list(transfer._rows([wire], transfer.Limits(), time.monotonic() + 10, budget))) == 1
    with pytest.raises(transfer.TransferDenied, match="^aggregate_content_bound$"):
        list(transfer._rows([wire], transfer.Limits(), time.monotonic() + 10, budget))


def _access(database_oid):
    return transfer.BoundDatabase(transfer.NativeDatabaseIdentity(
        "127.0.0.1", 55433, "hw_transfer_" + "a" * 32 + "_source",
        "b" * 64, database_oid, "hirewiz", 10, "", "disposable-local",
    ), "synthetic-not-provider")


def test_alias_final_authority_and_once_marker_refuse_before_any_connection(tmp_path, monkeypatch):
    def forbidden(_self):
        raise AssertionError("Native connection was forbidden")
    monkeypatch.setattr(transfer.BoundDatabase, "connect", forbidden)
    workdir = tmp_path / "attempt"
    workdir.mkdir(mode=0o700)
    tools = transfer.PostgresTools(Path("/unused/dump"), Path("/unused/restore"), "c" * 64, "d" * 64)
    source, target = _access(100), _access(101)
    with pytest.raises(transfer.TransferDenied, match="^source_destination_alias$"):
        transfer.transfer(source, source, tools, workdir)
    with pytest.raises(transfer.TransferDenied, match="^final_native_cut_required$"):
        transfer.transfer(source, target, tools, workdir, purpose="final")
    (workdir / "transfer.started").touch(mode=0o600)
    with pytest.raises(transfer.TransferDenied, match="^attempt_already_started_reconcile_before_retry$"):
        transfer.transfer(source, target, tools, workdir)


def test_ambient_redirect_settings_are_refused_without_reading_a_secret(monkeypatch):
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.10")
    with pytest.raises(transfer.TransferDenied, match="^ambient_libpq_refused$"):
        _access(100).environment(readonly=True)


@pytest.mark.parametrize("age,offset", [(31, 0), (-1, 0), (0, 1)])
def test_cut_cannot_be_stale_future_or_changed(age, offset):
    stable = datetime.now(UTC) - timedelta(minutes=10)
    class Boundary:
        def verify_native_cut(self, _source, _target):
            return transfer.NativeCutObservation(datetime.now(UTC) - timedelta(seconds=age),
                                                 stable + timedelta(seconds=offset))
    with pytest.raises(transfer.TransferDenied, match="^native_writer_cut_changed_or_stale$"):
        transfer._cut(Boundary(), _access(100), _access(101), stable)


def test_limits_cannot_be_silently_expanded():
    with pytest.raises(transfer.TransferDenied, match="^transfer_limits_refused$"):
        transfer.Limits(total_seconds=1801).validate()
    with pytest.raises(transfer.TransferDenied, match="^transfer_limits_refused$"):
        transfer.Limits(rows=True).validate()


def _locale_catalog():
    return {
        "settings": ("UTF8", "i", None, None, "und", None, "73.2", "73.2"),
        "collations": [("public", "candidate_text", -1, "i", False, None, None,
                        "und-u-ks-level2", None, "73.2", "73.2")],
        "index_collations": [("public", "users", "candidate_name_idx", 1,
                              "public", "candidate_text", -1)],
    }


@pytest.mark.parametrize("field,value", [
    ("database_rules", "[reorder Grek]"),
    ("database_version", "74.1"),
    ("determinism", True),
    ("collation_rules", "[reorder Latn]"),
    ("index_collation", "another_collation"),
])
def test_same_locale_names_do_not_mask_changed_unicode_ordering(field, value):
    source, target = _locale_catalog(), _locale_catalog()
    if field.startswith("database"):
        changed = list(target["settings"])
        if field == "database_rules":
            changed[5] = value
        else:
            changed[6] = changed[7] = value
        target["settings"] = tuple(changed)
    elif field == "index_collation":
        changed = list(target["index_collations"][0])
        changed[5] = value
        target["index_collations"] = [tuple(changed)]
    else:
        changed = list(target["collations"][0])
        changed[4 if field == "determinism" else 8] = value
        target["collations"] = [tuple(changed)]
    # The old encoding/LC names projection could falsely admit these cases.
    assert (source["settings"][0], *source["settings"][2:4]) == (target["settings"][0], *target["settings"][2:4])
    transfer._check_collation_versions(source["settings"], source["collations"])
    transfer._check_collation_versions(target["settings"], target["collations"])
    with pytest.raises(transfer.TransferDenied, match="^restored_collation_mismatch$"):
        transfer._matching_catalog(source, target)


def test_stale_recorded_collation_version_is_not_a_valid_copy_baseline():
    catalog = _locale_catalog()
    changed = list(catalog["settings"])
    changed[7] = "74.1"
    with pytest.raises(transfer.TransferDenied, match="^database_collation_version_stale_or_untracked$"):
        transfer._check_collation_versions(tuple(changed), catalog["collations"])
    changed = list(catalog["collations"][0])
    changed[10] = "74.1"
    with pytest.raises(transfer.TransferDenied, match="^referenced_collation_version_stale_or_untracked$"):
        transfer._check_collation_versions(catalog["settings"], [tuple(changed)])


def test_database_default_collation_uses_checked_database_provider_version():
    catalog = _locale_catalog()
    default = ("pg_catalog", "default", -1, "d", True, None, None, None, None, None, "73.2")
    transfer._check_collation_versions(catalog["settings"], [default])
