"""Private inspection HTTP adapter; no host parser or scanner fallback.

Cloud Run must enable the official sandbox launcher, private invoker IAM, one
request per instance, bounded resources and no secret/file mounts. Invocation
image/policy declarations bind configuration; they do not prove those controls.
"""
from __future__ import annotations

import base64
import contextlib
import contextvars
import hashlib
import hmac
import json
import math
import os
import re
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import IO, TypedDict

SANDBOX = "/usr/local/gcp/bin/sandbox"
CLAMD_MANIFEST = Path("/opt/document/clamd-native-manifest.json")
CLAMD_BINDING_ENV = "DOCUMENT_CLAMD_MANIFEST_BINDING"
CLAMD_BINDING_MODE = "gcp-ro-root-v1"
MAX_REQUEST = 7 * 1024 * 1024
MAX_OUTPUT = 1024 * 1024
MAX_LAYOUT_OUTPUT = 32 * 1024
ERROR = "document_inspection_refused"
UNAVAILABLE = "document_inspection_unavailable"
DOCX_ERROR = "docx_layout_refused"
DOCX_UNAVAILABLE = "docx_layout_unavailable"
LOCK = threading.Lock()
READ_SECONDS = 10


class InspectionDenied(ValueError):
    def __init__(self):
        super().__init__(ERROR)


class _RunDeadline(InspectionDenied):
    pass


_RUN_OUTCOMES = frozenset({"pending", "returned", "failed", "deadline", "unexpected_exit", "reap_failed"})
_DIAGNOSTIC_PHASES = frozenset({"admission", "launcher", "validation", "processor", "cleanup", "response"})
_VALIDATION_PHASES = frozenset({"not_started", "decode", "binding", "outcome", "complete"})
_PROCESSOR_OUTCOMES = frozenset({"not_validated", "clean", "refused", "unavailable"})


def _diagnostic_code(code: object) -> int | None:
    return code if type(code) is int and -255 <= code <= 255 else None


def _elapsed_ms(start: float) -> int:
    return min(120000, max(0, int((time.monotonic() - start) * 1000)))


class _RequestDiagnostic:
    """HTTP-only operational fields; never copy input, output or exception text."""

    def __init__(self) -> None:
        self.identifier = uuid.uuid4().hex
        self.started = time.monotonic()
        self.phase = "admission"
        self.failure_phase: str | None = None
        self.validation = "not_started"
        self.processor = "not_validated"
        self.launcher = "not_started"
        self.launcher_code: int | None = None
        self.launcher_elapsed_ms: int | None = None
        self.cleanup = "not_required"
        self.cleanup_code: int | None = None
        self.cleanup_elapsed_ms: int | None = None
        self.run_started = self.started

    def fail(self, phase: str, *, override: bool = False) -> None:
        if self.failure_phase is None or override:
            self.failure_phase = phase

    def begin_run(self, *, cleanup: bool = False) -> None:
        self.phase = "cleanup" if cleanup else "launcher"
        self.run_started = time.monotonic()
        if cleanup:
            self.cleanup = "pending"
        else:
            self.launcher = "pending"

    def end_run(self, code: object = None, error: BaseException | None = None, *, cleanup: bool = False) -> None:
        outcome = "returned"
        if error is not None:
            outcome = "deadline" if isinstance(error, (_RunDeadline, subprocess.TimeoutExpired)) else "failed"
            if isinstance(error, _LauncherRefused):
                outcome, code = error.outcome, error.code
            self.fail("cleanup" if cleanup else "launcher", override=cleanup)
        if cleanup:
            self.cleanup, self.cleanup_code = outcome, _diagnostic_code(code)
            self.cleanup_elapsed_ms = _elapsed_ms(self.run_started)
        else:
            self.launcher, self.launcher_code = outcome, _diagnostic_code(code)
            self.launcher_elapsed_ms = _elapsed_ms(self.run_started)

    def emit(self) -> None:
        if self.failure_phase is None:
            return
        try:
            for value, allowed in (
                (self.failure_phase, _DIAGNOSTIC_PHASES), (self.validation, _VALIDATION_PHASES),
                (self.processor, _PROCESSOR_OUTCOMES), (self.launcher, _RUN_OUTCOMES | {"not_started"}),
                (self.cleanup, _RUN_OUTCOMES | {"not_required"}),
            ):
                if type(value) is not str or value not in allowed:
                    return
            if type(self.identifier) is not str or re.fullmatch(r"[a-f0-9]{32}", self.identifier) is None:
                return
            for code in (self.launcher_code, self.cleanup_code):
                if code is not None and _diagnostic_code(code) is None:
                    return
            for elapsed in (self.launcher_elapsed_ms, self.cleanup_elapsed_ms):
                if elapsed is not None and (type(elapsed) is not int or not 0 <= elapsed <= 120000):
                    return
            event = {
                "event": "document_inspection_failure", "version": 1,
                "diagnostic_id": self.identifier, "phase": self.failure_phase,
                "validation": self.validation, "processor": self.processor,
                "launcher": self.launcher, "launcher_code": self.launcher_code,
                "launcher_elapsed_ms": self.launcher_elapsed_ms,
                "cleanup": self.cleanup, "cleanup_code": self.cleanup_code,
                "cleanup_elapsed_ms": self.cleanup_elapsed_ms, "elapsed_ms": _elapsed_ms(self.started),
            }
            output = json.dumps(event, separators=(",", ":"), allow_nan=False) + "\n"
            if len(output.encode("utf-8")) <= 1024:
                sys.stderr.write(output)
        except Exception:
            # Observability cannot change admission or a release refusal.
            pass


