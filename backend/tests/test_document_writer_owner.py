"""Isolated owner controls; no application imports or provider operations.

Default tests use selected source and fake native observations. The three real
stdlib fixtures require --linux-controls in a disposable Linux container whose
outer lifetime contains even a refused/failed helper. No container is created by
this test program, and the default run never forks or enables a subreaper.
"""
from __future__ import annotations

import ast
import ctypes
import json
import math
import os
import signal
import subprocess
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
DOCUMENT = ROOT / "infra" / "document-processing"
OWNER_PATH = DOCUMENT / "writer_owner.py"
RENDERER_PATH = DOCUMENT / "docx_renderer.py"
OWNER_SOURCE = OWNER_PATH.read_text()
OWNER = types.ModuleType("isolated_writer_owner")
exec(compile(OWNER_SOURCE, "writer_owner.py", "exec"), OWNER.__dict__)
LINUX_CONTROLS = "--linux-controls" in sys.argv
if LINUX_CONTROLS:
    sys.argv.remove("--linux-controls")


def facts():
    return {"v": 1, "code": None, "supported": 1, "adopted": 0, "escaped": 0,
            "reaped": 0, "cleanup": 0, "restored": 0, "reason": 0}


class InertOwnerChecks(unittest.TestCase):
    def test_children_are_fixed_bounded_own_thread_only(self):
        with patch.object(OWNER.os, "open", return_value=7) as opened, \
                patch.object(OWNER.os, "read", return_value=b"101 202 "), \
                patch.object(OWNER.os, "close"), patch.object(OWNER.os, "getpid", return_value=9):
            self.assertEqual(OWNER._children(time.monotonic() + 1), {101, 202})
            self.assertEqual(opened.call_args.args[0], "/proc/thread-self/children")
        for raw in (b"101 101", b"0", b"+12", b"2147483648", b"a", b"1 " * 65, b"9"):
            with self.subTest(raw=raw), patch.object(OWNER.os, "open", return_value=7), \
                    patch.object(OWNER.os, "read", return_value=raw), \
                    patch.object(OWNER.os, "close"), patch.object(OWNER.os, "getpid", return_value=9):
                with self.assertRaises(OWNER.OwnerRefused):
                    OWNER._children(time.monotonic() + 1)

    def test_custody_loss_never_authorizes_a_signal(self):
        state = facts()
        calls = []
        def terminal(pid):
            if pid == 202:
                raise ChildProcessError()
            return 81
        with patch.object(OWNER, "_terminal", side_effect=terminal), \
                patch.object(OWNER, "_children", return_value={101, 202}), \
                patch.object(OWNER.os, "getpgid", return_value=101), \
                patch.object(OWNER.os, "killpg", side_effect=lambda pid, sig: calls.append((pid, sig))), \
                patch.object(OWNER.os, "kill") as killed, \
                patch.object(OWNER, "_reap"):
            with self.assertRaises(ChildProcessError):
                OWNER._drain(101, group_ready=True, deadline=time.monotonic() + 1,
                             reaped=set(), facts=state)
            killed.assert_not_called()
            self.assertEqual(calls, [(101, signal.SIGKILL)])
            self.assertEqual(state["cleanup"], 0)

    def test_cleanup_reaps_adopted_escape_without_signaling_reaped_ids(self):
        state, events, reaped = facts(), [], set()
        rows = iter(({101, 202}, {202}, set()))
        def terminal(pid):
            if pid == 101:
                return 81
            return -9 if any(item == ("signal", 202) for item in events) else None
        def reap(pid, code, owned):
            self.assertNotIn(pid, owned)
            events.append(("reap", pid))
            owned.add(pid)
        def kill(pid, _):
            self.assertNotIn(pid, reaped)
            events.append(("signal", pid))
        with patch.object(OWNER, "_terminal", side_effect=terminal), \
                patch.object(OWNER, "_children", side_effect=lambda _: next(rows)), \
                patch.object(OWNER.os, "getpgid", side_effect=lambda pid: pid), \
                patch.object(OWNER.os, "killpg", side_effect=lambda pid, _: events.append(("group", pid))), \
                patch.object(OWNER.os, "kill", side_effect=kill), \
                patch.object(OWNER, "_reap", side_effect=reap):
            OWNER._drain(101, group_ready=True, deadline=time.monotonic() + 1,
                         reaped=reaped, facts=state)
        self.assertEqual(events, [("group", 101), ("reap", 101), ("signal", 202), ("reap", 202)])
        self.assertEqual((state["adopted"], state["escaped"], state["reaped"], state["cleanup"]), (1, 1, 2, 1))

    def test_first_actual_reap_releases_anchor_even_if_validation_fails(self):
        reaped = set()
        with patch.object(OWNER, "_terminal", return_value=0), \
                patch.object(OWNER.os, "waitpid", return_value=(101, 256)), \
                patch.object(OWNER.os, "waitstatus_to_exitcode", return_value=1):
            with self.assertRaises(OWNER.OwnerRefused):
                OWNER._reap(101, 0, reaped)
        self.assertEqual(reaped, {101})
        with patch.object(OWNER.os, "killpg") as group, patch.object(OWNER.os, "kill") as killed:
            with self.assertRaises(OWNER.OwnerRefused):
                OWNER._drain(101, group_ready=True, deadline=time.monotonic() + 1,
                             reaped=reaped, facts=facts())
            group.assert_not_called()
            killed.assert_not_called()

    def test_missing_enumeration_refuses_before_fork(self):
        with patch.object(OWNER.sys, "platform", "linux"), \
                patch.object(OWNER, "_children", side_effect=PermissionError()), \
                patch.object(OWNER, "_subreaper") as subreaper, \
                patch.object(OWNER.os, "fork") as fork:
            code, result = OWNER.run([OWNER.ENGINE, "--version"], deadline=time.monotonic() + 5)
            self.assertEqual(code, 2)
            self.assertEqual((result["cleanup"], result["supported"]), (0, 0))
            subreaper.assert_not_called()
            fork.assert_not_called()

    def test_cleanup_reserve_is_inside_same_deadline(self):
        with patch.object(OWNER.sys, "platform", "linux"), patch.object(OWNER.os, "fork") as fork:
            for seconds in (0.1, 1.9):
                code, result = OWNER.run([OWNER.ENGINE, "--version"], deadline=time.monotonic() + seconds)
                self.assertEqual((code, result["cleanup"]), (2, 0))
            fork.assert_not_called()
        with patch.object(OWNER.os, "killpg") as killed:
            with self.assertRaises(OWNER.OwnerRefused):
                OWNER._drain(101, group_ready=True, deadline=time.monotonic() - 1,
                             reaped=set(), facts=facts())
            killed.assert_not_called()

    def test_prctl_numeric_abi_and_self_restore(self):
        observed = []
        current = 0
        def prctl(option, argument, *padding):
            nonlocal current
            self.assertIsInstance(option, ctypes.c_int)
            self.assertTrue(all(isinstance(value, ctypes.c_ulong) and value.value == 0 for value in padding))
            observed.append(option.value)
            if option.value == 36:
                self.assertIsInstance(argument, ctypes.c_ulong)
                current = argument.value
            else:
                argument._obj.value = current
            return 0
        function = type("Prctl", (), {"__call__": lambda self, *args: prctl(*args)})()
        with patch.object(OWNER.ctypes, "CDLL", return_value=types.SimpleNamespace(prctl=function)):
            call, previous = OWNER._subreaper()
            self.assertEqual((previous, current), (0, 1))
            OWNER._restore(call, previous)
        self.assertEqual((current, observed), (0, [37, 36, 36, 37]))

    def test_fixed_engine_signal_restore_guard(self):
        tree = ast.parse(OWNER_SOURCE)
        call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute) and node.func.attr == "execv")
        self.assertEqual(ast.unparse(call.args[0]), "ENGINE")
        self.assertIn('("SIGPIPE", "SIGXFZ", "SIGXFSZ")', OWNER_SOURCE)
        self.assertIn("signal.signal(getattr(signal, name), signal.SIG_DFL)", OWNER_SOURCE)
        self.assertNotIn("waitpid(-1", OWNER_SOURCE)
        self.assertNotIn("P_ALL", OWNER_SOURCE)
        self.assertNotIn("signal.signal(signal.SIGCHLD", OWNER_SOURCE)

    def test_renderer_preserves_one_81_gate_and_absolute_deadline(self):
        class Refusal(Exception):
            def __init__(self, code=81, outcome="unexpected_exit"):
                self.authorized, self.code, self.outcome = True, code, outcome
        failure = types.SimpleNamespace(is_set=lambda: False)
        calls = []
        def owned(arguments, **kwargs):
            calls.append((arguments, kwargs))
            if len(calls) == 1:
                raise Refusal()
            return 0, b"result"
        service = types.SimpleNamespace(_CREATION_FAILED=failure, _CREATION_PENDING=failure,
                                        _LauncherRefused=Refusal, _run=owned)
        function = next(node for node in ast.parse(RENDERER_PATH.read_text()).body
                        if isinstance(node, ast.FunctionDef) and node.name == "_run")
        namespace = {"math": math, "time": time, "sys": sys,
                     "WRITER_OWNER": "/opt/document/writer_owner.py", "RendererUnavailable": ValueError}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "renderer-selection", "exec"), namespace)
        deadline = time.monotonic() + 5
        command = [OWNER.ENGINE, "--headless", "--version"]
        with patch.dict(sys.modules, {"service": service}):
            self.assertEqual(namespace["_run"](command, deadline=deadline, restart_once=True), b"result")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], calls[1][0])
        self.assertEqual(calls[0][0][-len(command):], command)
        self.assertEqual(calls[0][0][4], format(deadline, ".9f"))
        self.assertLessEqual(calls[1][1]["timeout"], calls[0][1]["timeout"])
        for outcome in ("reap_failed", "deadline"):
            service._run = lambda *_, outcome=outcome, **__: (_ for _ in ()).throw(Refusal(outcome=outcome))
            with patch.dict(sys.modules, {"service": service}), self.assertRaises(ValueError):
                namespace["_run"](command, deadline=deadline, restart_once=True)


