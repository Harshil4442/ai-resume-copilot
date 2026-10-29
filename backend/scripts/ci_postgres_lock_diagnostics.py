"""Opt-in diagnostics for the owned GitHub Actions PostgreSQL service only.

Never records SQL, parameters, URLs, environment values or process arguments.
Failure evidence is filtered in memory and encrypted before its first disk write.
This is a pytest plugin, not an application hook or a production configuration.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import selectors
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from psycopg import sql
from sqlalchemy.engine import make_url

IMAGE = "mirror.gcr.io/library/postgres:17-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24"
PUBLIC_KEY = Path(__file__).resolve().parents[2] / ".github/diagnostics/ci-lock-public.pem"
MAX_PLAINTEXT = 128 * 1024
MAX_ENVELOPE = 192 * 1024
MAX_ROWS = 24
INTERVAL = 0.25
SETTINGS = {
    "log_lock_waits": "on", "deadlock_timeout": "100ms",
    "log_parameter_max_length": "0", "log_parameter_max_length_on_error": "0",
    "log_min_error_statement": "panic",
}
SAMPLE = """SELECT a.pid,a.state,a.wait_event_type,a.wait_event,
    pg_blocking_pids(a.pid),extract(epoch FROM(clock_timestamp()-a.xact_start))*1000,
    l.locktype,l.mode,l.granted,l.database,l.relation,l.classid,l.objid,l.objsubid
    FROM pg_stat_activity a LEFT JOIN pg_locks l ON l.pid=a.pid
      AND (l.locktype='advisory' OR NOT l.granted)
    WHERE a.datname=current_database() AND a.pid<>pg_backend_pid()
      AND a.backend_type='client backend'
      AND (a.state LIKE 'idle in transaction%%' OR a.wait_event_type='Lock'
        OR clock_timestamp()-a.xact_start>interval '250 milliseconds')
    ORDER BY (a.wait_event_type='Lock') DESC NULLS LAST,a.xact_start,a.pid
    LIMIT 24"""


@dataclass(frozen=True)
class Scope:
    container: str
    port: int
    root: Path
    password: str = field(repr=False)


def scope(env: dict[str, str]) -> Scope:
    if any(env.get(k) != v for k, v in {
        "GITHUB_ACTIONS": "true", "RUNNER_OS": "Linux", "GITHUB_JOB": "backend",
    }.items()):
        raise ValueError("Diagnostics require the isolated Linux backend CI job.")
    container = env.get("CI_POSTGRES_CONTAINER_ID", "")
    run, attempt = env.get("GITHUB_RUN_ID", ""), env.get("GITHUB_RUN_ATTEMPT", "")
    if not re.fullmatch(r"[0-9a-f]{64}", container) or any(
        not re.fullmatch(r"[1-9][0-9]{0,19}", value) for value in (run, attempt)
    ):
        raise ValueError("An exact service container and run identity are required.")
    runner = Path(env.get("RUNNER_TEMP", ""))
    if (not runner.is_absolute() or not runner.is_dir() or runner.is_symlink()
            or runner.resolve(strict=True) != runner or runner.stat().st_uid != os.getuid()):
        raise ValueError("An owned canonical runner temporary directory is required.")
    url = make_url(env.get("HIREWIZ_TEST_POSTGRES_URL", ""))
    if (url.drivername != "postgresql+psycopg" or url.host != "127.0.0.1"
            or url.port not in {55433, 55434} or url.database != "hirewiz_admission_test"
            or url.username != "hirewiz" or url.password != "hirewiz" or url.query):
        raise ValueError("Only the disposable admission database is permitted.")
    return Scope(container, url.port, runner / f"hirewiz-ci-pg-lock-{run}-{attempt}", url.password)


def native(command: list[str]) -> str:
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=3)
    if len(result.stdout) > 8192:
        raise ValueError("Native service identity exceeds its bound.")
    return result.stdout.strip()


def container_identity(bound: Scope) -> str:
    template = ('{"id":{{json .Id}},"image":{{json .Config.Image}},'
                '"running":{{json .State.Running}},"ports":{{json .NetworkSettings.Ports}}}')
    value = json.loads(native(["docker", "inspect", "--type", "container", "--format",
                               template, bound.container]))
    bindings = value.get("ports", {}).get("5432/tcp", [])
    if (value.get("id") != bound.container or value.get("image") != IMAGE
            or value.get("running") is not True or not isinstance(bindings, list)
            or not any(b.get("HostIp") in {"127.0.0.1", "0.0.0.0"}
                       and b.get("HostPort") == str(bound.port) for b in bindings)):
        raise ValueError("The disposable PostgreSQL container identity does not match.")
    identity = native(["docker", "exec", bound.container, "psql", "-X", "-A", "-t",
                       "-U", "hirewiz", "-d", "postgres", "-c",
                       "SELECT system_identifier::text FROM pg_control_system()"])
    if not re.fullmatch(r"[0-9]{1,20}", identity):
        raise ValueError("The disposable PostgreSQL cluster identity is invalid.")
    return identity


def configure(connection: Any) -> dict[str, str]:
    previous = {key: connection.execute("SELECT current_setting(%s)", (key,)).fetchone()[0]
                for key in SETTINGS}
    applied: dict[str, str] = {}
    try:
        for key, value in SETTINGS.items():
            applied[key] = previous[key]
            connection.execute(sql.SQL("ALTER SYSTEM SET {} TO {}").format(
                sql.Identifier(key), sql.Literal(value)))
        if connection.execute("SELECT pg_reload_conf()").fetchone()[0] is not True:
            raise ValueError("Diagnostic settings did not reload.")
        readback(connection, SETTINGS)
        return previous
    except BaseException:
        restore(connection, applied)
        raise


def restore(connection: Any, previous: dict[str, str]) -> None:
    try:
        for key, value in previous.items():
            if key not in SETTINGS:
                raise ValueError("Unexpected diagnostic setting.")
            connection.execute(sql.SQL("ALTER SYSTEM SET {} TO {}").format(
                sql.Identifier(key), sql.Literal(value)))
    finally:
        if connection.execute("SELECT pg_reload_conf()").fetchone()[0] is not True:
            raise ValueError("Diagnostic settings did not reload.")
        readback(connection, previous)


def readback(connection: Any, expected: dict[str, str]) -> None:
    deadline = time.monotonic() + 1
    while True:
        current = {key: connection.execute("SELECT current_setting(%s)", (key,)).fetchone()[0]
                   for key in expected}
        if current == expected:
            return
        if time.monotonic() >= deadline:
            raise ValueError("Diagnostic settings readback did not match.")
        time.sleep(0.05)


def _word(value: Any) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z_]{1,80}", value) else None


def node_identity(value: str) -> dict[str, str]:
    match = re.fullmatch(r"(.*) \((setup|call|teardown)\)", value, flags=re.DOTALL)
    node, phase = (match[1], match[2]) if match else (value, "other")
    return {"node_sha256": hashlib.sha256(node.encode()).hexdigest(), "phase": phase}


def filter_rows(rows: list[Any]) -> list[dict[str, Any]]:
    result = []
    for row in rows[:MAX_ROWS]:
        if len(row) != 14 or type(row[0]) is not int or not 0 < row[0] < 2**31:
            continue
        blockers = row[4] if isinstance(row[4], list) else []
        tag = [x if type(x) in {int, bool} or x is None else None for x in row[8:14]]
        result.append({
            "pid": row[0], "state": row[1] if row[1] in {
                "active", "idle in transaction", "idle in transaction (aborted)", "idle"
            } else "other", "wait_type": _word(row[2]), "wait_event": _word(row[3]),
            "blockers": [v for v in blockers[:8] if type(v) is int and 0 < v < 2**31],
            "transaction_age_ms": min(86_400_000, max(0, int(row[5] or 0))),
            "lock_type": _word(row[6]), "lock_mode": _word(row[7]),
            "lock_granted": row[8] if type(row[8]) is bool else None,
            "lock_fingerprint": hashlib.sha256(json.dumps(tag).encode()).hexdigest(),
        })
    return result


def host_snapshot(proc: Path = Path("/proc")) -> dict[str, Any]:
    memory = {}
    try:
        for line in (proc / "meminfo").read_text()[:8192].splitlines():
            key, _, value = line.partition(":")
            if key in {"MemTotal", "MemAvailable", "SwapFree"}:
                memory[key + "_kb"] = int(value.split()[0])
    except (OSError, ValueError, IndexError):
        pass
    cpu = []
    try:
        cpu = [int(value) for value in (proc / "stat").read_text()[:4096].splitlines()[0].split()[1:11]]
    except (OSError, ValueError, IndexError):
        pass
    processes: list[dict[str, Any]] = []
    try:
        entries = sorted((p for p in proc.iterdir() if p.name.isdecimal()), key=lambda p: int(p.name))
    except OSError:
        entries = []
    for path in entries[:4096]:
        try:
            fields = (path / "stat").read_text()[:4096].rsplit(") ", 1)[1].split()
            processes.append({"pid": int(path.name), "ppid": int(fields[1]),
                              "state": fields[0] if fields[0] in "RSDZTtXxKWPI" else "?",
                              "cpu_ticks": int(fields[11]) + int(fields[12]),
                              "rss_pages": int(fields[21])})
        except (OSError, ValueError, IndexError):
            continue
    return {"cpu_count": os.cpu_count(), "cpu_ticks": cpu,
            "load_average": list(os.getloadavg()), "memory": memory,
            "process_count": len(entries), "process_scan_truncated": len(entries) > 4096,
            "top_cpu_processes": sorted(processes, key=lambda p: p["cpu_ticks"], reverse=True)[:8]}


def server_waits(container: str) -> list[dict[str, Any]]:
    """Keep numeric server-side holder evidence even if Python's GIL stalls."""
    command = ["docker", "logs", "--tail", "64", container]
    raw = bytearray()
    deadline = time.monotonic() + 3
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as process:
        assert process.stdout is not None
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while len(raw) < 32 * 1024 and time.monotonic() < deadline:
                    if not selector.select(max(0, deadline - time.monotonic())):
                        break
                    part = os.read(process.stdout.fileno(), min(4096, 32 * 1024 - len(raw)))
                    if not part:
                        break
                    raw.extend(part)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=1)
    result = []
    for line in raw.decode("utf-8", "replace").splitlines():
        prefix = re.search(r"\[([0-9]{1,10})\]\s+(LOG|DETAIL):", line)
        if not prefix:
            continue
        holder = re.search(r"Process holding the lock: ([0-9]+)\. Wait queue: ([0-9, ]+)\.", line)
        wait = re.search(r"process ([0-9]+) still waiting for [A-Za-z]+Lock .* after ([0-9.]+) ms", line)
        if holder:
            result.append({"pid": int(prefix[1]), "holder_pid": int(holder[1]),
                           "waiters": [int(v) for v in holder[2].split(",")[:8]]})
        elif wait:
            result.append({"pid": int(wait[1]), "wait_ms": min(86_400_000, int(float(wait[2])))})
    return result[-32:]


