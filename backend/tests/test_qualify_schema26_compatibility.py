"""Inert child/JUnit controls only; no database or native child execution."""
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
