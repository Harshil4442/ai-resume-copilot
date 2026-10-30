"""Installed SDK/wire + owned-emulator qualification, never cloud/IAM/capacity proof."""
from __future__ import annotations

import ast
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest

from scripts import schema25_app_release_contract_v3 as contract

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


def local_proof_helpers(root, source):
    """Resolve exact local modules, including namespace-package submodule imports."""
    for node in ast.walk(ast.parse(source.read_text())):
        if isinstance(node, ast.ImportFrom):
            modules, aliases = [node.module or ""], [name.name for name in node.names]
        elif isinstance(node, ast.Import):
            modules, aliases = [name.name for name in node.names], []
        else:
            continue
        for module in modules:
            if isinstance(node, ast.ImportFrom) and node.level:
                base = source.parent
                for _ in range(node.level - 1):
                    base = base.parent
                relative = ".".join(base.relative_to(root / "backend").parts)
                module = ".".join(part for part in (relative, module) if part)
            if module == "backend.tests" or module.startswith("backend.tests."):
                relative = module.removeprefix("backend.")
            elif module == "tests" or module.startswith(("tests.", "test_")):
                relative = module if module == "tests" or module.startswith("tests.") else "tests." + module
            else:
                continue
            location = root / "backend" / relative.replace(".", "/")
            helper = location.with_suffix(".py")
            if helper.is_file():
                yield helper
                continue
            assert location.is_dir(), f"Missing local proof module: {relative}"
            initializer = location / "__init__.py"
            if initializer.is_file():
                yield initializer
            for alias in aliases:
                helper = location / (alias + ".py")
                assert alias != "*" and helper.is_file(), f"Missing local proof helper: {relative}.{alias}"
                yield helper


def local_proof_ancestors(root, source):
    """Every existing package initializer is executable local proof source."""
    boundary = root / "backend"
    source.relative_to(boundary)
    parent = source.parent
    while True:
        initializer = parent / "__init__.py"
        if initializer != source and initializer.is_file():
            yield initializer
        if parent == boundary:
            break
        parent = parent.parent


def proof_closure(root, suites, fixtures):
    declared = {root / name for name in fixtures} | {root / "backend" / name for name in suites}
    pending = [root / "backend" / name for name in suites]
    observed = set()
    while pending:
        source = pending.pop()
        if source in observed:
            continue
        observed.add(source)
        for helper in (*local_proof_ancestors(root, source), *local_proof_helpers(root, source)):
            assert helper in declared, f"Unbound local proof helper: {helper.relative_to(root)}"
            pending.append(helper)
    assert observed <= declared
    return observed


def test_protocol_fingerprint_binds_the_finite_transitive_local_proof_helpers():
    proof_closure(ROOT, contract.PROOF_ROOTS, contract.PROTOCOL_FIXTURES)
    from scripts import qualify_schema26_compatibility
    assert qualify_schema26_compatibility.SUITES == contract.NATIVE_SUITES
    assert "backend/pyproject.toml" in contract.PROTOCOL_FIXTURES


def companion_proof_closure(root, roots, declared):
    """Exact relative ES imports used by these finite source/wire proof roots.

    Runtime browser imports resolve among the separately bound full20 inventory.
    Package imports are pinned by the unchanged frontend and companion locks.
    """
    pending, seen = list(roots), set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        assert name in declared, f"Unbound companion proof helper: {name}"
        source = root / name
        assert source.is_file() and not source.is_symlink()
        seen.add(name)
        # These actual module declarations are single-line. Inline source
        # strings written into synthetic extensions are not host-module imports;
        # every such extension file is independently bound by the full20 list.
        for relative in re.findall(r'''(?m)^import\s+(?:[^;\n]+?\s+from\s+)?["'](\.[^"']+)["']''', source.read_text()):
            helper = (source.parent / relative.split("?", 1)[0]).resolve()
            pending.append(str(helper.relative_to(root)))
    return seen


def test_pairing_source_package_and_actual_wire_proof_dependencies_are_bound():
    declared = set(contract.NEW_PATHS)
    observed = companion_proof_closure(ROOT, contract.COMPANION_PROOF_ROOTS, declared)
    assert {"browser-companion/tests/pairing-readiness-fixture.js",
        "browser-companion/tests/request-deadlines.js", "browser-companion/tests/native-https-fixture.js"} <= observed
    source = ROOT / "browser-companion/extension"
    assert set(contract.COMPANION_EXTENSION_PATHS) == {str(path.relative_to(ROOT)) for path in source.iterdir()}
    archive = ROOT / "frontend/public/downloads/hirewiz-browser-companion-0.8.6.zip"
    with ZipFile(archive) as package:
        assert package.namelist() == ["hirewiz-browser-companion/" + Path(name).name
            for name in contract.COMPANION_EXTENSION_PATHS]
        for name in contract.COMPANION_EXTENSION_PATHS:
            assert package.read("hirewiz-browser-companion/" + Path(name).name) == (ROOT / name).read_bytes()
    assert json.loads((source / "manifest.json").read_text())["version"] == "0.8.6"
    assert json.loads((ROOT / "browser-companion/package.json").read_text())["version"] == "0.8.6"


