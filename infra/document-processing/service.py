"""Private inspection HTTP adapter; no host parser or scanner fallback.

Cloud Run must enable the official sandbox launcher, private invoker IAM, one
request per instance, bounded resources and no secret/file mounts. Invocation
image/policy declarations bind configuration; they do not prove those controls.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import math
import os
import re
import selectors
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import IO, TypedDict

SANDBOX = "/usr/local/gcp/bin/sandbox"
MAX_REQUEST = 7 * 1024 * 1024
MAX_OUTPUT = 1024 * 1024
ERROR = "document_inspection_refused"
UNAVAILABLE = "document_inspection_unavailable"
LOCK = threading.Lock()
READ_SECONDS = 10


class InspectionDenied(ValueError):
    def __init__(self):
        super().__init__(ERROR)


def _expire_run(*_):
    raise InspectionDenied()


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
    def __init__(self, authorized: bool):
        super().__init__()
        self.authorized = authorized


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
                    raise InspectionDenied()
            return code, bytes(output)
    except BaseException:
        if reserved and not creator_started:
            with _CREATION_LOCK:
                _CREATION_PENDING.clear()
                _CREATION_FAILED.set()
        raise _LauncherRefused(authorized) from None
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
            raise _LauncherRefused(authorized) from None
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


def inspect_in_sandbox(payload: bytes) -> dict:
    image, policy = configuration()
    if not os.path.isfile(SANDBOX) or not os.access(SANDBOX, os.X_OK) or not 0 < len(payload) <= MAX_REQUEST:
        raise InspectionDenied()
    sha256, size, kind = _request_identity(payload)
    # Retain the existing write allowance for the scanner's temporary target.
    # No imported/exported file, bind, environment inheritance or egress permission.
    # The immutable processor receives request bytes only on stdin.
    name = "document-" + uuid.uuid4().hex
    cleanup_required = True
    try:
        code, output = _run([SANDBOX, "do", "--sandbox-name=" + name, "--write", "--",
            "/usr/local/bin/python3", "-I", "/opt/document/processor.py", "--stdin"],
            timeout=55, limit=MAX_OUTPUT, allow_outcome=True, input_bytes=payload)
        result = json.loads(output, object_pairs_hook=_pairs)
        if (type(result) is not dict or type(result.get("version")) is not int or result["version"] != 1
                or result.get("sha256") != sha256 or result.get("source_format") != kind
                or type(result.get("size_bytes")) is not int or result["size_bytes"] != size):
            raise InspectionDenied()
        if code in {1, 2}:
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
        if len(json.dumps(result, allow_nan=False).encode()) > MAX_OUTPUT:
            raise InspectionDenied()
        return result
    except _LauncherRefused as failure:
        cleanup_required = failure.authorized
        raise InspectionDenied() from None
    except Exception:
        raise InspectionDenied() from None
    finally:
        # Force cleanup after timeouts/nonzero do as well. Any failed
        # cleanup refuses release; no fallback or mutation retry.
        if cleanup_required:
            _run([SANDBOX, "delete", name, "--force"], timeout=5, limit=65536, cleanup=True)



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
        try:
            _, policy = configuration()
            lengths = self.headers.get_all("Content-Length", [])
            policies = self.headers.get_all("X-HireWiz-Document-Policy", [])
            if (self.path != "/inspect" or self.headers.get("Content-Type") != "application/json"
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
            result = inspect_in_sandbox(payload)
            status = 422 if result.get("error") == ERROR else 503 if result.get("error") == UNAVAILABLE else 200
            self._reply(status, result)
        except Exception:
            self.close_connection = True
            self._reply(503, {"error": ERROR})
        finally:
            if locked:
                LOCK.release()

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
