"""Pure worker tests: every scanner/process/socket operation is mocked."""
import ast
import hashlib
import importlib.util
import json
import os
import signal
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
PROCESSOR = ROOT / "infra/document-processing/processor.py"
POLICY = ROOT / "infra/document-processing/policy.json"
spec = importlib.util.spec_from_file_location("private_owned_clamd_processor", PROCESSOR)
processor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(processor)


class Child:
    pid = 424242
    def __init__(self):
        self.code = None
    def poll(self):
        return self.code
    def wait(self, timeout):
        if self.code is None:
            raise processor.subprocess.TimeoutExpired("private mock", timeout)
        return self.code


class Connection:
    def __init__(self, expected, reply):
        self.expected, self.reply = expected, reply
        self.sent = bytearray()
        self.replied = False
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def send(self, value):
        count = min(3, len(value))
        self.sent.extend(value[:count])
        return count
    def recv(self, count):
        if self.replied:
            return b""
        self.replied = True
        return self.reply


class OwnedTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="private-clamd-inert-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.directory.chmod(0o700)
        self.path = self.directory / "original.docx"
        self.content = b"public synthetic inert bytes"
        self.path.write_bytes(self.content)
        self.path.chmod(0o600)
        self.owner = processor.OwnedClamd(self.directory, time.monotonic() + 50)
        self.metadata = {"engine": "ClamAV", "version": "1.4.3", "definitions": "28151/2026-10-12T06:24:02+00:00"}
        self.header = "ClamAV 1.4.3/28151/Mon Oct 12 06:24:02 2026"
        for name in ("Popen", "run"):
            patcher = mock.patch.object(processor.subprocess, name, side_effect=AssertionError("native invocation forbidden"))
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(processor.socket, "socket", side_effect=AssertionError("native socket forbidden"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def scanned(self, reply=b"stream: OK\0", mutation=None, header=None):
        calls = []
        def start(deadline):
            self.owner.child = Child()
            self.owner.signatures = [100]
        def exchange(parts, deadline, limit):
            calls.append(parts)
            if parts == [b"zVERSION\0"]:
                return (header or self.header).encode() + b"\0"
            if mutation:
                mutation()
            return reply
        with mock.patch.object(processor, "_clamd_definition", return_value=(self.metadata, self.header)), \
                mock.patch.object(self.owner, "_start", side_effect=start) as startup, \
                mock.patch.object(self.owner, "_exchange", side_effect=exchange):
            value = self.owner.scan(self.path, self.content)
        return value, calls, startup.call_count

    def test_exact_clean_stream(self):
        value, calls, starts = self.scanned()
        self.assertEqual(value, self.metadata)
        self.assertEqual(starts, 1)
        parts = calls[1]
        self.assertEqual(parts[0], b"zINSTREAM\0")
        self.assertEqual(parts[-1], b"\0" * 4)
        self.assertEqual(b"".join(bytes(p) for p in parts[2:-1:2]), self.content)
        self.assertEqual(self.owner.scan_bindings[0]["sha256"], hashlib.sha256(self.content).hexdigest())

    def exchanged(self, reply=b"stream: OK\0", early=False, limit=512):
        parts = [b"zINSTREAM\0", b"\0\0\0\x03", b"abc", b"\0" * 4]
        connection = Connection(b"".join(parts), reply)
        self.owner.child = Child()
        def selected(readable, writable, exceptional, wait):
            readable = [connection] if early or bytes(connection.sent) == connection.expected else []
            return readable, writable, []
        with mock.patch.object(self.owner, "_socket", return_value=connection), \
                mock.patch.object(self.owner, "_drain"), \
                mock.patch.object(processor.select, "select", side_effect=selected):
            value = self.owner._exchange(parts, time.monotonic() + 30, limit)
        return value, connection

    def test_partial_native_send_and_complete_reply_eof(self):
        reply, connection = self.exchanged()
        self.assertEqual(reply, b"stream: OK\0")
        self.assertEqual(bytes(connection.sent), connection.expected)
        self.assertTrue(connection.replied)

    def test_early_native_reply_is_unavailable(self):
        with self.assertRaises(processor.InspectionUnavailable):
            self.exchanged(early=True)

    def test_overbound_native_reply_is_unavailable(self):
        with self.assertRaises(processor.InspectionUnavailable):
            self.exchanged(reply=b"x" * 513)

    def test_native_daemon_death_before_exchange(self):
        self.owner.child = Child()
        self.owner.child.code = 1
        with mock.patch.object(self.owner, "_socket", return_value=Connection(b"x", b"")), \
                mock.patch.object(self.owner, "_drain"):
            with self.assertRaises(processor.InspectionUnavailable):
                self.owner._exchange([b"x"], time.monotonic() + 30, 512)

    def test_final_log_check_runs_after_already_observed_pipe_eof(self):
        self.owner.child = Child()
        self.owner.pipes = {}
        def kill(pid, sig):
            if sig == signal.SIGTERM:
                self.owner.child.code = 0
            if sig == 0:
                raise ProcessLookupError
        with mock.patch.object(processor.os, "getpgid", return_value=self.owner.child.pid), \
                mock.patch.object(processor.os, "killpg", side_effect=kill), \
                mock.patch.object(self.owner, "_diagnostics") as final:
            self.owner.close()
        self.assertEqual(final.call_count, 1)

    def test_one_engine_two_sequential_scans(self):
        self.scanned()
        target = self.directory / "candidate.docx"
        target.write_bytes(self.content)
        target.chmod(0o600)
        self.path = target
        _, _, starts = self.scanned()
        self.assertEqual(starts, 0)
        self.assertEqual(len(self.owner.scan_bindings), 2)
        with self.assertRaises(processor.InspectionUnavailable):
            self.scanned()

    def test_detection_is_permanent_refusal(self):
        with self.assertRaises(processor.InspectionDenied):
            self.scanned(b"stream: Eicar-Signature FOUND\0")
        self.assertEqual(self.owner.scan_bindings[0]["result"], "CL_VIRUS")

    def test_limit_alert_is_refusal(self):
        with self.assertRaises(processor.InspectionDenied):
            self.scanned(b"stream: Heuristics.Limits.Exceeded FOUND\0")

    def test_error_is_unavailable(self):
        with self.assertRaises(processor.InspectionUnavailable):
            self.scanned(b"stream: error ERROR\0")

    def test_duplicate_or_truncated_reply(self):
        for reply in (b"stream: OK", b"stream: OK\0stream: OK\0", b"stream: OK\nx", b"other: OK\0"):
            self.owner = processor.OwnedClamd(self.directory, time.monotonic() + 50)
            with self.assertRaises(processor.InspectionUnavailable):
                self.scanned(reply)

    def test_no_virus_name_in_exception(self):
        with self.assertRaises(processor.InspectionDenied) as error:
            self.scanned(b"stream: Private.Name FOUND\0")
        self.assertEqual(str(error.exception), processor.ERROR)

    def test_header_drift(self):
        with self.assertRaises(processor.InspectionUnavailable):
            self.scanned(header=self.header.replace("28151", "28152"))

    def test_post_scan_byte_change(self):
        with self.assertRaises(processor.InspectionUnavailable):
            self.scanned(mutation=lambda: self.path.write_bytes(b"changed"))

    def test_pre_scan_byte_change(self):
        self.path.write_bytes(b"other")
        with self.assertRaises(processor.InspectionUnavailable):
            self.scanned()

    def test_file_permissions(self):
        self.path.chmod(0o644)
        with self.assertRaises(processor.InspectionUnavailable):
            self.scanned()

    def test_wrong_parent(self):
        self.owner.directory = self.directory / "other"
        with self.assertRaises(processor.InspectionUnavailable):
            self.scanned()

    def test_symlink_file(self):
        actual = self.path.with_name("actual")
        self.path.rename(actual)
        self.path.symlink_to(actual)
        with self.assertRaises(processor.InspectionUnavailable):
            self.scanned()

    def test_mutable_or_oversize_input(self):
        for value in (bytearray(self.content), b"", b"x" * (processor.MAX_FILE + 1)):
            with self.assertRaises(processor.InspectionUnavailable):
                self.owner.scan(self.path, value)

    def log(self, raw, final=False):
        self.owner.child = Child()
        if final:
            self.owner.child.code = 0
        self.owner.log_path.write_bytes(raw)
        self.owner.log_path.chmod(0o600)
        self.owner.log_fd = os.open(self.owner.log_path, os.O_RDONLY | os.O_NOFOLLOW)
        self.addCleanup(os.close, self.owner.log_fd)
        info = os.fstat(self.owner.log_fd)
        self.owner.log_identity = (info.st_dev, info.st_ino)

    def test_genuine_log_signature_record_parser(self):
        self.log(b"Loaded 123 signatures.\nSelf checking disabled.\n")
        self.owner._diagnostics()
        self.assertEqual(self.owner.signatures, [123])

    def test_tagged_native_startup_records_are_accepted_exactly(self):
        # ClamAV 1.4.3 localserver.c and server-th.c emit these fixed records.
        lines = ["Loaded 123 signatures.",
                 "LOCAL: Unix socket file " + str(self.owner.socket_path),
                 "LOCAL: Setting connection queue length to 2",
                 "Alerting of encrypted archives _and_ documents enabled."]
        self.log(("\n".join(lines) + "\n").encode("ascii"))
        self.owner._diagnostics()
        self.assertEqual(self.owner.signatures, [123])

    def test_native_local_records_reject_other_socket_queue_and_text(self):
        for line in ("LOCAL: Unix socket file /tmp/unowned.sock",
                     "LOCAL: Setting connection queue length to 3",
                     "LOCAL: arbitrary vendor text"):
            self.owner.pipes = {"stdout": {"raw": bytearray((line + "\n").encode("ascii")), "eof": True}}
            with self.assertRaises(processor.InspectionUnavailable):
                self.owner._diagnostics()

    def test_zero_or_duplicate_signature_records(self):
        for raw in (b"Loaded 0 signatures.\n", b"Loaded 1 signatures.\nLoaded 2 signatures.\n"):
            self.owner.pipes = {"stdout": {"raw": bytearray(raw), "eof": True}}
            with self.assertRaises(processor.InspectionUnavailable):
                self.owner._diagnostics()

    def test_partial_record_is_not_final_metadata(self):
        self.log(b"Loaded 123 signatures.")
        self.owner._diagnostics()
        self.assertEqual(self.owner.signatures, [])
        self.owner.child.code = 0
        with self.assertRaises(processor.InspectionUnavailable):
            self.owner._diagnostics()

    def test_warning_error_skipped_and_unforeseen_logs(self):
        for line in (b"WARNING: unknown", b"ERROR: native", b"skipped", b"arbitrary vendor text"):
            self.owner.pipes = {"stdout": {"raw": bytearray(line + b"\n"), "eof": True}}
            with self.assertRaises(processor.InspectionUnavailable):
                self.owner._diagnostics()

    def test_any_stderr_is_unavailable(self):
        self.owner.pipes = {"stderr": {"raw": bytearray(b"anything"), "eof": False}}
        with self.assertRaises(processor.InspectionUnavailable):
            self.owner._diagnostics()

    def test_log_cap(self):
        self.log(b"x" * 65537)
        with self.assertRaises(processor.InspectionUnavailable):
            self.owner._diagnostics()

    def test_log_same_inode(self):
        self.log(b"Loaded 123 signatures.\n")
        self.owner.log_path.unlink()
        self.owner.log_path.write_bytes(b"Loaded 123 signatures.\n")
        with self.assertRaises(processor.InspectionUnavailable):
            self.owner._diagnostics()

    def test_engine_reload_is_unavailable(self):
        self.owner.pipes = {"stdout": {"raw": bytearray(b"Database correctly reloaded (123 signatures)\n"), "eof": True}}
        with self.assertRaises(processor.InspectionUnavailable):
            self.owner._diagnostics()

    def test_source_header_freshness_boundaries(self):
        issued = datetime(2026, 10, 12, 6, 24, 2, tzinfo=UTC)
        with mock.patch.object(processor, "_command", return_value=(0, self.header)):
            for now in (issued, issued + timedelta(days=3)):
                metadata, _ = processor._clamd_definition(time.monotonic() + 30, now)
                self.assertEqual(metadata, self.metadata)
            for now in (issued - timedelta(microseconds=1), issued + timedelta(days=3, microseconds=1)):
                with self.assertRaises(processor.InspectionUnavailable):
                    processor._clamd_definition(time.monotonic() + 30, now)

    def test_unreviewed_or_malformed_engine(self):
        for header in ("ClamAV 1.5.0/28151/Mon Oct 12 06:24:02 2026", "unknown"):
            with mock.patch.object(processor, "_command", return_value=(0, header)):
                with self.assertRaises(processor.InspectionUnavailable):
                    processor._clamd_definition(time.monotonic() + 30)

    def test_bad_deadline(self):
        for deadline in (True, float("nan"), float("inf"), time.monotonic() - 1):
            with self.assertRaises(processor.InspectionUnavailable):
                processor.OwnedClamd._remaining(deadline)

    def test_expired_context_never_admits(self):
        self.owner.deadline = time.monotonic() - 1
        with self.assertRaises(processor.InspectionUnavailable):
            with self.owner:
                self.fail("admitted")

    def test_normal_stop_reap_before_following_work(self):
        self.owner.child = Child()
        order = []
        def kill(pid, sig):
            order.append(sig)
            if sig == signal.SIGTERM:
                self.owner.child.code = 0
            if sig == 0:
                raise ProcessLookupError
        with mock.patch.object(processor.os, "getpgid", return_value=self.owner.child.pid), \
                mock.patch.object(processor.os, "killpg", side_effect=kill):
            self.owner.close()
        self.assertTrue(self.owner.stopped)
        self.assertEqual(order, [signal.SIGTERM, 0])

    def test_cleanup_unknown_prevents_following_work(self):
        self.owner.child = Child()
        with mock.patch.object(processor.os, "getpgid", return_value=1):
            with self.assertRaises(processor.InspectionUnavailable):
                self.owner.close()
        self.assertFalse(self.owner.stopped)

    def test_native_elf_metadata_is_not_a_source_label(self):
        path = self.directory / "synthetic.elf"
        raw = bytearray(20)
        raw[:7] = b"\x7fELF\x02\x01\x01"
        raw[18:20] = (62).to_bytes(2, "little")
        path.write_bytes(raw)
        row = processor._native_file(path)
        self.assertEqual(row["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(row["elf_machine"], 62)
        path.write_bytes(b"not a native binary")
        with self.assertRaises(processor.InspectionUnavailable):
            processor._native_file(path)

    def test_ordinary_inspection_retains_summary_and_scan_before_parser(self):
        tree = ast.parse(PROCESSOR.read_text())
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        scan = ast.unparse(functions["scan_original"])
        for label in ("Scanned files", "Scanned directories", "Known viruses", "Engine version", "Infected files"):
            self.assertIn(label, scan)
        self.assertIn("/usr/bin/clamscan", scan)
        inspection = functions["inspect"]
        scans = [node for node in ast.walk(inspection) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "scan_original"]
        parsers = [node for node in ast.walk(inspection) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Name) and node.func.id == "parse_resume_file"]
        self.assertEqual(len(scans), 1)
        self.assertEqual(len(parsers), 1)
        self.assertLess(scans[0].lineno, parsers[0].lineno)
        self.assertFalse(any(isinstance(node, ast.Name) and node.id == "OwnedClamd"
                             for node in ast.walk(inspection)))

    def test_context_ends_before_renderer_import(self):
        tree = ast.parse((PROCESSOR).read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "validate_docx_edit")
        outer = next(n for n in function.body if isinstance(n, ast.With))
        nested = next(n for n in outer.body if isinstance(n, ast.With))
        call = nested.items[0].context_expr
        self.assertEqual(call.func.id, "OwnedClamd")
        following = outer.body[outer.body.index(nested) + 1]
        self.assertIsInstance(following, ast.ImportFrom)
        self.assertEqual(following.module, "docx_renderer")
        calls = [n for n in ast.walk(nested) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "scan"]
        self.assertEqual(len(calls), 2)

    def test_exact_config_parity_and_no_tcp(self):
        expected = {"OfficialDatabaseOnly": "yes", "FailIfCvdOlderThan": "3", "DisableCache": "yes",
            "AlertEncrypted": "yes", "AlertExceedsMax": "yes", "HeuristicAlerts": "yes", "MaxFileSize": "6M",
            "MaxScanSize": "20M", "MaxFiles": "500", "MaxRecursion": "16", "MaxScanTime": "20000",
            "BytecodeTimeout": "1000", "StreamMaxLength": "5M", "MaxThreads": "1", "MaxQueue": "2",
            "SelfCheck": "0", "BytecodeUnsigned": "no", "BytecodeSecurity": "TrustSigned", "Debug": "no",
            "LogFileMaxSize": "0", "LogRotate": "no", "ScanXMLDOCS": "yes"}
        self.assertTrue(all(processor.CLAMD_SETTINGS.get(k) == v for k, v in expected.items()))
        self.assertFalse({"TCPSocket", "TCPAddr", "VirusEvent", "User", "PidFile"} & set(processor.CLAMD_SETTINGS))
        self.assertLess(1 * 16 + (2 - 1) + 6, 64)

    def test_policy_splits_summary_and_keeps_resource_and_source_bounds(self):
        policy = json.loads(POLICY.read_text())
        self.assertNotIn("detection_requires_coherent_exit_and_summary", policy["scanner"])
        self.assertEqual(policy["processor_rlimits"], {
            "address_space_bytes": 2147483648, "core_bytes": 0, "cpu_seconds": 40,
            "file_bytes": 33554432, "open_descriptors": 64})
        self.assertEqual(policy["docx_layout"]["processor_absolute_work_seconds"], 50)
        self.assertEqual(policy["scanner"]["scan_command_timeout_seconds"], 30)
        self.assertEqual(policy["scanner"]["max_definition_age_seconds"], 259200)
        self.assertEqual(policy["structure"]["docx"]["max_entries"], 500)
        self.assertEqual(policy["structure"]["docx"]["max_expanded_bytes"], 20971520)
        self.assertEqual(policy["structure"]["pdf"]["max_pages"], 20)
        self.assertEqual(len(policy["source_sha256"]), 11)
        self.assertEqual(policy["scanner"]["result_contracts"]["inspect"], "unchanged_clamscan_exact_exit_and_summary")


if __name__ == "__main__":
    unittest.main()
