"""Bounded local orchestration failures; actual product assertions live in Chromium."""
from __future__ import annotations

import base64
import json
import os
import ssl
import subprocess
import sys
import time

import pytest
from backend.scripts import run_cold_browser_journey as journey_runner
from backend.scripts.run_cold_browser_journey import (
    _browser_failure_summary,
    _create_owned_tls,
    _disposable_postgres,
    _owned_status,
    _run_bounded,
    _spawn_owned,
    _stop,
    _wait,
    run_journey,
)


@pytest.mark.parametrize("port", [55433, 55434])
def test_disposable_database_accepts_only_named_loopback_test_listeners(monkeypatch, port):
    monkeypatch.setenv("HIREWIZ_TEST_POSTGRES_URL", f"postgresql+psycopg://hirewiz:hirewiz@127.0.0.1:{port}/hirewiz_admission_test")
    assert _disposable_postgres().port == port


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://hirewiz:hirewiz@db.example:55434/hirewiz_admission_test",
    "postgresql+psycopg://hirewiz:hirewiz@127.0.0.1:5432/hirewiz_admission_test",
    "postgresql+psycopg://hirewiz:hirewiz@127.0.0.1:55434/production",
    "postgresql+psycopg://hirewiz:hirewiz@127.0.0.1:55434/hirewiz_admission_test?host=remote.example",
    "postgresql+psycopg://hirewiz:hirewiz@127.0.0.1:55434/hirewiz_admission_test?options=-csearch_path=public",
    "sqlite:///hirewiz_admission_test",
])
def test_disposable_database_rejects_other_targets_and_connection_overrides(monkeypatch, url):
    monkeypatch.setenv("HIREWIZ_TEST_POSTGRES_URL", url)
    with pytest.raises(AssertionError, match="dedicated loopback"):
        _disposable_postgres()


def test_timeout_stops_the_owned_parent_and_child_process_group(tmp_path):
    marker = tmp_path / "owned-pids.json"
    code = f'''
import json, os, signal, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
def stop(*_):
    child.wait(timeout=5)
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
open({str(marker)!r}, "w").write(json.dumps([os.getpid(), child.pid]))
time.sleep(30)
'''
    with pytest.raises(AssertionError, match="bounded deadline"):
        _run_bounded([sys.executable, "-c", code], cwd=tmp_path, env={}, timeout=1)
    assert marker.is_file(), "Fixture process must have started before timeout"
    for pid in json.loads(marker.read_text()):
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_exited_server_cannot_be_counted_as_ready():
    process = subprocess.Popen([sys.executable, "-c", "raise SystemExit(7)"])
    process.wait(timeout=5)
    with pytest.raises(AssertionError, match="exited before readiness"):
        _wait("http://127.0.0.1:1/api/health", process, seconds=1)


def test_unowned_evidence_is_not_reused_or_deleted(tmp_path, monkeypatch):
    monkeypatch.delenv("HIREWIZ_TEST_POSTGRES_URL", raising=False)
    source = tmp_path / "credential-free-source"
    source.mkdir()
    monkeypatch.setattr(journey_runner, "ROOT", source)
    evidence = tmp_path / "unowned-evidence"
    evidence.mkdir()
    existing = evidence / "summary.json"
    existing.write_text('{"status":"passed","unrelated":true}')
    with pytest.raises(RuntimeError, match="previously owned"):
        run_journey(evidence)
    assert json.loads(existing.read_text()) == {"status": "passed", "unrelated": True}


def _wait_exited_without_reaping(process):
    deadline = time.monotonic() + 5
    while _owned_status(process) is None:
        assert time.monotonic() < deadline, "Synthetic leader did not exit"
        time.sleep(0.02)


