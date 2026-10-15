"""Fast whole-source preparation pins; no application, SQL or provider imports."""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend/scripts/nologin_role_preparation.py"
RELEASE = ROOT / "infra/gcp/monetary_release.py"


def source_controls(*, script=SCRIPT, pin=None):
    """Execute only the actual guard/coordinator definitions, never the module."""
    tree = ast.parse(SCRIPT.read_bytes())
    names = {"PreparationDenied", "_erasable", "prepare"}
    selected = [node for node in tree.body
                if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names]
    assert {node.name for node in selected} == names
    literals = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "RELEASE_SOURCE_SHA256"
                        for target in node.targets)]
    assert len(literals) == 1
    namespace = {"__file__": str(script), "Path": Path, "hashlib": hashlib, "ast": ast,
                 "RELEASE_SOURCE_SHA256": literals[0] if pin is None else pin}
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
                              *selected], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(SCRIPT), "exec"), namespace)
    return namespace


def fixture_controls(tmp_path, source):
    """A hash-reviewed synthetic file; never repin the shipping source in a test."""
    script = tmp_path / "backend/scripts/preparation.py"
    script.parent.mkdir(parents=True)
    release = tmp_path / "infra/gcp/monetary_release.py"
    release.parent.mkdir(parents=True)
    release.write_bytes(source)
    return source_controls(script=script, pin=hashlib.sha256(source).hexdigest()), release


def test_committed_preparation_pin_matches_entire_current_release_source():
    controls = source_controls()
    assert controls["RELEASE_SOURCE_SHA256"] == hashlib.sha256(RELEASE.read_bytes()).hexdigest()
    actual = controls["_erasable"]()
    tree = ast.parse(RELEASE.read_bytes())
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "ERASABLE_TABLES"
                           for target in node.targets)]
    assert len(assignments) == 1
    assert actual == frozenset(ast.literal_eval(assignments[0].value))
    assert {"payment_orders", "payment_events", "payment_transactions", "payment_refunds",
            "entitlement_ledger", "service_credit_events", "model_cost_liabilities"}.isdisjoint(actual)


@pytest.mark.parametrize("change", [b"\n# unreviewed factoring\n", b"\nERASABLE_TABLES = {'payment_orders'}\n"])
def test_any_unreviewed_byte_change_refuses_before_parse(tmp_path, change):
    controls, release = fixture_controls(tmp_path, b"ERASABLE_TABLES = {'resumes'}\n")
    release.write_bytes(release.read_bytes() + change)
    class NoParse:
        @staticmethod
        def parse(*args, **kwargs):
            raise AssertionError("Unpinned release was parsed")
    controls["ast"] = NoParse
    with pytest.raises(controls["PreparationDenied"], match="^preparation_source_contract_unavailable$"):
        controls["_erasable"]()


def test_stale_pin_refuses_before_database_connection(tmp_path):
    controls, release = fixture_controls(tmp_path, b"ERASABLE_TABLES = {'resumes'}\n")
    release.write_bytes(release.read_bytes() + b"\n# source changed\n")
    manifest = object()
    class ValidatedManifest:
        @staticmethod
        def model_validate(value):
            assert value is manifest
            return value
    class NoConnection:
        def connect(self):
            raise AssertionError("A stale source pin opened a database connection")
    controls["PreparationManifest"] = ValidatedManifest
    with pytest.raises(controls["PreparationDenied"], match="^preparation_source_contract_unavailable$"):
        controls["prepare"](NoConnection(), manifest)


@pytest.mark.parametrize("source", [
    b"UNRELATED = {'resumes'}\n",
    b"ERASABLE_TABLES = {'resumes'}\nERASABLE_TABLES = {'users'}\n",
    b"ERASABLE_TABLES = ['resumes']\n",
    b"ERASABLE_TABLES = {'resumes', 1}\n",
])
def test_reviewed_hash_still_requires_one_literal_string_set(tmp_path, source):
    controls, _ = fixture_controls(tmp_path, source)
    with pytest.raises(controls["PreparationDenied"], match="^preparation_source_contract_unavailable$"):
        controls["_erasable"]()


def test_reading_reviewed_erasure_set_never_executes_module(tmp_path):
    marker = tmp_path / "module-must-not-run"
    source = (f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n"
              "ERASABLE_TABLES = {'resumes', 'users'}\n").encode()
    controls, _ = fixture_controls(tmp_path, source)
    assert controls["_erasable"]() == frozenset({"resumes", "users"})
    assert not marker.exists()
