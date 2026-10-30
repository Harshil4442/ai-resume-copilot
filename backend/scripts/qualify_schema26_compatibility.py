"""Finite current-source PostgreSQL26 gate; no provider or rollout authority.

Prepared GitHub CI owns PostgreSQL17. This script starts no database and refuses
missing native prerequisites. Every selected JUnit case must actually pass;
source-only fake subprocess tests do not constitute this qualification.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
SUITES = (
    "tests/test_schema25_app_compatibility_v3_postgres.py",
    "tests/test_evidence_writer_fence_postgres.py",
    "tests/test_schema26_catalog_postgres.py",
    "tests/test_evidence_review_postgres.py",
    "tests/test_source_transcription_review_postgres.py",
    "tests/test_unified_synchronous_postgres.py",
    "tests/test_privacy_erasure_postgres.py",
)
REQUIRED_CASES = {
    ("test_privacy_erasure_postgres", "test_concurrent_same_fresh_intent_commits_one_erasure_and_one_detached_completion"): 1,
    ("test_privacy_erasure_postgres", "test_rollback_and_unknown_commit_observe_the_same_completion_without_repeating_native"): 2,
    ("test_privacy_erasure_postgres", "test_archived_e23_denial_commit_then_schema26_fence_requires_fresh_owner_intent"): 1,
    ("test_schema26_catalog_postgres", "test_schema26_actual_catalog_preserves_financial_shape_and_old25_reader_refuses"): 1,
    ("test_evidence_review_postgres", "test_one_exact_snapshot_cannot_authorize_two_concurrent_mutations"): 2,
    ("test_source_transcription_review_postgres", "test_one_shown_snapshot_authorizes_at_most_one_competing_action"): 3,
    ("test_source_transcription_review_postgres", "test_concurrent_imports_produce_one_server_item_per_exact_source_section"): 1,
    ("test_source_transcription_review_postgres", "test_cached_deferred_source_cannot_hide_rotation_from_context_or_locked_action"): 1,
    ("test_unified_synchronous_postgres", "test_changed_source_during_native_dispatch_admission_on_postgres"): 2,
    ("test_evidence_writer_fence_postgres", "test_already_admitted_pre_sql_old_handler_cannot_write_after_committed_fence"): 1,
    ("test_evidence_writer_fence_postgres", "test_contested_install_times_out_atomically_without_partial_fence"): 1,
    ("test_evidence_writer_fence_postgres", "test_install_waits_for_real_old_commit_before_new_cas_admission"): 1,
    ("test_evidence_writer_fence_postgres", "test_exact_candidate_flush_cannot_arm_a_later_unrelated_pending_item"): 1,
    ("test_evidence_writer_fence_postgres", "test_scope_commit_rollback_and_reused_connection_never_leak_marker"): 1,
}


class Compatibility26Denied(RuntimeError):
    pass


def need(value, reason):
    if not value:
        raise Compatibility26Denied("schema26_compatibility_" + reason)


def validate_report(path: Path):
    need(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 4 * 1024**2, "report_required")
    cases = ElementTree.parse(path).findall(".//testcase")
    need(bool(cases) and not any(case.find(key) is not None for case in cases
        for key in ("skipped", "failure", "error")), "every_native_case_required")
    for (module, name), count in REQUIRED_CASES.items():
        found = [case for case in cases if case.get("classname", "").split(".")[-1] == module
            and case.get("name", "").split("[", 1)[0] == name]
        need(len(found) == count, "mandatory_native_cases_missing")
    return len(cases)


def run() -> int:
    need(sys.platform == "linux" and os.getenv("CI") == "true" and os.getenv("GITHUB_ACTIONS") == "true",
        "owned_linux_ci_required")
    need(os.getenv("HIREWIZ_APP_COMPATIBILITY_REQUIRED") == "true" and not os.getenv("HIREWIZ_APP25_TEST_ROOT"),
        "mandatory_context_required")
    try:
        url = urlsplit(os.getenv("HIREWIZ_TEST_POSTGRES_URL", ""))
        valid = (url.scheme == "postgresql+psycopg" and url.hostname == "127.0.0.1" and url.port in {55433, 55434}
            and url.path == "/hirewiz_admission_test" and not url.query and not url.fragment and bool(url.username))
    except ValueError:
        valid = False
    need(valid, "owned_postgresql17_required")
    parent = Path(os.getenv("RUNNER_TEMP", ""))
    need(parent.is_absolute() and parent.resolve() == parent and parent.is_dir() and not parent.is_symlink()
        and parent.stat().st_uid == os.getuid(), "owned_temporary_required")
    directory = Path(tempfile.mkdtemp(prefix="hirewiz-schema26-native-", dir=parent))
    directory.chmod(0o700)
    metadata = directory.stat()
    anchor = (metadata.st_dev, metadata.st_ino, metadata.st_uid)
    report = directory / "junit.xml"
    try:
        result = subprocess.run([sys.executable, "-I", "-B", "-m", "pytest", "--noconftest", "-q", *SUITES,
            "--junitxml", str(report)], cwd=ROOT / "backend", stdin=subprocess.DEVNULL,
            capture_output=True, timeout=600, check=False)
    except BaseException:
        # No retry or directory removal. Nested fixture descendants may remain;
        # retain this invocation's report path without claiming their cleanup.
        raise Compatibility26Denied("schema26_compatibility_child_outcome_unknown") from None
    need(result.returncode == 0, "native_child_failed")
    need(len(result.stdout) <= 4 * 1024**2 and len(result.stderr) <= 4 * 1024**2, "child_output_bound")
    count = validate_report(report)
    observed = directory.stat()
    need(not directory.is_symlink() and (observed.st_dev, observed.st_ino, observed.st_uid) == anchor
        and set(directory.iterdir()) == {report}, "report_cleanup_owner_changed")
    # Only terminal successful original test execution permits owned cleanup.
    # Failed/unknown cases retain artifacts, never remove native resources here.
    report.unlink()
    directory.rmdir()
    return count



def main():
    count = run()
    print(f"Schema26 current-source native compatibility passed: {count} cases; no rollout authority.")


if __name__ == "__main__":
    main()
