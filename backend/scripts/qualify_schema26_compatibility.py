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
import time
from collections.abc import Collection, Mapping
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree

import pytest

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
    "tests/test_unified_browser_submission_postgres.py",
    "tests/test_submission_canary_postgres.py",
)
REQUIRED_CASES = {('test_privacy_erasure_postgres', 'test_concurrent_same_fresh_intent_commits_one_erasure_and_one_detached_completion'): 1, ('test_privacy_erasure_postgres', 'test_rollback_and_unknown_commit_observe_the_same_completion_without_repeating_native'): 2, ('test_privacy_erasure_postgres', 'test_archived_e23_denial_commit_then_schema26_fence_requires_fresh_owner_intent'): 1, ('test_schema26_catalog_postgres', 'test_schema26_actual_catalog_preserves_financial_shape_and_old25_reader_refuses'): 1, ('test_evidence_review_postgres', 'test_one_exact_snapshot_cannot_authorize_two_concurrent_mutations'): 2, ('test_source_transcription_review_postgres', 'test_one_shown_snapshot_authorizes_at_most_one_competing_action'): 3, ('test_source_transcription_review_postgres', 'test_concurrent_imports_produce_one_server_item_per_exact_source_section'): 1, ('test_source_transcription_review_postgres', 'test_cached_deferred_source_cannot_hide_rotation_from_context_or_locked_action'): 1, ('test_unified_synchronous_postgres', 'test_changed_source_during_native_dispatch_admission_on_postgres'): 2, ('test_evidence_writer_fence_postgres', 'test_already_admitted_pre_sql_old_handler_cannot_write_after_committed_fence'): 1, ('test_evidence_writer_fence_postgres', 'test_contested_install_times_out_atomically_without_partial_fence'): 1, ('test_evidence_writer_fence_postgres', 'test_install_waits_for_real_old_commit_before_new_cas_admission'): 1, ('test_evidence_writer_fence_postgres', 'test_exact_candidate_flush_cannot_arm_a_later_unrelated_pending_item'): 1, ('test_evidence_writer_fence_postgres', 'test_scope_commit_rollback_and_reused_connection_never_leak_marker'): 1, ('test_evidence_writer_schema26_step_postgres', 'test_native26_supplied_bootstrap_transaction_preserves_data_catalog_acl_and_holds'): 1, ('test_evidence_writer_schema26_step_postgres', 'test_native26_failure_after_real_ddl_rolls_back_the_fence_grant_and_original_rows'): 1, ('test_evidence_writer_schema26_step_postgres', 'test_native26_post_lock_cohort_includes_commit_between_first_select_and_lock'): 1, ('test_unified_browser_submission_postgres', 'test_parallel_acceptance_reserves_three_once_without_legacy_debit'): 1, ('test_unified_browser_submission_postgres', 'test_parallel_begin_exports_one_exact_five_lease_and_one_native_claim'): 1, ('test_unified_browser_submission_postgres', 'test_cancellation_during_native_claim_prevents_export_but_keeps_three_held'): 1, ('test_unified_browser_submission_postgres', 'test_parallel_preclaim_cancel_releases_three_once_and_never_releases_native_exposure'): 1, ('test_submission_canary_postgres', 'test_concurrent_exact_application_acceptance_counts_canary_and_reserves_three_once'): 1, ('test_schema26_catalog_postgres', 'test26_native_review_bound_is_five_and_catalog_drift_rolls_back_without_history_change'): 1, ('test_evidence_writer_schema26_step_postgres', 'test_native26_final_seam_refusal_rolls_back_without_migration_ddl'): 1}