def test_companion_actual_ci_entrypoints_and_literal_bridge_inputs_are_bound():
    import yaml

    package = json.loads((ROOT / "browser-companion/package.json").read_text())
    commands = [package["scripts"][name] for name in ("test", "test:browser", "test:native-runtime", "test:assistance")]
    steps = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]["browser-companion"]["steps"]
    commands += [step.get("run", "") for step in steps]
    actual = {"browser-companion/" + name for command in commands
        for name in re.findall(r"tests/[\w-]+\.test\.js", command)}
    assert actual <= set(contract.COMPANION_PROOF_ROOTS)
    assert {"browser-companion/tests/browser.test.js", "browser-companion/tests/assistance-browser.test.js"} <= actual
    assert {"backend/tests/browser_assistance_chrome_bridge.py", "backend/tests/browser_submit_chrome_bridge.py",
        "backend/tests/browser_submit_owned.py", "backend/tests/test_browser_assistance_isolated.py",
        "browser-companion/fixtures/razorpay/adapter.js", "frontend/lib/cn.ts",
        "frontend/vitest.config.mts", "frontend/vitest.setup.ts"} <= set(contract.NEW_PATHS)


@pytest.mark.parametrize("missing", ["browser-companion/tests/pairing-readiness-fixture.js",
    "browser-companion/tests/request-deadlines.js", "browser-companion/scripts/native-fixture-build.js",
    "browser-companion/fixtures/authority.js", "browser-companion/scripts/fixture-build.js",
    "browser-companion/fixtures/razorpay/form.js", "browser-companion/fixtures/razorpay/server.js"])
def test_companion_relative_imports_cannot_hide_unbound_proof_helpers(missing):
    with pytest.raises(AssertionError, match="Unbound companion proof helper"):
        companion_proof_closure(ROOT, contract.COMPANION_PROOF_ROOTS,
            set(contract.NEW_PATHS) - {missing})


@pytest.mark.parametrize("missing", ["backend/tests/test_schema25_app_rolling_release.py",
    "backend/tests/test_rolling_release.py", "backend/tests/test_document_worker_deployment.py", "backend/tests/browser_submit_owned.py"])
def test_actual_rolling_proof_roots_cannot_omit_their_executable_helpers(missing):
    assert missing in contract.PROTOCOL_FIXTURES
    with pytest.raises(AssertionError, match="Unbound local proof helper"):
        proof_closure(ROOT, contract.PROOF_ROOTS,
            tuple(name for name in contract.PROTOCOL_FIXTURES if name != missing))


@pytest.mark.parametrize("missing", ["backend/tests/fixtures/pairing_authority.py",
    "backend/tests/fixtures/stage5_native.py", "backend/tests/test_supported_evaluation.py",
    "backend/tests/model_cost_fixtures.py", "backend/tests/test_document_processor.py",
    "backend/tests/test_employer_admissions.py", "backend/tests/test_employer_services.py"])
def test_protocol_closure_rejects_an_unbound_transitive_helper(missing):
    assert missing in contract.PROTOCOL_FIXTURES
    with pytest.raises(AssertionError, match="Unbound local proof helper"):
        proof_closure(ROOT, SUITES, tuple(name for name in contract.PROTOCOL_FIXTURES if name != missing))


@pytest.mark.parametrize("statement", ["from backend.tests.fixtures import leaf",
    "from tests.fixtures import leaf", "from .fixtures import leaf", "import tests.fixtures.leaf"])
def test_package_style_local_import_cannot_hide_an_undeclared_helper(tmp_path, statement):
    source = tmp_path / "backend/tests/test_entry.py"
    helper = tmp_path / "backend/tests/fixtures/leaf.py"
    helper.parent.mkdir(parents=True)
    source.write_text(statement + "\n")
    helper.write_text("value = 'synthetic proof helper'\n")
    suites, fixtures = ("tests/test_entry.py",), ("backend/tests/fixtures/leaf.py",)
    assert proof_closure(tmp_path, suites, fixtures) == {source, helper}
    with pytest.raises(AssertionError, match="Unbound local proof helper"):
        proof_closure(tmp_path, suites, ())


@pytest.mark.parametrize("ancestor", ["backend/__init__.py", "backend/tests/__init__.py",
    "backend/tests/fixtures/__init__.py"])
def test_imported_helper_package_ancestors_require_explicit_binding(tmp_path, ancestor):
    source = tmp_path / "backend/tests/test_entry.py"
    helper = tmp_path / "backend/tests/fixtures/leaf.py"
    helper.parent.mkdir(parents=True)
    source.write_text("from backend.tests.fixtures import leaf\n")
    helper.write_text("value = 'synthetic helper'\n")
    initializer = tmp_path / ancestor
    initializer.write_text("value = 'executable package ancestor'\n")
    suites, fixtures = ("tests/test_entry.py",), ("backend/tests/fixtures/leaf.py",)
    with pytest.raises(AssertionError, match="Unbound local proof helper"):
        proof_closure(tmp_path, suites, fixtures)
    assert proof_closure(tmp_path, suites, (*fixtures, ancestor)) == {source, helper, initializer}


