"""Inert child/JUnit controls only; no database or native child execution."""
import ast
import hashlib
import json
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

from scripts import qualify_schema26_compatibility as gate


def report(path, *, change=None):
    suite = ElementTree.Element("testsuite")
    for (module, name), count in gate.REQUIRED_CASES.items():
        for index in range(count):
            case = ElementTree.SubElement(suite, "testcase", classname="tests." + module, name=f"{name}[{index}]")
            if change in {"skipped", "failure", "error"}:
                ElementTree.SubElement(case, change)
    if change == "missing":
        suite.remove(list(suite)[0])
    if change == "duplicate":
        first = list(suite)[0]
        ElementTree.SubElement(suite, "testcase", **first.attrib)
    if change == "empty":
        suite.clear()
    ElementTree.ElementTree(suite).write(path)


def test_every_real_native_case_must_be_selected_and_passed(tmp_path):
    path = tmp_path / "synthetic.xml"
    report(path)
    assert gate.validate_report(path) == sum(gate.REQUIRED_CASES.values())


@pytest.mark.parametrize("outcome", ["skipped", "failure", "error", "missing", "duplicate", "empty"])
def test_missing_or_optional_native_cases_never_count_as_qualified(tmp_path, outcome):
    path = tmp_path / "synthetic.xml"
    report(path, change=outcome)
    with pytest.raises(gate.Compatibility26Denied):
        gate.validate_report(path)


def context(monkeypatch, tmp_path):
    monkeypatch.setattr(gate.sys, "platform", "linux")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("HIREWIZ_APP_COMPATIBILITY_REQUIRED", "true")
    monkeypatch.setenv("HIREWIZ_TEST_POSTGRES_URL", "postgresql+psycopg://synthetic:synthetic@127.0.0.1:55433/hirewiz_admission_test")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.delenv("HIREWIZ_APP25_TEST_ROOT", raising=False)


@pytest.mark.parametrize("phase", ["passed", "failed", "unknown", "missing_case"])
def test_one_original_child_and_terminal_only_owned_report_cleanup(monkeypatch, tmp_path, phase):
    context(monkeypatch, tmp_path)
    calls = []

    def child(argv, **kwargs):
        calls.append((argv, kwargs))
        assert argv[1:7] == ["-I", "-B", "-m", "pytest", "--noconftest", "-q"]
        assert tuple(argv[7:-2]) == gate.SUITES and kwargs["timeout"] == 600
        assert kwargs["stdin"] == gate.subprocess.DEVNULL and kwargs["cwd"] == gate.ROOT / "backend"
        if phase == "unknown":
            raise gate.subprocess.TimeoutExpired(argv, 600)
        path = gate.Path(argv[-1])
        report(path, change="missing" if phase == "missing_case" else None)
        return SimpleNamespace(returncode=1 if phase == "failed" else 0, stdout=b"synthetic", stderr=b"")

    monkeypatch.setattr(gate.subprocess, "run", child)
    if phase == "passed":
        assert gate.run() == sum(gate.REQUIRED_CASES.values())
        assert not list(tmp_path.iterdir())
    else:
        with pytest.raises(gate.Compatibility26Denied):
            gate.run()
        assert len(list(tmp_path.iterdir())) == 1  # No false child/resource cleanup.
    assert len(calls) == 1


@pytest.mark.parametrize("name,value", [("CI", "false"), ("GITHUB_ACTIONS", "false"),
    ("HIREWIZ_APP_COMPATIBILITY_REQUIRED", "false"), ("HIREWIZ_APP25_TEST_ROOT", "/unqualified"),
    ("HIREWIZ_TEST_POSTGRES_URL", "postgresql+psycopg://synthetic@other.invalid:55433/hirewiz_admission_test"),
    ("HIREWIZ_TEST_POSTGRES_URL", "postgresql+psycopg://synthetic@127.0.0.1:5432/production")])
def test_unprepared_or_wrong_target_refuses_before_any_child(monkeypatch, tmp_path, name, value):
    context(monkeypatch, tmp_path)
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(gate.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("No child may start"))
    with pytest.raises(gate.Compatibility26Denied):
        gate.run()
    assert not list(tmp_path.iterdir())


