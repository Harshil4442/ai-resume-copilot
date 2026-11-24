"""Acquire only the reviewed official ClamAV 1.4.7 runtime, never source-compile.

The independent official release SHA256 and exact ELF pins are authoritative.
The vendor's detached SHA1 signature is documented but is not treated as strong
authentication, and no weak-signature policy override exists here.
Import and matches_files perform no network, package-manager or native call.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
RELEASE_SHA256 = "41daff09af32894f2eed16a99dc67e7ec0ae135871d4a839cf8c33e846565392"
PRUNE = ("clamav", "clamav-daemon", "clamav-freshclam", "clamdscan", "libclamav12", "libclamunrar12")
OLD_BINARIES = ("/usr/bin/clamscan", "/usr/bin/freshclam", "/usr/sbin/clamd", "/usr/bin/clamdscan")
PRESERVE = ("/usr/lib/libreoffice", "/usr/share/fonts", "/etc/fonts")


class SupplierError(ValueError):
    def __init__(self):
        super().__init__("document_clamav_supplier_unverified")


def require(value):
    if not value:
        raise SupplierError()


def release():
    path = HERE / "clamav-release.json"
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 32768)
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == RELEASE_SHA256)
    value = json.loads(raw)
    require(value["version"] == 1 and value["engine"] == "1.4.7" and value["platform"] == "linux/amd64")
    return value


def matches_files(files):
    """Exact three vendor ELF identities; never accept merely matching version text."""
    value = release()
    expected = {row["path"]: row for row in value["runtime_files"] if row["kind"] == "file"}
    if type(files) is not list or len(files) != 3:
        return False
    return files == [{"path": path, "sha256": expected[path]["sha256"], "size": expected[path]["size"],
                      "elf_machine": 62} for path in value["native_manifest_files"]]


def verify_package(path, value):
    expected = value["package"]
    require(path.is_file() and not path.is_symlink() and path.stat().st_size == expected["size"])
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(65536):
            digest.update(block)
    require(digest.hexdigest() == expected["sha256"])


def download(path, value):
    expected = value["package"]
    require(expected["url"] == "https://github.com/Cisco-Talos/clamav/releases/download/clamav-1.4.7/clamav-1.4.7.linux.x86_64.deb")
    request = urllib.request.Request(expected["url"], headers={"User-Agent": "HireWiz-pinned-ClamAV-acquisition/1"})
    count = 0
    with urllib.request.urlopen(request, timeout=45) as response, path.open("xb") as target:
        while block := response.read(65536):
            count += len(block)
            require(count <= expected["size"])
            target.write(block)
    verify_package(path, value)


def data_member(path):
    """Bounded Debian ar framing; no package scripts or general extraction."""
    raw = path.read_bytes()
    require(raw[:8] == b"!<arch>\n" and len(raw) <= 96 * 1024 * 1024)
    offset, members = 8, {}
    while offset < len(raw):
        header = raw[offset:offset + 60]
        require(len(header) == 60 and header[-2:] == b"`\n")
        name = header[:16].decode("ascii").strip().rstrip("/")
        size = int(header[48:58].decode("ascii").strip())
        require(name not in members and size > 0 and offset + 60 + size <= len(raw))
        offset += 60
        members[name] = raw[offset:offset + size]
        offset += size + size % 2
    require(offset == len(raw) and set(members) == {"debian-binary", "control.tar.gz", "data.tar.gz"}
            and members["debian-binary"] == b"2.0\n")
    return members["data.tar.gz"]


def stage_runtime(package, directory, value):
    expected = {row["path"]: row for row in value["runtime_files"]}
    found = set()
    with tarfile.open(fileobj=io.BytesIO(data_member(package)), mode="r|gz") as archive:
        for member in archive:
            name = "/" + member.name.removeprefix("./").lstrip("/")
            if name not in expected:
                continue
            require(name not in found and ".." not in Path(name).parts)
            row = expected[name]
            if row["kind"] == "symlink":
                require(member.issym() and member.linkname == row["target"]
                        and "/" not in member.linkname and member.linkname not in {".", ".."})
            else:
                require(member.isfile() and member.size == row["size"] and member.size <= 128 * 1024 * 1024)
                source = archive.extractfile(member)
                if source is None:
                    raise SupplierError()
                target = directory / name.removeprefix("/")
                target.parent.mkdir(parents=True, exist_ok=True)
                digest, count = hashlib.sha256(), 0
                with target.open("xb") as output:
                    while block := source.read(65536):
                        count += len(block)
                        require(count <= row["size"])
                        digest.update(block)
                        output.write(block)
                require(count == row["size"] and digest.hexdigest() == row["sha256"])
                target.chmod(row["mode"])
                with target.open("rb") as checked:
                    header = checked.read(20)
                require(header[:7] == b"\x7fELF\x02\x01\x01" and int.from_bytes(header[18:20], "little") == 62)
            found.add(name)
    require(found == set(expected))


def preservation_snapshot():
    result = {}
    for root in PRESERVE:
        path = Path(root)
        require(path.is_dir())
        for entry in sorted(path.rglob("*")):
            if entry.is_symlink():
                result[str(entry)] = "link:" + os.readlink(entry)
            elif entry.is_file():
                result[str(entry)] = hashlib.sha256(entry.read_bytes()).hexdigest()
    require(result)
    return result


def prune_old_engine():
    installed = []
    for name in PRUNE:
        result = subprocess.run(["dpkg-query", "-W", "-f=${db:Status-Status}", name],
                                capture_output=True, timeout=10, check=False)
        if result.returncode == 0 and result.stdout == b"installed":
            installed.append(name)
    if installed:
        args = ["apt-get", "--assume-yes", "--no-auto-remove", "purge", *installed]
        preview = subprocess.run(["apt-get", "--simulate", *args[1:]], capture_output=True, timeout=30, check=False)
        require(preview.returncode == 0)
        removed = [line.split()[1].split(":")[0] for line in preview.stdout.decode().splitlines()
                   if line.startswith(("Remv ", "Purg "))]
        require(set(installed) <= set(removed) <= set(PRUNE))
        completed = subprocess.run(args, capture_output=True, timeout=90, check=False)
        require(completed.returncode == 0)
    require(not any(os.path.lexists(name) for name in OLD_BINARIES))
    require(not list(Path("/usr/lib").glob("*/libclamav.so.*")))


def verify_installation(value):
    for row in value["runtime_files"]:
        path = Path(row["path"])
        if row["kind"] == "symlink":
            require(path.is_symlink() and os.readlink(path) == row["target"] and path.resolve(strict=True).is_file())
        else:
            require(path.is_file() and not path.is_symlink() and stat.S_IMODE(path.stat().st_mode) == row["mode"]
                    and path.stat().st_size == row["size"] and hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"])
    for executable in ("/usr/local/bin/clamscan", "/usr/local/bin/freshclam", "/usr/local/sbin/clamd"):
        arguments = [executable, "--version"]
        if executable == "/usr/local/bin/freshclam":
            # The vendor binary defaults to /usr/local/etc, whereas this image
            # deliberately owns one reviewed configuration under /etc/clamav.
            arguments.insert(1, "--config-file=/etc/clamav/freshclam.conf")
        elif executable == "/usr/local/sbin/clamd":
            # Version-only reporting must not require or start a global daemon.
            # Processing always supplies its own private per-request config.
            arguments.insert(1, "--config-file=/dev/null")
        observed = subprocess.run(arguments, capture_output=True, timeout=5, check=False)
        require(observed.returncode == 0 and not observed.stderr
                and re.fullmatch(rb"ClamAV 1\.4\.7(?:/[0-9]+/[A-Za-z0-9 :]{24})?\n?", observed.stdout))


def install(package=None):
    require(os.getuid() == 0 and platform.system() == "Linux" and platform.machine() in {"x86_64", "amd64"})
    value = release()
    before = preservation_snapshot()
    with tempfile.TemporaryDirectory(prefix="hirewiz-clamav-package-", dir="/tmp") as temporary:
        directory = Path(temporary)
        directory.chmod(0o700)
        selected = directory / "clamav.deb" if package is None else package
        if package is None:
            download(selected, value)
        verify_package(selected, value)
        stage = directory / "runtime"
        stage.mkdir(mode=0o700)
        stage_runtime(selected, stage, value)
        # Verification finishes before pruning or writing a system runtime file.
        require(not any(os.path.lexists(row["path"]) for row in value["runtime_files"]))
        prune_old_engine()
        for row in value["runtime_files"]:
            target = Path(row["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            if row["kind"] == "file":
                with (stage / row["path"].removeprefix("/")).open("rb") as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, 65536)
                target.chmod(row["mode"])
        for row in value["runtime_files"]:
            if row["kind"] == "symlink":
                Path(row["path"]).symlink_to(row["target"])
        config = Path("/etc/clamav/freshclam.conf")
        config.parent.mkdir(parents=True, exist_ok=True)
        require(not config.is_symlink())
        config.write_text("DatabaseDirectory /var/lib/clamav\nDatabaseOwner root\nDatabaseMirror database.clamav.net\n")
        config.chmod(0o444)
        Path("/var/lib/clamav").mkdir(mode=0o755, parents=True, exist_ok=True)
        verify_installation(value)
        require(preservation_snapshot() == before)


def _failure_stage(error):
    """A finite owned function name; exception values and native output stay private."""
    allowed = {"release", "verify_package", "download", "data_member", "stage_runtime",
               "preservation_snapshot", "prune_old_engine", "verify_installation", "install"}
    selected, trace = "setup", error.__traceback__
    for _ in range(64):
        if trace is None:
            break
        code = trace.tb_frame.f_code
        if code.co_filename == __file__ and code.co_name in allowed:
            selected = code.co_name
        trace = trace.tb_next
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--install", action="store_true", required=True)
    parser.add_argument("--package", type=Path)
    args = parser.parse_args()
    try:
        install(args.package)
        print(json.dumps({"supplier_runtime": "verified", "engine": "1.4.7",
                          "unused_static_archive_copied": False, "renderer_and_fonts_unchanged": True}))
        return 0
    except Exception as error:  # noqa: BLE001 - only the fixed public failure is emitted.
        print(json.dumps({"error": "document_clamav_supplier_unverified", "stage": _failure_stage(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
