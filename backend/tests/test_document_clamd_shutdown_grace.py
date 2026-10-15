"""Pure clock/process shutdown regressions; never launch a daemon or signal OS."""
import ast
import math
import signal
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
PROCESSOR = ROOT / "infra/document-processing/processor.py"


class Refused(ValueError):
    pass


class Clock:
    def __init__(self):
        self.now = 0.0
    def monotonic(self):
        return self.now


class Child:
    pid = 424242
    def __init__(self, clock, exit_at, exit_code=0):
        self.clock, self.exit_at, self.exit_code = clock, exit_at, exit_code
        self.code = None
        self.wait_timeout = None
    def poll(self):
        if self.code is None and self.clock.now >= self.exit_at:
            self.code = self.exit_code
        return self.code
    def wait(self, timeout):
        self.wait_timeout = timeout
        if self.poll() is None:
            raise AssertionError("mock child not terminal")
        return self.code


class ShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(PROCESSOR.read_bytes())
        owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "OwnedClamd")
        cls.nodes = [n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name in {"close", "_remaining"}]
        if {n.name for n in cls.nodes} != {"close", "_remaining"}:
            raise AssertionError("missing actual shutdown methods")

    def fixture(self, exit_at, *, exit_code=0, work_deadline=50.0):
        clock = Clock()
        child = Child(clock, exit_at, exit_code)
        sent, closed, diagnostics = [], [], []

        def killpg(pid, kind):
            self.assertEqual(pid, child.pid)
            if kind == 0:
                if child.poll() is not None:
                    raise ProcessLookupError()
                return
            sent.append((kind, clock.now))
            if kind == signal.SIGKILL:
                child.code = -9

        scope = {"math": math, "time": clock, "signal": signal,
            "os": SimpleNamespace(getpgid=lambda pid: pid, killpg=killpg),
            "InspectionUnavailable": Refused}
        exec(compile(ast.Module(body=self.nodes, type_ignores=[]), "actual_owned_shutdown", "exec"), scope)
        owner = SimpleNamespace(stopped=False, forced=False, child=child, deadline=work_deadline,
                                log_fd=None, log_identity=None)
        owner._remaining = scope["_remaining"].__func__
        scope["OwnedClamd"] = SimpleNamespace(_remaining=owner._remaining)
        owner.pipes = {name: {"eof": False, "stream": SimpleNamespace(close=lambda n=name: closed.append(n))}
                       for name in ("stdout", "stderr")}
        def drain(wait=0):
            clock.now += wait
            if child.poll() is not None:
                for state in owner.pipes.values():
                    state["eof"] = True
        owner._drain = drain
        owner._diagnostics = lambda: diagnostics.append(clock.now)
        return scope["close"], owner, child, clock, sent, closed, diagnostics

    def test_late_graceful_exit_uses_existing_budget_and_still_checks_EOF(self):
        close, owner, child, clock, sent, closed, diagnostics = self.fixture(1.2)
        close(owner)
        self.assertTrue(owner.stopped)
        self.assertFalse(owner.forced)
        self.assertEqual(child.code, 0)
        self.assertEqual([kind for kind, _ in sent], [signal.SIGTERM])
        self.assertTrue(all(s["eof"] for s in owner.pipes.values()))
        self.assertEqual(sorted(closed), ["stderr", "stdout"])
        self.assertEqual(len(diagnostics), 1)
        self.assertGreater(child.wait_timeout, 0.25)
        self.assertLess(clock.now, 2)
        # A negative native exit remains refusal even inside the extended grace.
        close, owner, _, _, _, _, _ = self.fixture(1.2, exit_code=1)
        with self.assertRaises(Refused):
            close(owner)
        self.assertFalse(owner.stopped)

    def test_grace_deadline_forces_and_refuses_with_reap_time_reserved(self):
        for exit_at, work_deadline, latest_grace in ((1.9, 50.0, 1.76), (0.8, 0.2, 0.21)):
            with self.subTest(work_deadline=work_deadline):
                close, owner, child, clock, sent, closed, diagnostics = self.fixture(
                    exit_at, work_deadline=work_deadline)
                with self.assertRaises(Refused):
                    close(owner)
                self.assertTrue(owner.forced)
                self.assertFalse(owner.stopped)
                self.assertEqual(child.code, -9)
                self.assertEqual([kind for kind, _ in sent], [signal.SIGTERM, signal.SIGKILL])
                self.assertLessEqual(sent[-1][1], latest_grace)
                self.assertGreaterEqual(child.wait_timeout, 0.24)
                self.assertLessEqual(clock.now, 2)
                self.assertEqual(len(diagnostics), 1)
                self.assertEqual(sorted(closed), ["stderr", "stdout"])


if __name__ == "__main__":
    unittest.main()
