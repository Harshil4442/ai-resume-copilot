"""Cutover failure observability must never reflect provider/candidate secrets."""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("HIREWIZ_ACTION_TEST_ROOT", str(Path(__file__).resolve().parents[2])))
sys.path[:0] = [str(ROOT / "infra/gcp"), str(ROOT / "backend")]
entry = importlib.import_module("fresh_cutover_execute")
CONTROLLED = (entry.native.release.ReleaseDenied, entry.writes.WriteDenied,
              entry.source_sql.SourceActionDenied, entry.writes.transfer.TransferDenied,
              entry.LinuxExecutionFilesDenied)
CODE = "write_owner_resume_captured_authority_incomplete"
CODES = frozenset({CODE})
BASE = {"event": "cutover_stopped_reconcile_only", "automatic_replay": False, "goal_complete": False}
TRANSFER_CODE = "fresh_final_transfer_not_verified_reconcile_only"
TRANSFER = {
    "status": "reconcile_required", "phase": "owned_transfer_observation_unavailable_reconcile_before_retry",
    "attempt_started": None, "cleanup": "group_absent_anchor_reaped", "native_cut_observed": False,
    "table_count": 0, "row_count": 0, "sequence_count": 0,
}


def test_actual_controlled_refusal_exposes_only_literal_stage_and_code():
    event = entry.stopped_event(entry.writes.WriteDenied(CODE), "prepared_owner_pristine", CODES, CONTROLLED)
    assert event == {**BASE, "stage": "prepared_owner_pristine", "fixed_code": CODE}
    assert entry.stopped_projection(event, CODES) == event


@pytest.mark.parametrize("message", [
    "gsk_synthetic_private_provider_key_DO_NOT_EMIT",
    "https://apikeys.googleapis.com/v2/keys:lookupKey?keyString=DO_NOT_EMIT",
    CODE + ": DO_NOT_EMIT",
    "candidate_email_DO_NOT_EMIT@example.test",
])
def test_controlled_error_text_is_never_reflected_unless_exact_known_literal(message):
    event = entry.stopped_event(entry.writes.WriteDenied(message), "prepared_owner_pristine", CODES, CONTROLLED)
    assert event == BASE and "DO_NOT_EMIT" not in json.dumps(event)


def test_exception_class_name_or_subclass_cannot_impersonate_exact_controlled_type():
    spoof = type("WriteDenied", (RuntimeError,), {})
    class Derived(entry.writes.WriteDenied):
        pass
    for error in (RuntimeError(CODE), ValueError(CODE), spoof(CODE), Derived(CODE)):
        assert entry.stopped_event(error, "prepared_owner_pristine", CODES, CONTROLLED) == BASE


def test_exception_formatting_and_extra_arguments_are_not_observed():
    class Poison:
        def __str__(self):
            raise AssertionError("provider object formatted")
    assert entry.stopped_event(entry.writes.WriteDenied(Poison()), "prepared_owner_pristine", CODES, CONTROLLED) == BASE
    assert entry.stopped_event(entry.writes.WriteDenied(CODE, "DO_NOT_EMIT"), "prepared_owner_pristine", CODES, CONTROLLED) == BASE
    event = entry.stopped_event(entry.writes.WriteDenied(CODE), "https://DO_NOT_EMIT", CODES, CONTROLLED)
    assert event == {**BASE, "stage": "unclassified", "fixed_code": CODE}


@pytest.mark.parametrize("value", [
    {"event": "cutover_stage", "stage": "https://DO_NOT_EMIT"},
    {"event": "cutover_stage", "stage": "prepared_owner_pristine", "provider_body": "DO_NOT_EMIT"},
    {**BASE, "automatic_replay": True},
    {**BASE, "goal_complete": True},
    {**BASE, "stage": "prepared_owner_pristine", "fixed_code": "DO_NOT_EMIT"},
    {**BASE, "stage": "prepared_owner_pristine", "fixed_code": CODE, "credential": "DO_NOT_EMIT"},
])
def test_host_rejects_untrusted_progress_or_refusal_projection(value):
    with pytest.raises(ValueError, match="^cutover_observability_projection_refused$"):
        if value["event"] == "cutover_stage":
            entry.stage_projection(value)
        else:
            entry.stopped_projection(value, CODES)