def test_exited_leader_with_inherited_stdout_cannot_leave_an_owned_child(tmp_path):
    marker = tmp_path / "exited-leader.json"
    code = f"""
import json, os, pathlib, subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
pathlib.Path({str(marker)!r}).write_text(json.dumps([os.getpid(), child.pid]))
raise SystemExit(0)
"""
    with pytest.raises(AssertionError, match="bounded deadline"):
        _run_bounded([sys.executable, "-c", code], cwd=tmp_path, env={}, timeout=0.7)
    leader, child = json.loads(marker.read_text())
    for pid in (leader, child):
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_successful_command_also_stops_child_that_closed_output(tmp_path):
    marker = tmp_path / "success-child.json"
    code = f"""
import json, os, pathlib, subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
pathlib.Path({str(marker)!r}).write_text(json.dumps([os.getpid(), child.pid]))
print("actual output")
raise SystemExit(7)
"""
    result = _run_bounded([sys.executable, "-c", code], cwd=tmp_path, env={}, timeout=5)
    assert result.returncode == 7
    assert result.stdout == "actual output\n"
    for pid in json.loads(marker.read_text()):
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_already_vanished_owned_group_is_not_signalled_again(tmp_path, monkeypatch):
    process = _spawn_owned([sys.executable, "-c", "raise SystemExit(0)"],
        cwd=tmp_path, env={}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _wait_exited_without_reaping(process)
    proof = _stop(process)
    with pytest.raises(ProcessLookupError):
        os.killpg(process.pid, 0)
    calls = []
    monkeypatch.setattr(os, "killpg", lambda *args: calls.append(args))
    assert _stop(process) == proof
    assert proof["verified_stopped"] is True
    assert calls == []


def test_reaped_leader_fails_closed_without_signalling_reusable_group(tmp_path, monkeypatch):
    process = _spawn_owned([sys.executable, "-c", "raise SystemExit(0)"],
        cwd=tmp_path, env={}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    process.wait(timeout=5)  # Deliberately violate the private ownership contract.
    calls = []
    monkeypatch.setattr(os, "killpg", lambda *args: calls.append(args))
    with pytest.raises(AssertionError, match="reaped before group cleanup"):
        _stop(process)
    assert calls == []


def test_termination_escalation_is_bounded_and_checks_group(tmp_path):
    marker = tmp_path / "term-ignored.json"
    code = f"""
import os, pathlib, signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))
time.sleep(30)
"""
    started = time.monotonic()
    with pytest.raises(AssertionError, match="bounded deadline"):
        _run_bounded([sys.executable, "-c", code], cwd=tmp_path, env={}, timeout=0.7)
    assert time.monotonic() - started < 10
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)


def test_browser_failure_summary_exposes_only_fixed_categories_and_spec_location(tmp_path):
    report = tmp_path / "playwright.json"
    report.write_text(json.dumps({"suites": [{"specs": [{"tests": [{"results": [{"errors": [{
        "message": "Error: expect(secret-candidate-content).toBe(expected-token)",
        "stack": "secret password at /private/work/frontend/journey/cold-no-ai.spec.ts:47:19\nBearer token",
        "location": {"file": "/private/path", "line": 77, "column": 8},
        "actual": "secret-session-cookie", "request": {"headers": {"Authorization": "secret"}},
    }]}]}]}]}]}))
    result = _browser_failure_summary(report)
    assert result == {"report_state": "read", "failures": [{"category": "assertion", "spec_line": 47, "spec_column": 19}]}
    assert "secret" not in json.dumps(result) and "/private/" not in json.dumps(result)


def _public_lab_diagnostic():
    return {"path": "/pricing", "width": 390, "response_status": 200,
            "errors": {"page": 1, "console": 2, "request": 3, "script": 1},
            "snapshot": {"ready_state": "complete", "visibility": "visible", "h1_count": 1,
                         "csp": {"script": 1, "style": 0, "other": 0},
                         "ancestors": [{"hidden": True, "display": "none", "visibility": "visible",
                                        "content_visibility": "visible", "bounds": [0, -2.5, 0, 0]}]}}


def _public_lab_attachment(diagnostic):
    return {"name": "public-lab-failure-v1", "contentType": "application/json",
            "body": base64.b64encode(json.dumps(diagnostic).encode()).decode()}


def test_public_lab_failure_exports_only_bounded_schema_without_opening_attachment_paths(tmp_path):
    diagnostic = _public_lab_diagnostic()
    attachment = _public_lab_attachment(diagnostic)
    attachment["path"] = "/private/secret-cookie-file"
    attachment["request"] = {"Authorization": "secret-token"}
    report = tmp_path / "playwright.json"
    report.write_text(json.dumps({"results": [{"attachments": [attachment], "errors": [{
        "message": "expect(secret-candidate).toBeVisible()", "actual": "secret-password"}]}]}))
    result = _browser_failure_summary(report)
    assert result == {"report_state": "read", "failures": [{"category": "assertion"}],
                      "public_lab_failures": [diagnostic]}
    assert "secret" not in json.dumps(result) and "/private/" not in json.dumps(result)


@pytest.mark.parametrize("missing", ["snapshot", "csp"])
def test_public_lab_failure_retains_fixed_context_if_observation_is_unavailable(tmp_path, missing):
    diagnostic = _public_lab_diagnostic()
    diagnostic.update(path="/", width=1440, response_status=None)
    if missing == "snapshot":
        diagnostic["snapshot"] = None
    else:
        diagnostic["snapshot"]["csp"] = None
    report = tmp_path / "playwright.json"
    report.write_text(json.dumps({"attachments": [_public_lab_attachment(diagnostic)]}))
    assert _browser_failure_summary(report)["public_lab_failures"] == [diagnostic]


@pytest.mark.parametrize("mutate", [
    lambda item: item.update(raw_error="secret-password"),
    lambda item: item.update(path="/pricing?token=secret"),
    lambda item: item.update(width=True),
    lambda item: item.update(width=1024),
    lambda item: item.update(response_status=600),
    lambda item: item["errors"].update(message="secret-cookie"),
    lambda item: item["errors"].update(page=256),
    lambda item: item["errors"].update(page=False),
    lambda item: item["snapshot"].update(text="secret-candidate"),
    lambda item: item["snapshot"].update(ready_state="secret-token"),
    lambda item: item["snapshot"].update(visibility="secret-url"),
    lambda item: item["snapshot"].update(h1_count=1025),
    lambda item: item["snapshot"]["csp"].update(blockedURI="secret-url"),
    lambda item: item["snapshot"]["ancestors"].extend([item["snapshot"]["ancestors"][0]] * 8),
    lambda item: item["snapshot"]["ancestors"][0].update(html="secret-candidate"),
    lambda item: item["snapshot"]["ancestors"][0].update(hidden="secret"),
    lambda item: item["snapshot"]["ancestors"][0].update(display="secret-style"),
    lambda item: item["snapshot"]["ancestors"][0].update(bounds=[0, 0, -1, 0]),
    lambda item: item["snapshot"]["ancestors"][0].update(bounds=[0, float("nan"), 0, 0]),
    lambda item: item["snapshot"]["ancestors"][0].update(bounds=[0, float("inf"), 0, 0]),
    lambda item: item["snapshot"]["ancestors"][0].update(bounds=[10**400, 0, 0, 0]),
])
def test_public_lab_failure_rejects_raw_fields_and_invalid_or_unbounded_values(tmp_path, mutate):
    diagnostic = _public_lab_diagnostic()
    mutate(diagnostic)
    report = tmp_path / "playwright.json"
    report.write_text(json.dumps({"attachments": [_public_lab_attachment(diagnostic)]}))
    assert _browser_failure_summary(report) == {"report_state": "read", "failures": []}


@pytest.mark.parametrize("attachment", [
    {"name": "public-lab-failure-v1", "contentType": "application/json", "path": "/private/secret"},
    {"name": "public-lab-failure-v1", "contentType": "application/json", "body": "secret-not-base64"},
    {"name": "public-lab-failure-v1", "contentType": "application/json", "body": "A" * 8193},
    {"name": "public-lab-failure-v1", "contentType": "text/plain", "body": "secret"},
    {"name": "raw-browser-log", "contentType": "application/json", "body": "secret"},
])
def test_public_lab_failure_ignores_wrong_oversized_or_path_only_attachments(tmp_path, attachment):
    report = tmp_path / "playwright.json"
    report.write_text(json.dumps({"attachments": [attachment]}))
    assert _browser_failure_summary(report) == {"report_state": "read", "failures": []}


def test_public_lab_failure_deduplicates_and_bounds_retained_diagnostics(tmp_path):
    diagnostics = []
    for count in range(7):
        item = _public_lab_diagnostic()
        item["errors"]["page"] = count
        diagnostics.append(item)
    attachments = [_public_lab_attachment(item) for item in [diagnostics[0], *diagnostics]]
    report = tmp_path / "playwright.json"
    report.write_text(json.dumps({"attachments": attachments}))
    assert _browser_failure_summary(report)["public_lab_failures"] == diagnostics[:4]


def test_browser_failure_summary_reports_missing_browser_without_raw_global_error(tmp_path):
    report = tmp_path / "playwright.json"
    report.write_text(json.dumps({"errors": [{"message": "browserType.launch: Executable doesn't exist at /private/home/token"}]}))
    assert _browser_failure_summary(report) == {"report_state": "read", "failures": [{"category": "browser_not_installed"}]}


def test_browser_failure_summary_uses_fixed_spec_coordinates_when_stack_is_absent(tmp_path):
    report = tmp_path / "playwright.json"
    report.write_text(json.dumps({"errors": [{
        "message": "Error: expect(page with private token).toHaveURL(expected)",
        "location": {"file": "/private/owned/source/frontend/journey/cold-no-ai.spec.ts", "line": 46, "column": 22},
    }, {"message": "unknown secret", "location": {"file": "/private/other.ts", "line": 7, "column": 8}}]}))
    assert _browser_failure_summary(report) == {"report_state": "read", "failures": [
        {"category": "assertion", "spec_line": 46, "spec_column": 22}, {"category": "browser_error"}]}


def test_owned_tls_keeps_hostname_and_certificate_verification_enabled(tmp_path):
    key, certificate = tmp_path / "key.pem", tmp_path / "certificate.pem"
    context = _create_owned_tls(key, certificate)
    try:
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert key.stat().st_mode & 0o777 == 0o600
        assert context.cert_store_stats()["x509_ca"] >= 1
    finally:
        key.unlink(missing_ok=True)


def test_owned_tls_setup_failure_removes_the_partial_private_key(tmp_path, monkeypatch):
    key, certificate = tmp_path / "key.pem", tmp_path / "certificate.pem"
    def failed(command, **kwargs):
        key.write_text("synthetic incomplete private key")
        certificate.write_text("synthetic incomplete certificate")
        raise subprocess.TimeoutExpired(command, 10)
    monkeypatch.setattr(journey_runner.subprocess, "run", failed)
    with pytest.raises(subprocess.TimeoutExpired):
        _create_owned_tls(key, certificate)
    assert not key.exists() and not certificate.exists()


def test_browser_failure_summary_is_bounded_and_handles_missing_invalid_reports(tmp_path):
    report = tmp_path / "playwright.json"
    assert _browser_failure_summary(report) == {"report_state": "absent"}
    report.write_text("not JSON; secret-password")
    assert _browser_failure_summary(report) == {"report_state": "unreadable"}
    report.write_text(" " * 2_000_001)
    assert _browser_failure_summary(report) == {"report_state": "too_large"}
    report.write_text(json.dumps({"errors": [{"message": "unknown error", "stack": f"cold-no-ai.spec.ts:{line}:1"} for line in range(1, 20)]}))
    result = _browser_failure_summary(report)
    assert len(result["failures"]) == 8
    assert all(error["category"] == "browser_error" for error in result["failures"])


def test_unified_cold_policy_is_test_only_and_shares_one_funding_authority(monkeypatch):
    from backend.app.domains.credits import release, runtime
    from backend.app.domains.free_core import policy
    from backend.tests.fixtures.cold_browser_credit_policy import environment

    monkeypatch.setenv("APP_ENV", "test")
    configured_environment = environment()
    for name, value in configured_environment.items():
        monkeypatch.setenv(name, value)
    reviewed = release.current_review()
    paid = runtime.current_runtime(2)
    free = policy.current_policy()
    assert paid.policy == reviewed.policy == free.financial_policy
    assert paid.budgets == free.execution_budgets
    assert paid.routes[0].mode == "native" and paid.routes[0].model_quotes == ()
    assert paid.routes[0].supported_limits["maximum_results"] == 5
    assert paid.routes[0].credits == 1 and free.operations["resume_parse"].per_account_30_days == 1
    assert runtime.current_runtime(1) == paid  # Explicit synthetic public scope.
    assert "UNIFIED_CREDIT_CANARY_OWNER_IDS" not in configured_environment
    # Even a synthetic public review cannot survive a changed runtime hash.
    changed = json.loads(configured_environment["UNIFIED_CREDIT_RUNTIME_REVIEW_JSON"])
    changed["budgets"]["version"] = "unreviewed-changed-budget"
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", json.dumps(changed))
    assert not runtime.enabled_for(2)
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValueError):
        release.current_review()
    with pytest.raises(ValueError):
        policy.current_policy()


def test_cold_credit_fixture_can_prepare_before_loading_any_application_database():
    probe = "\n".join([
        "import sys",
        "from backend.tests.fixtures.cold_browser_credit_policy import environment",
        "configured = environment()",
        "assert configured['UNIFIED_CREDIT_EXECUTION_AUDIENCE'] == 'reviewed_public'",
        "assert 'backend.app.database' not in sys.modules and 'app.database' not in sys.modules",
        "print('isolated-fixture-ready')",
    ])
    result = subprocess.run([sys.executable, "-I", "-c",
        f"import sys; sys.path.insert(0, {str(journey_runner.ROOT)!r});\n" + probe],
        env={"PATH": os.environ.get("PATH", "")}, capture_output=True, text=True,
        timeout=15, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated-fixture-ready"
