"""Pure synthetic AV reply/admission guards, never native acceptance evidence."""
import ast
import base64
import copy
import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend/scripts/document_test_image.py"
PROCESSOR = ROOT / "infra/document-processing/processor.py"


class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(SCRIPT.read_bytes())
        names = {"_validate_clamd_protocol", "validate_clamd_native"}
        nodes = [n for n in cls.tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        if {n.name for n in nodes} != names:
            raise AssertionError("missing fixed protocol guards")
        cls.scope = {"base64": base64, "hashlib": hashlib, "json": json, "re": re,
                     "MEMORY": 2 * 1024**3, "DocumentImageError": ValueError}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "pure_protocol_guard", "exec"), cls.scope)
        cls.programs = {n.targets[0].id: ast.literal_eval(n.value) for n in cls.tree.body
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id in {"CLAMD_NATIVE_PROGRAM", "CLAMD_STARTUP_DIAGNOSTIC_PROGRAM"}}

    def setUp(self):
        self.source, self.candidate = b"public synthetic source", b"public synthetic candidate"
        self.metadata = {"engine": "ClamAV", "version": "1.4.3", "definitions": "28151/2026-10-12T06:24:02+00:00"}
        self.request = {"source": base64.b64encode(self.source).decode(),
            "candidate_sha256": hashlib.sha256(self.candidate).hexdigest(),
            "candidate_size_bytes": len(self.candidate)}
        self.payload = json.dumps(self.request).encode()
        paths = ["/usr/sbin/clamd", "/usr/bin/clamscan", "/usr/lib/x86_64-linux-gnu/libclamav.so.12.0.3"]
        self.value = {"case": "clamd_two_clean", "outcome": "clean",
            "native_installation": {"version": 1, "engine": "1.4.3", "files": [
                {"path": path, "sha256": "a" * 64, "size": 200, "elf_machine": 62} for path in paths]},
            "owned_peer": {"pid": 424242, "uid": 65534, "gid": 65534}, "loaded_signatures": 100,
            "scans": [self.binding(self.source, result="CL_CLEAN"), self.binding(self.candidate, result="CL_CLEAN")],
            "protocol_scans": [self.binding(self.source, reply="OK"), self.binding(self.candidate, reply="OK")],
            "instream_attempts": 2, "zip_admission_failure": None,
            "daemon_returncode": 0, "daemon_stopped_before_writer": True, "native_log_removed": True,
            "renderer_version_after_stop": "LibreOffice 25.2.3.2", "resident_parser_exercised": False,
            "resident_parser_cgroup_current_bytes": None, "cgroup_memory_max_bytes": 2 * 1024**3,
            "cgroup_memory_peak_bytes": 1024**3, "performance_proven": False, "cloud_run_isolation_proven": False}

    def binding(self, content, **extra):
        return {"sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content),
                "metadata": copy.deepcopy(self.metadata), **extra}

    def check(self, value=None, expected=None):
        value = self.value if value is None else value
        expected = value["outcome"] if expected is None else expected
        return self.scope["validate_clamd_native"](value, value["case"], expected, False, self.payload, "linux/amd64")

    def refused(self, value):
        with self.assertRaises((ValueError, KeyError, TypeError)):
            self.check(value)

    def nonclean(self, reply="OK"):
        value = copy.deepcopy(self.value)
        value["case"], value["outcome"] = "clamd_max_filesize", "candidate_nonclean"
        value["scans"] = value["scans"][:1]
        value["protocol_scans"][1]["reply"] = reply
        if reply == "OK":
            value["zip_admission_failure"] = self.binding(self.candidate, reason="member_bytes")
        return value

    def test_clean_requires_two_completed_and_two_admitted_scans(self):
        self.check()
        for key in ("scans", "protocol_scans"):
            value = copy.deepcopy(self.value)
            value[key] = value[key][:1]
            with self.subTest(key=key):
                self.refused(value)
        value = copy.deepcopy(self.value)
        value["zip_admission_failure"] = self.binding(self.candidate, reason="member_bytes")
        self.refused(value)

    def test_native_FOUND_remains_actual_virus_not_ZIP_refusal(self):
        for outcome, replies, results in (
                ("source_refused", ["FOUND"], ["CL_VIRUS"]),
                ("candidate_refused", ["OK", "FOUND"], ["CL_CLEAN", "CL_VIRUS"])):
            value = copy.deepcopy(self.value)
            value["outcome"] = outcome
            value["protocol_scans"] = value["protocol_scans"][:len(replies)]
            value["scans"] = value["scans"][:len(results)]
            value["instream_attempts"] = len(replies)
            for row, reply in zip(value["protocol_scans"], replies, strict=True):
                row["reply"] = reply
            for row, result in zip(value["scans"], results, strict=True):
                row["result"] = result
            self.check(value)
            if outcome == "candidate_refused":
                self.check(value, "candidate_nonclean")
            value["zip_admission_failure"] = self.binding(self.candidate, reason="encrypted")
            self.refused(value)

    def test_completed_OK_and_actual_ZIP_refusal_are_not_admitted_clean(self):
        value = self.nonclean()
        self.check(value)
        for reason in ("malformed", "unsupported", "encrypted", "member_bytes", "total_bytes", "members", "depth", "deadline"):
            value["zip_admission_failure"]["reason"] = reason
            self.check(value)
        value["scans"].append(self.binding(self.candidate, result="CL_CLEAN"))
        self.refused(value)

    def test_completed_OTHER_is_distinct_from_missing_native_reply(self):
        value = self.nonclean("OTHER")
        self.check(value)
        value["protocol_scans"] = value["protocol_scans"][:1]
        self.refused(value)
        value = self.nonclean("OTHER")
        value["zip_admission_failure"] = self.binding(self.candidate, reason="member_bytes")
        self.refused(value)

    def test_attempt_count_has_exact_complete_reply_bound(self):
        for count in (True, False, 0, 1, 3, 2.0, None):
            value = copy.deepcopy(self.value)
            value["instream_attempts"] = count
            with self.subTest(count=count):
                self.refused(value)
        value = copy.deepcopy(self.value)
        value["protocol_scans"].append(copy.deepcopy(value["protocol_scans"][1]))
        value["instream_attempts"] = 3
        self.refused(value)

    def test_exact_source_candidate_identity_and_admitted_metadata_join(self):
        for index in (0, 1):
            for key, wrong in (("sha256", "b" * 64), ("size_bytes", 1)):
                value = copy.deepcopy(self.value)
                value["protocol_scans"][index][key] = wrong
                with self.subTest(index=index, key=key):
                    self.refused(value)
        value = copy.deepcopy(self.value)
        value["protocol_scans"].reverse()
        self.refused(value)
        value = copy.deepcopy(self.value)
        value["protocol_scans"][1]["metadata"]["definitions"] = "28152/2026-10-12T06:24:02+00:00"
        self.refused(value)
        value = self.nonclean()
        value["protocol_scans"][1]["metadata"]["definitions"] = "28152/2026-10-12T06:24:02+00:00"
        value["zip_admission_failure"]["metadata"] = copy.deepcopy(value["protocol_scans"][1]["metadata"])
        self.refused(value)

    def test_typed_exact_protocol_schema_and_finite_reply(self):
        for key, wrong in (("reply", "PRIVATE_CANARY"), ("reply", True), ("size_bytes", True),
                           ("sha256", 1), ("metadata", None)):
            value = copy.deepcopy(self.value)
            value["protocol_scans"][1][key] = wrong
            with self.subTest(key=key, wrong=wrong):
                self.refused(value)
        value = copy.deepcopy(self.value)
        value["protocol_scans"][1]["raw_vendor_message"] = "PRIVATE_CANARY"
        self.refused(value)
        for key in ("protocol_scans", "instream_attempts", "zip_admission_failure"):
            value = copy.deepcopy(self.value)
            del value[key]
            self.refused(value)

    def test_refusal_is_fixed_and_bound_to_actual_candidate_OK(self):
        value = self.nonclean()
        mutations = [("reason", "PRIVATE_CANARY"), ("reason", True), ("sha256", "b" * 64),
                     ("size_bytes", 1), ("metadata", self.binding(self.source)["metadata"] | {"version": "wrong"})]
        for key, wrong in mutations:
            changed = copy.deepcopy(value)
            changed["zip_admission_failure"][key] = wrong
            with self.subTest(key=key):
                self.refused(changed)
        value["zip_admission_failure"]["raw_member_name"] = "PRIVATE_CANARY"
        self.refused(value)
        value = self.nonclean()
        value["zip_admission_failure"] = None
        self.refused(value)
        value = self.nonclean()
        value["scans"].append(self.binding(self.candidate, result="CL_VIRUS"))
        value["outcome"] = "candidate_refused"
        self.refused(value)
        # bool compares equal to integer one; the receipt must still refuse it.
        self.candidate = b"x"
        self.request["candidate_sha256"] = hashlib.sha256(self.candidate).hexdigest()
        self.request["candidate_size_bytes"] = 1
        self.payload = json.dumps(self.request).encode()
        value = self.nonclean()
        value["protocol_scans"][1] = self.binding(self.candidate, reply="OK")
        value["zip_admission_failure"] = self.binding(self.candidate, reason="member_bytes")
        self.check(value)
        value["zip_admission_failure"]["size_bytes"] = True
        self.refused(value)

    def test_original_main_acceptance_and_ordinary_corpus_stay_separate(self):
        for program in self.programs.values():
            tree = ast.parse(program)
            main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
            checks = [ast.dump(n.args[0], include_attributes=False) for n in ast.walk(main)
                      if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "require"]
            self.assertIn(ast.dump(ast.parse("outcome == actual_expected", mode="eval").body, include_attributes=False), checks)
            self.assertIn(ast.dump(ast.parse("source_metadata == candidate_metadata and len(owner.scan_bindings) == 2", mode="eval").body, include_attributes=False), checks)
            self.assertIn(ast.dump(ast.parse("outcome != 'clean'", mode="eval").body, include_attributes=False), checks)
            self.assertIn(ast.dump(ast.parse("owner.stopped and not owner.forced and owner.child is not None and owner.child.returncode == 0", mode="eval").body, include_attributes=False), checks)
            response = next(n for n in ast.walk(main) if isinstance(n, ast.Assign)
                            and any(isinstance(t, ast.Name) and t.id == "response" for t in n.targets)).value
            values = {ast.literal_eval(k): ast.unparse(v) for k, v in zip(response.keys, response.values, strict=True)}
            self.assertEqual(values["protocol_scans"], "owner.protocol_bindings")
            self.assertEqual(values["instream_attempts"], "owner.instream_attempts")
            self.assertEqual(values["zip_admission_failure"], "owner.zip_admission_failure")
            self.assertEqual(values["scans"], "owner.scan_bindings")

    def test_all_three_literal_raise_tables_match_actual_processor(self):
        tree = ast.parse(PROCESSOR.read_bytes())
        names = {"_command", "_native_file", "_installed_clamd", "_clamd_definition", "_read_clamd_maps", "_zip_check", "_zip_admit", "_read_clamd_manifest"}
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        functions += [n for n in next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "OwnedClamd").body
                      if isinstance(n, ast.FunctionDef)]
        table = {n.name: tuple(sorted(x.lineno for x in ast.walk(n) if isinstance(x, ast.Raise))) for n in functions
                 if any(isinstance(x, ast.Raise) for x in ast.walk(n))}
        for tree in (self.tree, *(ast.parse(p) for p in self.programs.values())):
            actual = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == "CLAMD_FAILURE_LINES" for t in n.targets))
            self.assertEqual(actual, table)


if __name__ == "__main__":
    unittest.main()