_REQUEST_DIAGNOSTIC = contextvars.ContextVar[_RequestDiagnostic | None]("document_request_diagnostic", default=None)


def _expire_run(*_):
    raise _RunDeadline()


@contextlib.contextmanager
def _absolute_deadline(seconds: int | float):
    """Bound the main-thread request without interrupting child construction."""
    if (type(seconds) not in {int, float} or not math.isfinite(seconds)
            or not 0 < seconds <= 55 or threading.current_thread() is not threading.main_thread()):
        raise InspectionDenied()
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_delay, previous_interval = signal.getitimer(signal.ITIMER_REAL)
    if previous_interval != 0:
        raise InspectionDenied()
    start = time.monotonic()
    delay = min(seconds, previous_delay) if previous_delay > 0 else seconds
    try:
        signal.signal(signal.SIGALRM, _expire_run)
        signal.setitimer(signal.ITIMER_REAL, delay)
        yield start + seconds
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_delay > 0:
            signal.setitimer(signal.ITIMER_REAL, max(0.000001, previous_delay - (time.monotonic() - start)))


# A constructor runs in a signal-free thread. Its uncertain result quarantines
# subsequent launches until a late owned child has been cleaned up successfully.
_CREATION_LOCK = threading.Lock()
_CREATION_PENDING = threading.Event()
_CREATION_FAILED = threading.Event()
_BOOTSTRAP = """import os,select,sys
gate=int(sys.argv[1])
try:
    ready,_,_=select.select([gate],[],[],5)
    admitted=bool(ready) and os.read(gate,1)==b'\\x01'
finally:
    os.close(gate)
if not admitted:
    raise SystemExit(125)
os.execv(sys.argv[2],sys.argv[2:])
"""


class _LauncherRefused(InspectionDenied):
    def __init__(self, authorized: bool, *, code: object = None, outcome: str = "failed") -> None:
        super().__init__()
        self.authorized = authorized
        self.code = _diagnostic_code(code)
        self.outcome = outcome if outcome in _RUN_OUTCOMES else "failed"


class _CreationState(TypedDict):
    child: subprocess.Popen[bytes] | None
    gate_write: IO[bytes] | None
    cancelled: bool
    failed: bool


