"""Synthetic host/native metadata controls; no scanner, image or provider calls."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_document_processor import load, payload, result_for

ROOT = Path(__file__).resolve().parents[2]


def info(*, uid: int = 0, gid: int = 0, mode: int = 0o100444, size: int = 100) -> Any:
    return SimpleNamespace(
        st_uid=uid,
        st_gid=gid,
        st_mode=mode,
        st_size=size,
        st_dev=1,
        st_ino=2,
        st_mtime_ns=3,
        st_ctime_ns=4,
    )


def manifest() -> bytes:
    rows = [
        {"path": path, "sha256": "a" * 64, "size": 100, "elf_machine": 62}
        for path in (
            "/usr/sbin/clamd",
            "/usr/bin/clamscan",
            "/usr/lib/x86_64-linux-gnu/libclamav.so.12.0.3",
        )
    ]
    return json.dumps(
        {"version": 1, "engine": "1.4.3", "files": rows}, separators=(",", ":")
    ).encode()


def mock_read(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
    before: Any,
    after: Any = None,
) -> list:
    calls = []
    monkeypatch.setattr(
        module.os, "open", lambda path, flags: calls.append(("open", path, flags)) or 77
    )
    stats = iter((before, before if after is None else after))
    monkeypatch.setattr(module.os, "fstat", lambda _: next(stats))
    monkeypatch.setattr(
        module.os, "read", lambda fd, limit: calls.append(("read", fd, limit)) or raw
    )
    monkeypatch.setattr(module.os, "close", lambda fd: calls.append(("close", fd)))
    return calls


@pytest.mark.parametrize("mapped", [False, True])
def test_direct_native_path_and_exact_host_bound_mapped_path(
    mapped: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    processor = load("processor")
    raw = manifest()
    if mapped:
        monkeypatch.setenv(
            processor.CLAMD_BINDING_ENV,
            processor.CLAMD_BINDING_MODE + ":" + hashlib.sha256(raw).hexdigest(),
        )
    else:
        monkeypatch.delenv(processor.CLAMD_BINDING_ENV, raising=False)
    monkeypatch.setattr(processor.os, "getuid", lambda: 0)
    monkeypatch.setattr(processor.os, "getgid", lambda: 0)
    calls = mock_read(
        processor,
        monkeypatch,
        raw,
        info(uid=65534 if mapped else 0, gid=65534 if mapped else 0, size=len(raw)),
    )
    assert processor._read_clamd_manifest() == raw
    assert calls == [
        ("open", processor.CLAMD_MANIFEST, os.O_RDONLY | os.O_NOFOLLOW),
        ("read", 77, 16385),
        ("close", 77),
    ]


@pytest.mark.parametrize(
    "change",
    [
        "absent",
        "unknown_mode",
        "hash_mismatch",
        "different_uid",
        "different_gid",
        "writable",
        "processor_uid",
        "processor_gid",
        "short_read",
        "inode_changed",
    ],
)
def test_mapped_owner_is_never_a_general_uid_or_caller_assertion(
    change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    processor = load("processor")
    raw = manifest()
    binding = processor.CLAMD_BINDING_MODE + ":" + hashlib.sha256(raw).hexdigest()
    before = info(uid=65534, gid=65534, size=len(raw))
    after = None
    current_uid = current_gid = 0
    if change == "absent":
        monkeypatch.delenv(processor.CLAMD_BINDING_ENV, raising=False)
    else:
        if change == "unknown_mode":
            binding = "untrusted:" + hashlib.sha256(raw).hexdigest()
        if change == "hash_mismatch":
            binding = processor.CLAMD_BINDING_MODE + ":" + "b" * 64
        monkeypatch.setenv(processor.CLAMD_BINDING_ENV, binding)
    if change == "different_uid":
        before.st_uid = 123
    if change == "different_gid":
        before.st_gid = 123
    if change == "writable":
        before.st_mode = 0o100644
    if change == "processor_uid":
        current_uid = 65534
    if change == "processor_gid":
        current_gid = 65534
    if change == "short_read":
        raw = raw[:-1]
    if change == "inode_changed":
        after = info(uid=65534, gid=65534, size=len(raw))
        after.st_ino += 1
    monkeypatch.setattr(processor.os, "getuid", lambda: current_uid)
    monkeypatch.setattr(processor.os, "getgid", lambda: current_gid)
    calls = mock_read(processor, monkeypatch, raw, before, after)
    with pytest.raises(processor.InspectionUnavailable):
        processor._read_clamd_manifest()
    if calls:
        assert calls[-1] == ("close", 77)


@pytest.mark.parametrize(
    "failure",
    [
        "not_root",
        "writable",
        "oversize",
        "changed",
        "duplicate",
        "unknown_field",
        "wrong_native_path",
        "bool_size",
    ],
)
def test_outer_manifest_binding_is_actual_root_file_and_strict_known_native_shape(
    failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = load("service")
    raw = manifest()
    value = json.loads(raw)
    if failure == "duplicate":
        raw = b'{"version":1,"version":1,"engine":"1.4.3","files":[]}'
    if failure == "unknown_field":
        value["customer_secret"] = "synthetic-must-not-escape"
        raw = json.dumps(value).encode()
    if failure == "wrong_native_path":
        value["files"][0]["path"] = "/tmp/untrusted"
        raw = json.dumps(value).encode()
    if failure == "bool_size":
        value["files"][0]["size"] = True
        raw = json.dumps(value).encode()
    before = info(size=len(raw))
    if failure == "not_root":
        before.st_uid = 65534
    if failure == "writable":
        before.st_mode = 0o100644
    if failure == "oversize":
        before.st_size = 16385
    after = info(size=len(raw))
    if failure == "changed":
        after.st_ino += 1
    mock_read(service, monkeypatch, raw, before, after)
    monkeypatch.setattr(Path, "lstat", lambda _: before)
    monkeypatch.setattr(Path, "resolve", lambda self, **_: self)
    monkeypatch.setattr(
        Path, "glob", lambda *_: [Path("/usr/lib/x86_64-linux-gnu/libclamav.so.12.0.3")]
    )
    with pytest.raises(service.InspectionDenied) as caught:
        service._host_clamd_binding()
    assert "synthetic-must-not-escape" not in str(caught.value)


def test_outer_verified_binding_is_exact_descriptor_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = load("service")
    raw = manifest()
    before = info(size=len(raw))
    calls = mock_read(service, monkeypatch, raw, before)
    monkeypatch.setattr(Path, "lstat", lambda _: before)
    monkeypatch.setattr(Path, "resolve", lambda self, **_: self)
    monkeypatch.setattr(
        Path, "glob", lambda *_: [Path("/usr/lib/x86_64-linux-gnu/libclamav.so.12.0.3")]
    )
    assert (
        service._host_clamd_binding()
        == service.CLAMD_BINDING_MODE + ":" + hashlib.sha256(raw).hexdigest()
    )
    assert calls[-1] == ("close", 77)


@pytest.mark.parametrize("failure", ["delete", "host_erase"])
def test_unknown_native_delete_or_host_erase_refuses_and_quarantines_without_capture(
    failure: str, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    service = load("service")
    monkeypatch.setenv(
        "DOCUMENT_WORKER_IMAGE_DIGEST", "registry.invalid/document@sha256:" + "a" * 64
    )
    monkeypatch.setenv("DOCUMENT_WORKER_POLICY_SHA256", "b" * 64)
    monkeypatch.setattr(service.os.path, "isfile", lambda _: True)
    monkeypatch.setattr(service.os, "access", lambda *_: True)
    monkeypatch.setattr(
        service,
        "_host_clamd_binding",
        lambda: service.CLAMD_BINDING_MODE + ":" + "c" * 64,
    )
    content = b"synthetic exact stdin bytes"
    original = payload(content)
    rows = []
    directory = None

    def run(args: list[str], **options: Any) -> tuple[int, bytes]:
        nonlocal directory
        rows.append(args[1])
        if args[1] == "do":
            directory = Path("/tmp") / args[2].split("=", 1)[1]
            assert options["input_bytes"] == original
            assert "--write" not in args and "--allow-egress" not in args
            assert (
                sum(arg == "--mount" for arg in args) == 1
                and sum(arg == "--env" for arg in args) == 1
            )
            assert content.decode() not in "".join(args)
            return 0, json.dumps(result_for(content)).encode()
        assert directory is not None and directory.exists()
        if failure == "delete":
            raise service.InspectionDenied()
        return 0, b""

    erased = []
    monkeypatch.setattr(service, "_run", run)
    monkeypatch.setattr(
        service,
        "_remove_request_tmp",
        lambda *_: (
            erased.append("erase") or (_ for _ in ()).throw(service.InspectionDenied())
        ),
    )
    try:
        with pytest.raises(service.InspectionDenied):
            service.inspect_in_sandbox(original)
        assert rows == ["do", "delete"]
        assert erased == ([] if failure == "delete" else ["erase"])
        assert service._CREATION_FAILED.is_set()
        assert capsys.readouterr() == ("", "")
    finally:
        if directory is not None and directory.exists():
            # Synthetic fake-controller directory only; no native action occurred.
            service.shutil.rmtree(directory)


def test_owned_host_erase_cannot_follow_a_replaced_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = load("service")
    before = info(uid=os.getuid(), gid=os.getgid(), mode=stat.S_IFLNK | 0o700)
    monkeypatch.setattr(Path, "lstat", lambda _: before)
    monkeypatch.setattr(
        service.shutil,
        "rmtree",
        lambda *_: pytest.fail("Replaced symlink must never be traversed"),
    )
    service.shutil.rmtree.avoids_symlink_attacks = True
    with pytest.raises(service.InspectionDenied):
        service._remove_request_tmp(Path("/tmp/document-" + "a" * 32), (1, 2))


def test_host_setup_consumes_launcher_budget_and_cleanup_shares_original_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import contextlib

    service = load("service")
    monkeypatch.setenv(
        "DOCUMENT_WORKER_IMAGE_DIGEST", "registry.invalid/document@sha256:" + "a" * 64
    )
    monkeypatch.setenv("DOCUMENT_WORKER_POLICY_SHA256", "b" * 64)
    monkeypatch.setattr(service.os.path, "isfile", lambda _: True)
    monkeypatch.setattr(service.os, "access", lambda *_: True)
    clock = [100.0]
    budgets = []

    @contextlib.contextmanager
    def deadline(seconds: int):
        budgets.append(seconds)
        end = clock[0] + seconds
        yield end
        assert clock[0] < end

    def host_binding() -> str:
        clock[0] += 10
        return service.CLAMD_BINDING_MODE + ":" + "c" * 64

    monkeypatch.setattr(service, "_absolute_deadline", deadline)
    monkeypatch.setattr(service.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(service, "_host_clamd_binding", host_binding)
    content = b"synthetic budget case"
    actual_erase = service._remove_request_tmp
    phases = []

    def run(args: list[str], **options: Any) -> tuple[int, bytes]:
        phases.append(args[1])
        if args[1] == "do":
            assert options["timeout"] == 45
            return 0, json.dumps(result_for(content)).encode()
        assert options["timeout"] == 5
        clock[0] += 3
        return 0, b""

    def erase(directory: Path, identity: tuple[int, int]) -> None:
        assert phases == ["do", "delete"] and clock[0] == 113
        clock[0] += 2
        actual_erase(directory, identity)

    monkeypatch.setattr(service, "_run", run)
    monkeypatch.setattr(service, "_remove_request_tmp", erase)
    assert (
        service.inspect_in_sandbox(payload(content))["sha256"]
        == hashlib.sha256(content).hexdigest()
    )
    assert budgets == [55, 7] and clock[0] == 115


@pytest.mark.parametrize("failure", ["chown", "lstat", "mkdir_result_lost"])
def test_setup_failure_after_mkdir_cannot_leave_an_untracked_admitted_directory(
    failure: str, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    service = load("service")
    monkeypatch.setenv(
        "DOCUMENT_WORKER_IMAGE_DIGEST", "registry.invalid/document@sha256:" + "a" * 64
    )
    monkeypatch.setenv("DOCUMENT_WORKER_POLICY_SHA256", "b" * 64)
    monkeypatch.setattr(service.os.path, "isfile", lambda _: True)
    monkeypatch.setattr(service.os, "access", lambda *_: True)
    monkeypatch.setattr(
        service,
        "_host_clamd_binding",
        lambda: service.CLAMD_BINDING_MODE + ":" + "c" * 64,
    )
    created = []
    mkdir = os.mkdir
    original_lstat = Path.lstat

    def make(path: Any, *args: Any, **options: Any) -> None:
        mkdir(path, *args, **options)
        created.append(Path(path))
        if failure == "mkdir_result_lost":
            raise PermissionError("synthetic unobserved mkdir result")

    monkeypatch.setattr(service.os, "mkdir", make)
    if failure == "chown":
        monkeypatch.setattr(
            service.os,
            "chown",
            lambda *_args, **_options: (_ for _ in ()).throw(
                PermissionError("synthetic setup refusal")
            ),
        )
    elif failure == "lstat":
        monkeypatch.setattr(
            Path,
            "lstat",
            lambda self: (
                (_ for _ in ()).throw(PermissionError("synthetic setup refusal"))
                if self in created
                else original_lstat(self)
            ),
        )
    monkeypatch.setattr(
        service,
        "_run",
        lambda *_args, **_options: pytest.fail(
            "Failed setup must never authorize a launcher"
        ),
    )
    try:
        with pytest.raises(service.InspectionDenied):
            service.inspect_in_sandbox(payload(b"synthetic never-transmitted request"))
        assert len(created) == 1
        assert not created[0].exists() or service._CREATION_FAILED.is_set()
        assert capsys.readouterr() == ("", "")
    finally:
        monkeypatch.setattr(Path, "lstat", original_lstat)
        if created and created[0].exists():
            service.shutil.rmtree(created[0])