@pytest.mark.parametrize("ancestor", ["backend/__init__.py", "backend/tests/__init__.py"])
def test_entry_suite_ancestors_are_bound_even_without_local_imports(tmp_path, ancestor):
    source = tmp_path / "backend/tests/test_entry.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 'entry suite without imports'\n")
    initializer = tmp_path / ancestor
    initializer.write_text("value = 'executable entry ancestor'\n")
    with pytest.raises(AssertionError, match="Unbound local proof helper"):
        proof_closure(tmp_path, ("tests/test_entry.py",), ())
    assert proof_closure(tmp_path, ("tests/test_entry.py",), (ancestor,)) == {source, initializer}


@pytest.mark.parametrize("statement", ["from backend.tests import hidden", "from .tests import hidden"])
def test_declared_package_ancestor_cannot_hide_a_transitive_helper(tmp_path, statement):
    source = tmp_path / "backend/tests/test_entry.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 'entry suite'\n")
    initializer = tmp_path / "backend/__init__.py"
    initializer.write_text(statement + "\n")
    hidden = tmp_path / "backend/tests/hidden.py"
    hidden.write_text("value = 'transitive initializer dependency'\n")
    suites, fixtures = ("tests/test_entry.py",), ("backend/__init__.py",)
    with pytest.raises(AssertionError, match="Unbound local proof helper"):
        proof_closure(tmp_path, suites, fixtures)
    assert proof_closure(tmp_path, suites, (*fixtures, "backend/tests/hidden.py")) == {source, initializer, hidden}


def test_installed_gapic_redis_and_data_emulator_protocol_selection_has_no_skipped_cases():
    from tests.expense_policy_fixtures import synthetic_expense_policy
    from tests.model_cost_fixtures import synthetic_policy

    required = os.getenv("HIREWIZ_APP_COMPATIBILITY_REQUIRED") == "true"
    if os.getenv("HIREWIZ_AUTHORITY_EMULATOR_REQUIRED") != "true":
        if required:
            pytest.fail("The mandatory v3 native qualification needs the owned CI data emulator")
        pytest.skip("Run the mandatory qualification in the explicit owned emulator context")
    binary = os.getenv("HIREWIZ_OWNED_REDIS_SERVER")
    if not binary or not Path(binary).is_file() or not os.access(binary, os.X_OK):
        pytest.fail("The mandatory v3 native qualification needs the prepared owned TLS Redis executable")
    with tempfile.TemporaryDirectory(prefix="hirewiz-app26-v3-native-") as temporary:
        path = Path(temporary)
        path.chmod(0o700)
        report = path / "junit.xml"
        result = subprocess.run([sys.executable, "-I", "-B", "-m", "pytest", "--noconftest", "-q", *SUITES,
            "--junitxml", str(report)], cwd=ROOT / "backend",
            env={**os.environ, "APP_ENV": "test", "HIREWIZ_AUTHORITY_EMULATOR_REQUIRED": "true",
                 "HIREWIZ_METADATA_PACING_REQUIRED": "true", "HIREWIZ_APP_COMPATIBILITY_REQUIRED": "true",
                 "HIREWIZ_EXPENSE_POLICY_JSON": json.dumps(synthetic_expense_policy()),
                 "LLM_MODEL_COST_POLICY_JSON": json.dumps(synthetic_policy())},
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
    exact = ".venv/bin/pytest -q -p scripts.ci_postgres_lock_diagnostics " + " ".join(
        "--ignore=" + name for name in contract.HISTORICAL_CURRENT_IGNORES) + "\n"
    assert exact in full["run"]
    # The dedicated installed-SDK/emulator/wire gate is retained exactly.
    contract.verify_ci(ROOT)


def test_mandatory_protocol_without_an_owned_emulator_fails_instead_of_skipping(monkeypatch):
    monkeypatch.setenv("HIREWIZ_APP_COMPATIBILITY_REQUIRED", "true")
    monkeypatch.delenv("HIREWIZ_AUTHORITY_EMULATOR_REQUIRED", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("No protocol child may start"))
    with pytest.raises(pytest.fail.Exception, match="owned CI data emulator"):
        test_installed_gapic_redis_and_data_emulator_protocol_selection_has_no_skipped_cases()


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


@pytest.mark.parametrize("outcome", ["passed", "skipped", "failure", "error", "empty"])
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
        assert json.loads(env["HIREWIZ_EXPENSE_POLICY_JSON"])["provenance"] == "synthetic_test"
        assert json.loads(env["LLM_MODEL_COST_POLICY_JSON"])["version"] == "synthetic-policy-v1"
        assert kwargs["cwd"] == ROOT / "backend" and kwargs["timeout"] == 240
        root = ElementTree.Element("testsuite")
        if outcome != "empty":
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