def _cleanup_child(child, *, authorized: bool = True) -> bool:
    """Always attempt bounded reap and pipe closure, even when kill fails."""
    failed = False
    kill_failed = False
    code = None
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except BaseException:
        kill_failed = True
    try:
        code = child.wait(timeout=2)
    except BaseException:
        failed = True
    finally:
        for stream in (child.stdin, child.stdout):
            if stream is not None:
                try:
                    stream.close()
                except BaseException:
                    failed = True
    # An ungated bootstrap cannot fork or exec a target. Exit 125 acknowledges
    # its fixed EOF/timeout path; reaping that sole child completes cleanup even
    # on platforms where killpg rejects its already-exited group (macOS EPERM).
    if kill_failed and (authorized or code != 125):
        failed = True
    return not failed


def _run(arguments: list[str], *, timeout: int | float, limit: int,
         allow_outcome: bool = False, input_bytes: bytes | None = None,
         cleanup: bool = False) -> tuple[int, bytes]:
    """Own the child before authorizing the launcher or delivering payload.

    Only the main request thread owns the deadline. The constructor worker
    retains any late child result and cleans it up after cancellation. Until
    confirmed late cleanup, no other launcher is admitted. No payload travels
    through argv, environment, gate or logs; both payload pipes are nonblocking.
    """
    if (type(arguments) is not list or not arguments or any(type(arg) is not str for arg in arguments)
            or type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 < timeout <= 55
            or type(limit) is not int or not 0 < limit <= MAX_OUTPUT
            or type(allow_outcome) is not bool or type(cleanup) is not bool
            or (input_bytes is not None and (type(input_bytes) is not bytes
                                            or not 0 < len(input_bytes) <= MAX_REQUEST))):
        raise InspectionDenied()
    if cleanup and (len(arguments) != 4 or arguments[:2] != [SANDBOX, "delete"]
            or re.fullmatch(r"document-[a-f0-9]{32}", arguments[2]) is None
            or arguments[3] != "--force" or input_bytes is not None or allow_outcome
            or timeout != 5 or limit != 65536):
        raise InspectionDenied()
    child = None
    code = None
    failure = "failed"
    authorized = False
    gate_write = None
    creator_started = False
    reserved = False
    state_lock = threading.Lock()
    ready = threading.Event()
    state: _CreationState = {"child": None, "gate_write": None, "cancelled": False, "failed": False}
    try:
        with _absolute_deadline(timeout) as deadline:
            with _CREATION_LOCK:
                if _CREATION_PENDING.is_set() or (_CREATION_FAILED.is_set() and not cleanup):
                    raise InspectionDenied()
                reserved = True
                _CREATION_PENDING.set()
            spawn = subprocess.Popen

            def create():
                owned = None
                gate_read = None
                local_write = None
                released = False
                try:
                    with state_lock:
                        if state["cancelled"]:
                            released = True
                            return
                    # Both allocation and local ownership are signal-free.
                    # Cancellation before publication leaves both ends here.
                    gate_read, local_write = os.pipe()
                    os.set_blocking(local_write, False)
                    with state_lock:
                        if state["cancelled"]:
                            released = True
                            return
                        # FileIO owns the published writer: close is idempotent
                        # if a main-thread deadline interrupts after close returns.
                        state["gate_write"] = os.fdopen(local_write, "wb", buffering=0)
                        local_write = None
                    owned = spawn([sys.executable, "-I", "-c", _BOOTSTRAP,
                        str(gate_read), *arguments],
                        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        pass_fds=(gate_read,), start_new_session=True,
                        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "TZ": "UTC"})
                    if owned is None or type(getattr(owned, "pid", None)) is not int or owned.pid <= 0:
                        raise InspectionDenied()
                    with state_lock:
                        if not state["cancelled"]:
                            state["child"] = owned
                            owned = None
                            released = True
                except BaseException:
                    # A constructor exception gives no owned result. Keep
                    # admission quarantined; do not claim unknown cleanup.
                    with state_lock:
                        state["failed"] = True
                finally:
                    for descriptor in (gate_read, local_write):
                        if descriptor is not None:
                            try:
                                os.close(descriptor)
                            except OSError:
                                pass
                    if owned is not None:
                        released = _cleanup_child(owned, authorized=False)
                    with _CREATION_LOCK:
                        if not released:
                            _CREATION_FAILED.set()
                        _CREATION_PENDING.clear()
                    ready.set()

            creator = threading.Thread(target=create, daemon=True)
            # Store cancellation state before starting: a deadline during start
            # still closes the gate and leaves any running worker owning cleanup.
            creator_started = True
            try:
                creator.start()
            except RuntimeError:
                # This fresh Thread has not started; distinguish an explicit
                # start failure from asynchronous interruption with unknown start.
                creator_started = False
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not ready.wait(remaining):
                raise InspectionDenied()
            with state_lock:
                child = state["child"]
                state["child"] = None
                gate_write = state["gate_write"]
                state["gate_write"] = None
                if child is None or gate_write is None or state["failed"]:
                    raise InspectionDenied()
            if child.stdout is None or (input_bytes is not None and child.stdin is None):
                raise InspectionDenied()
            os.set_blocking(child.stdout.fileno(), False)
            if child.stdin is not None:
                os.set_blocking(child.stdin.fileno(), False)
            if deadline - time.monotonic() <= 0:
                raise InspectionDenied()
            authorized = True  # A deadline after this point requires controller deletion.
            if gate_write.write(b"\x01") != 1:
                raise InspectionDenied()
            gate_write.close()
            gate_write = None
            written = 0
            output = bytearray()
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ, "output")
                if child.stdin is not None:
                    selector.register(child.stdin, selectors.EVENT_WRITE, "input")
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise InspectionDenied()
                    for key, _ in selector.select(min(remaining, 0.1)):
                        if key.data == "input":
                            if input_bytes is None or child.stdin is None:
                                raise InspectionDenied()
                            try:
                                count = os.write(key.fd, memoryview(input_bytes)[written:written + 65536])
                            except BlockingIOError:
                                continue
                            if count <= 0:
                                raise InspectionDenied()
                            written += count
                            if written == len(input_bytes):
                                selector.unregister(child.stdin)
                                child.stdin.close()
                        else:
                            try:
                                chunk = os.read(key.fd, min(65536, limit + 1 - len(output)))
                            except BlockingIOError:
                                continue
                            if not chunk:
                                selector.unregister(child.stdout)
                            else:
                                output.extend(chunk)
                                if len(output) > limit:
                                    raise InspectionDenied()
                remaining = deadline - time.monotonic()
                if (input_bytes is not None and written != len(input_bytes)) or remaining <= 0:
                    raise InspectionDenied()
                code = child.wait(timeout=remaining)
                if code not in ((0, 1, 2) if allow_outcome else (0,)):
                    failure = "unexpected_exit"
                    raise InspectionDenied()
            return code, bytes(output)
    except BaseException as exc:
        if reserved and not creator_started:
            with _CREATION_LOCK:
                _CREATION_PENDING.clear()
                _CREATION_FAILED.set()
        if isinstance(exc, (_RunDeadline, subprocess.TimeoutExpired)):
            failure = "deadline"
        raise _LauncherRefused(authorized, code=code, outcome=failure) from None
    finally:
        with state_lock:
            state["cancelled"] = True
            if child is None:
                child = state["child"]
            state["child"] = None
            if gate_write is None:
                gate_write = state["gate_write"]
            state["gate_write"] = None
        if gate_write is not None:
            try:
                gate_write.close()
            except OSError:
                pass
        if child is not None and not _cleanup_child(child, authorized=authorized):
            with _CREATION_LOCK:
                _CREATION_FAILED.set()
            raise _LauncherRefused(authorized, code=code, outcome="reap_failed") from None
        # A worker that never returns remains quarantined. Constructor time is
        # not unbounded on the request thread, and its gate never receives data.