def failed_report(path, *, kind="failure", diagnostic_type="AssertionError", secret="SYNTHETIC_PRIVATE_VALUE"):
    module, name = next(iter(gate.REQUIRED_CASES))
    suite = ElementTree.Element("testsuite")
    case = ElementTree.SubElement(suite, "testcase", classname="tests." + module, name=name + "[" + secret + "]")
    node = ElementTree.SubElement(case, kind, type=diagnostic_type, message="SQL/password/path: " + secret)
    node.text = "Untrusted diagnostic body: " + secret
    ElementTree.ElementTree(suite).write(path)
    return module + "::" + name


def anchor(directory):
    directory.chmod(0o700)
    observed = directory.stat()
    return observed.st_dev, observed.st_ino, observed.st_uid


@pytest.mark.parametrize("kind", ["failure", "error", "skipped"])
@pytest.mark.parametrize("safe_type", [True, False])
def test_failure_diagnostic_emits_only_known_case_and_safe_type_hash(tmp_path, kind, safe_type):
    path = tmp_path / "junit.xml"
    secret = "SYNTHETIC_PRIVATE_SQL_PASSWORD_AND_TOKEN"
    expected = failed_report(path, kind=kind, diagnostic_type="AssertionError" if safe_type else secret, secret=secret)
    original = path.read_bytes()
    result = gate._failure_summary(path, anchor(tmp_path), 7)
    assert result["qualified"] is False and result["child_returncode"] == 7
    assert result["report_state"] == "observed_unqualified" and result["counts"][kind] == 1
    assert result["cases"][0]["case_id"] == expected
    assert result["cases"][0]["diagnostic_type"] == ("AssertionError" if safe_type else "OTHER")
    assert len(result["cases"][0]["diagnostic_sha256"]) == 64
    assert secret not in json.dumps(result) and str(tmp_path) not in json.dumps(result)
    assert path.read_bytes() == original


@pytest.mark.parametrize("prefix", ["", "tests.", "backend.tests."])
def test_repository_collection_namespaces_emit_the_same_public_case_id(tmp_path, prefix):
    path = tmp_path / "junit.xml"
    expected = failed_report(path)
    module, _ = next(iter(gate.REQUIRED_CASES))
    tree = ElementTree.parse(path)
    case = tree.find(".//testcase")
    assert case is not None
    case.set("classname", prefix + module)
    tree.write(path)
    result = gate._failure_summary(path, anchor(tmp_path), 1)
    assert result["cases"][0]["case_id"] == expected and result["qualified"] is False


@pytest.mark.parametrize("mode", ["missing", "oversize", "symlink", "anchor", "public_directory",
    "malformed", "entities", "unknown_module", "unknown_case", "mixed_status", "too_many"])
def test_unavailable_or_untrusted_report_never_exposes_or_adopts_diagnostics(tmp_path, mode):
    path = tmp_path / "junit.xml"
    failed_report(path)
    owner = anchor(tmp_path)
    if mode == "missing":
        path.unlink()
    elif mode == "oversize":
        path.write_bytes(b"x" * (gate.MAX_REPORT_BYTES + 1))
    elif mode == "symlink":
        path.unlink()
        private = tmp_path / "credential-never-emit"
        private.write_text("SYNTHETIC_PRIVATE_VALUE")
        path.symlink_to(private)
    elif mode == "anchor":
        owner = (owner[0], owner[1] + 1, owner[2])
    elif mode == "public_directory":
        tmp_path.chmod(0o755)
    elif mode == "malformed":
        path.write_bytes(b"<untrusted-xml>SYNTHETIC_PRIVATE_VALUE")
    elif mode == "entities":
        path.write_bytes(b'<!DOCTYPE t [<!ENTITY x "SYNTHETIC_PRIVATE_VALUE">]><testsuite>&x;</testsuite>')
    else:
        tree = ElementTree.parse(path)
        case = tree.find(".//testcase")
        assert case is not None
        if mode == "unknown_module":
            case.set("classname", "SYNTHETIC_PRIVATE_VALUE")
        elif mode == "unknown_case":
            case.set("name", "SYNTHETIC_PRIVATE_VALUE")
        elif mode == "mixed_status":
            ElementTree.SubElement(case, "error", message="SYNTHETIC_PRIVATE_VALUE")
        elif mode == "too_many":
            for _ in range(256):
                ElementTree.SubElement(tree.getroot(), "testcase", **case.attrib)
        tree.write(path)
    result = gate._failure_summary(path, owner, 1)
    assert result == {"schema_version": "schema26-ci-failure-v1", "qualified": False, "report_state": "unavailable"}
    assert "SYNTHETIC_PRIVATE_VALUE" not in json.dumps(result)


