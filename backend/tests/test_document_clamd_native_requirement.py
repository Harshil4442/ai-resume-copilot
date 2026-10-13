"""Pure own-code requirement labels and wire guards; no native/app imports."""
import ast
import json
import math
import time
import unittest
from contextvars import ContextVar
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend/scripts/document_test_image.py"


class NativeRequirementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(SCRIPT.read_bytes())
        constants = {"CLAMD_FAILURE_LINES", "CLAMD_FAILURE_PHASES", "CLAMD_FAILURE_CLASSES",
            "CLAMD_NATIVE_REQUIREMENTS", "_FAILURE_CASES", "_FAILURE_STAGES", "_FAILURE", "ERROR",
            "CLAMD_STARTUP_INDEX_SHA", "CLAMD_STARTUP_STATUSES", "CLAMD_STARTUP_SHAPES",
            "CLAMD_STARTUP_DIAGNOSTIC_PROGRAM"}
        functions = {"_clamd_diagnostic_pairs", "_clamd_diagnostic_values", "_clamd_diagnostic_object",
            "_native_requirement_value", "_note_clamd_failure", "_failure_report", "_progress",
            "_clamd_startup_values", "_clamd_startup_object", "_diagnose_clamd_startup"}
        classes = {"_ClamdFailure", "_ClamdNativeRequirement", "_ClamdStartupObservation"}
        nodes = [node for node in cls.tree.body if (
            isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants for t in node.targets)
            or isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in constants
            or isinstance(node, ast.FunctionDef) and node.name in functions
            or isinstance(node, ast.ClassDef) and node.name in classes)]
        cls.host = {"json": json, "ContextVar": ContextVar, "NamedTuple": NamedTuple,
            "DocumentImageError": ValueError, "math": math, "time": time, "Path": Path}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "pure_requirement_host", "exec"), cls.host)

    def setUp(self):
        token = self.host["_FAILURE"].set({"stage": "case_validation", "case": "clamd_max_filesize",
            "exit_code": 1, "completed_cases": 8})
        self.addCleanup(self.host["_FAILURE"].reset, token)

    def program(self, constant):
        text = next(ast.literal_eval(node.value) for node in self.tree.body if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == constant for t in node.targets))
        tree = ast.parse(text)
        nodes = [node for node in tree.body if (
            isinstance(node, ast.FunctionDef) and node.name in {"main", "require", "native_requirement_receipt"}
            or isinstance(node, ast.Assign) and any(isinstance(t, ast.Name)
                and t.id in {"NATIVE_REQUIREMENT_SITES", "NATIVE_REQUIREMENTS"} for t in node.targets))]
        scope = {}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "pure_requirement_program", "exec"), scope)
        return tree, scope

    def own_error(self, scope, line):
        # Real Python traceback and the unchanged actual require body; no main native call.
        fixture = "\n" * (line - 2) + "def main():\n    require(False)\n"
        exec(compile(fixture, "public_inert_require_site", "exec"), scope)
        try:
            scope["main"]()
        except ValueError as error:
            return error
        raise AssertionError("owned require did not refuse")

    def value(self, label=None):
        result = {"version": 1, "status": "native_clamd_proof_unavailable", "phase": "ownership",
            "chain": [{"class": "ValueError", "frames": []}], "truncated": False}
        if label is not None:
            result["native_requirement"] = label
        return result

    def encoded(self, value):
        return (json.dumps(value, separators=(",", ":")) + "\n").encode()

    def note(self, value):
        self.host["_note_clamd_failure"](self.encoded(value), 1, case="clamd_max_filesize")
        return self.host["_failure_report"]()

    def test_actual_program_AST_sites_and_own_traceback_labels(self):
        expected = ("shutdown", "pid", "uid", "signatures", "log_cleanup", "deadline",
                    "case_outcome", "clean_metadata", "negative_nonclean")
        for constant in ("CLAMD_NATIVE_PROGRAM", "CLAMD_STARTUP_DIAGNOSTIC_PROGRAM"):
            tree, scope = self.program(constant)
            main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
            stages = {n.value.value: n.lineno for n in ast.walk(main) if isinstance(n, ast.Assign)
                and isinstance(n.value, ast.Constant) and any(isinstance(t, ast.Name)
                    and t.id == "CLAMD_PHASE" for t in n.targets)}
            calls = sorted((n for n in ast.walk(main) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == "require"
                and stages["ownership"] < n.lineno < stages["memory"]), key=lambda n: n.lineno)
            self.assertEqual(scope["NATIVE_REQUIREMENT_SITES"], dict(zip((n.lineno for n in calls), expected, strict=True)))
            for line, label in scope["NATIVE_REQUIREMENT_SITES"].items():
                error = self.own_error(scope, line)
                self.assertEqual(scope["native_requirement_receipt"](error), label)

    def test_unknown_caller_site_type_and_code_identity_never_receive_label(self):
        _, scope = self.program("CLAMD_NATIVE_PROGRAM")
        line = next(iter(scope["NATIVE_REQUIREMENT_SITES"]))
        error = self.own_error(scope, line)
        original_main = scope["main"]
        exec(compile("\n" * (line - 2) + "def main():\n    require(False)\n", "another_code_object", "exec"), scope)
        self.assertIsNot(scope["main"].__code__, original_main.__code__)
        self.assertIsNone(scope["native_requirement_receipt"](error))
        unknown = self.own_error(scope, line + 1000)
        self.assertIsNone(scope["native_requirement_receipt"](unknown))
        self.assertIsNone(scope["native_requirement_receipt"](ValueError("unexported")))
        self.assertIsNone(scope["native_requirement_receipt"](TypeError("unexported").with_traceback(error.__traceback__)))
        exec("def wrapper():\n    require(False)\n", scope)
        try:
            scope["wrapper"]()
        except ValueError as wrapped:
            self.assertIsNone(scope["native_requirement_receipt"](wrapped))

    def test_traceback_bound_and_no_args_locals_or_error_text_access(self):
        tree, scope = self.program("CLAMD_NATIVE_PROGRAM")
        line = next(iter(scope["NATIVE_REQUIREMENT_SITES"]))
        self.own_error(scope, line)
        def deep(count):
            if count:
                return deep(count - 1)
            scope["main"]()
        try:
            deep(70)
        except ValueError as error:
            self.assertIsNone(scope["native_requirement_receipt"](error))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "native_requirement_receipt")
        attributes = {n.attr for n in ast.walk(function) if isinstance(n, ast.Attribute)}
        self.assertFalse({"f_locals", "args", "co_filename", "co_consts"} & attributes)
        calls = [n.func.id for n in ast.walk(function) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        self.assertNotIn("str", calls)

    def test_optional_label_separate_from_unchanged_five_field_core(self):
        core = self.value()
        self.assertEqual(self.note(core)["clamd_failure"], core)
        for label in self.host["CLAMD_NATIVE_REQUIREMENTS"]:
            report = self.note(self.value(label))
            self.assertEqual(report["clamd_failure"], core)
            self.assertEqual(report["native_requirement"], label)
        for invalid in (True, 1, "unexported", "CASE_OUTCOME"):
            value = self.value()
            value["native_requirement"] = invalid
            self.assertNotIn("clamd_failure", self.note(value))
        value = self.value("shutdown")
        value["phase"] = "source_scan"
        self.assertNotIn("clamd_failure", self.note(value))
        for chain in ([], [{"class": True, "frames": []}], [{"class": 1, "frames": []}],
                      [{"class": "InspectionUnavailable", "frames": []}],
                      [{"class": "InspectionUnavailable", "frames": []}, {"class": "ValueError", "frames": []}]):
            value = self.value("case_outcome")
            value["chain"] = chain
            self.assertNotIn("clamd_failure", self.note(value))

    def test_canonical_EOF_duplicate_keys_and_extra_fields_refused(self):
        raw = self.encoded(self.value("case_outcome"))
        for invalid in (raw[:-1], raw + b"\n", b" " + raw, raw + b"unexported",
                        raw.replace(b'"native_requirement":"case_outcome"',
                            b'"native_requirement":"case_outcome","native_requirement":"case_outcome"')):
            self.host["_note_clamd_failure"](invalid, 1, case="clamd_max_filesize")
            self.assertNotIn("native_requirement", self.host["_failure_report"]())
        value = self.value("case_outcome")
        value["unexported"] = "unexported"
        self.assertNotIn("clamd_failure", self.note(value))

    def test_stale_wrong_case_code_and_stored_labels_cannot_escape(self):
        self.assertEqual(self.note(self.value("case_outcome"))["native_requirement"], "case_outcome")
        stored = self.host["_FAILURE"].get()
        stored["clamd_native_requirement"] = self.host["_ClamdNativeRequirement"]("clamd_two_clean", "shutdown")
        self.assertNotIn("native_requirement", self.host["_failure_report"]())
        stored["clamd_native_requirement"] = self.host["_ClamdNativeRequirement"]("clamd_max_filesize", "unexported")
        self.assertNotIn("native_requirement", self.host["_failure_report"]())
        stored["clamd_native_requirement"] = self.host["_ClamdNativeRequirement"]("clamd_max_filesize", "shutdown")
        stored["clamd_failure"] = self.host["_ClamdFailure"]("clamd_max_filesize", "ownership",
            (("InspectionUnavailable", ()),), False)
        self.assertIn("clamd_failure", self.host["_failure_report"]())
        self.assertNotIn("native_requirement", self.host["_failure_report"]())
        self.host["_progress"]("case_request", case="clamd_resident_parser")
        self.assertNotIn("clamd_native_requirement", stored)
        for code in (0, 2, True):
            self.host["_note_clamd_failure"](self.encoded(self.value("shutdown")), code, case="clamd_max_filesize")
            self.assertNotIn("native_requirement", self.host["_failure_report"]())

    def test_existing_derivative_optional_label_does_not_change_original_failure(self):
        stored = self.host["_FAILURE"].get()
        stored.update(case="clamd_two_clean")
        original = {"version": 1, "status": "native_clamd_proof_unavailable", "phase": "source_scan",
            "chain": [{"class": "InspectionUnavailable", "frames": [{"function": "_diagnostics", "line": 623}]}],
            "truncated": False}
        self.host["_note_clamd_failure"](self.encoded(original), 1, case="clamd_two_clean")
        calls = []
        def inert_container(*args):
            calls.append(args)
            return 1, self.encoded(self.value("shutdown"))
        self.host["container"] = inert_container
        self.host["_diagnose_clamd_startup"](["owned"], "immutable", "linux/amd64", Path("/owned/request.json"), case="clamd_two_clean")
        report = self.host["_failure_report"]()
        self.assertEqual(report["clamd_failure"], original)
        self.assertNotIn("native_requirement", report)
        self.assertEqual(report["clamd_startup_diagnostic"]["native_requirement"], "shutdown")
        self.assertFalse(report["clamd_startup_diagnostic"]["acceptance_evidence"])
        self.host["_diagnose_clamd_startup"](["owned"], "immutable", "linux/amd64", Path("/owned/request.json"), case="clamd_two_clean")
        self.assertEqual(len(calls), 1)
        stored.pop("clamd_startup_observation", None)  # A separate inert decoder scenario.
        invalid = self.value("shutdown")
        invalid["chain"] = [{"class": "InspectionUnavailable", "frames": []}]
        self.host["container"] = lambda *args: (1, self.encoded(invalid))
        self.host["_diagnose_clamd_startup"](["owned"], "immutable", "linux/amd64", Path("/owned/request.json"), case="clamd_two_clean")
        report = self.host["_failure_report"]()
        self.assertEqual(report["clamd_failure"], original)
        self.assertEqual(report["clamd_startup_diagnostic"]["status"], "unavailable")
        self.assertNotIn("native_requirement", report["clamd_startup_diagnostic"])


if __name__ == "__main__":
    unittest.main()
