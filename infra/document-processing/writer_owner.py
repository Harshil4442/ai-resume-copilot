"""Own one fixed Writer engine and its adopted children in a private helper.

This process alone becomes a subreaper. It never changes the processor's signal
handling or takes another Popen owner's wait status. All work, including cleanup,
uses the caller's original absolute monotonic deadline.
"""
from __future__ import annotations

import ctypes
import json
import math
import os
import re
import selectors
import signal
import sys
import time

ENGINE = "/usr/lib/libreoffice/program/soffice.bin"
CHILDREN = "/proc/thread-self/children"
MAX_CHILDREN = 64
MAX_CHILD_BYTES = 1024
CLEANUP_RESERVE = 2.0


class OwnerRefused(ValueError):
    pass


def _remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if not math.isfinite(value) or value <= 0:
        raise OwnerRefused()
    return value


def _children(deadline: float) -> set[int]:
    _remaining(deadline)
    descriptor = os.open(CHILDREN, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        raw = os.read(descriptor, MAX_CHILD_BYTES + 1)
    finally:
        os.close(descriptor)
    _remaining(deadline)
    tokens = raw.split()
    if (len(raw) > MAX_CHILD_BYTES or len(tokens) > MAX_CHILDREN
            or any(re.fullmatch(rb"[1-9][0-9]{0,9}", token) is None for token in tokens)):
        raise OwnerRefused()
    result = {int(token) for token in tokens}
    if (len(result) != len(tokens) or any(pid > 2147483647 or pid == os.getpid() for pid in result)):
        raise OwnerRefused()
    return result


def _terminal(pid: int) -> int | None:
    # None confirms a running immediate child. ECHILD is a custody refusal,
    # never permission to signal a PID obtained only from a proc file.
    status = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    if status is None:
        return None
    if (type(status.si_pid) is not int or status.si_pid != pid
            or type(status.si_code) is not int or type(status.si_status) is not int):
        raise OwnerRefused()
    if status.si_code == os.CLD_EXITED and 0 <= status.si_status <= 255:
        return status.si_status
    if status.si_code in {os.CLD_KILLED, os.CLD_DUMPED} and 1 <= status.si_status <= 255:
        return -status.si_status
    raise OwnerRefused()


def _reap(pid: int, expected: int, reaped: set[int]) -> None:
    if pid in reaped or _terminal(pid) != expected:
        raise OwnerRefused()
    actual_pid, status = os.waitpid(pid, os.WNOHANG)
    if actual_pid == pid:
        # Mark the anchor released before any further validation can fail.
        reaped.add(pid)
    if actual_pid != pid or os.waitstatus_to_exitcode(status) != expected:
        raise OwnerRefused()


def _drain(engine: int, *, group_ready: bool, deadline: float,
           reaped: set[int], facts: dict[str, int | None]) -> None:
    _remaining(deadline)
    if engine in reaped:
        raise OwnerRefused()
    _terminal(engine)
    if group_ready:
        if os.getpgid(engine) != engine:
            raise OwnerRefused()
        _remaining(deadline)
        try:
            os.killpg(engine, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        _remaining(deadline)
        os.kill(engine, signal.SIGKILL)
    seen: set[int] = set()
    while True:
        _remaining(deadline)
        children = _children(deadline)
        if not children:
            if engine not in reaped:
                raise OwnerRefused()
            facts["cleanup"] = 1
            return
        if children & reaped or len(seen | children) > MAX_CHILDREN:
            raise OwnerRefused()
        for pid in sorted(children):
            _remaining(deadline)
            code = _terminal(pid)
            if pid not in seen and pid != engine:
                facts["adopted"] += 1
                if os.getpgid(pid) != engine:
                    facts["escaped"] += 1
            seen.add(pid)
            if code is None:
                # No competing waiter or SIGCHLD handler exists in this helper;
                # the native immediate-child anchor remains held until _reap.
                _remaining(deadline)
                os.kill(pid, signal.SIGKILL)
                continue
            _reap(pid, code, reaped)
            facts["reaped"] += 1
        time.sleep(min(0.005, _remaining(deadline)))


def _subreaper() -> tuple[object, int]:
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.restype = ctypes.c_int
    previous = ctypes.c_int(-1)
    if (prctl(ctypes.c_int(37), ctypes.byref(previous), ctypes.c_ulong(0),
              ctypes.c_ulong(0), ctypes.c_ulong(0)) != 0 or previous.value not in {0, 1}):
        raise OwnerRefused()
    if prctl(ctypes.c_int(36), ctypes.c_ulong(1), ctypes.c_ulong(0),
             ctypes.c_ulong(0), ctypes.c_ulong(0)) != 0:
        raise OwnerRefused()
    return prctl, previous.value


def _restore(prctl, value: int) -> None:
    current = ctypes.c_int(-1)
    if (prctl(ctypes.c_int(36), ctypes.c_ulong(value), ctypes.c_ulong(0),
              ctypes.c_ulong(0), ctypes.c_ulong(0)) != 0
            or prctl(ctypes.c_int(37), ctypes.byref(current), ctypes.c_ulong(0),
                     ctypes.c_ulong(0), ctypes.c_ulong(0)) != 0 or current.value != value):
        raise OwnerRefused()


def run(arguments: list[str], *, deadline: float) -> tuple[int, dict[str, int | None]]:
    facts: dict[str, int | None] = {
        "v": 1, "code": None, "supported": 0, "adopted": 0, "escaped": 0,
        "reaped": 0, "cleanup": 0, "restored": 0, "reason": 1,
    }
    engine = None
    prctl = None
    previous = 0
    descriptors: list[int] = []
    group_ready = False
    reaped: set[int] = set()
    code = None
    try:
        if (sys.platform != "linux" or signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL
                or type(deadline) is not float or not math.isfinite(deadline)
                or not CLEANUP_RESERVE < _remaining(deadline) <= 55
                or type(arguments) is not list or not arguments or arguments[0] != ENGINE
                or any(type(arg) is not str or "\x00" in arg for arg in arguments)
                or not all(hasattr(os, name) for name in (
                    "fork", "waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT",
                    "CLD_EXITED", "CLD_KILLED", "CLD_DUMPED", "waitstatus_to_exitcode"))):
            raise OwnerRefused()
        if _children(deadline):
            raise OwnerRefused()
        prctl, previous = _subreaper()
        current = ctypes.c_int(-1)
        if (prctl(ctypes.c_int(37), ctypes.byref(current), ctypes.c_ulong(0),
                  ctypes.c_ulong(0), ctypes.c_ulong(0)) != 0 or current.value != 1):
            raise OwnerRefused()
        facts["supported"] = 1
        admitted_read, admitted_write = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        descriptors.extend((admitted_read, admitted_write))
        ready_read, ready_write = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        descriptors.extend((ready_read, ready_write))
        execution_deadline = deadline - CLEANUP_RESERVE
        _remaining(execution_deadline)
        engine = os.fork()
        if engine == 0:
            try:
                os.close(admitted_write)
                os.close(ready_read)
                os.setsid()
                if os.write(ready_write, b"\x01") != 1:
                    os._exit(125)
                os.close(ready_write)
                with selectors.DefaultSelector() as selector:
                    selector.register(admitted_read, selectors.EVENT_READ)
                    if (not selector.select(_remaining(execution_deadline))
                            or os.read(admitted_read, 2) != b"\x01"):
                        os._exit(125)
                os.close(admitted_read)
                # Match Popen's existing restore_signals=True exec semantics.
                for name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ"):
                    if hasattr(signal, name):
                        signal.signal(getattr(signal, name), signal.SIG_DFL)
                os.execv(ENGINE, arguments)
            except BaseException:
                os._exit(125)
        os.close(admitted_read)
        descriptors.remove(admitted_read)
        os.close(ready_write)
        descriptors.remove(ready_write)
        with selectors.DefaultSelector() as selector:
            selector.register(ready_read, selectors.EVENT_READ)
            if (not selector.select(_remaining(execution_deadline))
                    or os.read(ready_read, 2) != b"\x01"):
                raise OwnerRefused()
        os.close(ready_read)
        descriptors.remove(ready_read)
        _terminal(engine)
        if os.getpgid(engine) != engine or engine not in _children(deadline):
            raise OwnerRefused()
        group_ready = True
        _remaining(execution_deadline)
        if os.write(admitted_write, b"\x01") != 1:
            raise OwnerRefused()
        os.close(admitted_write)
        descriptors.remove(admitted_write)
        while True:
            _remaining(execution_deadline)
            code = _terminal(engine)
            if code is not None:
                facts["code"] = code
                break
            time.sleep(min(0.005, _remaining(execution_deadline)))
        facts["reason"] = 0 if code in {0, 81} else 2
    except BaseException:
        facts["reason"] = 1
    finally:
        for descriptor in descriptors:
            try:
                os.close(descriptor)
            except OSError:
                facts["reason"] = 1
        if engine is not None and engine > 0:
            try:
                _drain(engine, group_ready=group_ready, deadline=deadline, reaped=reaped, facts=facts)
            except BaseException:
                facts["reason"] = 3
        if prctl is not None:
            try:
                _remaining(deadline)
                _restore(prctl, previous)
                facts["restored"] = 1
            except BaseException:
                facts["reason"] = 4
    if type(deadline) is not float or not math.isfinite(deadline) or time.monotonic() >= deadline:
        facts["reason"] = 1
    if (type(code) is not int or code not in {0, 81}
            or facts["reason"] != 0 or facts["supported"] != 1
            or facts["cleanup"] != 1 or facts["restored"] != 1):
        return 2, facts
    return code, facts


def main() -> None:
    if (len(sys.argv) < 5 or sys.argv[1] != "--deadline" or sys.argv[3] != "--"
            or re.fullmatch(r"[0-9]{1,12}\.[0-9]{1,9}", sys.argv[2]) is None):
        raise SystemExit(2)
    code, facts = run(sys.argv[4:], deadline=float(sys.argv[2]))
    # Fixed scalar-only telemetry: never argv, PID, path, input or error text.
    record = "HWWOWNER1 " + json.dumps(facts, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    if len(record.encode("ascii")) <= 512:
        os.write(2, record.encode("ascii"))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