def test_actual_public_case_allowlist_is_exactly_the_finite_original_suite_source():
    expected = {}
    for suite in gate.SUITES:
        path = gate.ROOT / "backend" / suite
        expected[path.stem] = tuple(node.name for node in ast.parse(path.read_text()).body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"))
    assert gate.PUBLIC_CASES == expected


@pytest.mark.parametrize("mode", ["public_relative", "exact_source", "spoofed", "out_of_range"])
def test_line_hints_expose_only_bounded_numbers_from_the_exact_case_source(tmp_path, mode):
    path = tmp_path / "junit.xml"
    failed_report(path)
    module, _ = next(iter(gate.REQUIRED_CASES))
    source = gate.ROOT / "backend/tests" / (module + ".py")
    line = len(source.read_text().splitlines()) + 1 if mode == "out_of_range" else 20
    location = str(source) if mode == "exact_source" else "tests/" + source.name
    if mode == "spoofed":
        location = "/SYNTHETIC_PRIVATE_VALUE/" + location
    tree = ElementTree.parse(path)
    node = tree.find(".//failure")
    assert node is not None
    node.text = location + ":" + str(line) + ": AssertionError: SYNTHETIC_PRIVATE_VALUE\n"
    tree.write(path)
    result = gate._failure_summary(path, anchor(tmp_path), 1)
    assert result["cases"][0]["source_line_hints"] == ([] if mode in {"spoofed", "out_of_range"} else [20])
    assert "SYNTHETIC_PRIVATE_VALUE" not in json.dumps(result) and str(source) not in json.dumps(result)


def test_nonzero_child_reports_case_only_then_original_refusal_and_retains_original(tmp_path, monkeypatch, capsys):
    context(monkeypatch, tmp_path)
    saved = {}
    def child(argv, **kwargs):
        path = gate.Path(argv[-1])
        saved["case"] = failed_report(path)
        saved["path"], saved["sha"] = path, hashlib.sha256(path.read_bytes()).hexdigest()
        return SimpleNamespace(returncode=7, stdout=b"SYNTHETIC_PRIVATE_VALUE", stderr=b"SYNTHETIC_PRIVATE_VALUE")
    monkeypatch.setattr(gate.subprocess, "run", child)
    with pytest.raises(gate.Compatibility26Denied, match="^schema26_compatibility_native_child_failed$"):
        gate.run()
    output = capsys.readouterr()
    value = json.loads(output.out)
    assert value["qualified"] is False and value["child_returncode"] == 7
    assert value["cases"][0]["case_id"] == saved["case"]
    assert "SYNTHETIC_PRIVATE_VALUE" not in output.out + output.err
    assert hashlib.sha256(saved["path"].read_bytes()).hexdigest() == saved["sha"]


@pytest.mark.parametrize("outcome", ["success", "post_constructor_failure"])
def test_frozen_archived_candidate_constructor_uses_its_own_redacted_engine_without_io(tmp_path, outcome, monkeypatch):
    """Exact old constructor and corrected fixture statements, never native SQL."""
    import os
    import subprocess
    import sys
    monkeypatch.syspath_prepend(str(gate.ROOT / "infra/gcp"))
    import unified_credit_schema25_release as release

    files = release.prior_app_files(gate.ROOT, "e23c940e6133323c3c1ff11e646a4ca9b2b0b165")
    assert len(files) == 274
    assert release.prior_app_digest(files) == "fa3b778e5fa1b9fe900dc5625e18682a0f0987b68259bb8eed8ff3ae5f40800a"
    for name, raw in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    module = ast.parse((gate.ROOT / "backend/tests/test_privacy_erasure_postgres.py").read_text())
    schedule = next(ast.literal_eval(node.value) for node in module.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "INTERRUPTED_OLD_DELETE" for target in node.targets))
    body = ast.parse(schedule).body
    creation = next(node for node in body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "candidate_engine" for target in node.targets))
    owned = next(node for node in body if isinstance(node, ast.Try))
    # Run the actual fixture's private-engine creation, constructor and finally.
    # Stop explicitly before its HTTP/native schedule; no substitute constructor.
    isolated = ast.Module(body=[creation, ast.Expr(value=ast.Call(func=ast.Name(id="engine_created", ctx=ast.Load()),
        args=[], keywords=[])), ast.Try(body=[owned.body[0], ast.Expr(value=ast.Call(
            func=ast.Name(id="post_constructor_probe", ctx=ast.Load()), args=[], keywords=[]))],
            handlers=[], orelse=[], finalbody=owned.finalbody)], type_ignores=[])
    probe = ast.unparse(ast.fix_missing_locations(isolated))
    program = r'''
import json, socket, sys
from pathlib import Path
attempts=[]
def denied(*args, **kwargs):
    attempts.append('denied')
    raise AssertionError('Source-only constructor probe forbids IO')
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
import psycopg
import google.auth, google.auth._default
Engine.connect=denied
psycopg.connect=denied
psycopg.Connection.connect=classmethod(denied)
psycopg.AsyncConnection.connect=classmethod(denied)
google.auth.default=google.auth._default.default=denied
socket.getaddrinfo=socket.create_connection=denied
for name in ('connect','connect_ex','bind','listen'):
    setattr(socket.socket,name,denied)
sys.path.insert(0,sys.argv[1])
import app
from app.database import engine, SessionLocal
from app.domains.candidate_accounts.service import CandidateAccountService
from app.domains.recovery.store import GuardUnavailable
assert Path(app.__file__).resolve().parent == Path(sys.argv[1]).resolve()/'app'
assert engine.hide_parameters is False and SessionLocal.kw['bind'] is engine
import_attempts=len(attempts)
class ControlledRetention:
    def __getattr__(self, name):
        raise AssertionError('Constructor must not call retention IO')
negative=False
try:
    CandidateAccountService(engine, ControlledRetention())
except GuardUnavailable:
    negative=True
assert negative
observed, disposed=[],[]
class ProbeStopped(RuntimeError): pass
def engine_created():
    assert candidate_engine is not engine and candidate_engine.pool is not engine.pool
    assert candidate_engine.url == engine.url and candidate_engine.hide_parameters is True
    event.listen(candidate_engine,'engine_disposed',lambda value: disposed.append(value is candidate_engine))
def post_constructor_probe():
    assert accounts.sessions.kw['bind'] is candidate_engine
    assert engine.hide_parameters is False and SessionLocal.kw['bind'] is engine
    observed.append('post_constructor')
    if sys.argv[3] == 'post_constructor_failure':
        raise ProbeStopped()
try:
    exec(compile(sys.argv[2],'actual_owned_fixture_constructor','exec'),globals(),globals())
except ProbeStopped:
    assert sys.argv[3] == 'post_constructor_failure'
assert observed == ['post_constructor'] and disposed == [True] and len(attempts) == import_attempts
assert engine.hide_parameters is False and SessionLocal.kw['bind'] is engine
print(json.dumps({'old_engine_guard_refused':negative,'post_constructor_reached':True,
    'private_engine_disposed':True,'old_http_engine_unchanged':True,
    'blocked_import_attempts':import_attempts,'constructor_io_attempts':len(attempts)-import_attempts}))
'''
    result = subprocess.run([sys.executable, "-I", "-B", "-c", program, str(tmp_path / "backend"), probe, outcome],
        cwd=tmp_path / "backend", capture_output=True, timeout=30,
        env={"PATH": os.environ.get("PATH", ""), "APP_ENV": "test",
            "DATABASE_URL": "postgresql+psycopg://synthetic:unused@127.0.0.1:1/no_connection",
            "JWT_SECRET": "interrupted-erasure-synthetic-source-only", "RATE_LIMIT_STORAGE_URL": "memory://",
            "AUTO_DB_MIGRATE": "false", "PYTHON_DOTENV_DISABLED": "1", "OTEL_SDK_DISABLED": "true",
            "PYTHONDONTWRITEBYTECODE": "1"})
    code = result.returncode
    assert code == 0, "Exact archived constructor probe failed; raw child streams are not public diagnostics"
    observed = json.loads(result.stdout)
    blocked_imports = observed.pop("blocked_import_attempts")
    assert type(blocked_imports) is int and 0 <= blocked_imports <= 16  # Denied, never successful IO.
    assert observed == {"old_engine_guard_refused": True, "post_constructor_reached": True,
        "private_engine_disposed": True, "old_http_engine_unchanged": True, "constructor_io_attempts": 0}


