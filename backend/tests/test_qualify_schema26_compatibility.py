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