def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise InspectionDenied()
        result[key] = value
    return result


def configuration() -> tuple[str, str]:
    image = os.environ.get("DOCUMENT_WORKER_IMAGE_DIGEST", "")
    policy = os.environ.get("DOCUMENT_WORKER_POLICY_SHA256", "")
    if (not re.fullmatch(r"[A-Za-z0-9._:/-]+@sha256:[a-f0-9]{64}", image)
            or len(image) > 512 or not re.fullmatch(r"[a-f0-9]{64}", policy)):
        raise InspectionDenied()
    return image, policy


def _request_identity(payload: bytes) -> tuple[str, int, str]:
    """Identify original bytes without opening a document in the HTTP host."""
    try:
        request = json.loads(payload, object_pairs_hook=_pairs)
        if (type(request) is not dict or set(request) != {"version", "source_format", "sha256", "content_base64"}
                or type(request["version"]) is not int or request["version"] != 1
                or request["source_format"] not in {"pdf", "docx"}
                or not isinstance(request["sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", request["sha256"])
                or not isinstance(request["content_base64"], str)):
            raise InspectionDenied()
        content = base64.b64decode(request["content_base64"], validate=True)
        if (not 0 < len(content) <= 5 * 1024 * 1024
                or hashlib.sha256(content).hexdigest() != request["sha256"]):
            raise InspectionDenied()
        return request["sha256"], len(content), request["source_format"]
    except Exception:
        raise InspectionDenied() from None


def _docx_binding(payload: bytes, image: str, policy: str) -> tuple[dict, dict]:
    # Protocol-only decoding: no scanner/document parser executes in the host.
    if "/opt/document" not in sys.path:
        sys.path.insert(0, "/opt/document")
    from processor import decode_docx_request
    _, decoded = decode_docx_request(payload)
    request = decoded["request"]
    if request["worker_image"] != image or request["policy_sha256"] != policy:
        raise InspectionDenied()
    return decoded["binding"], request


def _docx_result(result: dict, binding: dict, request: dict, code: int, image: str, policy: str) -> None:
    from app.services.docx_layout_validation import LAYOUT_REASONS, check_layout_receipt
    if any(type(result.get(key)) is not type(value) or result.get(key) != value for key, value in binding.items()):
        raise InspectionDenied()
    if code == 1:
        if (set(result) != set(binding) | {"error", "reason", "unit_id"} or result["error"] != DOCX_ERROR
                or result["reason"] not in LAYOUT_REASONS | {"document_safety"}
                or (result["unit_id"] is not None and (type(result["unit_id"]) is not str
                    or not re.fullmatch(r"docx:[a-f0-9]{16}:p[0-9]{1,6}:[a-f0-9]{12}", result["unit_id"])
                    or not any(type(edit) is dict and edit.get("unit_id") == result["unit_id"] for edit in request["edits"])))):
            raise InspectionDenied()
    elif code == 2:
        if set(result) != set(binding) | {"error"} or result["error"] != DOCX_UNAVAILABLE:
            raise InspectionDenied()
    else:
        if code != 0 or set(result) != set(binding) | {"scan", "candidate_scan", "layout"}:
            raise InspectionDenied()
        for key in ("scan", "candidate_scan"):
            scan = result[key]
            if (type(scan) is not dict or set(scan) != {"engine", "version", "definitions"}
                    or scan["engine"] != "ClamAV" or any(type(scan[k]) is not str or not 0 < len(scan[k]) <= 128
                        or any(ord(c) < 32 or ord(c) > 126 for c in scan[k]) for k in ("version", "definitions"))):
                raise InspectionDenied()
        check_layout_receipt(result["layout"], source_sha256=binding["sha256"], source_size_bytes=binding["size_bytes"],
            candidate_sha256=binding["candidate_sha256"], candidate_size_bytes=binding["candidate_size_bytes"],
            edits_sha256=binding["edits_sha256"], image=image, policy=policy)


def _host_clamd_binding() -> str:
    """Bind one actual immutable host manifest; never accept a request assertion."""
    descriptor = os.open(CLAMD_MANIFEST, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o222
                or not 0 < info.st_size <= 16384):
            raise InspectionDenied()
        raw = os.read(descriptor, 16385)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_size", "st_mtime_ns", "st_ctime_ns")
        after = os.fstat(descriptor)
        current = CLAMD_MANIFEST.lstat()
        if (len(raw) != info.st_size or any(getattr(info, key) != getattr(after, key)
                or getattr(info, key) != getattr(current, key) for key in fields)):
            raise InspectionDenied()
        manifest = json.loads(raw, object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(InspectionDenied()))
        if (type(manifest) is not dict or set(manifest) != {"version", "engine", "files"}
                or type(manifest["version"]) is not int or manifest["version"] != 1
                or manifest["engine"] != "1.4.3" or type(manifest["files"]) is not list or len(manifest["files"]) != 3):
            raise InspectionDenied()
        libraries = {path.resolve(strict=True) for path in Path("/usr/lib").glob("*/libclamav.so.*")}
        if len(libraries) != 1:
            raise InspectionDenied()
        paths = [Path("/usr/sbin/clamd").resolve(strict=True), Path("/usr/bin/clamscan").resolve(strict=True), *libraries]
        machines = set()
        for row, path in zip(manifest["files"], paths, strict=True):
            if (type(row) is not dict or set(row) != {"path", "sha256", "size", "elf_machine"}
                    or row["path"] != str(path) or type(row["sha256"]) is not str
                    or re.fullmatch(r"[a-f0-9]{64}", row["sha256"]) is None
                    or type(row["size"]) is not int or not 20 <= row["size"] <= 128 * 1024**2
                    or type(row["elf_machine"]) is not int or row["elf_machine"] not in {62, 183}):
                raise InspectionDenied()
            machines.add(row["elf_machine"])
        if len(machines) != 1:
            raise InspectionDenied()
        return CLAMD_BINDING_MODE + ":" + hashlib.sha256(raw).hexdigest()
    except Exception:
        raise InspectionDenied() from None
    finally:
        os.close(descriptor)


def _remove_request_tmp(directory: Path, identity: tuple[int, int]) -> None:
    if (directory.parent != Path("/tmp") or re.fullmatch(r"document-[a-f0-9]{32}", directory.name) is None
            or not shutil.rmtree.avoids_symlink_attacks):
        raise InspectionDenied()
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != identity
            or info.st_uid != os.getuid() or info.st_gid != os.getgid() or stat.S_IMODE(info.st_mode) != 0o700):
        raise InspectionDenied()
    shutil.rmtree(directory)
    if os.path.lexists(directory):
        raise InspectionDenied()


