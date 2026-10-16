"""Pure protocol/refusal checks; no PostgreSQL, provider or credential acquisition."""

from __future__ import annotations

import functools
import logging
import os
import struct
import subprocess
import sys
import time
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import database_transfer as transfer


def _wire(*rows):
    encoded = transfer.COPY_HEADER + struct.pack("!II", 0, 0)
    for row in rows:
        encoded += struct.pack("!h", len(row))
        for value in row:
            encoded += struct.pack("!i", -1 if value is None else len(value))
            if value is not None:
                encoded += value
    return encoded + struct.pack("!h", -1)


@pytest.mark.parametrize("width", [1, 2, 3, 7, 64])
def test_copy_binary_exact_rows_survive_arbitrary_short_chunks(width):
    wire = _wire((None, b"", b"\x00\xff"), (b"same",), (b"same",))
    rows = list(transfer._rows([wire[i:i + width] for i in range(0, len(wire), width)],
                              transfer.Limits(), time.monotonic() + 10))
    assert len(rows) == 3 and rows[1] == rows[2] and rows[0] != rows[1]
    assert rows[0] == struct.pack("!hiii", 3, -1, 0, 2) + b"\x00\xff"


@pytest.mark.parametrize("wire,reason", [
    (_wire((b"abc",))[:-1], "copy_incomplete"),
    (_wire((b"abc",)) + b"tail", "copy_after_trailer"),
    (transfer.COPY_HEADER + struct.pack("!II", 1, 0), "copy_header_refused"),
    (transfer.COPY_HEADER + struct.pack("!IIh", 0, 0, -2), "copy_column_bound"),
    (transfer.COPY_HEADER + struct.pack("!IIhi", 0, 0, 1, -2), "copy_field_bound"),
])
def test_partial_malformed_or_trailing_binary_copy_is_refused(wire, reason):
    with pytest.raises(transfer.TransferDenied, match="^" + reason + "$"):
        list(transfer._rows([wire], transfer.Limits(), time.monotonic() + 10))


def test_global_content_budget_does_not_reset_for_the_next_relation():
    wire = _wire((b"example",))
    budget = transfer._ContentBudget(len(wire) * 2, 1)
    assert len(list(transfer._rows([wire], transfer.Limits(), time.monotonic() + 10, budget))) == 1
    with pytest.raises(transfer.TransferDenied, match="^aggregate_content_bound$"):
        list(transfer._rows([wire], transfer.Limits(), time.monotonic() + 10, budget))


def _access(database_oid):
    return transfer.BoundDatabase(transfer.NativeDatabaseIdentity(
        "127.0.0.1", 55433, "hw_transfer_" + "a" * 32 + "_source",
        "b" * 64, database_oid, "hirewiz", 10, "", "disposable-local",
    ), "synthetic-not-provider")


def test_alias_final_authority_and_once_marker_refuse_before_any_connection(tmp_path, monkeypatch):
    def forbidden(_self):
        raise AssertionError("Native connection was forbidden")
    monkeypatch.setattr(transfer.BoundDatabase, "connect", forbidden)
    workdir = tmp_path / "attempt"
    workdir.mkdir(mode=0o700)
    tools = transfer.PostgresTools(Path("/unused/dump"), Path("/unused/restore"), "c" * 64, "d" * 64)
    source, target = _access(100), _access(101)
    with pytest.raises(transfer.TransferDenied, match="^source_destination_alias$"):
        transfer.transfer(source, source, tools, workdir)
    with pytest.raises(transfer.TransferDenied, match="^final_native_cut_required$"):
        transfer.transfer(source, target, tools, workdir, purpose="final")
    (workdir / "transfer.started").touch(mode=0o600)
    with pytest.raises(transfer.TransferDenied, match="^attempt_already_started_reconcile_before_retry$"):
        transfer.transfer(source, target, tools, workdir)


def test_ambient_redirect_settings_are_refused_without_reading_a_secret(monkeypatch):
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.10")
    with pytest.raises(transfer.TransferDenied, match="^ambient_libpq_refused$"):
        _access(100).environment(readonly=True)


