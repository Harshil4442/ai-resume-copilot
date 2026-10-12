"""Secretless PDF/DOCX inspection, invoked only inside the Cloud Run sandbox.

ClamAV must accept the exact original before any document parser sees it. This
module provides no host-execution fallback, database or optional model work.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import math
import os
import re
import resource
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile

MAX_FILE = 5 * 1024 * 1024
MAX_REQUEST = 7 * 1024 * 1024
MAX_OUTPUT = 1024 * 1024
DATABASE = "/var/lib/clamav"
ERROR = "document_inspection_refused"
UNAVAILABLE = "document_inspection_unavailable"
DOCX_ERROR = "docx_layout_refused"
DOCX_UNAVAILABLE = "docx_layout_unavailable"


class InspectionDenied(ValueError):
    def __init__(self):
        super().__init__(ERROR)


class InspectionUnavailable(ValueError):
    def __init__(self):
        super().__init__(UNAVAILABLE)


class DocxLayoutDenied(ValueError):
    def __init__(self, reason: str, unit_id: str | None = None):
        self.reason = reason
        self.unit_id = unit_id
        super().__init__(DOCX_ERROR)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise InspectionUnavailable()
        result[key] = value
    return result


def decode_request(payload: bytes) -> tuple[str, bytes, str]:
    try:
        if not 0 < len(payload) <= MAX_REQUEST:
            raise InspectionUnavailable()
        value = json.loads(payload, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(InspectionUnavailable()))
        if (type(value) is not dict or set(value) != {"version", "source_format", "content_base64", "sha256"}
                or type(value["version"]) is not int or value["version"] != 1
                or value["source_format"] not in {"pdf", "docx"}
                or not isinstance(value["sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"])
                or not isinstance(value["content_base64"], str)):
            raise InspectionUnavailable()
        content = base64.b64decode(value["content_base64"], validate=True)
        if not 0 < len(content) <= MAX_FILE:
            raise InspectionDenied()
        if hashlib.sha256(content).hexdigest() != value["sha256"]:
            raise InspectionUnavailable()
        return value["source_format"], content, value["sha256"]
    except InspectionDenied:
        raise
    except Exception:
        raise InspectionUnavailable() from None


def _command(arguments: list[str], timeout: int, *, allow_detection: bool = False,
             deadline: float | None = None) -> tuple[int, str]:
    try:
        command_timeout: int | float = timeout
        if deadline is not None:
            if type(deadline) not in {int, float} or not math.isfinite(deadline):
                raise InspectionUnavailable()
            remaining = deadline - time.monotonic()
            if not math.isfinite(remaining) or remaining <= 0:
                raise InspectionUnavailable()
            command_timeout = min(timeout, remaining)
        result = subprocess.run(arguments, stdin=subprocess.DEVNULL, capture_output=True,
            timeout=command_timeout, check=False, env={"PATH": "/usr/bin:/bin", "LANG": "C", "TZ": "UTC",
                                             "HOME": "/nonexistent"})
        if deadline is not None and time.monotonic() >= deadline:
            raise InspectionUnavailable()
        if (len(result.stdout) > 65536 or result.stderr
                or result.returncode not in ((0, 1) if allow_detection else (0,))):
            raise InspectionUnavailable()
        return result.returncode, result.stdout.decode("ascii", errors="strict")
    except Exception:
        raise InspectionUnavailable() from None


def scan_original(path: Path, *, now: datetime | None = None, deadline: float | None = None) -> dict:
    """Require fresh definitions and a coherent exact-one-file scanner outcome.

    Genuine detection is a permanent document refusal. Any missing, stale,
    incomplete, inconsistent or errored scan remains service unavailability.
    Scanner names/output are private and never cross the processor protocol.
    """
    version_arguments = ["/usr/bin/clamscan", "--database=" + DATABASE, "--version"]
    _, version = (_command(version_arguments, 5) if deadline is None
                  else _command(version_arguments, 5, deadline=deadline))
    match = re.fullmatch(r"ClamAV ([0-9]+\.[0-9]+\.[0-9]+(?:[.-][A-Za-z0-9]+)?)/([0-9]{1,10})/([A-Za-z0-9 :]{24})", version.strip())
    if not match:
        raise InspectionUnavailable()
    try:
        issued = datetime.strptime(match[3], "%a %b %d %H:%M:%S %Y").replace(tzinfo=UTC)
        current = now or datetime.now(UTC)
        if not timedelta(0) <= current - issued <= timedelta(days=3):
            raise InspectionUnavailable()
    except Exception:
        raise InspectionUnavailable() from None
    scan_arguments = ["/usr/bin/clamscan", "--stdout", "--database=" + DATABASE,
        "--official-db-only=yes", "--fail-if-cvd-older-than=3", "--disable-cache",
        "--alert-encrypted=yes", "--alert-exceeds-max=yes", "--heuristic-alerts=yes",
        "--scan-archive=yes", "--scan-pdf=yes", "--scan-ole2=yes",
        "--max-filesize=6M", "--max-scansize=20M", "--max-files=500",
        "--max-recursion=16", "--max-scantime=20000", "--bytecode-timeout=1000",
        "--tempdir=" + str(path.parent), "--", str(path)]
    code, output = (_command(scan_arguments, 30, allow_detection=True) if deadline is None
                    else _command(scan_arguments, 30, allow_detection=True, deadline=deadline))
    clean_lines = [line for line in output.splitlines() if line.endswith(": OK")]
    found_lines = [line for line in output.splitlines() if line.endswith(" FOUND")]
    def number(name: str) -> int:
        values = re.findall(r"^" + re.escape(name) + r": ([0-9]+)$", output, re.M)
        if len(values) != 1:
            raise InspectionUnavailable()
        return int(values[0])
    if (number("Scanned files") != 1 or number("Scanned directories") != 0
            or number("Known viruses") < 1
            or re.findall(r"^Engine version: (.+)$", output, re.M) != [match[1]]
            or re.search(r"\b(?:ERROR|WARNING|skipped)\b", output, re.I)
            or ("Total errors:" in output and number("Total errors") != 0)):
        raise InspectionUnavailable()
    if code == 1:
        if (clean_lines or number("Infected files") != 1 or len(found_lines) != 1
                or not re.fullmatch(re.escape(str(path)) + r": [A-Za-z0-9][A-Za-z0-9._-]{0,199} FOUND", found_lines[0])):
            raise InspectionUnavailable()
        raise InspectionDenied()
    if (code != 0 or clean_lines != [str(path) + ": OK"] or found_lines
            or number("Infected files") != 0 or re.search(r"\bFOUND\b", output, re.I)):
        raise InspectionUnavailable()
    return {"engine": "ClamAV", "version": match[1], "definitions": match[2] + "/" + issued.isoformat()}


# Inserted into processor.py; this fragment is never another runtime module.
CLAMD = "/usr/sbin/clamd"
CLAMD_MANIFEST = Path("/opt/document/clamd-native-manifest.json")
CLAMD_ENGINE = "1.4.3"
CLAMD_SETTINGS = {
    "Foreground": "yes", "LocalSocketMode": "600", "FixStaleSocket": "no",
    "DatabaseDirectory": DATABASE, "OfficialDatabaseOnly": "yes", "FailIfCvdOlderThan": "3",
    "DisableCache": "yes", "AlertEncrypted": "yes", "AlertExceedsMax": "yes",
    "HeuristicAlerts": "yes", "ScanArchive": "yes", "ScanPDF": "yes", "ScanOLE2": "yes",
    "MaxFileSize": "6M", "MaxScanSize": "20M", "MaxFiles": "500", "MaxRecursion": "16",
    "MaxScanTime": "20000", "BytecodeTimeout": "1000", "StreamMaxLength": "5M",
    "MaxThreads": "1", "MaxQueue": "2", "MaxConnectionQueueLength": "2",
    "ReadTimeout": "30", "CommandReadTimeout": "5", "SendBufTimeout": "200",
    "SelfCheck": "0", "ConcurrentDatabaseReload": "no", "ExitOnOOM": "yes",
    "LogClean": "no", "LogVerbose": "no", "LogSyslog": "no", "ExtendedDetectionInfo": "no",
    "LogFileMaxSize": "0", "LogRotate": "no", "LogTime": "no",
    "Debug": "no", "LeaveTemporaryFiles": "no", "GenerateMetadataJson": "no",
    "AllowAllMatchScan": "no", "Bytecode": "yes", "BytecodeSecurity": "TrustSigned",
    "BytecodeUnsigned": "no", "ScanXMLDOCS": "yes", "ScanPE": "yes", "ScanELF": "yes",
    "ScanSWF": "yes", "ScanHWP3": "yes", "ScanOneNote": "yes", "ScanImage": "yes",
    "ScanImageFuzzyHash": "yes", "ScanMail": "yes", "ScanPartialMessages": "no",
    "ScanHTML": "yes", "PhishingSignatures": "yes", "PhishingScanURLs": "yes",
    "HeuristicScanPrecedence": "no", "DetectPUA": "no", "StructuredDataDetection": "no",
}


def _native_file(path: Path, deadline: float | None = None) -> dict:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 20 <= info.st_size <= 128 * 1024**2:
            raise InspectionUnavailable()
        header = os.read(descriptor, 20)
        machine = int.from_bytes(header[18:20], "little")
        if header[:7] != b"\x7fELF\x02\x01\x01" or machine not in {62, 183}:
            raise InspectionUnavailable()
        digest = hashlib.sha256(header)
        count = len(header)
        while block := os.read(descriptor, 65536):
            if deadline is not None:
                OwnedClamd._remaining(deadline)
            count += len(block)
            if count > info.st_size:
                raise InspectionUnavailable()
            digest.update(block)
        if count != info.st_size:
            raise InspectionUnavailable()
        return {"path": str(path), "sha256": digest.hexdigest(), "size": count, "elf_machine": machine}
    finally:
        os.close(descriptor)


def _installed_clamd() -> dict:
    # Real installed files, not source labels or caller-supplied paths.
    libraries = {p.resolve(strict=True) for p in Path("/usr/lib").glob("*/libclamav.so.*")}
    if len(libraries) != 1:
        raise InspectionUnavailable()
    paths = [Path(CLAMD).resolve(strict=True), Path("/usr/bin/clamscan").resolve(strict=True), *libraries]
    files = [_native_file(p) for p in paths]
    if len({row["elf_machine"] for row in files}) != 1:
        raise InspectionUnavailable()
    _, version = _command([CLAMD, "--version"], 5)
    match = re.fullmatch(r"ClamAV ([0-9]+\.[0-9]+\.[0-9]+)(?:/[0-9]+/[A-Za-z0-9 :]{24})?", version.strip())
    if not match or match[1] != CLAMD_ENGINE:
        raise InspectionUnavailable()
    return {"version": 1, "engine": CLAMD_ENGINE, "files": files}


def write_clamd_native_manifest() -> None:
    """Explicit image-build acquisition; never called while inspecting input."""
    if os.getuid() != 0 or CLAMD_MANIFEST.exists():
        raise InspectionUnavailable()
    raw = json.dumps(_installed_clamd(), sort_keys=True, separators=(",", ":")).encode()
    descriptor = os.open(CLAMD_MANIFEST, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    CLAMD_MANIFEST.chmod(0o444)


def _clamd_definition(deadline: float, now: datetime | None = None) -> tuple[dict, str]:
    _, version = _command(["/usr/bin/clamscan", "--database=" + DATABASE, "--version"], 5,
                          deadline=deadline)
    match = re.fullmatch(r"ClamAV ([0-9]+\.[0-9]+\.[0-9]+(?:[.-][A-Za-z0-9]+)?)/([0-9]{1,10})/([A-Za-z0-9 :]{24})", version.strip())
    if not match or match[1] != CLAMD_ENGINE:
        raise InspectionUnavailable()
    try:
        issued = datetime.strptime(match[3], "%a %b %d %H:%M:%S %Y").replace(tzinfo=UTC)
        if not timedelta(0) <= (now or datetime.now(UTC)) - issued <= timedelta(days=3):
            raise InspectionUnavailable()
    except Exception:
        raise InspectionUnavailable() from None
    return {"engine": "ClamAV", "version": match[1], "definitions": match[2] + "/" + issued.isoformat()}, version.strip()


class OwnedClamd:
    """One foreground engine for exactly this request's two sequential scans."""
    def __init__(self, directory: Path, deadline: float):
        self.directory, self.deadline = directory, deadline
        self.child = None
        self.pipes = {}
        self.signatures = []
        self.scan_bindings = []
        self.stopped = False
        self.forced = False
        self.manifest = None
        self.peer = None
        self.log_fd = None
        self.log_identity = None
        self.log_path = directory / "clamd.log"
        self.socket_path = directory / "clamd.sock"
        self.config_path = directory / "clamd.conf"

    def __enter__(self):
        self._remaining(self.deadline)
        return self

    @staticmethod
    def _remaining(deadline):
        if type(deadline) not in {int, float} or not math.isfinite(deadline):
            raise InspectionUnavailable()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise InspectionUnavailable()
        return remaining

    def _diagnostics(self):
        allowed = {
            "Received 0 file descriptor(s) from systemd.", "Not loading PUA signatures.",
            "Only loading official signatures.", 'Bytecode: Security mode set to "TrustSigned".',
            "Reading databases from " + DATABASE, "Self checking disabled.", "Socket file removed.",
            "Log file size limit disabled.",
            "LOCAL: Unix socket file " + str(self.socket_path),
            "LOCAL: Setting connection queue length to " + CLAMD_SETTINGS["MaxConnectionQueueLength"],
            "Alerting of encrypted archives _and_ documents enabled.",
            "Archive support enabled.", "Image (graphics) scanning support enabled.",
            "Detection using image fuzzy hash enabled.", "Alerting of encrypted archives enabled.",
            "Alerting of encrypted documents enabled.", "Heuristic alerts enabled.",
            "Heuristic alerting enabled for scans that exceed set maximums.",
            "Portable Executable support enabled.", "ELF support enabled.", "Mail files support enabled.",
            "OLE2 support enabled.", "PDF support enabled.", "SWF support enabled.", "HTML support enabled.",
            "XMLDOCS support enabled.", "HWP3 support enabled.", "OneNote support enabled.",
            "Limits: Global time limit set to 20000 milliseconds.",
            "Limits: Global size limit set to 20971520 bytes.", "Limits: File size limit set to 6291456 bytes.",
            "Limits: Recursion level limit set to 16.", "Limits: Files limit set to 500.",
        }
        self.signatures = []
        records = [(name, bytes(state["raw"]), state["eof"]) for name, state in self.pipes.items()]
        if self.log_fd is not None:
            os.lseek(self.log_fd, 0, os.SEEK_SET)
            info = os.fstat(self.log_fd)
            current = self.log_path.lstat()
            if ((info.st_dev, info.st_ino) != self.log_identity
                    or (current.st_dev, current.st_ino) != self.log_identity
                    or not stat.S_ISREG(current.st_mode) or stat.S_IMODE(current.st_mode) != 0o600
                    or current.st_uid != os.getuid() or current.st_gid != os.getgid()
                    or info.st_size > 65536):
                raise InspectionUnavailable()
            log = os.read(self.log_fd, 65537)
            if len(log) > 65536:
                raise InspectionUnavailable()
            records.append(("log", log, self.child is not None and self.child.poll() is not None))
        for name, raw, final in records:
            if name == "stderr" and raw:
                raise InspectionUnavailable()
            if raw and not raw.endswith(b"\n") and final:
                raise InspectionUnavailable()
            lines = raw.splitlines()
            if raw and not raw.endswith(b"\n"):
                lines = lines[:-1]
            for line in lines:
                value = line.decode("ascii", errors="strict")
                if re.search(r"\b(?:ERROR|WARNING|skipped)\b", value, re.I):
                    raise InspectionUnavailable()
                if value in allowed:
                    continue
                loaded = re.fullmatch(r"Loaded ([1-9][0-9]{0,8}) signatures\.", value)
                if loaded:
                    self.signatures.append(int(loaded[1]))
                    continue
                if (re.fullmatch(r"clamd daemon 1\.4\.3 \(OS: [A-Za-z0-9_.-]+, ARCH: [A-Za-z0-9_.-]+, CPU: [A-Za-z0-9_.-]+\)", value)
                        or re.fullmatch(r"Limits: (?:MaxEmbeddedPE|MaxHTMLNormalize|MaxHTMLNoTags|MaxScriptNormalize|MaxZipTypeRcg) limit set to [0-9]+ bytes\.", value)
                        or re.fullmatch(r"Limits: (?:MaxPartitions|MaxIconsPE|MaxRecHWP3|PCREMatchLimit|PCRERecMatchLimit|PCREMaxFileSize) limit set to [0-9]+\.", value)
                        or re.fullmatch(r"(?:\+\+\+ Started|--- Stopped) at [A-Za-z0-9 :]{24}", value)
                        or re.fullmatch(r"instream\(local\): [A-Za-z0-9][A-Za-z0-9._-]{0,199} FOUND", value)):
                    continue
                raise InspectionUnavailable()
        if len(self.signatures) > 1:
            raise InspectionUnavailable()

    def _drain(self, wait=0):
        active = [state["stream"] for state in self.pipes.values() if not state["eof"]]
        ready, _, _ = select.select(active, [], [], wait) if active else ([], [], [])
        for stream in ready:
            state = next(value for value in self.pipes.values() if value["stream"] is stream)
            block = os.read(stream.fileno(), 4096)
            if not block:
                state["eof"] = True
            else:
                state["raw"].extend(block)
                if len(state["raw"]) > 65536:
                    raise InspectionUnavailable()
        self._diagnostics()

    def _socket(self, deadline):
        metadata = self.socket_path.lstat()
        if (not stat.S_ISSOCK(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.getuid() or metadata.st_gid != os.getgid()
                or self.child.poll() is not None):
            raise InspectionUnavailable()
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.settimeout(self._remaining(deadline))
            connection.connect(str(self.socket_path))
            pid, uid, gid = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if (pid != self.child.pid or uid != os.getuid() or gid != os.getgid()
                    or os.getpgid(pid) != pid or self.child.poll() is not None):
                raise InspectionUnavailable()
            self.peer = {"pid": pid, "uid": uid, "gid": gid}
            connection.setblocking(False)
            return connection
        except BaseException:
            connection.close()
            raise

    def _exchange(self, parts, deadline, limit):
        with self._socket(deadline) as connection:
            index, offset, reply = 0, 0, bytearray()
            while True:
                self._drain()
                if self.child.poll() is not None:
                    raise InspectionUnavailable()
                readable, writable, _ = select.select([connection], [connection] if index < len(parts) else [], [],
                                                      min(0.05, self._remaining(deadline)))
                if readable:
                    block = connection.recv(limit + 1)
                    if index < len(parts):
                        raise InspectionUnavailable()
                    if not block:
                        self._drain()
                        self._remaining(deadline)
                        return bytes(reply)
                    reply.extend(block)
                    if len(reply) > limit:
                        raise InspectionUnavailable()
                if writable:
                    count = connection.send(memoryview(parts[index])[offset:])
                    if count <= 0:
                        raise InspectionUnavailable()
                    offset += count
                    if offset == len(parts[index]):
                        index, offset = index + 1, 0

    def _start(self, deadline):
        # The generated installation manifest is a read-only native build file.
        descriptor = os.open(CLAMD_MANIFEST, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o222 or not 0 < info.st_size <= 16384:
                raise InspectionUnavailable()
            raw = os.read(descriptor, 16385)
            if len(raw) != info.st_size:
                raise InspectionUnavailable()
            manifest = json.loads(raw, object_pairs_hook=_pairs)
        finally:
            os.close(descriptor)
        if (type(manifest) is not dict or set(manifest) != {"version", "engine", "files"}
                or type(manifest["version"]) is not int or manifest["version"] != 1
                or manifest["engine"] != CLAMD_ENGINE):
            raise InspectionUnavailable()
        if type(manifest["files"]) is not list or len(manifest["files"]) != 3:
            raise InspectionUnavailable()
        for row in manifest["files"]:
            if type(row) is not dict or set(row) != {"path", "sha256", "size", "elf_machine"}:
                raise InspectionUnavailable()
            if _native_file(Path(row["path"]), deadline) != row:
                raise InspectionUnavailable()
        if [row["path"] for row in manifest["files"][:2]] != [str(Path(CLAMD).resolve(strict=True)), str(Path("/usr/bin/clamscan").resolve(strict=True))]:
            raise InspectionUnavailable()
        self.manifest = manifest
        parent = self.directory.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or stat.S_IMODE(parent.st_mode) != 0o700
                or parent.st_uid != os.getuid() or parent.st_gid != os.getgid()):
            raise InspectionUnavailable()
        temporary = self.directory / "clamd-tmp"
        temporary.mkdir(mode=0o700)
        if self.socket_path.exists():
            raise InspectionUnavailable()
        log_write = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(log_write)
        self.log_fd = os.open(self.log_path, os.O_RDONLY | os.O_NOFOLLOW)
        log_stat = os.fstat(self.log_fd)
        self.log_identity = (log_stat.st_dev, log_stat.st_ino)
        config = {**CLAMD_SETTINGS, "LocalSocket": str(self.socket_path), "TemporaryDirectory": str(temporary),
                  "LogFile": str(self.log_path)}
        content = "".join(key + " " + value + "\n" for key, value in config.items()).encode("ascii")
        descriptor = os.open(self.config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        self._remaining(deadline)
        self.child = subprocess.Popen([CLAMD, "--config-file=" + str(self.config_path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "TZ": "UTC", "HOME": "/nonexistent"},
            cwd=self.directory, start_new_session=True)
        for name in ("stdout", "stderr"):
            stream = getattr(self.child, name)
            os.set_blocking(stream.fileno(), False)
            self.pipes[name] = {"stream": stream, "raw": bytearray(), "eof": False}
        while not self.socket_path.exists() or not self.signatures:
            if self.child.poll() is not None:
                raise InspectionUnavailable()
            self._drain(min(0.05, self._remaining(deadline)))
        executable = Path("/proc/" + str(self.child.pid) + "/exe").resolve(strict=True)
        if str(executable) != manifest["files"][0]["path"] or _native_file(executable, deadline) != manifest["files"][0]:
            raise InspectionUnavailable()
        descriptor = os.open("/proc/" + str(self.child.pid) + "/maps", os.O_RDONLY | os.O_NOFOLLOW)
        try:
            raw = os.read(descriptor, 1048577)
            if len(raw) > 1048576:
                raise InspectionUnavailable()
            maps = raw.decode("ascii", errors="strict")
        finally:
            os.close(descriptor)
        self._remaining(deadline)
        libraries = {line.split(maxsplit=5)[5] for line in maps.splitlines()
                     if len(line.split(maxsplit=5)) == 6 and "libclamav.so." in line.split(maxsplit=5)[5]}
        if libraries != {manifest["files"][2]["path"]}:
            raise InspectionUnavailable()

    def scan(self, path: Path, content: bytes, *, now: datetime | None = None) -> dict:
        try:
            if self.stopped or len(self.scan_bindings) >= 2 or type(content) is not bytes or not 0 < len(content) <= MAX_FILE:
                raise InspectionUnavailable()
            file_metadata = path.lstat()
            if (not stat.S_ISREG(file_metadata.st_mode) or stat.S_IMODE(file_metadata.st_mode) != 0o600
                    or file_metadata.st_uid != os.getuid() or file_metadata.st_size != len(content)
                    or path.parent != self.directory):
                raise InspectionUnavailable()
            deadline = min(self.deadline, time.monotonic() + 30)
            metadata, header = _clamd_definition(deadline, now)
            if self.child is None:
                self._start(deadline)
            version = self._exchange([b"zVERSION\0"], min(deadline, time.monotonic() + 5), 256)
            if version != header.encode("ascii") + b"\0":
                raise InspectionUnavailable()
            if path.is_symlink() or path.read_bytes() != content:
                raise InspectionUnavailable()
            chunks = [b"zINSTREAM\0"]
            view = memoryview(content)
            for offset in range(0, len(content), 65536):
                block = view[offset:offset + 65536]
                chunks.extend((struct.pack(">I", len(block)), block))
            chunks.append(b"\0\0\0\0")
            result = self._exchange(chunks, deadline, 512)
            if path.read_bytes() != content or len(self.signatures) != 1:
                raise InspectionUnavailable()
            binding = {"sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content),
                       "metadata": metadata}
            if result == b"stream: OK\0":
                self.scan_bindings.append({**binding, "result": "CL_CLEAN"})
                return metadata
            if re.fullmatch(rb"stream: [A-Za-z0-9][A-Za-z0-9._-]{0,199} FOUND\x00", result):
                self.scan_bindings.append({**binding, "result": "CL_VIRUS"})
                raise InspectionDenied()
            raise InspectionUnavailable()
        except InspectionDenied:
            raise
        except Exception:
            raise InspectionUnavailable() from None

    def close(self):
        if self.stopped:
            return
        stop_deadline = time.monotonic() + 2
        grace_deadline = min(stop_deadline, self.deadline, time.monotonic() + 0.5)
        try:
            if self.child is not None:
                if self.child.poll() is not None or os.getpgid(self.child.pid) != self.child.pid:
                    raise InspectionUnavailable()
                os.killpg(self.child.pid, signal.SIGTERM)
                while self.child.poll() is None and time.monotonic() < grace_deadline:
                    self._drain(0.01)
                if self.child.poll() is None:
                    self.forced = True
                    os.killpg(self.child.pid, signal.SIGKILL)
                code = self.child.wait(timeout=max(0.001, stop_deadline - time.monotonic()))
                while not all(state["eof"] for state in self.pipes.values()):
                    self._drain(min(0.01, self._remaining(stop_deadline)))
                # Even when pipe EOF was seen during the grace loop, the native
                # LogFile may receive a final warning before terminal wait.
                self._diagnostics()
                if code != 0 or self.forced:
                    raise InspectionUnavailable()
                try:
                    os.killpg(self.child.pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    raise InspectionUnavailable()
            self.stopped = True
        except Exception:
            raise InspectionUnavailable() from None
        finally:
            if self.child is not None and self.child.poll() is None:
                try:
                    if os.getpgid(self.child.pid) == self.child.pid:
                        os.killpg(self.child.pid, signal.SIGKILL)
                        self.child.wait(timeout=max(0.001, stop_deadline - time.monotonic()))
                except Exception:
                    pass
            for state in self.pipes.values():
                state["stream"].close()
            if self.log_fd is not None:
                os.close(self.log_fd)
                self.log_fd = None
            # Only request-owned native diagnostics, never exported or retained.
            if self.log_identity is not None:
                self.log_path.unlink(missing_ok=True)

    def __exit__(self, kind, error, traceback):
        self.close()
        if kind is None:
            self._remaining(self.deadline)
        return False


def validate_pdf(content: bytes) -> None:
    """Bounded reachable action graph, aligned with existing native-TeX guards."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject
    try:
        if not content.startswith(b"%PDF-"):
            raise InspectionDenied()
        reader = PdfReader(io.BytesIO(content), strict=True)
        if reader.is_encrypted or not 0 < len(reader.pages) <= 20:
            raise InspectionDenied()
        active_keys = {"/AA", "/AcroForm", "/JavaScript", "/JS", "/EmbeddedFiles", "/EF", "/AF",
                       "/RichMediaContent", "/RichMediaSettings", "/XFA"}
        active_actions = {"/JavaScript", "/Launch", "/GoToR", "/GoToE", "/SubmitForm", "/ResetForm",
            "/ImportData", "/Rendition", "/Movie", "/Sound", "/Hide", "/Named", "/SetOCGState",
            "/Trans", "/GoTo3DView"}
        def passive(action) -> bool:
            if isinstance(action, IndirectObject):
                action = action.get_object()
            if not isinstance(action, DictionaryObject):
                return False
            if action.get("/S") == "/GoTo":
                return "/D" in action and set(action) <= {"/S", "/D", "/Type"}
            if action.get("/S") != "/URI" or set(action) - {"/S", "/URI", "/Type"}:
                return False
            uri = action.get("/URI")
            if not isinstance(uri, str) or len(uri) > 2048 or any(ord(c) < 32 for c in uri):
                return False
            parsed = urlsplit(uri)
            return (parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)
                    and not parsed.username and not parsed.password) or (parsed.scheme.lower() == "mailto" and bool(parsed.path))
        pending = [(reader.trailer, 0)]
        seen_objects: set[int] = set()
        seen_references: set[tuple[int, int]] = set()
        visited = 0
        while pending:
            obj, depth = pending.pop()
            if depth > 128 or visited > 20000:
                raise InspectionDenied()
            if isinstance(obj, IndirectObject):
                identity = (obj.idnum, obj.generation)
                if identity in seen_references:
                    continue
                seen_references.add(identity)
                obj = obj.get_object()
            if not isinstance(obj, (ArrayObject, DictionaryObject)) or id(obj) in seen_objects:
                continue
            seen_objects.add(id(obj))
            visited += 1
            if isinstance(obj, DictionaryObject):
                if active_keys & set(obj) or obj.get("/S") in active_actions:
                    raise InspectionDenied()
                if obj.get("/S") in {"/URI", "/GoTo"} and not passive(obj):
                    raise InspectionDenied()
                if "/A" in obj and not passive(obj["/A"]):
                    raise InspectionDenied()
                if "/OpenAction" in obj:
                    opening = obj["/OpenAction"]
                    if not isinstance(opening, ArrayObject) and (
                            not passive(opening) or not isinstance(opening, DictionaryObject) or opening.get("/S") != "/GoTo"):
                        raise InspectionDenied()
                pending.extend((value, depth + 1) for value in obj.values())
            else:
                pending.extend((value, depth + 1) for value in obj)
    except PdfReadError:
        raise InspectionDenied() from None


def validate_docx(content: bytes) -> None:
    from stat import S_IFDIR, S_IFMT, S_IFREG

    from lxml import etree
    xml_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    def passive_field(instruction: str) -> bool:
        # Word fields can fetch external content or invoke DDE when rendered.
        # Only page counters and their presentation switch are supported here.
        return (len(instruction) <= 1024 and re.fullmatch(
            r"\s*(?:PAGE|NUMPAGES)(?:\s+\\\*\s+(?:MERGEFORMAT|CHARFORMAT))?\s*",
            instruction, re.I | re.ASCII) is not None)
    total = 0
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if (not 0 < len(entries) <= 500 or len(names) != len(set(names))
                    or not {"[Content_Types].xml", "word/document.xml"} <= set(names)):
                raise InspectionDenied()
            for entry in entries:
                name = entry.filename
                path = PurePosixPath(name.rstrip("/"))
                mode = S_IFMT(entry.external_attr >> 16)
                if (not name or len(name) > 255 or "\\" in name or "\x00" in name or path.is_absolute()
                        or ".." in path.parts or str(path) != name.rstrip("/")
                        or entry.flag_bits & 1 or entry.compress_type not in {ZIP_STORED, ZIP_DEFLATED}
                        or mode not in ({0, S_IFDIR} if entry.is_dir() else {0, S_IFREG})
                        or any(part.lower() in {"embeddings", "activex"} for part in path.parts)
                        or name.lower().endswith((".bin", ".exe", ".dll", ".js", ".vbs"))):
                    raise InspectionDenied()
                total += entry.file_size
                if total > 20 * 1024 * 1024 or entry.file_size > 10 * 1024 * 1024:
                    raise InspectionDenied()
                data = archive.read(entry)
                if len(data) != entry.file_size:
                    raise InspectionDenied()
                if not name.endswith((".xml", ".rels")):
                    continue
                parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
                tree = etree.fromstring(data, parser)
                if tree.getroottree().docinfo.doctype:
                    raise InspectionDenied()
                count = 0
                field_parts: list[str] | None = None
                field_separated = False
                for node in tree.iter():
                    count += 1
                    if count > 100000 or len(list(node.iterancestors())) > 64 or not isinstance(node.tag, str):
                        raise InspectionDenied()
                    tag = etree.QName(node.tag).localname.lower()
                    if node.tag == "{" + xml_ns + "}fldSimple":
                        if not passive_field(node.get("{" + xml_ns + "}instr", "")):
                            raise InspectionDenied()
                    if node.tag == "{" + xml_ns + "}fldChar":
                        field_type = node.get("{" + xml_ns + "}fldCharType")
                        if field_type == "begin" and field_parts is None:
                            field_parts, field_separated = [], False
                        elif field_type in {"separate", "end"} and field_parts is not None:
                            if not passive_field("".join(field_parts)):
                                raise InspectionDenied()
                            if field_type == "end":
                                field_parts = None
                            elif field_separated:
                                raise InspectionDenied()
                            else:
                                field_separated = True
                        else:
                            raise InspectionDenied()
                    if node.tag == "{" + xml_ns + "}instrText":
                        if field_parts is None or field_separated:
                            raise InspectionDenied()
                        field_parts.append(node.text or "")
                        if sum(len(part) for part in field_parts) > 1024:
                            raise InspectionDenied()
                    if tag in {"altchunk", "object", "oleobject", "control"}:
                        raise InspectionDenied()
                    if tag == "relationship" and node.get("TargetMode") == "External":
                        target = urlsplit(node.get("Target", ""))
                        if (not node.get("Type", "").endswith("/hyperlink")
                                or target.scheme not in {"http", "https", "mailto"}
                                or target.username or target.password):
                            raise InspectionDenied()
                    if tag == "override" and "macroenabled" in node.get("ContentType", "").lower():
                        raise InspectionDenied()
                if field_parts is not None:
                    raise InspectionDenied()
                if name == "word/document.xml" and tree.tag != "{" + xml_ns + "}document":
                    raise InspectionDenied()
    except (BadZipFile, OSError, ValueError, KeyError, etree.XMLSyntaxError):
        raise InspectionDenied() from None


def inspect(payload: bytes) -> dict:
    kind, content, sha256 = decode_request(payload)
    with tempfile.TemporaryDirectory(prefix="document-", dir="/tmp") as directory:
        path = Path(directory) / ("original." + kind)
        path.write_bytes(content)
        path.chmod(0o600)
        scan = scan_original(path)
        if path.read_bytes() != content:
            raise InspectionUnavailable()
        try:
            (validate_pdf if kind == "pdf" else validate_docx)(content)
            from app.services.parsing import parse_resume_file
            raw_text, sections, skills, experience, contact = parse_resume_file(content, filename="original." + kind, use_llm=False)
            parsed = {"raw_text": raw_text, "sections": sections, "skills": skills,
                      "experience_years": experience, "contact_info": contact}
            if (not isinstance(raw_text, str) or type(sections) is not dict
                    or any(not isinstance(k, str) or not isinstance(v, str) for k, v in sections.items())
                    or type(skills) is not list or any(not isinstance(s, str) for s in skills)
                    or isinstance(experience, bool) or not isinstance(experience, (int, float))
                    or not math.isfinite(experience)
                    or type(contact) is not dict or set(contact) != {"name", "email", "phone", "linkedin", "github"}
                    or any(v is not None and not isinstance(v, str) for v in contact.values())):
                raise InspectionUnavailable()
            if (len(raw_text.encode("utf-8")) > 256 * 1024 or len(sections) > 30
                    or any(len(k.encode("utf-8")) > 80 for k in sections)
                    or sum(len(v.encode("utf-8")) for v in sections.values()) > 256 * 1024
                    or len(skills) > 500 or any(len(s.encode("utf-8")) > 200 for s in skills)
                    or not 0 <= experience <= 40
                    or any(v is not None and len(v.encode("utf-8")) > 1000 for v in contact.values())):
                raise InspectionDenied()
            result = {"version": 1, "sha256": sha256, "size_bytes": len(content), "source_format": kind,
                      "scan": scan, "parsed": parsed}
            if len(json.dumps(result, allow_nan=False).encode()) > MAX_OUTPUT - 1024:
                raise InspectionDenied()
            return result
        except InspectionDenied:
            raise
        except Exception:
            raise InspectionUnavailable() from None


def decode_docx_request(payload: bytes) -> tuple[bytes, dict]:
    """One source only: the worker regenerates the exact expected candidate."""
    try:
        if not 0 < len(payload) <= MAX_REQUEST:
            raise InspectionUnavailable()
        value = json.loads(payload, object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(InspectionUnavailable()))
        if (type(value) is not dict or set(value) != {"version", "source_format", "sha256", "content_base64",
                "edits", "candidate_sha256", "candidate_size_bytes", "worker_image", "policy_sha256"}
                or value["source_format"] != "docx" or type(value["version"]) is not int or value["version"] != 1
                or type(value["content_base64"]) is not str or type(value["edits"]) is not list
                or not 0 < len(value["edits"]) <= 100
                or len(json.dumps(value["edits"], ensure_ascii=True, allow_nan=False).encode()) > 256 * 1024
                or type(value["candidate_size_bytes"]) is not int or not 0 < value["candidate_size_bytes"] <= MAX_FILE
                or any(type(value[key]) is not str or not re.fullmatch(r"[a-f0-9]{64}", value[key])
                       for key in ("sha256", "candidate_sha256", "policy_sha256"))
                or type(value["worker_image"]) is not str or len(value["worker_image"]) > 512
                or not re.fullmatch(r"[A-Za-z0-9._/:-]+@sha256:[a-f0-9]{64}", value["worker_image"])):
            raise InspectionUnavailable()
        content = base64.b64decode(value["content_base64"], validate=True)
        if not 0 < len(content) <= MAX_FILE or hashlib.sha256(content).hexdigest() != value["sha256"]:
            raise InspectionUnavailable()
        binding = {"version": 1, "operation": "docx_layout", "sha256": value["sha256"],
            "size_bytes": len(content), "source_format": "docx", "candidate_sha256": value["candidate_sha256"],
            "candidate_size_bytes": value["candidate_size_bytes"],
            "edits_sha256": hashlib.sha256(json.dumps(value["edits"], sort_keys=True,
                separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()}
        return content, {"request": value, "binding": binding}
    except Exception:
        raise InspectionUnavailable() from None


def validate_docx_edit(payload: bytes) -> dict:
    deadline = time.monotonic() + 50
    source, decoded = decode_docx_request(payload)
    request, binding = decoded["request"], decoded["binding"]
    with tempfile.TemporaryDirectory(prefix="docx-layout-", dir="/tmp") as temporary:
        directory = Path(temporary)
        directory.chmod(0o700)
        original = directory / "original.docx"
        original.write_bytes(source)
        original.chmod(0o600)
        with OwnedClamd(directory, deadline) as antivirus:
            scan = antivirus.scan(original, source)
            if original.read_bytes() != source:
                raise InspectionUnavailable()
            validate_docx(source)
            from app.services.docx_layout_validation import LAYOUT_REASONS, DocxLayoutError
            from app.services.resume_layout import ResumeLayoutError, _apply_docx
            try:
                candidate = _apply_docx(source, request["edits"])
            except ResumeLayoutError as error:
                raise DocxLayoutDenied("edit_binding", error.unit_id) from None
            if (len(candidate) != binding["candidate_size_bytes"]
                    or hashlib.sha256(candidate).hexdigest() != binding["candidate_sha256"]):
                raise InspectionUnavailable()
            validate_docx(candidate)
            target = directory / "candidate.docx"
            target.write_bytes(candidate)
            target.chmod(0o600)
            candidate_scan = antivirus.scan(target, candidate)
            if target.read_bytes() != candidate or original.read_bytes() != source:
                raise InspectionUnavailable()
        from docx_renderer import validate_rendered_edit
        try:
            layout = validate_rendered_edit(source, candidate, request["edits"],
                image=request["worker_image"], policy=request["policy_sha256"], deadline=deadline)
        except DocxLayoutError as error:
            if error.reason not in LAYOUT_REASONS or error.reason == "malformed_input":
                raise InspectionUnavailable() from None
            raise DocxLayoutDenied(error.reason, error.unit_id) from None
        if original.read_bytes() != source or target.read_bytes() != candidate or time.monotonic() >= deadline:
            raise InspectionUnavailable()
        return {**binding, "scan": scan, "candidate_scan": candidate_scan, "layout": layout}


def main():
    logging.disable(logging.CRITICAL)
    warnings.simplefilter("ignore")
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (40, 40))
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2, 32 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    # Explicit fixed parser-only package path; -I ignores caller Python settings.
    sys.path.insert(0, "/opt/document")
    binding = {}
    layout_mode = sys.argv[1:] == ["--docx-layout-stdin"]
    try:
        if sys.argv[1:] == ["--stdin"] or layout_mode:
            # The launcher owns the absolute input deadline. Read at most the
            # fixed envelope cap plus one byte; oversize/empty input fails closed.
            payload = sys.stdin.buffer.read(MAX_REQUEST + 1)
        elif sys.argv[1:] == ["/tmp/request.json"]:
            with open("/tmp/request.json", "rb") as stream:
                payload = stream.read(MAX_REQUEST + 1)
        else:
            raise InspectionUnavailable()
        if layout_mode:
            _, decoded = decode_docx_request(payload)
            binding = decoded["binding"]
            result = validate_docx_edit(payload)
        else:
            kind, content, sha256 = decode_request(payload)
            binding = {"sha256": sha256, "size_bytes": len(content), "source_format": kind}
            result = inspect(payload)
        if len(json.dumps(result, allow_nan=False).encode()) > (32768 if layout_mode else MAX_OUTPUT):
            raise InspectionUnavailable()
        print(json.dumps(result, allow_nan=False))
    except DocxLayoutDenied as error:
        print(json.dumps({**binding, "error": DOCX_ERROR, "reason": error.reason, "unit_id": error.unit_id}))
        raise SystemExit(1) from None
    except InspectionDenied:
        result = {"version": 1, **binding, "error": DOCX_ERROR if layout_mode else ERROR}
        if layout_mode:
            result.update(reason="document_safety", unit_id=None)
        print(json.dumps(result))
        raise SystemExit(1) from None
    except Exception:
        print(json.dumps({"version": 1, **binding, "error": DOCX_UNAVAILABLE if layout_mode else UNAVAILABLE}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
