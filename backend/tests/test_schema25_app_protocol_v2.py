"""Installed SDK/wire + owned-emulator qualification, never cloud/IAM/capacity proof."""
from __future__ import annotations

import ast
import importlib.metadata
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

from scripts import schema25_app_release_contract_v2 as contract

ROOT = Path(__file__).resolve().parents[2]
SDK_PACKAGES = ("google-cloud-firestore", "google-api-core", "google-cloud-storage",
                "google-auth", "proto-plus", "protobuf", "grpcio", "redis", "limits")
SUITES = contract.PROTOCOL_SUITES


def test_exact_installed_native_dependencies_and_reviewed_transport_source():
    locked = {row["name"]: row["version"] for row in tomllib.loads((ROOT / "backend/uv.lock").read_text())["package"]}
    assert all(importlib.metadata.version(name) == locked[name] for name in SDK_PACKAGES)
    sys.path.insert(0, str(ROOT / "infra/gcp"))
    import unified_credit_schema25_release as release
    assert contract.source_fingerprint(ROOT, release.prior_app_files(ROOT, contract.PRIOR_RELEASE))
    assert all((ROOT / name).is_file() for name in contract.TRANSPORT_PATHS)


def test_protocol_fingerprint_binds_the_finite_transitive_local_proof_helpers():
    declared = {ROOT / name for name in contract.PROTOCOL_FIXTURES} | {
        ROOT / "backend" / name for name in SUITES}
    pending = [ROOT / "backend" / name for name in SUITES]
    observed = set()
    while pending:
        source = pending.pop()
        if source in observed:
            continue
        observed.add(source)
        for node in ast.walk(ast.parse(source.read_text())):
            modules = [node.module or ""] if isinstance(node, ast.ImportFrom) else (
                [name.name for name in node.names] if isinstance(node, ast.Import) else [])
            for module in modules:
                if module.startswith("backend.tests."):
                    relative = module.removeprefix("backend.")
                elif module.startswith(("tests.", "test_")):
                    relative = module if module.startswith("tests.") else "tests." + module
                else:
                    continue
                helper = ROOT / "backend" / (relative.replace(".", "/") + ".py")
                assert helper.is_file() and helper in declared, f"Unbound local proof helper: {relative}"
                pending.append(helper)
    assert observed <= declared
    assert ROOT / "backend/pyproject.toml" in declared


def test_installed_gapic_redis_and_data_emulator_protocol_selection_has_no_skipped_cases():
    required = os.getenv("HIREWIZ_APP_COMPATIBILITY_REQUIRED") == "true"
    if os.getenv("HIREWIZ_AUTHORITY_EMULATOR_REQUIRED") != "true":
        if required:
            pytest.fail("The mandatory v2 native qualification needs the owned CI data emulator")
        pytest.skip("Run the mandatory qualification in the explicit owned emulator context")
    binary = os.getenv("HIREWIZ_OWNED_REDIS_SERVER")
    if not binary or not Path(binary).is_file() or not os.access(binary, os.X_OK):
        pytest.fail("The mandatory v2 native qualification needs the prepared owned TLS Redis executable")
    with tempfile.TemporaryDirectory(prefix="hirewiz-app25-v2-native-") as temporary:
        path = Path(temporary)
        path.chmod(0o700)
        report = path / "junit.xml"
        result = subprocess.run([sys.executable, "-I", "-B", "-m", "pytest", "--noconftest", "-q", *SUITES,
            "--junitxml", str(report)], cwd=ROOT / "backend",
            env={**os.environ, "APP_ENV": "test", "HIREWIZ_AUTHORITY_EMULATOR_REQUIRED": "true",
                 "HIREWIZ_METADATA_PACING_REQUIRED": "true", "HIREWIZ_APP_COMPATIBILITY_REQUIRED": "true"},
            capture_output=True, timeout=240)
        # Do not export raw request/log/fixture content; the exact command/source
        # and finite failure/skip counts are sufficient to refuse qualification.
        status = result.returncode
        assert status == 0, "Native protocol subprocess failed; inspect privately"
        assert report.is_file() and report.stat().st_size <= 4 * 1024**2
        cases = ElementTree.parse(report).findall(".//testcase")
        assert cases and not any(case.find("skipped") is not None or case.find("failure") is not None
                                 or case.find("error") is not None for case in cases)


