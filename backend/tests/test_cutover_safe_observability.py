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