def inspect_in_sandbox(payload: bytes, *, docx_layout: bool = False) -> dict:
    diagnostic = _REQUEST_DIAGNOSTIC.get()
    image, policy = configuration()
    if type(docx_layout) is not bool or not os.path.isfile(SANDBOX) or not os.access(SANDBOX, os.X_OK) or not 0 < len(payload) <= MAX_REQUEST:
        raise InspectionDenied()
    if docx_layout:
        binding, request = _docx_binding(payload, image, policy)
        sha256, size, kind = binding["sha256"], binding["size_bytes"], "docx"
    else:
        sha256, size, kind = _request_identity(payload)
    # Read-only image root; only a fresh empty request-owned /tmp is writable.
    # No other mount, environment passthrough, archive, egress or payload argv.
    name = "document-" + uuid.uuid4().hex
    directory = Path("/tmp") / name
    identity = None
    host_created = False
    host_creation_pending = False
    cleanup_required = False
    delete_confirmed = False
    try:
        if diagnostic is not None:
            diagnostic.begin_run()
        with _absolute_deadline(55) as launch_deadline:
            manifest_binding = _host_clamd_binding()
            host_creation_pending = True
            try:
                os.mkdir(directory, 0o700)
            except FileExistsError:
                host_creation_pending = False
                raise
            host_created = True
            host_creation_pending = False
            info = directory.lstat()
            identity = (info.st_dev, info.st_ino)
            os.chown(directory, -1, os.getgid(), follow_symlinks=False)
            info = directory.lstat()
            if ((info.st_dev, info.st_ino) != identity or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_gid != os.getgid() or stat.S_IMODE(info.st_mode) != 0o700):
                raise InspectionDenied()
        remaining = launch_deadline - time.monotonic()
        if remaining <= 0:
            raise InspectionDenied()
        cleanup_required = True
        code, output = _run([SANDBOX, "do", "--sandbox-name=" + name,
            "--mount", "type=bind,source=" + str(directory) + ",destination=/tmp",
            "--env", CLAMD_BINDING_ENV + "=" + manifest_binding, "--",
            "/usr/local/bin/python3", "-I", "/opt/document/processor.py",
            "--docx-layout-stdin" if docx_layout else "--stdin"],
            timeout=remaining, limit=MAX_LAYOUT_OUTPUT if docx_layout else MAX_OUTPUT, allow_outcome=True, input_bytes=payload)
        if diagnostic is not None:
            diagnostic.end_run(code)
            diagnostic.phase = "validation"
            diagnostic.validation = "decode"
        result = json.loads(output, object_pairs_hook=_pairs)
        if diagnostic is not None:
            diagnostic.validation = "binding"
        if (type(result) is not dict or type(result.get("version")) is not int or result["version"] != 1
                or result.get("sha256") != sha256 or result.get("source_format") != kind
                or type(result.get("size_bytes")) is not int or result["size_bytes"] != size):
            raise InspectionDenied()
        if diagnostic is not None:
            diagnostic.validation = "outcome"
        if docx_layout:
            _docx_result(result, binding, request, code, image, policy)
        elif code in {1, 2}:
            if (set(result) != {"version", "sha256", "size_bytes", "source_format", "error"}
                    or result["error"] != (ERROR if code == 1 else UNAVAILABLE)):
                raise InspectionDenied()
        elif (code != 0 or set(result) != {"version", "sha256", "size_bytes", "source_format", "scan", "parsed"}
                or type(result["scan"]) is not dict or set(result["scan"]) != {"engine", "version", "definitions"}
                or result["scan"]["engine"] != "ClamAV"
                or any(not isinstance(result["scan"][k], str) or not 0 < len(result["scan"][k]) <= 128
                       or any(ord(c) < 32 for c in result["scan"][k]) for k in {"version", "definitions"})
                or type(result["parsed"]) is not dict
                or set(result["parsed"]) != {"raw_text", "sections", "skills", "experience_years", "contact_info"}):
            raise InspectionDenied()
        result.update(worker_image=image, policy_sha256=policy)
        if len(json.dumps(result, allow_nan=False).encode()) > (MAX_LAYOUT_OUTPUT if docx_layout else MAX_OUTPUT):
            raise InspectionDenied()
        if diagnostic is not None:
            diagnostic.validation = "complete"
            diagnostic.processor = "refused" if code == 1 else "unavailable" if code == 2 else "clean"
            if code != 0:
                diagnostic.fail("processor")
        return result
    except _LauncherRefused as failure:
        if diagnostic is not None:
            diagnostic.end_run(error=failure)
        cleanup_required = failure.authorized
        raise InspectionDenied() from None
    except Exception as exc:
        if diagnostic is not None:
            if diagnostic.phase == "launcher":
                diagnostic.end_run(error=exc)
            else:
                diagnostic.fail("validation")
        raise InspectionDenied() from None
    finally:
        # Keep host temporary bytes until native sandbox deletion is confirmed.
        # Host erase shares the existing delete5 + reap2 envelope; unknown
        # cleanup quarantines subsequent admission and never returns a receipt.
        try:
            with _absolute_deadline(7):
                if cleanup_required:
                    if diagnostic is not None:
                        diagnostic.begin_run(cleanup=True)
                    try:
                        cleanup_code, _ = _run([SANDBOX, "delete", name, "--force"], timeout=5, limit=65536, cleanup=True)
                    except BaseException as exc:
                        if diagnostic is not None:
                            diagnostic.end_run(error=exc, cleanup=True)
                        raise
                    else:
                        if diagnostic is not None:
                            diagnostic.end_run(cleanup_code, cleanup=True)
                        if type(cleanup_code) is not int or cleanup_code != 0:
                            raise InspectionDenied()
                        delete_confirmed = True
                if host_creation_pending or host_created and identity is None:
                    raise InspectionDenied()
                if identity is not None and (not cleanup_required or delete_confirmed):
                    _remove_request_tmp(directory, identity)
        except BaseException:
            if host_created or host_creation_pending:
                with _CREATION_LOCK:
                    _CREATION_FAILED.set()
            raise