def test_full_backend_suite_reuses_the_exact_prepared_owned_redis_binary():
    import yaml

    steps = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]["backend"]["steps"]
    full = next(step for step in steps if step.get("name") == "Run backend tests")
    native = next(step for step in steps if step.get("name") == contract.PROTOCOL_STEP)
    exact = "${{ steps.native-metadata-redis.outputs.binary }}"
    assert full["env"]["HIREWIZ_OWNED_REDIS_SERVER"] == native["env"]["HIREWIZ_OWNED_REDIS_SERVER"] == exact
    assert full["env"]["HIREWIZ_AUTHORITY_EMULATOR_REQUIRED"] == "true"
    assert ".venv/bin/pytest -q -p scripts.ci_postgres_lock_diagnostics\n" in full["run"]
    # The dedicated installed-SDK/emulator/wire gate is retained exactly.
    contract.verify_ci(ROOT)


@pytest.mark.parametrize("prerequisite", ["unset", "missing", "not_executable"])
def test_selected_native_protocol_refuses_an_unprepared_redis_binary(monkeypatch, tmp_path, prerequisite):
    monkeypatch.setenv("HIREWIZ_AUTHORITY_EMULATOR_REQUIRED", "true")
    monkeypatch.delenv("HIREWIZ_APP_COMPATIBILITY_REQUIRED", raising=False)
    monkeypatch.delenv("HIREWIZ_OWNED_REDIS_SERVER", raising=False)
    if prerequisite != "unset":
        binary = tmp_path / "not-a-prepared-redis"
        if prerequisite == "not_executable":
            binary.write_text("synthetic non-executable prerequisite")
            binary.chmod(0o600)
        monkeypatch.setenv("HIREWIZ_OWNED_REDIS_SERVER", str(binary))
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("No protocol child may start"))
    with pytest.raises(pytest.fail.Exception, match="prepared owned TLS Redis executable"):
        test_installed_gapic_redis_and_data_emulator_protocol_selection_has_no_skipped_cases()


@pytest.mark.parametrize("outcome", ["passed", "skipped", "failure", "error"])
def test_selected_protocol_requires_every_case_without_optional_child_prerequisites(monkeypatch, tmp_path, outcome):
    """Synthetic child/JUnit contract checks; this is not native execution proof."""
    binary = tmp_path / "synthetic-executable-prerequisite"
    binary.write_text("synthetic prerequisite; never executed")
    binary.chmod(0o700)
    monkeypatch.setenv("HIREWIZ_OWNED_REDIS_SERVER", str(binary))
    monkeypatch.setenv("HIREWIZ_AUTHORITY_EMULATOR_REQUIRED", "true")
    monkeypatch.delenv("HIREWIZ_APP_COMPATIBILITY_REQUIRED", raising=False)

    def child(command, **kwargs):
        assert command[1:6] == ["-I", "-B", "-m", "pytest", "--noconftest"]
        assert tuple(command[7:-2]) == SUITES
        env = kwargs["env"]
        assert env["HIREWIZ_APP_COMPATIBILITY_REQUIRED"] == env["HIREWIZ_METADATA_PACING_REQUIRED"] == "true"
        assert env["HIREWIZ_AUTHORITY_EMULATOR_REQUIRED"] == "true"
        assert env["HIREWIZ_OWNED_REDIS_SERVER"] == str(binary)
        assert kwargs["cwd"] == ROOT / "backend" and kwargs["timeout"] == 240
        root = ElementTree.Element("testsuite")
        case = ElementTree.SubElement(root, "testcase", name="synthetic-protocol-control")
        if outcome != "passed":
            ElementTree.SubElement(case, outcome)
        ElementTree.ElementTree(root).write(command[-1])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", child)
    if outcome == "passed":
        test_installed_gapic_redis_and_data_emulator_protocol_selection_has_no_skipped_cases()
    else:
        with pytest.raises(AssertionError):
            test_installed_gapic_redis_and_data_emulator_protocol_selection_has_no_skipped_cases()