@pytest.mark.parametrize("age,offset", [(31, 0), (-1, 0), (0, 1)])
def test_cut_cannot_be_stale_future_or_changed(age, offset):
    stable = datetime.now(UTC) - timedelta(minutes=10)
    class Boundary:
        def verify_native_cut(self, _source, _target):
            return transfer.NativeCutObservation(datetime.now(UTC) - timedelta(seconds=age),
                                                 stable + timedelta(seconds=offset))
    with pytest.raises(transfer.TransferDenied, match="^native_writer_cut_changed_or_stale$"):
        transfer._cut(Boundary(), _access(100), _access(101), stable)


def test_limits_cannot_be_silently_expanded():
    with pytest.raises(transfer.TransferDenied, match="^transfer_limits_refused$"):
        transfer.Limits(total_seconds=1801).validate()
    with pytest.raises(transfer.TransferDenied, match="^transfer_limits_refused$"):
        transfer.Limits(rows=True).validate()


def _locale_catalog():
    return {
        "settings": ("UTF8", "i", None, None, "und", None, "73.2", "73.2"),
        "collations": [("public", "candidate_text", -1, "i", False, None, None,
                        "und-u-ks-level2", None, "73.2", "73.2")],
        "index_collations": [("public", "users", "candidate_name_idx", 1,
                              "public", "candidate_text", -1)],
    }


@pytest.mark.parametrize("field,value", [
    ("database_rules", "[reorder Grek]"),
    ("database_version", "74.1"),
    ("determinism", True),
    ("collation_rules", "[reorder Latn]"),
    ("index_collation", "another_collation"),
])
def test_same_locale_names_do_not_mask_changed_unicode_ordering(field, value):
    source, target = _locale_catalog(), _locale_catalog()
    if field.startswith("database"):
        changed = list(target["settings"])
        if field == "database_rules":
            changed[5] = value
        else:
            changed[6] = changed[7] = value
        target["settings"] = tuple(changed)
    elif field == "index_collation":
        changed = list(target["index_collations"][0])
        changed[5] = value
        target["index_collations"] = [tuple(changed)]
    else:
        changed = list(target["collations"][0])
        changed[4 if field == "determinism" else 8] = value
        target["collations"] = [tuple(changed)]
    # The old encoding/LC names projection could falsely admit these cases.
    assert (source["settings"][0], *source["settings"][2:4]) == (target["settings"][0], *target["settings"][2:4])
    transfer._check_collation_versions(source["settings"], source["collations"])
    transfer._check_collation_versions(target["settings"], target["collations"])
    with pytest.raises(transfer.TransferDenied, match="^restored_collation_mismatch$"):
        transfer._matching_catalog(source, target)


def test_stale_recorded_collation_version_is_not_a_valid_copy_baseline():
    catalog = _locale_catalog()
    changed = list(catalog["settings"])
    changed[7] = "74.1"
    with pytest.raises(transfer.TransferDenied, match="^database_collation_version_stale_or_untracked$"):
        transfer._check_collation_versions(tuple(changed), catalog["collations"])
    changed = list(catalog["collations"][0])
    changed[10] = "74.1"
    with pytest.raises(transfer.TransferDenied, match="^referenced_collation_version_stale_or_untracked$"):
        transfer._check_collation_versions(catalog["settings"], [tuple(changed)])


def test_database_default_collation_uses_checked_database_provider_version():
    catalog = _locale_catalog()
    default = ("pg_catalog", "default", -1, "d", True, None, None, None, None, None, "73.2")
    transfer._check_collation_versions(catalog["settings"], [default])


def test_owned_transfer_refuses_unsupported_platform_before_any_child(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer.sys, "platform", "darwin")
    def forbidden():
        raise AssertionError("No child may launch on an unsupported platform")
    monkeypatch.setattr(transfer.os, "fork", forbidden)
    with pytest.raises(transfer.TransferDenied, match="^owned_transfer_linux_single_thread_and_cleanup_budget_required$"):
        transfer.owned_transfer(_access(100), _access(101), None, tmp_path)


def test_owned_transfer_cleanup_reserve_is_inside_the_existing_budget(tmp_path):
    with pytest.raises(transfer.TransferDenied, match="^owned_transfer_linux_single_thread_and_cleanup_budget_required$"):
        transfer.owned_transfer(_access(100), _access(101), None, tmp_path,
                                limits=transfer.Limits(total_seconds=2))


def _owned_directory(tmp_path):
    directory = tmp_path / "owned-attempt"
    directory.mkdir(mode=0o700)
    return directory


