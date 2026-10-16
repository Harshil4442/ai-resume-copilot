"""One-record direct-resume coordinator with an owned child-process deadline.

No parser, model, legacy dispatcher or application request data enters this runtime.
An unknown/terminated call leaves the reviewed domain leases and retry work intact.
"""
from __future__ import annotations

import contextlib
import json
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

Mode = Literal["scan", "cleanup"]
EXECUTION_SECONDS = 240
MAX_OUTPUT_BYTES = 1024
_LOCK = threading.Lock()


class CoordinatorUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("resume_coordinator_unavailable")


@dataclass(frozen=True)
class Outcome:
    mode: Mode
    scan_completed: int = 0
    swept: int = 0
    cleanup_completed: int = 0


def enabled(mode: str) -> bool:
    return (mode in {"scan", "cleanup"}
            and os.getenv("SERVICE_ROLE") == "resume-upload-coordinator"
            and os.getenv("RESUME_UPLOAD_COORDINATOR_ENABLED") == "true"
            and (mode == "cleanup" or (
                os.getenv("RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED") == "true"
                and os.getenv("RESUME_DIRECT_UPLOAD_ENABLED") == "true")))


def _run_domain(factory, store, mode: Mode) -> Outcome:
    # The reviewed functions own row locks, exact-target generation pins, leases,
    # commit/retry and cleanup semantics; this caller never retries their writes.
    from ..domains.resume_uploads import privacy, tasks

    if not enabled(mode):
        raise CoordinatorUnavailable()
    if mode == "scan":
        return Outcome(mode, scan_completed=tasks.process_due(factory, store, limit=1))
    with factory() as db:
        swept = privacy.sweep(db, limit=100)
    return Outcome(mode, swept=swept,
                   cleanup_completed=privacy.cleanup_due(factory, store, limit=1))


def _pairs(items):
    data = {}
    for key, value in items:
        if key in data:
            raise CoordinatorUnavailable()
        data[key] = value
    return data


def _decode(output: bytes, mode: Mode) -> Outcome:
    try:
        data = json.loads(output, object_pairs_hook=_pairs)
        if (not isinstance(data, dict) or set(data) != {
                "mode", "scan_completed", "swept", "cleanup_completed"}
                or data["mode"] != mode
                or any(type(data[k]) is not int for k in (
                    "scan_completed", "swept", "cleanup_completed"))
                or not 0 <= data["scan_completed"] <= 1
                or not 0 <= data["swept"] <= 100
                or not 0 <= data["cleanup_completed"] <= 1
                or (mode == "scan" and (data["swept"] or data["cleanup_completed"]))
                or (mode == "cleanup" and data["scan_completed"])):
            raise CoordinatorUnavailable()
        return Outcome(**data)
    except Exception:
        raise CoordinatorUnavailable() from None


def _child_command(mode: Mode) -> list[str]:
    return [sys.executable, "-m", "app.services.resume_upload_coordinator", mode]


def execute(mode: Mode) -> Outcome:
    if mode not in {"scan", "cleanup"} or not _LOCK.acquire(blocking=False):
        raise CoordinatorUnavailable()
    process = None
    try:
        environment = dict(os.environ)
        # Controlled module root; no user Python injection, tracing or key log.
        for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "SSLKEYLOGFILE"):
            environment.pop(key, None)
        deadline = time.monotonic() + EXECUTION_SECONDS
        process = subprocess.Popen(_child_command(mode),
            cwd=Path(__file__).resolve().parents[2], env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True)
        assert process.stdout is not None
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CoordinatorUnavailable()
                if not selector.select(remaining):
                    raise CoordinatorUnavailable()
                chunk = os.read(process.stdout.fileno(), MAX_OUTPUT_BYTES + 1)
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > MAX_OUTPUT_BYTES:
                    raise CoordinatorUnavailable()
        if process.wait(timeout=max(0.001, deadline - time.monotonic())) != 0:
            raise CoordinatorUnavailable()
        if time.monotonic() >= deadline:
            raise CoordinatorUnavailable()
        return _decode(bytes(output), mode)
    except Exception:
        raise CoordinatorUnavailable() from None
    finally:
        cleanup_failed = False
        if process is not None:
            # This PID owns a session created by this invocation; no other group
            # or global worker is touched, even after a successful child exit.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except Exception:
                cleanup_failed = True
            try:
                process.wait(timeout=3)
                if process.stdout is not None:
                    process.stdout.close()
            except Exception:
                cleanup_failed = True
        _LOCK.release()
        if cleanup_failed:
            raise CoordinatorUnavailable() from None


def _child(mode: Mode) -> int:
    try:
        if not enabled(mode):
            return 65
        # Lazy resources: importing the health/default-disabled app never opens
        # SQL or GCS. No legacy entrypoint, migration or candidate auth is imported.
        from ..database import SessionLocal, engine
        from ..domains.resume_uploads.storage import GCSObjectStore

        try:
            # Suppress provider/driver incidental stdout/stderr; only fixed count
            # fields are emitted after success. Exceptions are never serialized.
            with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                outcome = _run_domain(SessionLocal, GCSObjectStore(), mode)
            print(json.dumps(asdict(outcome), separators=(",", ":")), flush=True)
            return 0
        finally:
            engine.dispose()
    except Exception:
        return 65


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"scan", "cleanup"}:
        raise SystemExit(64)
    raise SystemExit(_child("scan" if sys.argv[1] == "scan" else "cleanup"))