def protocol_report(path, *, count=1, failures=1, unknown=False):
    from scripts import schema25_app_release_contract_v3 as contract

    source = gate.ROOT / "backend" / contract.PROTOCOL_SUITES[0]
    name = next(node.name for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"))
    suite = ElementTree.Element("testsuite")
    for index in range(count):
        case = ElementTree.SubElement(suite, "testcase", classname="tests." + source.stem + ".SYNTHETIC_PRIVATE_CLASS",
            name=("test_SYNTHETIC_PRIVATE_VALUE" if unknown else name) + "[SYNTHETIC_PRIVATE_PARAMETER]")
        if index < failures:
            ElementTree.SubElement(case, "failure", type="SYNTHETIC_PRIVATE_TYPE").text = "SQL/token/URL: SYNTHETIC_PRIVATE_VALUE"
    ElementTree.ElementTree(suite).write(path)
    return source.stem + "::" + name


def test_protocol_observes_source_known_ids_and_counts_without_child_streams_or_parameters(tmp_path):
    path = tmp_path / "junit.xml"
    expected = protocol_report(path, count=300)
    original = path.read_bytes()
    result = gate.protocol_failure_summary(path, anchor(tmp_path), 1)
    assert result["observation_scope"] == "native_protocol" and result["qualified"] is False
    assert result["counts"] == {"passed": 299, "failure": 1, "error": 0, "skipped": 0}
    assert result["cases"][0]["case_id"] == expected and result["cases"][0]["diagnostic_type"] == "OTHER"
    assert result["case_details_truncated"] is False and path.read_bytes() == original
    assert "SYNTHETIC_PRIVATE" not in json.dumps(result)


def test_protocol_source_names_include_actual_pytest_test26_functions_without_an_underscore():
    cases = gate._protocol_public_cases()
    assert "test26_and_frozen25_marker_contracts_are_disjoint" in cases["test_evidence_writer_schema26_contract"]
    assert "test25_and_older_collectors_do_not_gain26_authority" in cases["test_neon_schema26_release_transport"]


def test_unknown_protocol_function_remains_unavailable_and_failure_detail_bound_is_explicit(tmp_path):
    path = tmp_path / "junit.xml"
    protocol_report(path, unknown=True)
    result = gate.protocol_failure_summary(path, anchor(tmp_path), 1)
    assert result == {"schema_version": "schema26-ci-failure-v1", "qualified": False,
        "report_state": "unavailable", "observation_scope": "native_protocol"}
    assert "SYNTHETIC_PRIVATE" not in json.dumps(result)
    protocol_report(path, count=33, failures=33)
    result = gate.protocol_failure_summary(path, anchor(tmp_path), 1)
    assert result["counts"]["failure"] == 33 and len(result["cases"]) == 32
    assert result["case_details_truncated"] is True and result["qualified"] is False


@pytest.mark.parametrize("failed", [False, True])
def test_original_protocol_child_summary_precedes_same_refusal_without_replay_or_selection_change(
    tmp_path, monkeypatch, capsys, failed,
):
    from test_schema25_app_protocol_v3 import SUITES
    from test_schema25_app_protocol_v3 import (
        test_installed_gapic_redis_and_data_emulator_protocol_selection_has_no_skipped_cases as invoke,
    )

    binary = tmp_path / "owned-synthetic-not-executed"
    binary.write_text("unused synthetic binary")
    binary.chmod(0o700)
    monkeypatch.setenv("HIREWIZ_AUTHORITY_EMULATOR_REQUIRED", "true")
    monkeypatch.setenv("HIREWIZ_APP_COMPATIBILITY_REQUIRED", "true")
    monkeypatch.setenv("HIREWIZ_OWNED_REDIS_SERVER", str(binary))
    calls = []
    def child(argv, **kwargs):
        path = gate.Path(argv[-1])
        expected = protocol_report(path, failures=int(failed))
        calls.append((argv, kwargs, path, expected))
        assert argv[1:7] == ["-I", "-B", "-m", "pytest", "--noconftest", "-vv"]
        assert tuple(argv[7:-2]) == SUITES and kwargs["timeout"] == 240 and kwargs["capture_output"] is True
        assert kwargs["env"]["HIREWIZ_AUTHORITY_EMULATOR_REQUIRED"] == "true"
        return SimpleNamespace(returncode=int(failed), stdout=b"SYNTHETIC_PRIVATE_STDOUT", stderr=b"SYNTHETIC_PRIVATE_STDERR")
    monkeypatch.setattr(gate.subprocess, "run", child)
    if failed:
        with pytest.raises(AssertionError, match=r"^Native protocol subprocess failed; inspect privately(?:\n|$)"):
            invoke()
        output = capsys.readouterr()
        observed = json.loads(output.out)
        assert observed["qualified"] is False and observed["cases"][0]["case_id"] == calls[0][3]
        assert observed["child_returncode"] == 1 and observed["counts"]["failure"] == 1
        assert "SYNTHETIC_PRIVATE" not in output.out + output.err
    else:
        invoke()
        assert capsys.readouterr().out == ""
    assert len(calls) == 1 and not calls[0][2].exists()  # Existing context cleanup, no new cleanup claim.


def _public_progress_case():
    from scripts import schema25_app_release_contract_v3 as contract
    source = gate.ROOT / "backend" / contract.PROTOCOL_SUITES[0]
    name = next(node.name for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"))
    return "tests/" + source.name + "::" + name


@pytest.mark.parametrize("ending,phase", [(" ", "started"), (" PASSED [  1%]", "completed"),
    (" FAILED [100%]", "completed"), (" ERROR", "completed")])
def test_timeout_observes_only_public_identity_and_phase_without_parameters_or_streams(ending, phase):
    node = _public_progress_case()
    progress = ("collecting ... collected 2 items\n" + node + "[SYNTHETIC_PRIVATE_PARAMETER]" + ending).encode()
    failure = gate.subprocess.TimeoutExpired(["original-owned-child"], 240,
        output=progress, stderr=b"SYNTHETIC_PRIVATE_STDERR")
    result = gate.protocol_timeout_summary(failure)
    assert result == {"schema_version": "schema26-ci-timeout-v1", "qualified": False,
        "observation_scope": "native_protocol", "child_outcome": "timeout",
        "report_state": "observed_unqualified", "timeout_seconds": 240,
        "last_observed_case_id": node.removeprefix("tests/").replace(".py::", "::"),
        "last_observed_phase": phase}
    assert "SYNTHETIC_PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("mode", ["missing", "text", "oversize", "utf8", "unknown_path",
    "unknown_function", "partial_node", "partial_parameter", "trailing_noise", "no_collection", "control", "timeout"])
