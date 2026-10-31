"""Finite real-source and file-drift controls, separate from native protocol."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts import qualify_native_quality_acquisition as q

ROOT = Path(__file__).resolve().parents[2]


def test_actual_runner_source_and_new_component_are_bound_without_claiming_CI_qualification():
    value = q.source_identity(ROOT)
    assert value["runner_release"] == q.RUNNER_RELEASE
    assert value["runner_eligibility"] == "historical_7be_CI_failed_no_Build_not_eligible"
    assert value["runner_app_and_contract_sha256"] == q.RUNNER_FINGERPRINT
    assert set(value["component_paths"]) == set(q.COMPONENT_PATHS)
    assert value["full_ci_suites"] == list(q.SUITES)
    assert value["runner_entry_sha256"] == hashlib.sha256(q.git(ROOT, "show", q.RUNNER_RELEASE + ":backend/scripts/native_acquisition_job.py")).hexdigest()
    assert len(value["controller_sha256"]) == 64


@pytest.mark.parametrize("name", q.COMPONENT_PATHS)
def test_actual_component_file_only_drift_invalidates_its_closure(tmp_path, name):
    for path in q.COMPONENT_PATHS:
        dest = tmp_path / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT / path).read_bytes())
    before = q.component_hashes(tmp_path)
    (tmp_path / name).write_bytes((tmp_path / name).read_bytes() + b"\n# source-only drift\n")
    after = q.component_hashes(tmp_path)
    assert before[name] != after[name]
    assert {key for key in before if before[key] != after[key]} == {name}


def test_all_pinned_runner_paths_are_actual_original_bytes():
    for name in q.RUNNER_PATHS:
        assert (ROOT / name).read_bytes() == q.git(ROOT, "show", q.RUNNER_RELEASE + ":" + name)
