"""Pure AST corpus-admission guards; no app imports or native process calls."""
from __future__ import annotations

import ast
import copy
import io
import types
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ORDINARY = ("clean_pdf", "clean_docx", "page_counter", "active_pdf", "includetext",
            "eicar_exact_file", "eicar_zip_member", "missing_definitions",
            "clean_tex", "clean_texzip", "eicar_tex_source", "eicar_texzip_member")
LAYOUT = ("styled_multipage_fit", "rendered_reflow_refusal")
CLAMD = ("clamd_two_clean", "clamd_source_eicar", "clamd_candidate_eicar", "clamd_zip_member_eicar",
         "clamd_encrypted_pdf", "clamd_encrypted_zip", "clamd_max_filesize", "clamd_max_scansize",
         "clamd_max_files", "clamd_max_recursion", "clamd_resident_parser")


class NativeCorpusGuards(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.engine = types.SimpleNamespace(_FAILURE_CASES=ORDINARY + LAYOUT + CLAMD)
        self.engine.validate_clamd_native = lambda *args: self.calls.append(args)
        source = Path(__file__).with_name("native_docx_ci_fixtures.py")
        self.tree = ast.parse(source.read_bytes())
        helper = next(n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == "_complete_corpus")
        scope = {"engine": self.engine}
        exec(compile(ast.Module(body=[helper], type_ignores=[]), "corpus_guard_AST", "exec"), scope)
        self.validate = scope["_complete_corpus"]
        self.report = {"platform": "linux/amd64",
                       "corpus": [{"case": name, "passed": True} for name in ORDINARY],
                       "docx_layout_corpus": [{"case": name, "passed": True} for name in LAYOUT],
                       "request_clamd_corpus": [{"case": name} for name in CLAMD]}
        self.contracts = [(name, ("public synthetic " + name).encode(), "clean", name == CLAMD[-1]) for name in CLAMD]

    def test_all_twenty_five_groups_and_exact_validator_arguments(self):
        ordinary, layouts = self.validate(self.report, self.contracts)
        self.assertIs(ordinary, self.report["corpus"])
        self.assertIs(layouts, self.report["docx_layout_corpus"])
        self.assertEqual(len(self.calls), 11)
        for call, contract, row in zip(self.calls, self.contracts, self.report["request_clamd_corpus"], strict=True):
            name, payload, expected, resident = contract
            self.assertIs(call[0], row)
            self.assertEqual(call[1:], (name, expected, resident, payload, "linux/amd64"))
        # Report order is not authorization; the exact source contracts bind names.
        self.report["request_clamd_corpus"].reverse()
        self.calls.clear()
        self.validate(self.report, self.contracts)
        self.assertEqual([call[1] for call in self.calls], list(CLAMD))

    def test_missing_duplicate_malformed_or_nonpassing_original_groups(self):
        for field in ("corpus", "docx_layout_corpus"):
            for mode in ("missing", "duplicate", "dict", "row", "case", "passed"):
                with self.subTest(field=field, mode=mode):
                    report = copy.deepcopy(self.report)
                    if mode == "missing":
                        report[field].pop()
                    elif mode == "duplicate":
                        report[field][-1] = copy.deepcopy(report[field][0])
                    elif mode == "dict":
                        report[field] = {}
                    elif mode == "row":
                        report[field][0] = None
                    elif mode == "case":
                        report[field][0]["case"] = {}
                    else:
                        report[field][0]["passed"] = 1
                    with self.assertRaises(AssertionError):
                        self.validate(report, self.contracts)
        self.assertEqual(self.calls, [])

    def test_all_eleven_daemon_rows_required_and_unique(self):
        for mode in ("missing", "duplicate", "dict", "row", "case", "other"):
            with self.subTest(mode=mode):
                report = copy.deepcopy(self.report)
                if mode == "missing":
                    report["request_clamd_corpus"].pop()
                elif mode == "duplicate":
                    report["request_clamd_corpus"][-1] = report["request_clamd_corpus"][0]
                elif mode == "dict":
                    report["request_clamd_corpus"] = {}
                elif mode == "row":
                    report["request_clamd_corpus"][0] = False
                else:
                    report["request_clamd_corpus"][0]["case"] = [] if mode == "case" else "unapproved"
                with self.assertRaises(AssertionError):
                    self.validate(report, self.contracts)
        self.assertEqual(self.calls, [])

    def test_contract_identity_types_duplicates_and_complete_scope(self):
        for mode in ("missing", "duplicate", "payload", "resident", "tuple", "extra_scope"):
            with self.subTest(mode=mode):
                contracts = copy.deepcopy(self.contracts)
                if mode == "missing":
                    contracts.pop()
                elif mode == "duplicate":
                    contracts[-1] = contracts[0]
                elif mode == "payload":
                    contracts[0] = (contracts[0][0], "text", "clean", False)
                elif mode == "resident":
                    contracts[0] = (contracts[0][0], b"bytes", "clean", 0)
                elif mode == "tuple":
                    contracts[0] = list(contracts[0])
                else:
                    self.engine._FAILURE_CASES = ORDINARY + LAYOUT + CLAMD + ("unapproved",)
                with self.assertRaises(AssertionError):
                    self.validate(self.report, contracts)
                self.engine._FAILURE_CASES = ORDINARY + LAYOUT + CLAMD
        self.assertEqual(self.calls, [])

    def test_runtime_wires_exact_contracts_before_native_reuse(self):
        runtime = next(n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == "_runtime")
        calls = [n for n in ast.walk(runtime) if isinstance(n, ast.Call)]
        guards = [n for n in calls if isinstance(n.func, ast.Name) and n.func.id == "_complete_corpus"]
        self.assertEqual(len(guards), 1)
        guard = guards[0]
        self.assertEqual(ast.dump(guard.args[0]), ast.dump(ast.Name(id="report", ctx=ast.Load())))
        self.assertEqual(ast.unparse(guard.args[1]), "engine.clamd_corpus()")
        native_calls = [n for n in calls if isinstance(n.func, ast.Attribute)
                        and isinstance(n.func.value, ast.Name) and n.func.value.id == "engine"
                        and n.func.attr in {"docker_command", "image_metadata", "container"}]
        self.assertTrue(native_calls)
        self.assertTrue(all(guard.lineno < n.lineno for n in native_calls))
        self.assertIn("engine.source_hashes()", ast.unparse(runtime))
        self.assertIn("engine.HASH_PROGRAM", ast.unparse(runtime))

    def test_fixture_zip_timestamps_are_stable_and_contents_preserved(self):
        proof = Path(__file__).resolve().parents[1] / "scripts/document_test_image.py"
        if not proof.exists():
            proof = Path(__file__).with_name("document_test_image.py")
        tree = ast.parse(proof.read_bytes())
        helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_canonical_clamd_zip")
        scope = {"io": io, "ZipFile": ZipFile}
        exec(compile(ast.Module(body=[helper], type_ignores=[]), "canonical_fixture_AST", "exec"), scope)
        stable = scope["_canonical_clamd_zip"]
        def archive(date):
            stream = io.BytesIO()
            with ZipFile(stream, "w") as zipped:
                info = ZipInfo("public-fixture.txt", date)
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                info.comment = b"public synthetic metadata"
                zipped.writestr(info, b"public synthetic fixture bytes")
            return stream.getvalue()
        first = archive((2026, 10, 13, 12, 0, 0))
        second = archive((2026, 10, 13, 12, 7, 0))
        self.assertNotEqual(first, second)
        self.assertEqual(stable(first), stable(second))
        self.assertEqual(stable(stable(first)), stable(first))
        with ZipFile(io.BytesIO(first)) as original, ZipFile(io.BytesIO(stable(first))) as observed:
            a, b = original.infolist()[0], observed.infolist()[0]
            self.assertEqual(b.date_time, (1980, 1, 1, 0, 0, 0))
            self.assertEqual(observed.read(b), original.read(a))
            self.assertEqual((a.filename, a.compress_type, a.external_attr, a.comment),
                             (b.filename, b.compress_type, b.external_attr, b.comment))

    def test_native_validator_refusal_is_preserved(self):
        error = ValueError("fixed inert refusal")
        def refuse(*args):
            self.calls.append(args)
            raise error
        self.engine.validate_clamd_native = refuse
        with self.assertRaises(ValueError) as caught:
            self.validate(self.report, self.contracts)
        self.assertIs(caught.exception, error)
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
