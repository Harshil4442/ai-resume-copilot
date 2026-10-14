"""Standalone service ownership regressions; no app, SDK, scanner or cloud import."""

from __future__ import annotations

import contextlib
import importlib.util
import os
import subprocess
import sys
import threading
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def service():
    path = REPO / "infra" / "document-processing" / "service.py"
    spec = importlib.util.spec_from_file_location("isolated_document_service", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Event:
    def __init__(self):
        self.flag = False

    def is_set(self):
        return self.flag

    def set(self):
        self.flag = True

    def clear(self):
        self.flag = False

    def wait(self, _):
        return self.flag


class Harness:
    def __init__(self, m, mode="exit", status=0, allow=False):
        self.m = m
        self.mode = mode
        self.status = status
        self.allow = allow
        self.events = []
        self.clock = 0.0
        self.reads = 0
        self.spawns = 0
        self.reaps = 0
        self.waitids = 0
        self.delayed = None
        harness = self

        class Stream:
            def fileno(self):
                return 10

            def close(self):
                harness.events.append("pipe_close")

        class Gate:
            def write(self, b):
                assert b == b"\x01"
                harness.events.append("authorize")
                return 1

            def close(self):
                harness.events.append("gate_close")

        class Child:
            pid = 1234
            returncode = None
            stdin = None
            stdout = Stream()

            def wait(self, timeout):
                assert 0 <= timeout <= 2 and self.returncode is None
                harness.reaps += 1
                harness.events.append("reap")
                if harness.mode == "reap_timeout":
                    raise subprocess.TimeoutExpired("synthetic", timeout)
                self.returncode = (
                    1
                    if harness.mode == "actual_mismatch"
                    else harness.status
                    if harness.mode != "pending"
                    else -9
                )
                return self.returncode

        self.child = Child()

        def spawn(*args, **kwargs):
            harness.spawns += 1
            harness.events.append("spawn")
            assert kwargs["start_new_session"] is True and kwargs["env"] == {
                "PATH": "/usr/bin:/bin",
                "HOME": "/nonexistent",
                "LANG": "C",
                "TZ": "UTC",
            }
            if harness.mode == "constructor_unknown":
                raise OSError("PRIVATE_VENDOR_ERROR_NOT_SURFACED")
            return harness.child

        def killpg(pid, sig):
            assert pid == 1234
            if sig:
                # This assertion is the old-source regression: signal must precede every reap.
                assert harness.reaps == 0 and harness.child.returncode is None
                harness.events.append("signal")
                if harness.mode.startswith("eperm"):
                    raise PermissionError(1, "private_not_exported")
                if harness.mode == "signal_error":
                    raise OSError(5, "private_not_exported")
            else:
                assert harness.reaps == 1
                harness.events.append("absence_read")
                if harness.mode in {"present", "eperm_present"}:
                    return
                if harness.mode == "eperm_unknown":
                    raise PermissionError(1, "private_not_exported")
                raise ProcessLookupError(3, "private_not_exported")

        def waitid(which, pid, flags):
            assert (
                which == 1
                and pid == 1234
                and flags == 14
                and harness.reaps == 0
                and harness.child.returncode is None
            )
            harness.events.append("observe_unreaped")
            harness.waitids += 1
            if harness.mode == "pending":
                return None
            if harness.mode == "waitid_error":
                raise OSError(5, "private_not_exported")
            if harness.mode == "wrong_pid":
                return types.SimpleNamespace(si_pid=999, si_code=5, si_status=0)
            if harness.mode == "bad_status":
                return types.SimpleNamespace(si_pid=1234, si_code=5, si_status=True)
            return types.SimpleNamespace(
                si_pid=1234,
                si_code=5 if harness.status >= 0 else 6,
                si_status=abs(harness.status),
            )

        class Thread:
            def __init__(self, *, target, daemon):
                self.target = target
                assert daemon

            def start(self):
                if harness.mode == "late_constructor":
                    harness.delayed = self.target
                else:
                    self.target()

        class Selector:
            def __enter__(self):
                self.items = {}
                return self

            def __exit__(self, *_):
                pass

            def register(self, stream, event, data):
                self.items[10] = types.SimpleNamespace(fd=10, data=data)

            def unregister(self, _):
                self.items = {}

            def get_map(self):
                return self.items

            def select(self, _):
                return [(self.items[10], 1)]

        def read(fd, size):
            assert fd == 10
            harness.reads += 1
            if harness.mode == "overflow":
                return b"x" * size
            return b"ok" if harness.reads == 1 else b""

        def monotonic():
            harness.clock += 0.0001
            return harness.clock

        def sleep(seconds):
            harness.clock += seconds

        @contextlib.contextmanager
        def deadline(seconds):
            yield monotonic() + seconds

        m.os = types.SimpleNamespace(
            pipe=lambda: (20, 21),
            set_blocking=lambda *_: None,
            fdopen=lambda *_, **__: Gate(),
            close=lambda *_: None,
            read=read,
            killpg=killpg,
            waitid=waitid,
            P_PID=1,
            WEXITED=2,
            WNOHANG=4,
            WNOWAIT=8,
            CLD_EXITED=5,
            CLD_KILLED=6,
            CLD_DUMPED=7,
        )
        m.time = types.SimpleNamespace(monotonic=monotonic, sleep=sleep)
        m.subprocess = types.SimpleNamespace(
            Popen=spawn, DEVNULL=-3, PIPE=-1, TimeoutExpired=subprocess.TimeoutExpired
        )
        m.threading = types.SimpleNamespace(
            Lock=threading.Lock, Event=Event, Thread=Thread
        )
        m.selectors = types.SimpleNamespace(
            DefaultSelector=Selector, EVENT_READ=1, EVENT_WRITE=2
        )
        m._CREATION_PENDING = Event()
        m._CREATION_FAILED = Event()
        m._absolute_deadline = deadline

    def run(self):
        return self.m._run(
            ["fixed-public-child"], timeout=0.04, limit=8, allow_outcome=self.allow
        )


class Controls(unittest.TestCase):
    def test_positive_terminal0_observed_then_signal_then_only_reap_and_native_absence(
        self,
    ):
        m = service()
        h = Harness(m)
        self.assertEqual(h.run(), (0, b"ok"))
        self.assertIn("observe_unreaped", h.events)
        self.assertLess(h.events.index("observe_unreaped"), h.events.index("signal"))
        self.assertLess(h.events.index("signal"), h.events.index("reap"))
        self.assertLess(h.events.index("reap"), h.events.index("absence_read"))
        self.assertEqual(h.reaps, 1)
        self.assertFalse(m._CREATION_FAILED.is_set())
        self.assertFalse(m._CREATION_PENDING.is_set())

    def test_existing_allowed1_and2_semantics(self):
        for status in (1, 2):
            with self.subTest(status=status):
                m = service()
                h = Harness(m, status=status, allow=True)
                self.assertEqual(h.run(), (status, b"ok"))
                self.assertEqual(h.reaps, 1)

    def test_normal81_and_signal_refusals_preserve_codes_after_confirmed_cleanup(self):
        for status in (81, -9, -15):
            with self.subTest(status=status):
                m = service()
                h = Harness(m, status=status)
                with self.assertRaises(m._LauncherRefused) as caught:
                    h.run()
                self.assertEqual(caught.exception.code, status)
                self.assertEqual(caught.exception.outcome, "unexpected_exit")
                self.assertTrue(caught.exception.authorized)
                self.assertEqual(h.reaps, 1)
                self.assertIn("absence_read", h.events)
                self.assertFalse(m._CREATION_FAILED.is_set())

    def test_exit0_is_not_accepted_if_actual_reap_disagrees_or_group_is_not_absent(
        self,
    ):
        for mode in (
            "actual_mismatch",
            "present",
            "eperm_present",
            "eperm_unknown",
            "signal_error",
            "reap_timeout",
        ):
            with self.subTest(mode=mode):
                m = service()
                h = Harness(m, mode)
                with self.assertRaises(m._LauncherRefused) as caught:
                    h.run()
                self.assertEqual(caught.exception.outcome, "reap_failed")
                self.assertTrue(m._CREATION_FAILED.is_set())
                self.assertEqual(h.reaps, 1)
                old_spawns = h.spawns
                with self.assertRaises(m._LauncherRefused):
                    h.run()
                self.assertEqual(h.spawns, old_spawns)

    def test_eperm_is_unconfirmed_until_actual_postreap_ESRCH(self):
        m = service()
        h = Harness(m, "eperm_absent")
        self.assertEqual(h.run(), (0, b"ok"))
        self.assertEqual(h.events.count("signal"), 1)
        self.assertEqual(h.events.count("reap"), 1)
        self.assertEqual(h.events.count("absence_read"), 1)

    def test_deadline_overflow_and_waitid_failure_all_cleanup_without_signal_after_reap(
        self,
    ):
        for mode in ("pending", "overflow", "waitid_error", "wrong_pid", "bad_status"):
            with self.subTest(mode=mode):
                m = service()
                h = Harness(m, mode)
                with self.assertRaises(m._LauncherRefused) as caught:
                    h.run()
                self.assertTrue(caught.exception.authorized)
                self.assertEqual(h.reaps, 1)
                self.assertLess(h.events.index("signal"), h.events.index("reap"))
                self.assertIn("absence_read", h.events)
                if mode == "pending":
                    self.assertEqual(caught.exception.outcome, "deadline")

    def test_already_reaped_child_refuses_without_nonzero_signal(self):
        m = service()
        h = Harness(m)
        h.child.returncode = 0
        self.assertFalse(m._cleanup_child(h.child))
        self.assertNotIn("signal", h.events)
        self.assertEqual(h.reaps, 0)

    def test_pre_authorization_constructor_unknown_stays_quarantined(self):
        m = service()
        h = Harness(m, "constructor_unknown")
        with self.assertRaises(m._LauncherRefused) as caught:
            h.run()
        self.assertFalse(caught.exception.authorized)
        self.assertTrue(m._CREATION_FAILED.is_set())
        self.assertFalse(m._CREATION_PENDING.is_set())
        self.assertEqual(h.reaps, 0)
        self.assertNotIn("authorize", h.events)

    def test_pending_constructor_retains_cancellation_gate_and_quarantine(self):
        m = service()
        h = Harness(m, "late_constructor")
        with self.assertRaises(m._LauncherRefused) as caught:
            h.run()
        self.assertFalse(caught.exception.authorized)
        self.assertTrue(m._CREATION_PENDING.is_set())
        self.assertEqual(h.spawns, 0)
        with self.assertRaises(m._LauncherRefused):
            h.run()
        self.assertEqual(h.spawns, 0)
        h.delayed()
        self.assertFalse(m._CREATION_PENDING.is_set())
        self.assertFalse(m._CREATION_FAILED.is_set())
        self.assertNotIn("authorize", h.events)

    def test_missing_nonreaping_capability_refuses_before_creation_or_gate(self):
        m = service()
        h = Harness(m)
        del m.os.WNOWAIT
        with self.assertRaises(m._LauncherRefused) as caught:
            h.run()
        self.assertFalse(caught.exception.authorized)
        self.assertEqual(h.spawns, 0)
        self.assertFalse(m._CREATION_PENDING.is_set())
        self.assertFalse(m._CREATION_FAILED.is_set())

    def test_unauthed_terminal125_requires_actual_group_absence(self):
        m = service()
        h = Harness(m, status=125)
        self.assertTrue(m._cleanup_child(h.child, authorized=False))
        self.assertEqual(h.reaps, 1)
        self.assertIn("absence_read", h.events)
        m = service()
        h = Harness(m, "present", status=125)
        self.assertFalse(m._cleanup_child(h.child, authorized=False))
        self.assertEqual(h.reaps, 1)

    @unittest.skipIf(
        sys.platform != "linux" and not all(hasattr(os, name) for name in (
            "waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT",
            "CLD_EXITED", "CLD_KILLED", "CLD_DUMPED")),
        "Host lacks native nonreaping wait; Linux CI must execute this control.",
    )
    def test_native_local_children_0_81_and_deadline_use_nonreaping_status_and_proved_absence(
        self,
    ):
        for label, program, budget, expected in (
            ("zero", 'import os; os.write(1,b"ok")', 2, 0),
            ("normal81", "import os; os._exit(81)", 2, 81),
            ("deadline", "import time; time.sleep(10)", 0.15, None),
        ):
            with self.subTest(label=label):
                m = service()
                children = []
                events = []

                def spawn(*args, children=children, **kwargs):
                    child = subprocess.Popen(*args, **kwargs)
                    children.append(child)
                    return child

                def killpg(pid, sig, children=children, events=events):
                    self.assertEqual(pid, children[0].pid)
                    if sig:
                        self.assertIsNone(children[0].returncode)
                        events.append("signal_before_reap")
                    else:
                        self.assertIsInstance(children[0].returncode, int)
                        events.append("native_absence_read_after_reap")
                    return os.killpg(pid, sig)

                def waitid(which, pid, flags, children=children, events=events):
                    self.assertEqual(flags, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                    self.assertIsNone(children[0].returncode)
                    result = os.waitid(which, pid, flags)
                    if result is not None:
                        events.append("terminal_observed_unreaped")
                    return result

                allowed = (
                    "P_PID",
                    "WEXITED",
                    "WNOHANG",
                    "WNOWAIT",
                    "CLD_EXITED",
                    "CLD_KILLED",
                    "CLD_DUMPED",
                    "pipe",
                    "set_blocking",
                    "fdopen",
                    "close",
                    "read",
                    "write",
                )
                m.os = types.SimpleNamespace(
                    **{name: getattr(os, name) for name in allowed},
                    killpg=killpg,
                    waitid=waitid,
                )
                m.subprocess = types.SimpleNamespace(
                    Popen=spawn,
                    DEVNULL=subprocess.DEVNULL,
                    PIPE=subprocess.PIPE,
                    TimeoutExpired=subprocess.TimeoutExpired,
                )
                argv = [sys.executable, "-I", "-c", program]
                if expected == 0:
                    self.assertEqual(
                        m._run(argv, timeout=budget, limit=1024), (0, b"ok")
                    )
                else:
                    with self.assertRaises(m._LauncherRefused) as caught:
                        m._run(argv, timeout=budget, limit=1024)
                    self.assertEqual(caught.exception.code, expected)
                    if expected == 81:
                        self.assertEqual(caught.exception.outcome, "unexpected_exit")
                    else:
                        self.assertIn(
                            caught.exception.outcome, {"deadline", "failed"}
                        )  # Existing remaining-budget guard may beat SIGALRM.
                self.assertEqual(events.count("signal_before_reap"), 1)
                self.assertEqual(events.count("native_absence_read_after_reap"), 1)
                if expected is not None:
                    self.assertIn("terminal_observed_unreaped", events)
                self.assertFalse(m._CREATION_PENDING.is_set())
                self.assertFalse(m._CREATION_FAILED.is_set())

    def test_unexpected_cleanup_exception_preserves_unknown_custody_quarantine(self):
        m = service()
        h = Harness(m)

        def unconfirmed(*args, **kwargs):
            raise KeyboardInterrupt()

        m._cleanup_child = unconfirmed
        with self.assertRaises(m._LauncherRefused) as caught:
            h.run()
        self.assertEqual(caught.exception.outcome, "reap_failed")
        self.assertTrue(m._CREATION_FAILED.is_set())
        self.assertEqual(h.reaps, 0)
        previous_spawns = h.spawns
        with self.assertRaises(m._LauncherRefused):
            h.run()
        self.assertEqual(h.spawns, previous_spawns)



if __name__ == "__main__":
    unittest.main(verbosity=2)
