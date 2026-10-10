"""Private inspection HTTP adapter; no host parser or scanner fallback.

Cloud Run must enable the official sandbox launcher, private invoker IAM, one
request per instance, bounded resources and no secret/file mounts. Invocation
image/policy declarations bind configuration; they do not prove those controls.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import re
import selectors
import signal
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

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


def _run(arguments: list[str], *, timeout: int, limit: int,
         allow_outcome: bool = False) -> tuple[int, bytes]:
    """Cap pipe bytes while running; killing the launcher alone is insufficient.

    The separate explicit sandbox delete below cleans the controller-owned child.
    No stdout/stderr is forwarded into Cloud Logging.
    """
    child = None
    try:
        child = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, start_new_session=True,
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "TZ": "UTC"})
        deadline = time.monotonic() + timeout
        output = bytearray()
        assert child.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise InspectionDenied()
                for key, _ in selector.select(min(remaining, 1)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(chunk)
                        if len(output) > limit:
                            raise InspectionDenied()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise InspectionDenied()
            code = child.wait(timeout=remaining)
            if code not in ((0, 1, 2) if allow_outcome else (0,)):
                raise InspectionDenied()
        return code, bytes(output)
    except Exception:
        raise InspectionDenied() from None
    finally:
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            if child.stdout:
                child.stdout.close()


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
    # Only internally generated regular-file names enter import-tar. No bind,
    # exports, environment inheritance or egress permission is requested.
    name = "document-" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="document-import-") as directory:
        Path(directory).chmod(0o700)
        archive_path = Path(directory) / "request.tar"
        with archive_path.open("xb") as stream:
            with tarfile.open(fileobj=stream, mode="w") as archive:
                entry = tarfile.TarInfo("tmp/request.json")
                # Readable by the sandbox's documented nonroot user. The host
                # tar is private; this one file exists only in this overlay.
                entry.mode, entry.size, entry.uid, entry.gid = 0o444, len(payload), 0, 0
                archive.addfile(entry, io.BytesIO(payload))
        archive_path.chmod(0o600)
        try:
            # One synchronous create/inspect/destroy call uses the existing
            # combined 10-second create + 45-second inspection allowance.
            code, output = _run([SANDBOX, "do", "--sandbox-name=" + name, "--write",
                "--import-tar=" + str(archive_path), "--", "/usr/local/bin/python3", "-I",
                "/opt/document/processor.py", "/tmp/request.json"], timeout=55, limit=MAX_OUTPUT, allow_outcome=True)
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
        except Exception:
            raise InspectionDenied() from None
        finally:
            # Force cleanup after timeouts/nonzero do as well. Any failed
            # cleanup refuses release; no fallback or mutation retry.
            _run([SANDBOX, "delete", name, "--force"], timeout=5, limit=65536)


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
