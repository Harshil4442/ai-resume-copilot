"""Pure owned-log admission regression; no processor/app/native import."""
import ast
import hashlib
import os
import re
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
PROCESSOR = ROOT / "infra/document-processing/processor.py"


class LogSizeSentinelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(PROCESSOR.read_bytes())
        owned = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "OwnedClamd")
        method = next(node for node in owned.body if isinstance(node, ast.FunctionDef) and node.name == "_diagnostics")
        nodes = [node for node in tree.body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id in {"DATABASE", "CLAMD_SETTINGS"} for target in node.targets)]
        nodes.append(method)
        cls.scope = {"os": os, "stat": stat, "re": re, "InspectionUnavailable": ValueError}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "owned_log_guard_only", "exec"), cls.scope)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="owned-log-sentinel-inert-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.directory.chmod(0o700)
        self.owner = SimpleNamespace(socket_path=self.directory / "clamd.sock",
            log_path=self.directory / "clamd.log", log_fd=None, pipes={}, signatures=[],
            child=SimpleNamespace(poll=lambda: None))

    def inspect_pipe(self, record):
        self.owner.pipes = {"stdout": {"raw": bytearray(record + b"\nLoaded 123 signatures.\n"), "eof": True}}
        self.scope["_diagnostics"](self.owner)

    def test_actual_native_zero_size_sentinel_same_inode_log_admitted(self):
        # Official size-option zero maps to INT64_MAX on both supported64-bit images.
        record = b"Log file size limited to 9223372036854775807 bytes."
        self.assertEqual(len(record), 51)
        self.assertEqual(hashlib.sha256(record).hexdigest(),
            "4deff7b4790d4f43d52e4d5b831726e9b76a84dda16e8416b81cb22ad0f50350")
        self.owner.log_path.write_bytes(record + b"\nLoaded 123 signatures.\n")
        self.owner.log_path.chmod(0o600)
        self.owner.log_fd = os.open(self.owner.log_path, os.O_RDONLY | os.O_NOFOLLOW)
        self.addCleanup(os.close, self.owner.log_fd)
        info = os.fstat(self.owner.log_fd)
        self.owner.log_identity = info.st_dev, info.st_ino
        self.scope["_diagnostics"](self.owner)
        self.assertEqual(self.owner.signatures, [123])

    def test_exact_supported_zero_size_records_admitted_on_stdout(self):
        for record in (b"Log file size limit disabled.",
                       b"Log file size limited to 9223372036854775807 bytes."):
            with self.subTest(record_kind="documented zero semantics"):
                self.inspect_pipe(record)
                self.assertEqual(self.owner.signatures, [123])

    def test_other_log_limits_never_admitted_by_numeric_pattern(self):
        for size in (b"0", b"1", b"1048576", b"65536", b"9223372036854775806",
                     b"18446744073709551615", b"-1", b"9223372036854775807 extra"):
            with self.subTest(size_kind="unconfigured finite or malformed"):
                with self.assertRaises(ValueError):
                    self.inspect_pipe(b"Log file size limited to " + size + b" bytes.")

    def test_warning_error_skipped_and_unknown_records_still_refused(self):
        exact = b"Log file size limited to 9223372036854775807 bytes."
        for extra in (b"WARNING: synthetic", b"ERROR: synthetic", b"skipped", b"unforeseen vendor text"):
            with self.subTest(refusal_kind="existing fail-closed guard"):
                with self.assertRaises(ValueError):
                    self.inspect_pipe(exact + b"\n" + extra)


if __name__ == "__main__":
    unittest.main()