def _live_linux_process(pid):
    path = Path(f"/proc/{pid}/stat")
    try:
        # A killed orphan may await PID1's reap, but a zombie cannot keep doing
        # work. Do not equate this synthetic observation with native rollback.
        return path.read_text().rsplit(")", 1)[1].split()[0] not in {"Z", "X"}
    except FileNotFoundError:
        return False


def _assert_stopped(pid):
    end = time.monotonic() + 2
    while _live_linux_process(pid) and time.monotonic() < end:
        time.sleep(0.01)
    assert not _live_linux_process(pid)


def _hanging_command(directory, limits):
    import sys
    identity = directory / "descendant.identity"
    child = ("import os,time;from pathlib import Path;"
             f"Path({str(identity)!r}).write_text(str(os.getpid())+' '+str(os.getpgrp()));"
             "time.sleep(60)")
    launcher = ("import subprocess,sys,time;"
                f"subprocess.Popen([sys.executable,'-c',{child!r}]);time.sleep(60)")
    transfer._command([sys.executable, "-c", launcher], {}, time.monotonic() + 60,
                      limits)
    raise AssertionError("The blocking command must not finish")


_ISOLATED_LINUX_CASES = {
    "test_owned_transfer_hard_deadline_kills_a_real_hanging_command_and_descendant",
    "test_owned_transfer_caller_cancellation_stops_a_real_hanging_descendant",
    "test_owned_transfer_keeps_final_native_refusal_without_any_connection",
    "test_owned_transfer_once_marker_and_typed_terminal_never_expose_credentials",
}
_ISOLATED_CASE_ENV = "HIREWIZ_OWNED_TRANSFER_CHILD_CASE"


def _isolated_linux_case(case):
    # Only the four fixed meaningful process cases can enter the child mode.
    assert case.__name__ in _ISOLATED_LINUX_CASES
    @functools.wraps(case)
    def run(*args, **kwargs):
        marker = os.environ.get(_ISOLATED_CASE_ENV)
        if marker == case.__name__:
            return case(*args, **kwargs)
        assert marker is None, "Unexpected isolated child-case selection"
        filename = Path(__file__).resolve()
        environment = {
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC",
            "PYTHONPATH": str(filename.parents[1]),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            _ISOLATED_CASE_ENV: case.__name__,
        }
        command = [sys.executable, "-I", "-B", "-m", "pytest", "--noconftest",
                   "--confcutdir", str(filename.parent), "--import-mode=importlib",
                   "-q", str(filename) + "::" + case.__name__]
        process = subprocess.Popen(command, cwd=filename.parents[1], env=environment,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=15)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, transfer.signal.SIGKILL)
                process.wait(timeout=2)
        # Successful child output is irrelevant. Failures retain the actual
        # exit and bounded synthetic-only diagnostics; no caller credential env.
        assert process.returncode == 0, ("Isolated Linux case exited " + str(process.returncode)
                                         + "\n" + (stdout + stderr)[-8192:].decode("utf-8", "replace"))
    return run


@pytest.mark.skipif(transfer.sys.platform != "linux", reason="Actual owned process semantics require Linux")
@_isolated_linux_case
def test_owned_transfer_hard_deadline_kills_a_real_hanging_command_and_descendant(tmp_path, monkeypatch):
    directory = _owned_directory(tmp_path)
    def hang(_source, _target, _tools, workdir, **kwargs):
        _hanging_command(workdir, kwargs["limits"])
    monkeypatch.setattr(transfer, "transfer", hang)
    entered = time.monotonic()
    result = transfer.owned_transfer(_access(100), _access(101), None, directory,
                                    limits=transfer.Limits(total_seconds=3, command_seconds=60))
    elapsed = time.monotonic() - entered
    pid, group = map(int, (directory / "descendant.identity").read_text().split())
    assert group != transfer.os.getpgrp()
    _assert_stopped(pid)
    with pytest.raises(ProcessLookupError):
        transfer.os.killpg(group, 0)
    assert elapsed < 3
    assert result["status"] == "reconcile_required"
    assert result["phase"] == "owned_transfer_deadline_reconcile_before_retry"
    assert result["cleanup"] == "group_absent_anchor_reaped"
    assert result["attempt_started"] is None
    assert result["production_released"] is False
    assert (directory / "transfer.owner.started").is_file()