@unittest.skipUnless(LINUX_CONTROLS and sys.platform == "linux", "explicit disposable Linux controls required")
class LinuxStdlibChecks(unittest.TestCase):
    def test_owned_exit_zero_81_and_adopted_session_escape(self):
        engines = (
            "import os; os._exit(0)",
            "import os; os._exit(81)",
            "import os,time; r,w=os.pipe(); pid=os.fork(); "
            "exec('if pid == 0:\n os.close(r)\n os.setsid()\n os.write(w,b\"\\x01\")\n"
            " os.close(w)\n time.sleep(20)\n os._exit(42)\n"
            "else:\n os.close(w)\n assert os.read(r,1)==b\"\\x01\"\n os.close(r)\n os._exit(81)')",
        )
        for index, engine in enumerate(engines):
            with self.subTest(index=index):
                program = ("import json,sys,time; from pathlib import Path; "
                           "ns={'__name__':'fixture_helper'}; "
                           "exec(compile(Path(sys.argv[1]).read_text(),'fixed-owner','exec'),ns); "
                           "ns['ENGINE']=sys.executable; "
                           "code,facts=ns['run']([sys.executable,'-I','-c',sys.argv[2]],deadline=time.monotonic()+6); "
                           "print(json.dumps({'result':code,'facts':facts},sort_keys=True,separators=(',',':')))")
                child = subprocess.Popen([sys.executable, "-I", "-B", "-c", program,
                                          str(OWNER_PATH), engine],
                                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         start_new_session=True)
                try:
                    stdout, _ = child.communicate(timeout=8)
                except subprocess.TimeoutExpired:
                    # Signal only an unreaped owned anchor, then perform first
                    # actual reap. Outer disposable-container lifetime remains
                    # the backstop for an intentionally refused helper.
                    if child.returncode is None:
                        os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                        os.killpg(child.pid, signal.SIGKILL)
                    child.communicate(timeout=2)
                    raise
                self.assertEqual(child.returncode, 0)
                self.assertLessEqual(len(stdout), 512)
                result = json.loads(stdout)
                self.assertEqual(result["result"], 0 if index == 0 else 81)
                observed = result["facts"]
                self.assertEqual((observed["cleanup"], observed["restored"], observed["reason"]), (1, 1, 0))
                self.assertEqual(observed["reaped"], 1 if index < 2 else 2)
                self.assertEqual((observed["adopted"], observed["escaped"]), (0, 0) if index < 2 else (1, 1))


if __name__ == "__main__":
    unittest.main()
