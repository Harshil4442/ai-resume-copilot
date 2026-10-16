"""Isolated Writer restart controls; no app/conftest/native-process imports."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(os.environ.get("HIREWIZ_RENDERER_TEST_ROOT", str(Path(__file__).resolve().parents[2])))
CANDIDATE = Path(os.environ.get("HIREWIZ_RENDERER_CANDIDATE", str(ROOT / "infra/document-processing/docx_renderer.py")))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class WriterRestartControls(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.service = load("isolated_writer_owned_service", ROOT / "infra/document-processing/service.py")
        self.renderer = load("isolated_writer_restart_renderer", CANDIDATE)
        self.stack.enter_context(patch.dict(sys.modules, {"service": self.service}))
        self.service._CREATION_PENDING.clear()
        self.service._CREATION_FAILED.clear()
        self.command = ["/usr/lib/libreoffice/program/soffice.bin", "--headless",
                        "-env:UserInstallation=file:///tmp/synthetic/profile", "--convert-to", "pdf"]

    def refusal(self, code=81, *, authorized=True, outcome="unexpected_exit"):
        return self.service._LauncherRefused(authorized, code=code, outcome=outcome)

    def test_conversion_81_then_zero_preserves_command_profile_source_and_deadline(self):
        calls = []
        now = [100.0]
        source = b"public synthetic resume source"
        output = b"%PDF-1.7\npublic synthetic comparison"
        def owned(arguments, *, timeout, limit):
            calls.append((tuple(arguments), timeout, limit))
            if len(calls) == 1:
                now[0] += 2.5
                raise self.refusal()
            (Path(arguments[-2]) / "input.pdf").write_bytes(output)
            return 0, b"bounded engine status"
        with tempfile.TemporaryDirectory() as temp, patch.object(self.service, "_run", owned), \
                patch.object(self.renderer.time, "monotonic", lambda: now[0]):
            folder = Path(temp) / "before"
            result = self.renderer._render(source, folder, deadline=110.0)
            self.assertEqual(result, output)
            self.assertEqual((folder / "input.docx").read_bytes(), source)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], calls[1][0])
        self.assertEqual(calls[0][0][:7], (
            sys.executable, "-I", "/opt/document/writer_owner.py", "--deadline",
            "110.000000000", "--", "/usr/lib/libreoffice/program/soffice.bin"))
        self.assertEqual([row[1] for row in calls], [10.0, 7.5])
        self.assertEqual([row[2] for row in calls], [65536, 65536])

    def test_second_81_refuses_without_third_attempt(self):
        calls = []
        def owned(*_args, **_kwargs):
            calls.append(1)
            raise self.refusal()
        with patch.object(self.service, "_run", owned), self.assertRaises(self.renderer.RendererUnavailable):
            self.renderer._run(self.command, deadline=self.renderer.time.monotonic() + 10, restart_once=True)
        self.assertEqual(len(calls), 2)

    def test_expired_shared_deadline_after_81_prevents_second_launch(self):
        calls = []
        now = [100.0]
        def owned(*_args, **_kwargs):
            calls.append(1)
            now[0] = 111.0
            raise self.refusal()
        with patch.object(self.service, "_run", owned), patch.object(self.renderer.time, "monotonic", lambda: now[0]), \
                self.assertRaises(self.renderer.RendererUnavailable):
            self.renderer._run(self.command, deadline=110.0, restart_once=True)
        self.assertEqual(len(calls), 1)

    def test_only_authorized_clean_expected_exit_81_can_restart(self):
        class ForeignRefusal(self.service._LauncherRefused):
            pass
        variants = [ForeignRefusal(True, code=81, outcome="unexpected_exit")] + [self.refusal(code) for code in (79, 77, 2, -9, None)] + [
            self.refusal(authorized=False), self.refusal(outcome="reap_failed"),
            self.refusal(outcome="deadline"), self.refusal(outcome="failed")]
        for failure in variants:
            calls = []
            def owned(*_args, _calls=calls, _failure=failure, **_kwargs):
                _calls.append(1)
                raise _failure
            with self.subTest(code=failure.code, outcome=failure.outcome), patch.object(self.service, "_run", owned), \
                    self.assertRaises(self.renderer.RendererUnavailable):
                self.renderer._run(self.command, deadline=self.renderer.time.monotonic() + 10, restart_once=True)
            self.assertEqual(len(calls), 1)

    def test_pending_or_failed_creation_cleanup_quarantine_prevents_restart(self):
        for flag in (self.service._CREATION_PENDING, self.service._CREATION_FAILED):
            calls = []
            flag.set()
            def owned(*_args, _calls=calls, **_kwargs):
                _calls.append(1)
                raise self.refusal()
            try:
                with patch.object(self.service, "_run", owned), self.assertRaises(self.renderer.RendererUnavailable):
                    self.renderer._run(self.command, deadline=self.renderer.time.monotonic() + 10, restart_once=True)
                self.assertEqual(len(calls), 1)
            finally:
                flag.clear()

    def test_unconfirmed_timeout_or_private_exception_is_fixed_refusal(self):
        for failure in (TimeoutError("synthetic private timeout"), ValueError("synthetic private vendor detail")):
            calls = []
            def owned(*_args, _calls=calls, _failure=failure, **_kwargs):
                _calls.append(1)
                raise _failure
            with patch.object(self.service, "_run", owned), self.assertRaises(self.renderer.RendererUnavailable) as error:
                self.renderer._run(self.command, deadline=self.renderer.time.monotonic() + 10, restart_once=True)
            self.assertEqual(str(error.exception), "docx_renderer_unavailable")
            self.assertEqual(len(calls), 1)

    def test_version_and_default_helper_keep_single_attempt_semantics(self):
        calls = []
        def owned(*_args, **_kwargs):
            calls.append(1)
            raise self.refusal()
        with patch.object(self.service, "_run", owned), self.assertRaises(self.renderer.RendererUnavailable):
            self.renderer._version(deadline=self.renderer.time.monotonic() + 10)
        self.assertEqual(len(calls), 1)
        calls.clear()
        with patch.object(self.service, "_run", owned), self.assertRaises(self.renderer.RendererUnavailable):
            self.renderer._run(self.command, deadline=self.renderer.time.monotonic() + 10)
        self.assertEqual(len(calls), 1)

    def test_command_is_frozen_across_retry_and_version_limit_is_unchanged(self):
        calls = []
        def owned(arguments, *, timeout, limit):
            calls.append(tuple(arguments))
            if len(calls) == 1:
                arguments[:] = ["/untrusted/replacement"]
                raise self.refusal()
            return 0, b"safe status"
        with patch.object(self.service, "_run", owned):
            self.assertEqual(self.renderer._run(self.command, deadline=self.renderer.time.monotonic() + 10,
                                                limit=1024, restart_once=True), b"safe status")
        self.assertEqual(calls[0], calls[1])
        with patch.object(self.renderer, "_run", return_value=b"LibreOffice 24.8.7.2 bd155fa\n") as run:
            self.assertEqual(self.renderer._version(deadline=self.renderer.time.monotonic() + 10), "LibreOffice 24.8.7.2 bd155fa")
        self.assertEqual(run.call_args.kwargs["limit"], 1024)
        self.assertNotIn("restart_once", run.call_args.kwargs)

    def test_nonzero_return_and_nonbool_restart_option_are_refused(self):
        with patch.object(self.service, "_run", return_value=(81, b"status")) as owned, \
                self.assertRaises(self.renderer.RendererUnavailable):
            self.renderer._run(self.command, deadline=self.renderer.time.monotonic() + 10, restart_once=True)
        self.assertEqual(owned.call_count, 1)
        with patch.object(self.service, "_run", side_effect=AssertionError("must not execute")), \
                self.assertRaises(self.renderer.RendererUnavailable):
            self.renderer._run(self.command, deadline=self.renderer.time.monotonic() + 10, restart_once=1)


if __name__ == "__main__":
    unittest.main()