class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(READ_SECONDS)
        self._read_lock = threading.Lock()
        self._read_active = True
        self._read_expired = False
        self._read_deadline = time.monotonic() + READ_SECONDS
        self._read_timer = threading.Timer(READ_SECONDS, self._expire_read)
        self._read_timer.daemon = True
        self._read_timer.start()

    def _expire_read(self):
        with self._read_lock:
            if self._read_active:
                self._read_expired = True
                self._read_active = False
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def _finish_request_read(self, *, admit: bool = False):
        with self._read_lock:
            expired = self._read_expired or time.monotonic() >= self._read_deadline
            self._read_active = False
            self._read_timer.cancel()
            # Expiry and dispatch admission share the same lock. A delayed
            # timer cannot extend the absolute read deadline either.
            if admit and expired:
                raise InspectionDenied()

    def finish(self):
        self._finish_request_read()
        super().finish()

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        self._reply(503, {"error": ERROR})

    def _reply(self, status: int, result: dict):
        output = json.dumps(result, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(output)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(output)

    def do_POST(self):
        locked = False
        diagnostic = _RequestDiagnostic()
        diagnostic_token = _REQUEST_DIAGNOSTIC.set(diagnostic)
        try:
            _, policy = configuration()
            lengths = self.headers.get_all("Content-Length", [])
            policies = self.headers.get_all("X-HireWiz-Document-Policy", [])
            if (self.path not in {"/inspect", "/validate-docx"} or self.headers.get("Content-Type") != "application/json"
                    or self.headers.get("Transfer-Encoding") or self.headers.get("Content-Encoding")
                    or len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,8}", lengths[0])
                    or not 0 < int(lengths[0]) <= MAX_REQUEST or len(policies) != 1
                    or not hmac.compare_digest(policies[0], policy)):
                raise InspectionDenied()
            locked = LOCK.acquire(blocking=False)
            if not locked:
                raise InspectionDenied()
            payload = self.rfile.read(int(lengths[0]))
            if len(payload) != int(lengths[0]):
                raise InspectionDenied()
            self._finish_request_read(admit=True)
            result = inspect_in_sandbox(payload, docx_layout=True) if self.path == "/validate-docx" else inspect_in_sandbox(payload)
            status = 422 if result.get("error") in {ERROR, DOCX_ERROR} else 503 if result.get("error") in {UNAVAILABLE, DOCX_UNAVAILABLE} else 200
            diagnostic.phase = "response"
            self._reply(status, result)
        except Exception:
            diagnostic.fail(diagnostic.phase)
            self.close_connection = True
            self._reply(503, {"error": ERROR})
        finally:
            try:
                if locked:
                    LOCK.release()
            finally:
                _REQUEST_DIAGNOSTIC.reset(diagnostic_token)
                diagnostic.emit()

    def do_GET(self):
        self._reply(404, {"error": ERROR})


class InspectionHTTPServer(HTTPServer):
    def handle_error(self, request, client_address):
        # Disconnected clients and malformed requests never emit tracebacks.
        pass


if __name__ == "__main__":
    os.umask(0o077)
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    InspectionHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
