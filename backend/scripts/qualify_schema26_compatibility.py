"""Finite current-source PostgreSQL26 gate; no provider or rollout authority.

Prepared GitHub CI owns PostgreSQL17. This script starts no database and refuses
missing native prerequisites. Every selected JUnit case must actually pass;
source-only fake subprocess tests do not constitute this qualification.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Collection, Mapping
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
MAX_REPORT_BYTES = 4 * 1024**2
SUITES = (
    "tests/test_schema25_app_compatibility_v3_postgres.py",
    "tests/test_evidence_writer_fence_postgres.py",
    "tests/test_schema26_catalog_postgres.py",
    "tests/test_evidence_review_postgres.py",
    "tests/test_source_transcription_review_postgres.py",
    "tests/test_unified_synchronous_postgres.py",
    "tests/test_privacy_erasure_postgres.py",
    "tests/test_evidence_writer_schema26_step_postgres.py",
)
REQUIRED_CASES = {('test_privacy_erasure_postgres', 'test_concurrent_same_fresh_intent_commits_one_erasure_and_one_detached_completion'): 1, ('test_privacy_erasure_postgres', 'test_rollback_and_unknown_commit_observe_the_same_completion_without_repeating_native'): 2, ('test_privacy_erasure_postgres', 'test_archived_e23_denial_commit_then_schema26_fence_requires_fresh_owner_intent'): 1, ('test_schema26_catalog_postgres', 'test_schema26_actual_catalog_preserves_financial_shape_and_old25_reader_refuses'): 1, ('test_evidence_review_postgres', 'test_one_exact_snapshot_cannot_authorize_two_concurrent_mutations'): 2, ('test_source_transcription_review_postgres', 'test_one_shown_snapshot_authorizes_at_most_one_competing_action'): 3, ('test_source_transcription_review_postgres', 'test_concurrent_imports_produce_one_server_item_per_exact_source_section'): 1, ('test_source_transcription_review_postgres', 'test_cached_deferred_source_cannot_hide_rotation_from_context_or_locked_action'): 1, ('test_unified_synchronous_postgres', 'test_changed_source_during_native_dispatch_admission_on_postgres'): 2, ('test_evidence_writer_fence_postgres', 'test_already_admitted_pre_sql_old_handler_cannot_write_after_committed_fence'): 1, ('test_evidence_writer_fence_postgres', 'test_contested_install_times_out_atomically_without_partial_fence'): 1, ('test_evidence_writer_fence_postgres', 'test_install_waits_for_real_old_commit_before_new_cas_admission'): 1, ('test_evidence_writer_fence_postgres', 'test_exact_candidate_flush_cannot_arm_a_later_unrelated_pending_item'): 1, ('test_evidence_writer_fence_postgres', 'test_scope_commit_rollback_and_reused_connection_never_leak_marker'): 1, ('test_evidence_writer_schema26_step_postgres', 'test_native26_supplied_bootstrap_transaction_preserves_data_catalog_acl_and_holds'): 1, ('test_evidence_writer_schema26_step_postgres', 'test_native26_failure_after_real_ddl_rolls_back_the_fence_grant_and_original_rows'): 1, ('test_evidence_writer_schema26_step_postgres', 'test_native26_post_lock_cohort_includes_commit_between_first_select_and_lock'): 1}


PUBLIC_CASES = {'test_schema25_app_compatibility_v3_postgres': ('test_required_ci_missing_postgres_url_fails_before_requesting_any_database', 'test_pinned_historical_and_candidate_processes_exchange_pilot_storage', 'test_actual_processes_read_imported_evidence_without_changing_exact_storage', 'test_candidate_refuses_legacy_missing_or_stale_versions_and_preserves_reviewed_storage', 'test_fenced_historical_writer_cannot_mutate_newer_evidence_without_any_displayed_version'), 'test_evidence_writer_fence_postgres': ('test_required_bridge_never_skips_or_starts_a_service_without_its_url', 'test_catalog_and_installation_preserve_every_owned_customer_row', 'test_unmarked_dml_including_empty_statements_is_denied_without_data_change', 'test_exact_candidate_flush_cannot_arm_a_later_unrelated_pending_item', 'test_actual_candidate_create_update_delete_adapters_use_only_temporary_marker', 'test_scope_commit_rollback_and_reused_connection_never_leak_marker', 'test_preexisting_and_nested_scopes_refuse_and_rollback', 'test_failed_sql_rolls_back_and_preserves_original_error_and_no_marker', 'test_contested_install_times_out_atomically_without_partial_fence', 'test_install_waits_for_real_old_commit_before_new_cas_admission', 'test_already_admitted_pre_sql_old_handler_cannot_write_after_committed_fence'), 'test_schema26_catalog_postgres': ('test_schema26_actual_catalog_preserves_financial_shape_and_old25_reader_refuses', 'test_actual_privileged_fixture_role_is_not_a_qualified_runtime_writer', 'test_actual_catalog_drift_refuses_then_rollback_restores_the_original_fence'), 'test_evidence_review_postgres': ('test_ci_cannot_silently_skip_missing_native_postgres', 'test_one_exact_snapshot_cannot_authorize_two_concurrent_mutations'), 'test_source_transcription_review_postgres': ('test_ci_cannot_silently_skip_missing_native_transcription_postgres', 'test_one_shown_snapshot_authorizes_at_most_one_competing_action', 'test_concurrent_imports_produce_one_server_item_per_exact_source_section', 'test_cached_deferred_source_cannot_hide_rotation_from_context_or_locked_action'), 'test_unified_synchronous_postgres': ('test_concurrent_identical_inline_requests_cannot_generate_or_charge_twice', 'test_unsupported_personal_claim_refunds_once_with_supplier_cost_retained_on_postgres', 'test_unknown_supplier_hold_survives_personal_claim_route_rollback_on_postgres', 'test_changed_source_during_native_dispatch_admission_on_postgres'), 'test_privacy_erasure_postgres': ('test_concurrent_same_fresh_intent_commits_one_erasure_and_one_detached_completion', 'test_rollback_and_unknown_commit_observe_the_same_completion_without_repeating_native', 'test_archived_e23_denial_commit_then_schema26_fence_requires_fresh_owner_intent'), 'test_evidence_writer_schema26_step_postgres': ('test_native26_supplied_bootstrap_transaction_preserves_data_catalog_acl_and_holds', 'test_native26_failure_after_real_ddl_rolls_back_the_fence_grant_and_original_rows', 'test_native26_post_lock_cohort_includes_commit_between_first_select_and_lock')}
SAFE_DIAGNOSTIC_TYPES = frozenset({"AssertionError", "RuntimeError", "ValueError", "TypeError", "IntegrityError", "ProgrammingError", "OperationalError", "HTTPException", "TimeoutError", "TimeoutExpired", "pytest.fail.Exception", "Failed"})

class Compatibility26Denied(RuntimeError):
    pass


def need(value, reason):
    if not value:
        raise Compatibility26Denied("schema26_compatibility_" + reason)


def _line_hints(module: str, diagnostic: str) -> list[int]:
    source = ROOT / "backend/tests" / (module + ".py")
    if module not in PUBLIC_CASES or source.is_symlink() or not 0 < source.stat().st_size <= 1024**2:
        return []
    bound = len(source.read_bytes().splitlines())
    names = (str(source), "tests/" + source.name, "backend/tests/" + source.name)
    pattern = re.compile(r"^(?:" + "|".join(re.escape(name) for name in names)
        + r"):([1-9][0-9]{0,5})(?::|$)", re.MULTILINE)
    return sorted({int(match[1]) for match in pattern.finditer(diagnostic) if int(match[1]) <= bound})[:3]


def _protocol_public_cases() -> dict[str, set[str]]:
    """Finite public names from the already source-bound protocol suites/helpers.

    Imported test functions may be collected under a different selected module.
    Names are disclosure metadata only; this does not execute or select tests.
    """
    from scripts import schema25_app_release_contract_v3 as contract

    try:
        names = set()
        paths = {ROOT / "backend" / name for name in contract.PROTOCOL_SUITES}
        paths.update(ROOT / name for name in contract.PROTOCOL_FIXTURES
            if name.startswith("backend/tests/") and name.endswith(".py"))
        if not 0 < len(paths) <= 128:
            return {}
        for path in paths:
            if path.is_symlink() or not 0 < path.stat().st_size <= 1024**2:
                return {}
            for node in ast.walk(ast.parse(path.read_bytes())):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                    names.add(node.name)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    names.update(alias.asname or alias.name for alias in node.names
                        if (alias.asname or alias.name).startswith("test"))
        if not 0 < len(names) <= 8192:
            return {}
        return {Path(name).stem: names for name in contract.PROTOCOL_SUITES}
    except Exception:
        return {}


def protocol_failure_summary(report: Path, anchor: tuple[int, int, int], returncode: int) -> dict:
    """Observe only the original failed protocol report, never child streams."""
    return _read_failure_summary(report, anchor, returncode, _protocol_public_cases(), protocol=True)


def protocol_timeout_summary(error: subprocess.TimeoutExpired) -> dict:
    """Last genuinely observed public progress identity, never a timeout cause.

    Only source-known module/function names leave this boundary. Parameter IDs,
    class names, child streams and arbitrary progress text are discarded. A
    missing, partial or malformed progress record remains unknown.
    """
    unknown = {"schema_version": "schema26-ci-timeout-v1", "qualified": False,
        "observation_scope": "native_protocol", "child_outcome": "timeout",
        "report_state": "unknown"}
    try:
        if (type(error) is not subprocess.TimeoutExpired
                or type(error.timeout) not in (int, float) or not math.isfinite(error.timeout)
                or not 0 < error.timeout <= 240 or type(error.stdout) is not bytes
                or not 0 < len(error.stdout) <= MAX_REPORT_BYTES):
            return unknown
        lines = error.stdout.decode("utf-8", errors="strict").split("\n")
        if len(lines) > 32768:
            return unknown
        public = _protocol_public_cases()
        collected, last = False, None
        for line in lines:
            if re.fullmatch(r"(?:collecting \.\.\. )?collected [1-9][0-9]{0,3} items?", line.strip()):
                collected = True
                continue
            if not collected:
                continue
            if re.match(r"=+ (?:FAILURES|ERRORS|short test summary info|warnings summary|test session starts)\b", line):
                break  # Failure bodies are not progress observations.
            if not line.startswith("tests/"):
                if not line.strip():
                    continue
                return unknown
            match = re.fullmatch(r"tests/(test[A-Za-z0-9_]{0,199})\.py::"
                r"(?:[A-Za-z_][A-Za-z0-9_]{0,127}::){0,4}(test[A-Za-z0-9_]{0,199})(.*)", line)
            if match is None or match[1] not in public or match[2] not in public[match[1]]:
                return unknown
            suffix = match[3]
            if len(suffix) > 65536 or any(ord(char) < 32 for char in suffix):
                return unknown
            terminal = re.search(r" (PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)(?: +\[ *[0-9]{1,3}%\])? *$", suffix)
            if terminal is None and not suffix.endswith(" "):
                return unknown  # A partial final write cannot establish a complete node ID.
            parameter = suffix[:terminal.start()] if terminal else suffix.rstrip(" ")
            if parameter and not (parameter.startswith("[") and parameter.endswith("]")):
                return unknown
            last = {"last_observed_case_id": match[1] + "::" + match[2],
                "last_observed_phase": "completed" if terminal else "started"}
        return ({**unknown, "report_state": "observed_unqualified",
            "timeout_seconds": error.timeout, **last} if last else unknown)
    except Exception:
        return unknown


def _failure_summary(report: Path, anchor: tuple[int, int, int], returncode: int) -> dict:
    return _read_failure_summary(report, anchor, returncode, PUBLIC_CASES, protocol=False)


def _read_failure_summary(report: Path, anchor: tuple[int, int, int], returncode: int,
                          public_cases: Mapping[str, Collection[str]], *, protocol: bool) -> dict:
    """Case-only observation of the original failed child; never qualification.

    No parameter labels, arbitrary XML attributes, diagnostic text, process
    streams or filesystem paths leave this reader. The original report remains
    untouched, including when it is unavailable or its custody changes.
    """
    scope = {"observation_scope": "native_protocol"} if protocol else {}
    unavailable = {"schema_version": "schema26-ci-failure-v1", "qualified": False,
        "report_state": "unavailable", **scope}
    directory_fd = report_fd = None
    try:
        if report.name != "junit.xml" or type(returncode) is not int or not -2**31 < returncode < 2**31 or returncode == 0:
            return unavailable
        directory_fd = os.open(report.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        directory = os.fstat(directory_fd)
        if ((directory.st_dev, directory.st_ino, directory.st_uid) != anchor or directory.st_uid != os.getuid()
                or not stat.S_ISDIR(directory.st_mode) or stat.S_IMODE(directory.st_mode) != 0o700):
            return unavailable
        report_fd = os.open(report.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        before = os.fstat(report_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or not 0 < before.st_size <= MAX_REPORT_BYTES:
            return unavailable
        raw = os.read(report_fd, MAX_REPORT_BYTES + 1)
        after = os.fstat(report_fd)
        fields = ("st_dev", "st_ino", "st_uid", "st_size", "st_mtime_ns", "st_ctime_ns")
        if len(raw) != before.st_size or any(getattr(before, key) != getattr(after, key) for key in fields):
            return unavailable
        if b"<!doctype" in raw.lower() or b"<!entity" in raw.lower():
            return unavailable
        cases = ElementTree.fromstring(raw).findall(".//testcase")
        if not 0 < len(cases) <= (4096 if protocol else 256):
            return unavailable
        counts = dict.fromkeys(("passed", "failure", "error", "skipped"), 0)
        failures: list[dict] = []
        for ordinal, case in enumerate(cases, start=1):
            module, name = case.get("classname", ""), case.get("name", "").split("[", 1)[0]
            module = module.removeprefix("backend.tests.") if module.startswith("backend.tests.") else module.removeprefix("tests.")
            if protocol:
                module = module.split(".", 1)[0]  # Never disclose arbitrary class/parameter text.
            if module not in public_cases or name not in public_cases[module]:
                return unavailable
            observed = [kind for kind in ("failure", "error", "skipped") if case.find(kind) is not None]
            if len(observed) > 1:
                return unavailable
            status = observed[0] if observed else "passed"
            counts[status] += 1
            if observed and (not protocol or len(failures) < 32):
                node = case.find(status)
                assert node is not None
                diagnostic_type = node.get("type", "")
                failures.append({"case_id": module + "::" + name, "ordinal": ordinal, "status": status,
                    "diagnostic_type": diagnostic_type if diagnostic_type in SAFE_DIAGNOSTIC_TYPES else "OTHER",
                    "source_line_hints": _line_hints(module, "".join(node.itertext())),
                    "diagnostic_sha256": hashlib.sha256(json.dumps({"case": case.attrib,
                        "diagnostic": ElementTree.tostring(node, encoding="unicode")},
                        sort_keys=True, separators=(",", ":")).encode()).hexdigest()})
        result = {"schema_version": "schema26-ci-failure-v1", "qualified": False,
            "report_state": "observed_unqualified", "child_returncode": returncode,
            "counts": counts, "cases": failures, **scope}
        if protocol:
            result["case_details_truncated"] = sum(counts[key] for key in ("failure", "error", "skipped")) > len(failures)
        return result if len(json.dumps(result).encode()) <= 65536 else unavailable
    except Exception:
        return unavailable
    finally:
        for descriptor in (report_fd, directory_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass  # Diagnostic cleanup must never replace the original child refusal.


def validate_report(path: Path):
    need(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= MAX_REPORT_BYTES, "report_required")
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
    if result.returncode != 0:
        print(json.dumps(_failure_summary(report, anchor, result.returncode), sort_keys=True, separators=(",", ":")))
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