@pytest.mark.skipif(transfer.sys.platform != "linux", reason="Actual owned process semantics require Linux")
@_isolated_linux_case
def test_owned_transfer_caller_cancellation_stops_a_real_hanging_descendant(tmp_path, monkeypatch):
    import os
    import signal
    directory = _owned_directory(tmp_path)
    def hang(_source, _target, _tools, workdir, **kwargs):
        _hanging_command(workdir, kwargs["limits"])
    monkeypatch.setattr(transfer, "transfer", hang)
    parent = os.getpid()
    controller = os.fork()
    if controller == 0:
        end = time.monotonic() + 2
        identity = directory / "descendant.identity"
        while not identity.is_file() and time.monotonic() < end:
            time.sleep(0.01)
        os.kill(parent, signal.SIGINT)
        os._exit(0)
    try:
        result = transfer.owned_transfer(_access(100), _access(101), None, directory,
                                        limits=transfer.Limits(total_seconds=5, command_seconds=60))
    finally:
        os.waitpid(controller, 0)
    pid, group = map(int, (directory / "descendant.identity").read_text().split())
    _assert_stopped(pid)
    with pytest.raises(ProcessLookupError):
        transfer.os.killpg(group, 0)
    assert result["status"] == "cancelled"
    assert result["cleanup"] == "group_absent_anchor_reaped"
    assert result["production_released"] is False
    assert (directory / "transfer.owner.started").is_file()


@pytest.mark.skipif(transfer.sys.platform != "linux", reason="Actual owned process semantics require Linux")
@_isolated_linux_case
def test_owned_transfer_keeps_final_native_refusal_without_any_connection(tmp_path, monkeypatch):
    def forbidden(_self):
        raise AssertionError("Final cut refusal must precede SQL")
    monkeypatch.setattr(transfer.BoundDatabase, "connect", forbidden)
    directory = _owned_directory(tmp_path)
    result = transfer.owned_transfer(_access(100), _access(101), None, directory,
                                    purpose="final", limits=transfer.Limits(total_seconds=3))
    assert result["status"] == "refused"
    assert result["phase"] == "final_native_cut_required"
    assert result["attempt_started"] is True
    assert result["native_cut_observed"] is False
    assert result["production_released"] is False


@pytest.mark.skipif(transfer.sys.platform != "linux", reason="Actual owned process semantics require Linux")
@_isolated_linux_case
def test_owned_transfer_once_marker_and_typed_terminal_never_expose_credentials(tmp_path, monkeypatch, capfd):
    directory = _owned_directory(tmp_path)
    sentinel = "synthetic-password-and-vendor-body-must-not-escape"
    calls = directory / "synthetic-callback-count"
    captured_stdout = sys.stdout
    logger = logging.getLogger("hirewiz.synthetic.inherited.capture")
    handler = logging.StreamHandler(captured_stdout)
    monkeypatch.setattr(logger, "handlers", [handler])
    monkeypatch.setattr(logger, "level", logging.DEBUG)
    monkeypatch.setattr(logger, "propagate", False)
    def inherited_warning(message, *_args, **_kwargs):
        captured_stdout.write(str(message))
    monkeypatch.setattr(warnings, "showwarning", inherited_warning)
    parent_logging_disable = logging.root.manager.disable
    parent_warning_filters = list(warnings.filters)
    def synthetic(_source, _target, _tools, workdir, **_kwargs):
        print(sentinel, flush=True)
        sys.stdout.write(sentinel)
        sys.stdout.flush()
        sys.stderr.write(sentinel)
        sys.stderr.flush()
        os.write(1, sentinel.encode())
        os.write(2, sentinel.encode())
        logger.error(sentinel)
        handler.emit(logging.LogRecord("synthetic", logging.ERROR, "synthetic", 1, sentinel, (), None))
        warnings.warn(sentinel, UserWarning, stacklevel=1)
        warnings.showwarning(sentinel, UserWarning, "synthetic", 1)
        calls.write_text("one")
        return {"transfer": "verified", "purpose": "rehearsal", "schema": transfer.SCHEMA9,
                "table_rows": {sentinel: 3}, "sequence_count": 2,
                "native_cut_observed": False, "production_released": False}
    monkeypatch.setattr(transfer, "transfer", synthetic)
    first = transfer.owned_transfer(_access(100), _access(101), None, directory,
                                   limits=transfer.Limits(total_seconds=3))
    assert first["status"] == "verified"
    assert (first["table_count"], first["row_count"], first["sequence_count"]) == (1, 3, 2)
    second = transfer.owned_transfer(_access(100), _access(101), None, directory,
                                    limits=transfer.Limits(total_seconds=3))
    assert second["status"] == "refused"
    assert second["phase"] == "owned_attempt_already_started_reconcile_before_retry"
    assert second["attempt_started"] is False
    assert calls.read_text() == "one"
    assert sentinel not in repr(first) + repr(second)
    assert sentinel not in "".join(capfd.readouterr())
    assert logger.handlers == [handler] and handler.stream is captured_stdout
    assert logging.root.manager.disable == parent_logging_disable
    assert warnings.filters == parent_warning_filters and warnings.showwarning is inherited_warning
    other = tmp_path / "refusal"
    other.mkdir(mode=0o700)
    def secret_exception(*_args, **_kwargs):
        raise transfer.TransferDenied(sentinel)
    monkeypatch.setattr(transfer, "transfer", secret_exception)
    denied = transfer.owned_transfer(_access(100), _access(101), None, other,
                                    limits=transfer.Limits(total_seconds=3))
    assert denied["phase"] == "transfer_refused"
    assert sentinel not in repr(denied)


