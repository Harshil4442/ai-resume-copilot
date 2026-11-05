"""Owned synthetic runner/report controls; never actual-source qualification."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest
import test_mail_budget_postgres as mail_pg
from test_mail_budget_postgres import CI_POSTGRES_URL, prepared_ci_postgres_url

from scripts import qualify_schema27 as gate
from scripts.qualify_schema26_retained_v3 import RetainedV3Denied


@pytest.fixture
def ci(tmp_path):
    temporary = tmp_path / "runner"
    temporary.mkdir(mode=0o700)
    binary, report = temporary / "redis-server", temporary / "document.json"
    binary.write_text("synthetic executable, never invoked")
    binary.chmod(0o700)
    report.write_text("{}")
    return {"CI": "true", "GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
        "RUNNER_TEMP": str(temporary), "HIREWIZ_TEST_POSTGRES_URL": CI_POSTGRES_URL,
        "HIREWIZ_OWNED_REDIS_SERVER": str(binary), "HIREWIZ_DOCUMENT_TEST_REPORT": str(report),
        "HIREWIZ_NATIVE_TEX_TEST_IMAGE": "sha256:" + "a" * 64,
        "HIREWIZ_APP_COMPATIBILITY_REQUIRED": "true", "HIREWIZ_AUTHORITY_EMULATOR_REQUIRED": "true"}


def test_prepared_ci_context_keeps_only_closed_owned_inputs(ci):
    result = gate.environment(ci | {"DATABASE_URL": "forbidden", "GOOGLE_APPLICATION_CREDENTIALS": "forbidden"}, platform="linux")
    assert result["HIREWIZ_TEST_POSTGRES_URL"] == CI_POSTGRES_URL
    assert "DATABASE_URL" not in result and "GOOGLE_APPLICATION_CREDENTIALS" not in result
    assert prepared_ci_postgres_url(ci) == CI_POSTGRES_URL


@pytest.mark.parametrize("field,value", [("CI", "false"), ("GITHUB_ACTIONS", "false"),
    ("HIREWIZ_APP_COMPATIBILITY_REQUIRED", "false"), ("HIREWIZ_AUTHORITY_EMULATOR_REQUIRED", "false"),
    ("HIREWIZ_TEST_POSTGRES_URL", "postgresql+psycopg://x@remote.invalid/hirewiz_admission_test"),
    ("HIREWIZ_TEST_POSTGRES_URL", CI_POSTGRES_URL + "?target=other"),
    ("HIREWIZ_TEST_POSTGRES_URL", CI_POSTGRES_URL.replace("55433", "5432")),
    ("HIREWIZ_OWNED_REDIS_SERVER", ""), ("HIREWIZ_DOCUMENT_TEST_REPORT", ""),
    ("HIREWIZ_NATIVE_TEX_TEST_IMAGE", "latest"), ("HIREWIZ_APP25_TEST_ROOT", "/tmp/substitute")])
def test_missing_or_foreign_prerequisite_refuses_instead_of_skipping(ci, field, value):
    with pytest.raises((gate.Schema27QualificationDenied, RetainedV3Denied)):
        gate.environment(ci | {field: value}, platform="linux")


@pytest.mark.parametrize("changes", [{"CI": "false"}, {"GITHUB_ACTIONS": "false"},
    {"HIREWIZ_TEST_POSTGRES_URL": ""}, {"HIREWIZ_TEST_POSTGRES_URL": CI_POSTGRES_URL.replace("admission_test", "test")},
    {"HIREWIZ_TEST_POSTGRES_URL": CI_POSTGRES_URL + "#override"},
    {"HIREWIZ_MAIL_BUDGET_TEST_ROOT": "/tmp/substitute"}])
def test_shared_postgres_ci_branch_never_discovers_or_substitutes_a_database(ci, changes):
    with pytest.raises(ValueError):
        prepared_ci_postgres_url(ci | changes)


def test_missing_ci_database_fails_before_engine_creation_without_skip(monkeypatch):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("HIREWIZ_TEST_POSTGRES_URL", raising=False)
    monkeypatch.setattr(mail_pg, "create_engine", lambda *_a, **_k: pytest.fail("No database discovery"))
    with pytest.raises(ValueError, match="exact prepared CI PostgreSQL"):
        next(mail_pg.owned_pg_url.__wrapped__())


@pytest.mark.parametrize("version", [170000, 160000])
def test_ci_fixture_really_checks_server17_and_disposes_owned_client(monkeypatch, version):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("HIREWIZ_TEST_POSTGRES_URL", CI_POSTGRES_URL)
    monkeypatch.delenv("HIREWIZ_MAIL_BUDGET_TEST_ROOT", raising=False)
    observations, disposed = [], []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def scalar(self, statement):
            observations.append(str(statement))
            return {"SHOW server_version_num": version, "SELECT current_database()": "hirewiz_admission_test",
                    "SELECT session_user": "hirewiz"}[str(statement)]

    monkeypatch.setattr(mail_pg, "create_engine", lambda *_a, **_k: SimpleNamespace(
        connect=lambda: Connection(), dispose=lambda: disposed.append(True)))
    fixture = mail_pg.owned_pg_url.__wrapped__()
    if version == 170000:
        assert next(fixture) == CI_POSTGRES_URL
        assert observations == ["SHOW server_version_num", "SELECT current_database()", "SELECT session_user"]
        fixture.close()
    else:
        with pytest.raises(AssertionError):
            next(fixture)
    assert disposed == [True]


def identity():
    return {"family": gate.FAMILY, "source_only": True, "application_source": {"family": gate.FAMILY,
        "qualification_sha256": "a" * 64, "app_sha256": "b" * 64, "app_files": 311},
        **dict.fromkeys(("native_runner_eligible", "retained_producer_proof", "native_approval",
                        "processing_authorized", "authorizes_retail"), False)}


def test_current_application_consumer_keeps_non_native_role_and_exact_family_app_fingerprint():
    contract = SimpleNamespace(CANDIDATE_APP_SHA256="b" * 64, CANDIDATE_APP_FILES=311)
    gate.validate_application_identity(identity(), contract, "a" * 64)


@pytest.mark.parametrize("field", ["family", "source_only", "native_runner_eligible", "retained_producer_proof",
    "native_approval", "processing_authorized", "authorizes_retail", "qualification_sha256", "app_sha256", "app_files"])
def test_current_application_role_cannot_be_relabelled_native_or_other_source(field):
    value = identity()
    if field in {"qualification_sha256", "app_sha256", "app_files"}:
        value["application_source"][field] = 1
    else:
        value[field] = True if field != "source_only" else False
    with pytest.raises(gate.Schema27QualificationDenied):
        gate.validate_application_identity(value, SimpleNamespace(CANDIDATE_APP_SHA256="b" * 64,
            CANDIDATE_APP_FILES=311), "a" * 64)


def write_report(path, *, phase, damage=None):
    allowed = gate.public_cases(gate.ROOT, phase)
    suite = ElementTree.Element("testsuite")
    for module, names in allowed.items():
        required = {name: count for (mod, name), count in gate.REQUIRED_CASES[phase].items() if mod == module}
        entries = required or {sorted(names)[0]: 1}
        for name, count in entries.items():
            for ordinal in range(count):
                ElementTree.SubElement(suite, "testcase", classname=module, name=f"{name}[{ordinal}]")
    if damage in {"failure", "error", "skipped"}:
        ElementTree.SubElement(suite[0], damage)
    elif damage == "missing":
        suite.remove(suite[0])
    elif damage == "duplicate":
        ElementTree.SubElement(suite, "testcase", **suite[0].attrib)
    elif damage == "foreign":
        suite[0].set("classname", "not_a_selected_suite")
    elif damage == "empty":
        suite.clear()
    ElementTree.ElementTree(suite).write(path)
    return allowed


@pytest.mark.parametrize("phase", ["compatibility", "protocol"])
def test_each_selected_suite_and_exact_mandatory_cases_must_actually_pass(tmp_path, phase):
    report = tmp_path / "junit.xml"
    allowed = write_report(report, phase=phase)
    assert gate.validate_report(report, phase=phase, allowed=allowed) > 0


@pytest.mark.parametrize("phase", ["compatibility", "protocol"])
@pytest.mark.parametrize("damage", ["failure", "error", "skipped", "missing", "duplicate", "foreign", "empty"])
def test_missing_duplicate_optional_or_failed_case_cannot_qualify(tmp_path, phase, damage):
    report = tmp_path / "junit.xml"
    allowed = write_report(report, phase=phase, damage=damage)
    with pytest.raises(gate.Schema27QualificationDenied):
        gate.validate_report(report, phase=phase, allowed=allowed)


def test_actual_isolated_cli_refuses_before_any_source_qualification_or_native_child():
    result = subprocess.run([sys.executable, "-I", "-B", str(Path(gate.__file__)), "--phase", "compatibility"],
        env={"PATH": os.environ.get("PATH", ""), "CI": "false", "GITHUB_ACTIONS": "false"},
        capture_output=True, timeout=15, check=False)
    assert result.returncode != 0 and result.stdout == b""
    assert result.stderr.strip() == b"schema27_qualification_unavailable_or_child_outcome_unknown"
    assert b"Traceback" not in result.stderr


def test_actual_workflow_retains_historical_steps_and_makes_new27_phases_mandatory():
    import yaml

    workflow = yaml.safe_load((gate.ROOT / ".github/workflows/ci.yml").read_text())
    assert len(workflow["jobs"]) == 5
    assert all(job["runs-on"] == "ubuntu-24.04" for job in workflow["jobs"].values())
    steps = workflow["jobs"]["backend"]["steps"]
    commands = {
        "Qualify schema26 app v3 compatibility": "qualify_schema26_retained_v3.py --phase compatibility",
        "Qualify schema26 app v3 native protocol": "qualify_schema26_retained_v3.py --phase protocol",
        "Qualify schema27 app compatibility": "qualify_schema27.py --phase compatibility",
        "Qualify schema27 app native protocol": "qualify_schema27.py --phase protocol",
    }
    redis = next(i for i, step in enumerate(steps) if step.get("id") == "native-metadata-redis")
    for name, command in commands.items():
        found = [(i, step) for i, step in enumerate(steps) if step.get("name") == name]
        assert len(found) == 1
        index, step = found[0]
        assert index > redis and step["run"] == ".venv/bin/python -I -B scripts/" + command
        assert "if" not in step and not step.get("continue-on-error")
        assert step["env"]["HIREWIZ_APP_COMPATIBILITY_REQUIRED"] == "true"
        assert step["env"]["HIREWIZ_AUTHORITY_EMULATOR_REQUIRED"] == "true"
        assert step["env"]["HIREWIZ_TEST_POSTGRES_URL"] == CI_POSTGRES_URL
    assert sum(step.get("name") == "Qualify retained actual-source schema25 app v1" for step in steps) == 1
    assert sum(step.get("name") == "Qualify retained actual-source schema25 app v2" for step in steps) == 1
    full = next(step for step in steps if step.get("name") == "Run backend tests")
    import re

    assert re.findall(r"--ignore=(\S+)", full["run"]) == [
        "tests/test_schema25_app_compatibility_v2_postgres.py", "tests/test_schema25_app_rolling_v2.py",
        "tests/test_schema25_app_alias_retirement.py", "tests/test_schema25_app_protocol_v2.py",
        "tests/test_schema25_app_compatibility_v3_postgres.py", "tests/test_schema25_app_protocol_v3.py",
        "tests/test_schema25_app_rolling_v3.py"]


@pytest.mark.parametrize("outcome", ["failure", "timeout", "changed_source", "success"])
def test_original_child_once_no_replay_and_cleanup_only_after_success(monkeypatch, tmp_path, outcome):
    root = tmp_path / "source"
    (root / "backend").mkdir(parents=True)
    monkeypatch.setattr(gate, "environment", lambda *_a, **_k: {"RUNNER_TEMP": str(tmp_path)})
    stamps = iter(["a" * 64, "b" * 64 if outcome == "changed_source" else "a" * 64])
    monkeypatch.setattr(gate, "source_identity", lambda *_: next(stamps))
    monkeypatch.setattr(gate, "public_cases", lambda *_: {"test_owned": {"test_owned"}})
    monkeypatch.setattr(gate, "SUITES", {"compatibility": ("tests/test_owned.py",)})
    monkeypatch.setattr(gate, "REQUIRED_CASES", {"compatibility": {("test_owned", "test_owned"): 1}})
    calls = []

    def child(command, **kwargs):
        calls.append((command, kwargs))
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, gate.CHILD_SECONDS)
        report = Path(command[-1])
        report.write_text('<testsuite><testcase classname="test_owned" name="test_owned"/></testsuite>')
        return SimpleNamespace(returncode=int(outcome == "failure"), stdout=b"", stderr=b"")

    monkeypatch.setattr(gate.subprocess, "run", child)
    if outcome == "success":
        assert gate.run(root, "compatibility") == 1
    else:
        with pytest.raises(gate.Schema27QualificationDenied):
            gate.run(root, "compatibility")
    assert len(calls) == 1 and calls[0][1]["timeout"] == gate.CHILD_SECONDS
    assert calls[0][0][1:7] == ["-I", "-B", "-m", "pytest", "--noconftest", "-q"]
    directories = list(tmp_path.glob("hirewiz-schema27-*"))
    assert bool(directories) is (outcome != "success")