PUBLIC_CASES = {'test_schema25_app_compatibility_v3_postgres': ('test_required_ci_missing_postgres_url_fails_before_requesting_any_database', 'test_pinned_historical_and_candidate_processes_exchange_pilot_storage', 'test_actual_processes_read_imported_evidence_without_changing_exact_storage', 'test_candidate_refuses_legacy_missing_or_stale_versions_and_preserves_reviewed_storage', 'test_fenced_historical_writer_cannot_mutate_newer_evidence_without_any_displayed_version'), 'test_evidence_writer_fence_postgres': ('test_required_bridge_never_skips_or_starts_a_service_without_its_url', 'test_catalog_and_installation_preserve_every_owned_customer_row', 'test_unmarked_dml_including_empty_statements_is_denied_without_data_change', 'test_exact_candidate_flush_cannot_arm_a_later_unrelated_pending_item', 'test_actual_candidate_create_update_delete_adapters_use_only_temporary_marker', 'test_scope_commit_rollback_and_reused_connection_never_leak_marker', 'test_preexisting_and_nested_scopes_refuse_and_rollback', 'test_failed_sql_rolls_back_and_preserves_original_error_and_no_marker', 'test_contested_install_times_out_atomically_without_partial_fence', 'test_install_waits_for_real_old_commit_before_new_cas_admission', 'test_already_admitted_pre_sql_old_handler_cannot_write_after_committed_fence'), 'test_schema26_catalog_postgres': ('test_schema26_actual_catalog_preserves_financial_shape_and_old25_reader_refuses', 'test_actual_privileged_fixture_role_is_not_a_qualified_runtime_writer', 'test26_native_review_bound_is_five_and_catalog_drift_rolls_back_without_history_change', 'test_actual_catalog_drift_refuses_then_rollback_restores_the_original_fence'), 'test_evidence_review_postgres': ('test_ci_cannot_silently_skip_missing_native_postgres', 'test_one_exact_snapshot_cannot_authorize_two_concurrent_mutations'), 'test_source_transcription_review_postgres': ('test_ci_cannot_silently_skip_missing_native_transcription_postgres', 'test_one_shown_snapshot_authorizes_at_most_one_competing_action', 'test_concurrent_imports_produce_one_server_item_per_exact_source_section', 'test_cached_deferred_source_cannot_hide_rotation_from_context_or_locked_action'), 'test_unified_synchronous_postgres': ('test_concurrent_identical_inline_requests_cannot_generate_or_charge_twice', 'test_unsupported_personal_claim_refunds_once_with_supplier_cost_retained_on_postgres', 'test_unknown_supplier_hold_survives_personal_claim_route_rollback_on_postgres', 'test_changed_source_during_native_dispatch_admission_on_postgres'), 'test_privacy_erasure_postgres': ('test_concurrent_same_fresh_intent_commits_one_erasure_and_one_detached_completion', 'test_rollback_and_unknown_commit_observe_the_same_completion_without_repeating_native', 'test_archived_e23_denial_commit_then_schema26_fence_requires_fresh_owner_intent'), 'test_evidence_writer_schema26_step_postgres': ('test_native26_supplied_bootstrap_transaction_preserves_data_catalog_acl_and_holds', 'test_native26_failure_after_real_ddl_rolls_back_the_fence_grant_and_original_rows', 'test_native26_post_lock_cohort_includes_commit_between_first_select_and_lock', 'test_native26_final_seam_refusal_rolls_back_without_migration_ddl'), 'test_unified_browser_submission_postgres': ('test_parallel_acceptance_reserves_three_once_without_legacy_debit', 'test_parallel_begin_exports_one_exact_five_lease_and_one_native_claim', 'test_cancellation_during_native_claim_prevents_export_but_keeps_three_held', 'test_parallel_preclaim_cancel_releases_three_once_and_never_releases_native_exposure'), 'test_submission_canary_postgres': ('test_concurrent_exact_application_acceptance_counts_canary_and_reserves_three_once',)}
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
                or not 0 < error.timeout <= 300 or type(error.stdout) is not bytes
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


TIMING_SCHEMA = "schema26-ci-timing-v1"
TIMING_PHASES = ("setup", "call", "teardown")


def prepare_protocol_timing(path: Path) -> tuple[int, ...] | None:
    """Create observational custody only; failure never changes native acceptance."""
    descriptor = None
    try:
        directory = path.parent.stat()
        if (path.name != "timings.jsonl" or path.parent.resolve() != path.parent
                or path.parent.is_symlink() or directory.st_uid != os.getuid()
                or stat.S_IMODE(directory.st_mode) != 0o700):
            return None
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        file = os.fstat(descriptor)
        return (directory.st_dev, directory.st_ino, directory.st_uid,
                file.st_dev, file.st_ino, file.st_uid)
    except Exception:
        return None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except Exception:
                pass