def test_timeout_missing_truncated_malformed_or_unallowlisted_progress_remains_unknown(mode):
    node = _public_progress_case()
    raw = ("collected 2 items\n" + node + "[SYNTHETIC_PRIVATE_PARAMETER] ").encode()
    if mode == "missing":
        raw = None
    if mode == "text":
        raw = raw.decode()
    if mode == "oversize":
        raw = b"x" * (gate.MAX_REPORT_BYTES + 1)
    if mode == "utf8":
        raw += b"\xff"
    if mode == "unknown_path":
        raw = raw.replace(b"tests/test_", b"tests/test_unallowlisted_", 1)
    if mode == "unknown_function":
        raw += ("\n" + node.rsplit("::", 1)[0] + "::test_SYNTHETIC_PRIVATE ").encode()
    if mode == "partial_node":
        raw = ("collected 2 items\n" + node).encode()
    if mode == "partial_parameter":
        raw = raw[:-2]
    if mode == "trailing_noise":
        raw += b"\nSYNTHETIC_PRIVATE_TRUNCATED_NOISE"
    if mode == "no_collection":
        raw = raw.split(b"\n", 1)[1]
    if mode == "control":
        raw += b"\x1b"
    error = gate.subprocess.TimeoutExpired(["owned"], 241 if mode == "timeout" else 240,
        output=raw, stderr=b"SYNTHETIC_PRIVATE_STDERR")
    assert gate.protocol_timeout_summary(error) == {"schema_version": "schema26-ci-timeout-v1",
        "qualified": False, "observation_scope": "native_protocol", "child_outcome": "timeout",
        "report_state": "unknown"}