@pytest.mark.parametrize("permission_error", [False, True])
def test_owned_cleanup_refuses_observable_or_uninspectable_groups_after_reap(monkeypatch, permission_error):
    calls = []
    def group_signal(_pid, signal_number):
        calls.append(("group", signal_number))
        if signal_number == 0 and permission_error:
            raise PermissionError("synthetic permission refusal")
    def process_signal(_pid, signal_number):
        calls.append(("process", signal_number))
    def reap(pid, _flags):
        calls.append(("reap", 0))
        return pid, 0
    monkeypatch.setattr(transfer.os, "killpg", group_signal)
    monkeypatch.setattr(transfer.os, "kill", process_signal)
    monkeypatch.setattr(transfer.os, "waitpid", reap)
    result = transfer._owner_cleanup(123456, time.monotonic() + 0.03)
    assert result == ("anchor_reaped_group_unconfirmed" if permission_error else "cleanup_deadline_unconfirmed")
    after_reap = calls[calls.index(("reap", 0)) + 1:]
    assert after_reap and set(after_reap) == {("group", 0)}


def test_owned_stream_suppression_closes_inherited_capture_routes_in_a_real_child():
    # This cross-platform child exercises the actual stream helper without
    # faking Linux ownership, using duplicate capture FDs and cached handlers.
    backend = Path(__file__).resolve().parents[1]
    program = (
        "import os,sys,logging,warnings;"
        "from pathlib import Path;"
        f"sys.path.insert(0,{str(backend)!r});"
        "from scripts.database_transfer import _owner_discard_streams;"
        "cached=os.fdopen(os.dup(1),'w');sys.stdout=cached;"
        "logger=logging.getLogger('synthetic.cached');"
        "handler=logging.StreamHandler(cached);logger.addHandler(handler);logger.setLevel(1);"
        "warnings.showwarning=lambda message,*args,**kwargs:cached.write(str(message));"
        "_owner_discard_streams();"
        "sys.stdout.write('synthetic-stdout');sys.stdout.flush();"
        "sys.stderr.write('synthetic-stderr');sys.stderr.flush();"
        "os.write(1,b'synthetic-FD1');os.write(2,b'synthetic-FD2');"
        "logger.error('synthetic-logger');"
        "handler.emit(logging.LogRecord('synthetic',40,'synthetic',1,'synthetic-handler',(),None));"
        "warnings.warn('synthetic-warning');warnings.showwarning('synthetic-showwarning',UserWarning,'synthetic',1)"
    )
    result = subprocess.run([sys.executable, "-I", "-B", "-c", program],
                            env={"LANG": "C", "LC_ALL": "C", "TZ": "UTC"},
                            stdin=subprocess.DEVNULL, capture_output=True,
                            timeout=5, check=False)
    assert result.returncode == 0
    assert result.stdout == result.stderr == b""