def bounded_payload(value: dict[str, Any]) -> bytes:
    while True:
        raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
        if len(raw) <= MAX_PLAINTEXT:
            return raw
        samples = value["samples"]
        if samples:
            del samples[:max(1, len(samples) // 4)]
            value["truncated"] = True
        else:
            raise ValueError("Filtered diagnostics exceed the evidence bound.")


def encrypt(raw: bytes, public_pem: bytes) -> bytes:
    if len(raw) > MAX_PLAINTEXT or len(public_pem) > 8192:
        raise ValueError("Diagnostic encryption inputs exceed their bound.")
    key = serialization.load_pem_public_key(public_pem)
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 3072:
        raise ValueError("An RSA diagnostic public key of at least 3072 bits is required.")
    symmetric, nonce = AESGCM.generate_key(bit_length=256), os.urandom(12)
    label = b"hirewiz-ci-lock-diagnostics-v1"
    envelope = {"format": label.decode(), "key_sha256": hashlib.sha256(public_pem).hexdigest(),
                "wrapped_key": base64.b64encode(key.encrypt(symmetric, padding.OAEP(
                    mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=label))).decode(),
                "nonce": base64.b64encode(nonce).decode(),
                "ciphertext": base64.b64encode(AESGCM(symmetric).encrypt(nonce, raw, label)).decode()}
    result = json.dumps(envelope, separators=(",", ":")).encode()
    if len(result) > MAX_ENVELOPE:
        raise ValueError("Encrypted diagnostics exceed their bound.")
    return result


class Diagnostics:
    def __init__(self, bound: Scope):
        self.bound = bound
        self.connection: Any = None
        self.previous: dict[str, str] = {}
        self.samples: deque[dict[str, Any]] = deque(maxlen=128)
        self.failed_samples: list[dict[str, Any]] | None = None
        self.failures: deque[dict[str, str]] = deque(maxlen=16)
        self.server_events: deque[dict[str, Any]] = deque(maxlen=32)
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.errors = 0

    def start(self) -> None:
        identity = container_identity(self.bound)
        connection = psycopg.connect(host="127.0.0.1", port=self.bound.port, user="hirewiz",
            password=self.bound.password, dbname="hirewiz_admission_test", autocommit=True,
            connect_timeout=2, application_name="hirewiz_ci_lock_diagnostics",
            options="-cstatement_timeout=250 -clock_timeout=100")
        self.connection = connection
        try:
            server = connection.execute("SELECT system_identifier::text FROM pg_control_system()").fetchone()[0]
            version = int(connection.execute("SELECT current_setting('server_version_num')").fetchone()[0])
            if server != identity or not 170000 <= version < 180000:
                raise ValueError("The socket is not the isolated PostgreSQL 17 CI service.")
            self.previous = configure(connection)
            self.thread = threading.Thread(target=self._sample, name="ci-pg-lock-diagnostics", daemon=True)
            self.thread.start()
        except BaseException:
            self.close()
            raise

    def _sample(self) -> None:
        counter = 0
        while not self.stop.wait(INTERVAL):
            try:
                rows = filter_rows(self.connection.execute(SAMPLE).fetchall())
                if rows or counter % 8 == 0:
                    sample: dict[str, Any] = {"monotonic_ms": int(time.monotonic() * 1000),
                        **node_identity(os.environ.get("PYTEST_CURRENT_TEST", "")), "activity": rows}
                    if counter % 8 == 0:
                        sample["host"] = host_snapshot()
                    self.samples.append(sample)
                counter += 1
            except Exception:
                self.errors += 1

    def failed(self, node: str, phase: str) -> None:
        if self.failed_samples is None:
            self.failed_samples = list(self.samples)
        self.failures.append({"node_sha256": hashlib.sha256(node.encode()).hexdigest(),
                              "phase": phase if phase in {"setup", "call", "teardown"} else "other"})
        try:
            self.server_events.extend(server_waits(self.bound.container))
        except Exception:
            self.errors += 1

    def close(self) -> None:
        self.stop.set()
        try:
            if self.thread is not None:
                self.thread.join(timeout=2)
                if self.thread.is_alive():
                    self.connection.cancel_safe(timeout=0.5)
                    self.thread.join(timeout=1)
                if self.thread.is_alive():
                    raise RuntimeError("The diagnostic sampler did not stop.")
            if self.connection is not None:
                if self.previous:
                    restore(self.connection, self.previous)
        finally:
            if self.connection is not None:
                self.connection.close()
                self.connection = None
            if self.thread is not None and self.thread.is_alive():
                self.thread.join(timeout=1)

    def retain(self) -> Path:
        window = self.failed_samples if self.failed_samples is not None else self.samples
        raw = bounded_payload({"format": "hirewiz-ci-lock-diagnostics-v1", "samples": list(window),
            "failures": list(self.failures), "sampling_errors": min(self.errors, 1_000_000),
            "server_wait_events": list(self.server_events),
            "host_final": host_snapshot(), "truncated": False})
        sealed = encrypt(raw, PUBLIC_KEY.read_bytes())
        root = self.bound.root
        if root.exists() and (root.is_symlink() or not root.is_dir()
                              or root.resolve() != root or root.stat().st_uid != os.getuid()):
            raise ValueError("The evidence directory is not owned and canonical.")
        root.mkdir(mode=0o700, exist_ok=True)
        root.chmod(0o700)
        target = root / "failure.enc"
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(sealed)
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return target


_active: Diagnostics | None = None


def pytest_configure(config: Any) -> None:
    global _active
    if os.environ.get("HIREWIZ_CI_LOCK_DIAGNOSTICS") != "1":
        return
    try:
        candidate = Diagnostics(scope(dict(os.environ)))
        candidate.start()
        _active = candidate
    except Exception:
        print("CI PostgreSQL lock diagnostics unavailable; test behavior is unchanged.")


def pytest_runtest_logreport(report: Any) -> None:
    if _active is not None and report.failed:
        _active.failed(report.nodeid, report.when)


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    global _active
    active, _active = _active, None
    if active is None:
        return
    try:
        active.close()
    except Exception:
        print("CI PostgreSQL diagnostic cleanup failed; test result is unchanged.")
    if exitstatus:
        try:
            active.retain()
            print("Encrypted CI PostgreSQL diagnostic evidence saved.")
        except Exception:
            print("CI PostgreSQL diagnostic encryption failed; no plaintext artifact was saved.")


def pytest_unconfigure(config: Any) -> None:
    # A later plugin can abort configuration before sessionfinish is reached.
    # Preserve cleanup and encrypted evidence without changing pytest's result.
    if _active is not None:
        pytest_sessionfinish(None, 3)