def test_generic_refusal_remains_generic_and_does_not_authorize_replay_or_completion():
    assert entry.stopped_projection(BASE, CODES) == BASE
    assert entry.stopped_event(TimeoutError("DO_NOT_EMIT"), "identity_and_policy", CODES, CONTROLLED) == BASE
    assert entry.stage_event("prepared_owner_pristine") == {"event": "cutover_stage", "stage": "prepared_owner_pristine"}


def test_literal_catalogue_parses_trusted_source_without_executing_it(tmp_path):
    folder = tmp_path / "infra/gcp"
    folder.mkdir(parents=True)
    (tmp_path / "backend/scripts").mkdir(parents=True)
    marker = tmp_path / "must-not-be-created"
    (folder / "synthetic.py").write_text(
        "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('bad')\n"
        "raise RuntimeError('known_bounded_error')\n"
        "raise RuntimeError('https://provider.invalid/?keyString=DO_NOT_EMIT')\n"
    )
    assert entry.literal_codes(tmp_path) == frozenset({"known_bounded_error"})
    assert not marker.exists()


def test_committed_pristine_refusal_is_in_literal_catalogue():
    assert CODE in entry.literal_codes(ROOT)


@pytest.mark.parametrize("started", [None, False, True])
def test_transfer_refusal_retains_only_exact_safe_observation_through_host_projection(started):
    diagnostic = {**TRANSFER, "attempt_started": started}
    error = entry.native.release.ReleaseDenied(TRANSFER_CODE)
    error.__dict__["transfer"] = diagnostic
    error.__dict__["provider_body"] = "DO_NOT_EMIT"
    codes = CODES | {TRANSFER_CODE}
    expected = {**BASE, "stage": "transfer_migration_staging", "fixed_code": TRANSFER_CODE,
                "transfer": diagnostic}
    event = entry.stopped_event(error, "transfer_migration_staging", codes, CONTROLLED)
    assert event == expected and entry.stopped_projection(event, codes) == expected
    assert "DO_NOT_EMIT" not in json.dumps(event)
    assert event["automatic_replay"] is event["goal_complete"] is False


@pytest.mark.parametrize(("field", "value"), [
    ("status", "https://DO_NOT_EMIT"), ("phase", "DO_NOT_EMIT"), ("cleanup", "DO_NOT_EMIT"),
    ("attempt_started", 1), ("native_cut_observed", 1), ("table_count", True),
    ("row_count", -1), ("sequence_count", 257), ("row_count", 1_000_001),
])
def test_malformed_transfer_observation_falls_back_to_same_fixed_refusal_without_reflection(field, value):
    diagnostic = {**TRANSFER, field: value}
    error = entry.native.release.ReleaseDenied(TRANSFER_CODE)
    error.__dict__["transfer"] = diagnostic
    codes = CODES | {TRANSFER_CODE}
    expected = {**BASE, "stage": "transfer_migration_staging", "fixed_code": TRANSFER_CODE}
    assert entry.stopped_event(error, "transfer_migration_staging", codes, CONTROLLED) == expected
    assert entry.stopped_projection({**expected, "transfer": diagnostic}, codes) == expected
    assert "DO_NOT_EMIT" not in json.dumps(expected)


@pytest.mark.parametrize("diagnostic", [{**TRANSFER, "provider_body": "DO_NOT_EMIT"}, None, [], {}])
def test_transfer_observation_requires_exact_fields_and_plain_object(diagnostic):
    with pytest.raises(ValueError, match="^cutover_observability_projection_refused$"):
        entry.fresh.transfer_projection(diagnostic)


@pytest.mark.parametrize(("stage", "code"), [("identity_and_policy", TRANSFER_CODE), ("transfer_migration_staging", CODE)])
def test_transfer_observation_cannot_be_attached_to_another_stage_or_refusal(stage, code):
    error = entry.native.release.ReleaseDenied(code)
    error.__dict__["transfer"] = TRANSFER
    codes = CODES | {TRANSFER_CODE}
    expected = {**BASE, "stage": stage, "fixed_code": code}
    assert entry.stopped_event(error, stage, codes, CONTROLLED) == expected
    assert entry.stopped_projection({**expected, "transfer": TRANSFER}, codes) == expected


def test_transfer_projection_uses_existing_finite_worker_phases_and_bounded_counts():
    for phase in entry.fresh.transfer_protocol._OWNER_PHASES:
        value = {**TRANSFER, "phase": phase, "table_count": 256, "row_count": 1_000_000, "sequence_count": 256}
        assert entry.fresh.transfer_projection(value) == value
    assert TRANSFER_CODE in entry.literal_codes(ROOT)
