"""Private TeX adapter: candidate source runs only in a disposable sandbox.

The image contains no credentials. Cloud Run must inject the official sandbox
launcher and enforce private ingress/IAM; no host compilation fallback exists.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import importlib.util
import json
import os
import re
import socket
import stat
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

MAX_REQUEST = 8 * 1024 * 1024
MAX_OUTPUT = 15 * 1024 * 1024
MAX_PDF = 10 * 1024 * 1024
POLICY_PATH = Path("/opt/tex/policy.json")
LOCK = threading.Lock()
QUARANTINED = threading.Event()


class CompileRefused(ValueError):
    pass


def _initialize_subreaper():
    """Make this trusted Linux controller own orphaned launcher descendants."""
    if sys.platform != "linux":
        raise RuntimeError("compiler_unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                     ctypes.c_ulong, ctypes.c_ulong]
    prctl.restype = ctypes.c_int
    adopted = ctypes.c_int(0)
    address = ctypes.cast(ctypes.byref(adopted), ctypes.c_void_p).value
    if (address is None or prctl(36, 1, 0, 0, 0) != 0
            or prctl(37, address, 0, 0, 0) != 0 or adopted.value != 1):
        raise RuntimeError("compiler_unavailable")


def _terminal(child_pid):
    terminal = os.waitid(os.P_PID, child_pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    if terminal is not None and (type(terminal.si_pid) is not int or terminal.si_pid != child_pid
            or type(terminal.si_code) is not int
            or terminal.si_code not in {os.CLD_EXITED, os.CLD_KILLED, os.CLD_DUMPED}
            or type(terminal.si_status) is not int or not 0 <= terminal.si_status <= 255
            or terminal.si_code != os.CLD_EXITED and terminal.si_status == 0):
        raise RuntimeError("compiler_cleanup_unverified")
    return terminal


def _reap_adopted_group(group, deadline):
    """Reap exact adopted zombies while their direct-parent PID stays anchored.

    The terminal direct parent remains unreaped throughout this procedure, so
    the group's numeric identity cannot be reused. Each descendant's native
    WNOWAIT observation retains its own PID until exact-PID waitpid consumes it.
    The shared launcher subsequently reaps its parent and requires native ESRCH.
    """
    while time.monotonic() < deadline:
        if _terminal(group) is None:
            time.sleep(min(0.01, max(0, deadline - time.monotonic())))
            continue
        pending = False
        scanned = 0
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            scanned += 1
            if scanned > 1024 or time.monotonic() >= deadline:
                raise RuntimeError("compiler_cleanup_unverified")
            pid = int(entry.name)
            if pid == group:
                continue
            try:
                with (entry / "stat").open() as stream:
                    raw = stream.read(4097)
                if len(raw) > 4096:
                    raise RuntimeError("compiler_cleanup_unverified")
                if int(raw.split(" ", 1)[0]) != pid:
                    raise RuntimeError("compiler_cleanup_unverified")
                fields = raw.rsplit(")", 1)[1].split()
                if int(fields[2]) != group:
                    continue
                if int(fields[3]) != group or entry.stat().st_uid != os.getuid():
                    raise RuntimeError("compiler_cleanup_unverified")
                if int(fields[1]) != os.getpid():
                    pending = True
                    continue
                terminal = _terminal(pid)
                if terminal is None:
                    pending = True
                    continue
                actual_pid, status = os.waitpid(pid, os.WNOHANG)
                expected = (terminal.si_status << 8 if terminal.si_code == os.CLD_EXITED
                            else terminal.si_status | (128 if terminal.si_code == os.CLD_DUMPED else 0))
                if (type(actual_pid) is not int or type(status) is not int
                        or actual_pid != pid or status != expected):
                    raise RuntimeError("compiler_cleanup_unverified")
            except FileNotFoundError:
                # A process that vanished before kernel child custody was
                # observed will still be covered by the final group ESRCH.
                continue
            except (IndexError, ValueError):
                raise RuntimeError("compiler_cleanup_unverified") from None
        if not pending:
            return
        time.sleep(min(0.01, max(0, deadline - time.monotonic())))
    raise RuntimeError("compiler_cleanup_unverified")


def _controller_cleanup_adapter(module):
    """Stop the exact owned sandbox before reaping its platform controller.

    The shared document launcher's source stays unchanged. This process-local
    TeX adapter retains its ownership, deadline and post-reap absence checks,
    but uses the native sandbox controller when Unix signalling cannot stop a
    running platform process. An uncertain stop is never retried.
    """
    raw_run = module._run
    raw_cleanup = module._cleanup_child
    run_lock = threading.RLock()
    active = None
    deleting = False
    uncertain = False
    stops = {}

    def request_name(arguments):
        if (type(arguments) is list and len(arguments) >= 3
                and arguments[:2] == [module.SANDBOX, "do"]
                and type(arguments[2]) is str
                and arguments[2].startswith("--sandbox-name=")):
            name = arguments[2].removeprefix("--sandbox-name=")
            if re.fullmatch(r"document-[a-f0-9]{32}", name):
                return name
        return None

    def cleanup(child, *, authorized=True, expected_code=None):
        nonlocal deleting, uncertain
        stopped = True
        arguments = getattr(child, "args", None)
        # Only this synchronous request's exact admitted bootstrap/controller
        # can trigger a stop. Late unauthorized constructors keep shared custody.
        admitted = (authorized is True and not deleting
                 and active is not None and type(arguments) is list
                 and getattr(child, "returncode", False) is None
                 and type(getattr(child, "pid", None)) is int and child.pid > 0
                 and len(arguments) >= 8 and arguments[:4] == [
                     sys.executable, "-I", "-c", module._BOOTSTRAP]
                 and type(arguments[4]) is str and arguments[4].isdecimal()
                 and tuple(arguments[5:]) == active)
        owned = admitted and expected_code is None
        if (authorized is True and expected_code is None and not deleting
                and active is not None and not owned):
            name = request_name(list(active))
            if name is not None:
                stops[name] = False
            stopped = False
        if owned:
            name = request_name(list(active))
            if name is None or name in stops:
                stopped = False
            else:
                # Record the attempt before invoking: unknown outcomes stay
                # unconfirmed, including in the caller's finally block.
                stops[name] = False
                deleting = True
                started = time.monotonic()
                try:
                    # Three seconds plus the shared two-second child cleanup
                    # bounds this controller attempt to at most five seconds.
                    code, _ = raw_run([module.SANDBOX, "delete", name, "--force"],
                                      timeout=3, limit=65536)
                    stopped = code == 0 and time.monotonic() - started <= 5
                    stops[name] = stopped
                except BaseException:
                    stopped = False
                finally:
                    deleting = False
        # Preserve the original owned-PID anchor, native wait and process-group
        # absence checks even when controller deletion is unconfirmed.
        original_wait = getattr(child, "wait", None)
        if admitted and callable(original_wait):
            def wait_and_reap(*args, **kwargs):
                remaining = kwargs.get("timeout")
                if (type(remaining) not in (int, float) or not 0 <= remaining <= 2
                        or args):
                    raise RuntimeError("compiler_cleanup_unverified")
                deadline = time.monotonic() + remaining
                _reap_adopted_group(child.pid, deadline)
                kwargs["timeout"] = max(0, deadline - time.monotonic())
                return original_wait(**kwargs)
            child.wait = wait_and_reap
        try:
            reaped = raw_cleanup(child, authorized=authorized,
                                 expected_code=expected_code)
        except BaseException:
            reaped = False
        finally:
            if admitted and callable(original_wait):
                child.wait = original_wait
        if admitted and not reaped:
            name = request_name(list(active))
            if name is not None:
                stops[name] = False
        if owned and name is not None and name in stops:
            # A stop ACK alone never permits TMP release when parent custody
            # or post-reap group absence is unconfirmed.
            stops[name] = stopped and reaped
        if not deleting and active is not None and not (stopped and reaped):
            uncertain = True
        return stopped and reaped

    def run(arguments, *, timeout, limit, allow_outcome=False,
            input_bytes=None, cleanup=False):
        nonlocal active
        with run_lock:
            # Preserve the one failed request marker for this quarantined
            # instance. Duplicate finalizers and new names cannot retry it.
            if uncertain:
                raise module._LauncherRefused(False, outcome="failed")
            if (cleanup is True and type(arguments) is list and len(arguments) == 4
                    and arguments[:2] == [module.SANDBOX, "delete"]
                    and type(arguments[2]) is str
                    and arguments[3] == "--force" and arguments[2] in stops):
                if (type(timeout) not in (int, float) or timeout != 5
                        or type(limit) is not int or limit != 65536
                        or allow_outcome is not False or input_bytes is not None):
                    raise module._LauncherRefused(False, outcome="failed")
                confirmed = stops[arguments[2]]
                if not confirmed:
                    raise module._LauncherRefused(False, outcome="failed")
                del stops[arguments[2]]
                # Reuse the definite native stop receipt; do not issue another
                # delete. The caller still verifies its request TMP identity.
                return 0, b""
            name = request_name(arguments)
            if name is None:
                return raw_run(arguments, timeout=timeout, limit=limit,
                               allow_outcome=allow_outcome, input_bytes=input_bytes,
                               cleanup=cleanup)
            if active is not None or stops:
                raise module._LauncherRefused(False, outcome="failed")
            active = tuple(arguments)
            try:
                return raw_run(arguments, timeout=timeout, limit=limit,
                               allow_outcome=allow_outcome, input_bytes=input_bytes,
                               cleanup=cleanup)
            finally:
                active = None

    module._cleanup_child = cleanup
    module._run = run


def launcher():
    """Only the unchanged owned launch/cleanup implementation is imported."""
    if QUARANTINED.is_set():
        raise RuntimeError("compiler_unavailable")
    name = "hirewiz_tex_owned_launcher"
    if name not in sys.modules:
        try:
            _initialize_subreaper()
            specification = importlib.util.spec_from_file_location(name, "/opt/tex/owned_launcher.py")
            if specification is None or specification.loader is None:
                raise CompileRefused()
            module = importlib.util.module_from_spec(specification)
            specification.loader.exec_module(module)
            # TeX returns bounded PDF bytes; document inspection's 1 MiB JSON
            # cap stays unchanged in its separate accepted image and source.
            module.MAX_REQUEST, module.MAX_OUTPUT = MAX_REQUEST, MAX_OUTPUT
            _controller_cleanup_adapter(module)
            # Publish only after every required initializer succeeded. A failed
            # prctl/import/adapter cannot leave a raw launcher in this cache.
            sys.modules[name] = module
        except BaseException:
            QUARANTINED.set()
            raise RuntimeError("compiler_unavailable") from None
    return sys.modules[name]


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise CompileRefused()
        result[key] = value
    return result


def configuration():
    image = os.environ.get("NATIVE_TEX_IMAGE_DIGEST", "")
    policy = os.environ.get("NATIVE_TEX_ISOLATION_POLICY_SHA256", "")
    if (re.fullmatch(r"[A-Za-z0-9._:/-]+@sha256:[a-f0-9]{64}", image) is None
            or len(image) > 512 or re.fullmatch(r"[a-f0-9]{64}", policy) is None
            or POLICY_PATH.is_symlink() or hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest() != policy):
        raise CompileRefused()
    return image, policy


def checked_result(output, code, payload, image, policy):
    result = json.loads(output, object_pairs_hook=pairs)
    if code != 0:
        if code == 1 and result == {"error": "compile_refused"}:
            raise CompileRefused()
        raise RuntimeError("compiler_unavailable")
    if (type(result) is not dict or set(result) != {"protocol", "pdf", "sha256", "size", "renderer"}
            or result["protocol"] != "hirewiz-tex-v1" or result["renderer"] != "pdftex-no-shell-escape-v1"
            or type(result["size"]) is not int or not 0 < result["size"] <= MAX_PDF
            or type(result["pdf"]) is not str or type(result["sha256"]) is not str):
        raise RuntimeError("compiler_unavailable")
    pdf = base64.b64decode(result["pdf"], validate=True)
    if len(pdf) != result["size"] or not pdf.startswith(b"%PDF-") or hashlib.sha256(pdf).hexdigest() != result["sha256"]:
        raise RuntimeError("compiler_unavailable")
    return {**result, "request_sha256": hashlib.sha256(payload).hexdigest(),
            "renderer_image": image, "isolation_policy_sha256": policy}


def compile_payload(payload):
    if QUARANTINED.is_set() or type(payload) is not bytes or not 0 < len(payload) <= MAX_REQUEST:
        raise RuntimeError("compiler_unavailable")
    image, policy = configuration()
    owned = launcher()
    if not os.path.isfile(owned.SANDBOX) or not os.access(owned.SANDBOX, os.X_OK):
        raise RuntimeError("compiler_unavailable")
    # Never pass source bytes in argv/environment or mount host files. The
    # official sandbox's defaults deny network and keep its image root read-only.
    name = "document-" + uuid.uuid4().hex
    directory = Path("/tmp") / name
    identity = None
    invoked = False
    cleanup_confirmed = False
    try:
        directory.mkdir(mode=0o700)
        info = directory.lstat()
        identity = (info.st_dev, info.st_ino)
        os.chown(directory, -1, os.getgid(), follow_symlinks=False)
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or info.st_gid != os.getgid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise RuntimeError("compiler_unavailable")
        invoked = True
        code, output = owned._run([owned.SANDBOX, "do", "--sandbox-name=" + name,
            "--mount", "type=bind,source=" + str(directory) + ",destination=/tmp",
            "--", "/usr/bin/python3", "-I", "/processor.py"],
            timeout=28, limit=MAX_OUTPUT, allow_outcome=True, input_bytes=payload)
        return checked_result(output, code, payload, image, policy)
    except owned._LauncherRefused as error:
        if not error.authorized:
            invoked = False
        raise RuntimeError("compiler_unavailable") from None
    finally:
        try:
            with owned._absolute_deadline(7):
                if invoked:
                    owned._run([owned.SANDBOX, "delete", name, "--force"], timeout=5,
                               limit=65536, cleanup=True)
                if identity is not None:
                    owned._remove_request_tmp(directory, identity)
            cleanup_confirmed = True
        except BaseException:
            cleanup_confirmed = False
        if not cleanup_confirmed:
            QUARANTINED.set()
            raise RuntimeError("compiler_cleanup_unverified") from None


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)
        self._read_lock = threading.Lock()
        self._read_active = True
        self._read_deadline = time.monotonic() + 10
        self._read_timer = threading.Timer(10, self._expire_read)
        self._read_timer.daemon = True
        self._read_timer.start()

    def _expire_read(self):
        with self._read_lock:
            if self._read_active:
                self._read_active = False
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def finish_read(self):
        with self._read_lock:
            expired = not self._read_active or time.monotonic() >= self._read_deadline
            self._read_active = False
            self._read_timer.cancel()
            if expired:
                raise CompileRefused()

    def finish(self):
        with self._read_lock:
            self._read_active = False
            self._read_timer.cancel()
        super().finish()

    def log_message(self, *_args):
        pass

    def send_error(self, _code, _message=None, _explain=None):
        self.close_connection = True
        self.reply(503, {"error": "compiler_unavailable"})

    def reply(self, code, result):
        data = json.dumps(result, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            self.finish_read()
            image, policy = configuration()
            owned = launcher()
            ready = (self.path == "/ready" and not QUARANTINED.is_set()
                     and os.path.isfile(owned.SANDBOX) and os.access(owned.SANDBOX, os.X_OK))
            self.reply(200 if ready else 503, {"ready": ready, "renderer_image": image,
                                            "isolation_policy_sha256": policy})
        except Exception:
            self.reply(503, {"error": "compiler_unavailable"})

    def do_POST(self):
        acquired = False
        try:
            _, policy = configuration()
            lengths = self.headers.get_all("Content-Length", [])
            policies = self.headers.get_all("X-HireWiz-TeX-Policy", [])
            if (self.path != "/compile" or self.headers.get("Content-Type") != "application/json"
                    or len(lengths) != 1 or re.fullmatch(r"[0-9]{1,8}", lengths[0]) is None
                    or self.headers.get("Transfer-Encoding") or self.headers.get("Content-Encoding")
                    or len(policies) != 1 or not hmac.compare_digest(policies[0], policy)
                    or not 0 < int(lengths[0]) <= MAX_REQUEST or not LOCK.acquire(blocking=False)):
                raise RuntimeError("compiler_unavailable")
            acquired = True
            # A single absolute body deadline, including short/chunked writes.
            deadline = self._read_deadline
            length = int(lengths[0])
            chunks = bytearray()
            while len(chunks) < length:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                self.connection.settimeout(remaining)
                chunk = self.rfile.read1(min(65536, length - len(chunks)))
                if not chunk:
                    raise CompileRefused()
                chunks.extend(chunk)
            self.finish_read()
            self.reply(200, compile_payload(bytes(chunks)))
        except CompileRefused:
            self.reply(422, {"error": "compile_refused"})
        except Exception:
            self.reply(503, {"error": "compiler_unavailable"})
        finally:
            if acquired:
                LOCK.release()


class Server(HTTPServer):
    def handle_error(self, _request, _client_address):
        # Candidate data, compiler logs and connection failures are not logged.
        pass


if __name__ == "__main__":
    Server(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