class _ProtocolTiming:
    """One main-thread pytest observer. Never stores parameter IDs or test values."""

    def __init__(self, path: Path, anchor: tuple[int, ...], public):
        self.path, self.anchor, self.public = path, anchor, public
        self.descriptor: int | None = None
        self.started = time.monotonic_ns()
        # Nested selected controls patch module globals. Keep this invocation's
        # original cap so their own small writer cannot disable its observer.
        self.maximum_bytes = MAX_REPORT_BYTES
        self.items: dict[int, int] = {}
        self.nodes: dict[str, int] = {}  # In memory only; never serialized.
        try:
            self.descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
            self._identity()
            if os.fstat(self.descriptor).st_size:
                raise ValueError("timing_not_empty")
            self.write(["header", TIMING_SCHEMA])
        except Exception:
            self.close()

    def _identity(self):
        if self.descriptor is None:
            raise ValueError("timing_closed")
        directory, file = self.path.parent.stat(), os.fstat(self.descriptor)
        observed = self.path.lstat()
        if (self.path.name != "timings.jsonl" or self.path.parent.resolve() != self.path.parent
                or (directory.st_dev, directory.st_ino, directory.st_uid,
                    file.st_dev, file.st_ino, file.st_uid) != self.anchor
                or directory.st_uid != os.getuid() or stat.S_IMODE(directory.st_mode) != 0o700
                or not stat.S_ISREG(file.st_mode) or stat.S_IMODE(file.st_mode) != 0o600
                or file.st_nlink != 1 or (observed.st_dev, observed.st_ino) != (file.st_dev, file.st_ino)):
            raise ValueError("timing_custody")

    def close(self):
        descriptor, self.descriptor = self.descriptor, None
        if descriptor is not None:
            try:
                os.close(descriptor)
            except Exception:
                pass

    def write(self, row):
        if self.descriptor is None:
            return
        try:
            self._identity()
            raw = (json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n").encode()
            if len(raw) > 1024 or os.fstat(self.descriptor).st_size + len(raw) > self.maximum_bytes - 128:
                os.write(self.descriptor, b'["incomplete"]\n')
                self.close()
                return
            if os.write(self.descriptor, raw) != len(raw):
                self.close()
        except Exception:
            self.close()

    def event(self, *row):
        self.write([*row, max(0, time.monotonic_ns() - self.started)])

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_collection(self):
        self.event("global", "collection", "start")
        try:
            return (yield)
        finally:
            self.event("global", "collection", "end")

    def pytest_collection_modifyitems(self, items):
        if len(items) > 4096:
            self.write(["incomplete"])
            self.close()
            return
        for ordinal, item in enumerate(items, 1):
            match = re.fullmatch(r"tests/(test[A-Za-z0-9_]{0,199})\.py::"
                r"(?:[A-Za-z_][A-Za-z0-9_]{0,127}::){0,4}(test[A-Za-z0-9_]{0,199})", item.nodeid.split("[", 1)[0])
            if match is None or match[1] not in self.public or match[2] not in self.public[match[1]]:
                self.write(["incomplete"])
                self.close()
                return
            self.items[id(item)] = ordinal
            self.nodes[item.nodeid] = ordinal
            self.write(["case", ordinal, match[1] + "::" + match[2]])

    def phase(self, item, phase, edge):
        ordinal = self.items.get(id(item))
        if ordinal is not None:
            self.event("phase", ordinal, phase, edge)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_setup(self, item):
        self.phase(item, "setup", "start")
        try:
            return (yield)
        finally:
            self.phase(item, "setup", "end")

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_call(self, item):
        self.phase(item, "call", "start")
        try:
            return (yield)
        finally:
            self.phase(item, "call", "end")

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_teardown(self, item):
        self.phase(item, "teardown", "start")
        try:
            return (yield)
        finally:
            self.phase(item, "teardown", "end")

    def pytest_runtest_logreport(self, report):
        ordinal = self.nodes.get(report.nodeid)
        if ordinal is not None and report.when in TIMING_PHASES and report.outcome in {"passed", "failed", "skipped"}:
            self.event("status", ordinal, report.when, report.outcome)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_sessionfinish(self):
        self.event("global", "sessionfinish", "start")
        try:
            return (yield)
        finally:
            self.event("global", "sessionfinish", "end")

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_unconfigure(self):
        self.event("global", "unconfigure", "start")
        try:
            return (yield)
        finally:
            self.event("global", "unconfigure", "end")


def pytest_addoption(parser):
    parser.addoption("--hirewiz-protocol-timing")
    parser.addoption("--hirewiz-protocol-timing-anchor")


def pytest_configure(config):
    path, anchor = config.getoption("--hirewiz-protocol-timing"), config.getoption("--hirewiz-protocol-timing-anchor")
    if path is None or anchor is None:
        return
    try:
        if not re.fullmatch(r"[0-9]+(?::[0-9]+){5}", anchor):
            return
        plugin = _ProtocolTiming(Path(path), tuple(map(int, anchor.split(":"))), _protocol_public_cases())
        config._hirewiz_protocol_timing = plugin
        config.pluginmanager.register(plugin, "hirewiz-protocol-phase-observer")
    except Exception:
        return  # Diagnostics never replace the actual test outcome.


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_cmdline_main(config):
    try:
        return (yield)
    finally:
        plugin = getattr(config, "_hirewiz_protocol_timing", None)
        if plugin is not None:
            plugin.event("main_exit")
            plugin.close()


def protocol_timing_summary(path: Path, anchor: tuple[int, ...] | None) -> dict:
    """Read only finite public timing metadata; never an acceptance or cause."""
    unavailable = {"schema_version": TIMING_SCHEMA, "qualified": False, "report_state": "unavailable"}
    directory_fd = descriptor = None
    try:
        if anchor is None or len(anchor) != 6 or path.name != "timings.jsonl":
            return unavailable
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        directory = os.fstat(directory_fd)
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        before = os.fstat(descriptor)
        if ((directory.st_dev, directory.st_ino, directory.st_uid, before.st_dev, before.st_ino, before.st_uid) != anchor
                or directory.st_uid != os.getuid() or stat.S_IMODE(directory.st_mode) != 0o700
                or not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1 or not 0 < before.st_size <= MAX_REPORT_BYTES):
            return unavailable
        raw = os.read(descriptor, MAX_REPORT_BYTES + 1)
        after = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_uid", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
        if len(raw) != before.st_size or not raw.endswith(b"\n") or any(getattr(before, k) != getattr(after, k) for k in fields):
            return unavailable
        lines = raw.splitlines()
        if len(lines) > 65536 or any(len(line) > 1024 for line in lines):
            return unavailable
        rows = [json.loads(line) for line in lines]
        if not rows or rows[0] != ["header", TIMING_SCHEMA] or len(rows) > 65536:
            return unavailable
        public = _protocol_public_cases()
        cases: dict[int, dict] = {}
        starts: dict[tuple, int] = {}
        durations: dict[tuple, int] = {}
        statuses: dict[tuple, str] = {}
        last_time, last_event, incomplete, main_exit = 0, None, False, False
        key: tuple[int | str, str]
        for row in rows[1:]:
            if type(row) is not list or not row:
                return unavailable
            if incomplete or main_exit:
                return unavailable
            if row == ["incomplete"]:
                incomplete = True
                continue
            if row[0] == "case" and len(row) == 3:
                ordinal, identity = row[1:]
                if type(ordinal) is not int or ordinal != len(cases) + 1 or ordinal > 4096 or type(identity) is not str:
                    return unavailable
                parts = identity.split("::")
                if len(parts) != 2 or parts[0] not in public or parts[1] not in public[parts[0]]:
                    return unavailable
                cases[ordinal] = {"ordinal": ordinal, "case_id": identity}
                continue
            if type(row[-1]) is not int or not last_time <= row[-1] <= 300 * 10**9:
                return unavailable
            last_time = row[-1]
            if row[0] == "global" and len(row) == 4 and row[1] in {"collection", "sessionfinish", "unconfigure"}:
                key, edge = ("global", row[1]), row[2]
            elif row[0] == "phase" and len(row) == 5 and type(row[1]) is int and row[1] in cases and row[2] in TIMING_PHASES:
                key, edge = (row[1], row[2]), row[3]
            elif row[0] == "status" and len(row) == 5 and type(row[1]) is int and row[1] in cases and row[2] in TIMING_PHASES:
                key = (row[1], row[2])
                if key not in durations or key in statuses or row[3] not in {"passed", "failed", "skipped"}:
                    return unavailable
                statuses[key] = row[3]
                continue
            elif row[0] == "main_exit" and len(row) == 2 and not main_exit:
                main_exit = True
                last_event = {"phase": "pytest_main_hook_exit"}
                continue
            else:
                return unavailable
            if edge == "start" and key not in starts:
                starts[key] = last_time
            elif edge == "end" and key in starts and key not in durations:
                durations[key] = last_time - starts[key]
            else:
                return unavailable
            last_event = {"phase": key[1], "edge": edge}
            if type(key[0]) is int:
                last_event.update(cases[key[0]])
        slow = [{**case, "phase_ns": {p: durations.get((i, p)) for p in TIMING_PHASES},
                 "status": {p: statuses.get((i, p)) for p in TIMING_PHASES}} for i, case in cases.items()]
        slow.sort(key=lambda c: sum(v or 0 for v in c["phase_ns"].values()), reverse=True)
        suites: dict[str, dict] = {}
        for case in slow:
            module = case["case_id"].split("::", 1)[0]
            suite = suites.setdefault(module, {"cases": 0, **dict.fromkeys(TIMING_PHASES, 0)})
            suite["cases"] += 1
            for phase, duration in case["phase_ns"].items():
                suite[phase] += duration or 0
        result = {"schema_version": TIMING_SCHEMA, "qualified": False, "report_state": "observed_unqualified",
            "incomplete": incomplete or not main_exit, "collected": len(cases), "last_event": last_event,
            "pytest_main_hook_exit_observed": main_exit,
            "global_phase_ns": {p: durations.get(("global", p)) for p in ("collection", "sessionfinish", "unconfigure")},
            "phase_completed": {p: sum((i, p) in durations for i in cases) for p in TIMING_PHASES},
            "phase_status_counts": {p: {s: sum(statuses.get((i, p)) == s for i in cases)
                for s in ("passed", "failed", "skipped")} for p in TIMING_PHASES},
            "suite_phase_ns": suites,
            "slow_cases": slow[:16]}
        return result if len(json.dumps(result).encode()) <= 65536 else unavailable
    except Exception:
        return unavailable
    finally:
        for fd in (descriptor, directory_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except Exception:
                    pass


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
