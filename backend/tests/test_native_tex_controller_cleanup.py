"""Live-controller stop/custody failure boundaries; native proof is separate."""
from __future__ import annotations

import contextlib
import errno
import hashlib
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
NAME = "document-" + "a" * 32
DO = ["/usr/local/gcp/bin/sandbox", "do", "--sandbox-name=" + NAME,
      "--", "/usr/bin/python3", "-I", "/processor.py"]
DELETE = [DO[0], "delete", NAME, "--force"]


@pytest.fixture
def worker(monkeypatch):
    spec = importlib.util.spec_from_file_location("tex_controller_test", ROOT / "infra/native-tex/cloud_service.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    policy = ROOT / "infra/native-tex/policy.json"
    monkeypatch.setattr(module, "POLICY_PATH", policy)
    monkeypatch.setenv("NATIVE_TEX_IMAGE_DIGEST", "synthetic/native-tex@sha256:" + "1" * 64)
    monkeypatch.setenv("NATIVE_TEX_ISOLATION_POLICY_SHA256", hashlib.sha256(policy.read_bytes()).hexdigest())
    return module


def controller(worker, *, stop_unknown=False, reaped=True, authorized=True,
               foreign=False, released=False):
    events = []

    class Refused(Exception):
        def __init__(self, authorized, *, outcome="failed"):
            self.authorized, self.outcome = authorized, outcome

    module = SimpleNamespace(SANDBOX=DO[0], _BOOTSTRAP="trusted bootstrap", _LauncherRefused=Refused,
                             _absolute_deadline=lambda _seconds: contextlib.nullcontext())

    def child(arguments):
        args = list(arguments)
        if foreign and args[1] == "do":
            args[2] = "--sandbox-name=document-" + "b" * 32
        return SimpleNamespace(pid=42, returncode=0 if released else None,
            args=[sys.executable, "-I", "-c", module._BOOTSTRAP, "17", *args])

    def cleanup(process, **kwargs):
        kind = process.args[6]
        events.append(("reap", kind, kwargs))
        return reaped if kind == "do" else True

    def raw_run(arguments, **kwargs):
        if arguments[1] == "delete":
            events.append(("native_delete", list(arguments), kwargs))
            # A hung delete's own cleanup must not recursively delete the VM.
            module._cleanup_child(child(arguments), authorized=True,
                                  expected_code=None if stop_unknown else 0)
            if stop_unknown:
                raise Refused(True, outcome="deadline")
            return 0, b""
        events.append(("admitted", list(arguments), kwargs))
        confirmed = module._cleanup_child(child(arguments), authorized=authorized,
                                          expected_code=None)
        raise Refused(authorized, outcome="deadline" if confirmed else "reap_failed")

    module._run, module._cleanup_child = raw_run, cleanup
    worker._controller_cleanup_adapter(module)
    return module, events, Refused


def run_timeout(module, error):
    with pytest.raises(error):
        module._run(DO, timeout=2, limit=65536)


def finalize(module):
    return module._run(DELETE, timeout=5, limit=65536, cleanup=True)


def test_native_stop_precedes_parent_reap_and_success_receipt_is_consumed(worker):
    module, events, error = controller(worker)
    run_timeout(module, error)
    assert [row[0:2] for row in events if row[0] != "admitted"] == [
        ("native_delete", DELETE), ("reap", "delete"), ("reap", "do")]
    assert events[1][2] == {"timeout": 3, "limit": 65536}
    assert finalize(module) == (0, b"")
    assert len([row for row in events if row[0] == "native_delete"]) == 1
    # A new request uses a fresh name; a consumed receipt cannot affect it.
    fresh = list(DO)
    fresh[2] = "--sandbox-name=document-" + "c" * 32
    with pytest.raises(error):
        module._run(fresh, timeout=2, limit=65536)
    assert [row[1][2] for row in events if row[0] == "native_delete"] == [NAME, "document-" + "c" * 32]


@pytest.mark.parametrize("options", [
    {"stop_unknown": True}, {"reaped": False}, {"foreign": True}, {"released": True},
])
def test_unknown_stop_or_parent_custody_cannot_release_tmp_or_replay(worker, options):
    module, events, error = controller(worker, **options)
    run_timeout(module, error)
    calls = len([row for row in events if row[0] == "native_delete"])
    with pytest.raises(error):
        finalize(module)
    with pytest.raises(error):
        finalize(module)
    fresh = list(DO)
    fresh[2] = "--sandbox-name=document-" + "d" * 32
    with pytest.raises(error):
        module._run(fresh, timeout=2, limit=65536)
    assert len([row for row in events if row[0] == "native_delete"]) == calls
    assert calls == (0 if options.get("foreign") or options.get("released") else 1)


def test_unauthorized_late_constructor_never_stops_controller(worker):
    module, events, error = controller(worker, authorized=False)
    run_timeout(module, error)
    assert not any(row[0] == "native_delete" for row in events)
    assert events[-1][0:2] == ("reap", "do")


def test_cached_receipt_does_not_bypass_delete_argument_guards(worker):
    module, events, error = controller(worker)
    run_timeout(module, error)
    with pytest.raises(error):
        module._run(DELETE, timeout=55, limit=65536, cleanup=True)
    assert finalize(module) == (0, b"")
    assert len([row for row in events if row[0] == "native_delete"]) == 1


@pytest.mark.parametrize("unknown", [True, False])
def test_shipping_compile_retains_tmp_and_quarantines_unknown_cleanup(worker, monkeypatch, unknown):
    module, events, _ = controller(worker, stop_unknown=unknown)
    removed = []

    def remove(directory, identity):
        assert (directory.stat().st_dev, directory.stat().st_ino) == identity
        removed.append(directory)
        directory.rmdir()

    module._remove_request_tmp = remove
    monkeypatch.setattr(worker, "launcher", lambda: module)
    monkeypatch.setattr(worker.os.path, "isfile", lambda _path: True)
    monkeypatch.setattr(worker.os, "access", lambda *_args: True)
    expected = "compiler_cleanup_unverified" if unknown else "compiler_unavailable"
    with pytest.raises(RuntimeError, match=expected):
        worker.compile_payload(b"synthetic source")
    name = next(row[1][2].split("=", 1)[1] for row in events if row[0] == "admitted")
    directory = Path("/tmp") / name
    assert worker.QUARANTINED.is_set() is unknown
    assert directory.exists() is unknown
    assert len(removed) == (0 if unknown else 1)
    assert len([row for row in events if row[0] == "native_delete"]) == 1
    if unknown:
        with pytest.raises(RuntimeError, match="compiler_unavailable"):
            worker.compile_payload(b"different synthetic request")
        assert len([row for row in events if row[0] == "native_delete"]) == 1
        # Test-only disposal of the empty synthetic directory after assertions.
        directory.rmdir()


def test_tmp_removal_shares_absolute_cleanup_deadline_and_expiry_quarantines(worker, monkeypatch):
    module, events, _ = controller(worker)
    active = []

    @contextlib.contextmanager
    def deadline(seconds):
        active.append(seconds)
        try:
            yield
        finally:
            active.pop()

    class CleanupDeadline(BaseException):
        pass

    def remove(directory, identity):
        assert active == [7]
        assert (directory.stat().st_dev, directory.stat().st_ino) == identity
        raise CleanupDeadline()

    module._absolute_deadline, module._remove_request_tmp = deadline, remove
    monkeypatch.setattr(worker, "launcher", lambda: module)
    monkeypatch.setattr(worker.os.path, "isfile", lambda _path: True)
    monkeypatch.setattr(worker.os, "access", lambda *_args: True)
    with pytest.raises(RuntimeError, match="compiler_cleanup_unverified"):
        worker.compile_payload(b"synthetic source")
    assert worker.QUARANTINED.is_set()
    name = next(row[1][2].split("=", 1)[1] for row in events if row[0] == "admitted")
    directory = Path("/tmp") / name
    assert directory.is_dir()
    directory.rmdir()


def linux_wait_api(worker, monkeypatch):
    # The unit host may be macOS; real Linux custody is verified separately.
    monkeypatch.setattr(worker, "os", SimpleNamespace(getpid=worker.os.getpid,
        getuid=worker.os.getuid, waitid=None, waitpid=None, P_PID=1, WEXITED=4,
        WNOHANG=1, WNOWAIT=0x01000000, CLD_EXITED=1, CLD_KILLED=2, CLD_DUMPED=3))


def test_adopted_zombie_wait_keeps_parent_anchor_and_reaps_only_exact_native_child(worker, monkeypatch, tmp_path):
    linux_wait_api(worker, monkeypatch)
    calls = []
    child = tmp_path / "123"
    child.mkdir()
    (child / "stat").write_text(f"123 (synthetic) Z {worker.os.getpid()} 42 42 0")
    monkeypatch.setattr(worker, "Path", lambda _path: tmp_path)

    def observe(kind, pid, options):
        calls.append(("observe", pid, options))
        return SimpleNamespace(si_pid=pid, si_code=worker.os.CLD_KILLED, si_status=9)

    def wait(pid, options):
        calls.append(("reap", pid, options))
        return pid, 9

    monkeypatch.setattr(worker.os, "waitid", observe)
    monkeypatch.setattr(worker.os, "waitpid", wait)
    worker._reap_adopted_group(42, worker.time.monotonic() + 1)
    assert [row[:2] for row in calls] == [("observe", 42), ("observe", 123), ("reap", 123)]
    assert calls[0][2] & worker.os.WNOWAIT
    assert calls[-1][2] == worker.os.WNOHANG


def test_parent_terminal_deadline_refuses_without_signals_or_descendant_reap(worker, monkeypatch):
    linux_wait_api(worker, monkeypatch)
    calls = []
    clock = iter([0.0, 0.01, 0.1])
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(worker.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(worker.os, "waitid", lambda _kind, pid, _options: calls.append(pid))
    monkeypatch.setattr(worker.os, "waitpid", lambda *_args: pytest.fail("unobserved descendant must not be reaped"))
    with pytest.raises(RuntimeError, match="compiler_cleanup_unverified"):
        worker._reap_adopted_group(42, 0.05)
    assert calls == [42]


def test_unknown_adopted_native_wait_is_not_treated_as_absence(worker, monkeypatch, tmp_path):
    linux_wait_api(worker, monkeypatch)
    child = tmp_path / "123"
    child.mkdir()
    (child / "stat").write_text(f"123 (synthetic) Z {worker.os.getpid()} 42 42 0")
    monkeypatch.setattr(worker, "Path", lambda _path: tmp_path)

    def observe(_kind, pid, _options):
        if pid == 123:
            raise ChildProcessError(errno.ECHILD, "synthetic unknown custody")
        return SimpleNamespace(si_pid=pid, si_code=worker.os.CLD_EXITED, si_status=0)

    monkeypatch.setattr(worker.os, "waitid", observe)
    with pytest.raises(ChildProcessError):
        worker._reap_adopted_group(42, worker.time.monotonic() + 1)


@pytest.mark.parametrize("expected_code", [None, 0, 1])
def test_exact_authorized_do_parent_wait_reaps_adopted_group_before_shared_absence(worker, monkeypatch, expected_code):
    events = []
    module = SimpleNamespace(SANDBOX=DO[0], _BOOTSTRAP="trusted bootstrap", _LauncherRefused=RuntimeError)
    process = SimpleNamespace(pid=42, returncode=None,
        args=[sys.executable, "-I", "-c", module._BOOTSTRAP, "17", *DO])

    def wait(*, timeout):
        events.append(("direct_parent_wait", timeout))
        process.returncode = -9 if expected_code is None else expected_code
        return process.returncode

    process.wait = wait

    def cleanup(child, **_kwargs):
        if child is not process:
            return True
        child.wait(timeout=1.5)
        events.append(("original_group_absence",))
        return True

    def raw_run(arguments, **_kwargs):
        if arguments[1] == "delete":
            events.append(("delete",))
            return 0, b""
        return module._cleanup_child(process, authorized=True, expected_code=expected_code), b""

    module._run, module._cleanup_child = raw_run, cleanup
    monkeypatch.setattr(worker, "_reap_adopted_group",
        lambda group, deadline: events.append(("adopted_group_wait", group, deadline)))
    worker._controller_cleanup_adapter(module)
    assert module._run(DO, timeout=2, limit=65536) == (True, b"")
    assert [event[0] for event in events] == (
        ["delete"] if expected_code is None else []) + [
        "adopted_group_wait", "direct_parent_wait", "original_group_absence"]
    assert events[-3][1] == 42
    assert process.wait is wait


def test_failed_subreaper_initialization_cannot_publish_or_retry_raw_launcher(worker, monkeypatch):
    name = "hirewiz_tex_owned_launcher"
    monkeypatch.delitem(worker.sys.modules, name, raising=False)
    attempts = []

    def initialize():
        attempts.append(True)
        raise RuntimeError("synthetic prctl refusal")

    monkeypatch.setattr(worker, "_initialize_subreaper", initialize)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="compiler_unavailable"):
            worker.launcher()
        assert name not in worker.sys.modules
    assert attempts == [True] and worker.QUARANTINED.is_set()