def test_timeout_does_not_treat_failure_body_as_a_started_case():
    node = _public_progress_case()
    raw = ("collected 2 items\n" + node + " PASSED [ 50%]\n"
        "================= FAILURES =================\n"
        "tests/test_unknown.py::test_SYNTHETIC_PRIVATE ").encode()
    result = gate.protocol_timeout_summary(gate.subprocess.TimeoutExpired(["owned"], 240, output=raw))
    assert result["last_observed_phase"] == "completed" and "SYNTHETIC_PRIVATE" not in json.dumps(result)


def test_real_small_owned_pytest_timeout_reports_last_observed_start_not_a_cause(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys

    from scripts import schema25_app_release_contract_v3 as contract
    source = tmp_path / "backend/tests/test_owned_observation.py"
    source.parent.mkdir(parents=True)
    source.write_text("import time\ndef test_first(): pass\ndef test_wait(): time.sleep(30)\n")
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(contract, "PROTOCOL_SUITES", ("tests/test_owned_observation.py",))
    monkeypatch.setattr(contract, "PROTOCOL_FIXTURES", ())
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        subprocess.run([sys.executable, "-I", "-B", "-m", "pytest", "--noconftest", "-vv",
            "-p", "no:cacheprovider", "tests/test_owned_observation.py"], cwd=tmp_path / "backend",
            env={"PATH": os.environ.get("PATH", ""), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, timeout=2)
    observed = gate.protocol_timeout_summary(caught.value)
    assert observed["last_observed_case_id"] == "test_owned_observation::test_wait"
    assert observed["last_observed_phase"] == "started" and observed["qualified"] is False
    assert observed["timeout_seconds"] == 2 and "cause" not in observed


def test_actual_pytest_timeout_failure_transcript_excludes_captured_secret_parameters_and_streams(tmp_path):
    import os
    import subprocess
    import sys
    node = _public_progress_case()
    source = tmp_path / "test_outer_observation.py"
    source.write_text("import os, socket, subprocess, sys\n"
        + "def denied(*a,**k): raise AssertionError('External access forbidden')\n"
        + "for method in ('connect','connect_ex','bind','listen'): setattr(socket.socket,method,denied)\n"
        + "socket.getaddrinfo=socket.create_connection=denied\n"
        + "from sqlalchemy.engine import Engine\nEngine.connect=denied\n"
        + "import google.auth\ngoogle.auth.default=denied\n"
        + "sys.path[:0]=" + repr([str(gate.ROOT / "backend"), str(gate.ROOT)]) + "\n"
        + "from tests.test_schema25_app_protocol_v3 import test_installed_gapic_redis_and_data_emulator_protocol_selection_has_no_skipped_cases as invoke\n"
        + "PRIVATE='SYNTHETIC_PRIVATE_CAPTURE'\n"
        + "PROGRESS=" + repr("collected 2 items\n" + node + "[") + "+PRIVATE+'] '\n"
        + "def test_original_timeout(monkeypatch,tmp_path):\n"
        + " binary=tmp_path/'never-executed';binary.write_text('synthetic');binary.chmod(0o700)\n"
        + " monkeypatch.setenv('HIREWIZ_AUTHORITY_EMULATOR_REQUIRED','true')\n"
        + " monkeypatch.setenv('HIREWIZ_OWNED_REDIS_SERVER',str(binary))\n"
        + " def child(argv,**kw): raise subprocess.TimeoutExpired(argv,kw['timeout'],output=PROGRESS.encode(),stderr=PRIVATE.encode())\n"
        + " monkeypatch.setattr(subprocess,'run',child)\n invoke()\n")
    result = subprocess.run([sys.executable, "-I", "-B", "-m", "pytest", "--noconftest", "-q",
        "-p", "no:cacheprovider", str(source)], cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", ""), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, timeout=30)
    transcript = result.stdout + result.stderr
    assert result.returncode == 1 and b"TimeoutExpired" in transcript and b"240 seconds" in transcript
    assert b'"last_observed_phase":"started"' in transcript
    assert b"SYNTHETIC_PRIVATE_CAPTURE" not in transcript
