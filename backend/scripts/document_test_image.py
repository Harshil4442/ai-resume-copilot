"""Build a seven-file document image and check genuine engines in local Docker.

This is local native scanner/parser evidence, never Cloud Run sandbox proof.
Run with --platform linux/amd64 (CI) or linux/arm64 (a native ARM machine).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import selectors
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[2]
SOURCES = (
    "infra/document-processing/Dockerfile", "infra/document-processing/processor.py",
    "infra/document-processing/service.py", "backend/app/services/parsing.py",
    "backend/app/services/market/skill_extractor.py", "backend/app/services/market/skill_taxonomy.py",
    "backend/app/services/market/security.py",
)
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
LABEL = "hirewiz.document-test.source."
MEMORY = 2 * 1024**3
TMPFS = "rw,noexec,nosuid,nodev,size=64m,mode=1777"
ERROR = "document_engine_check_refused"
EICAR = (b"X5O!P%@AP[4" + bytes([92]) + b"PZX54(P^)7CC)7}$"
         + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*")
# Runtime hashes attest all six copied Python sources; the seventh Dockerfile
# remains bound to build input by its source label, like the native TeX helper.
RUNTIME = {
    path: "/opt/document/" + (path.rsplit("/", 1)[1] if path.startswith("infra/")
                            else path.removeprefix("backend/"))
    for path in SOURCES if not path.endswith("Dockerfile")
}
HASH_PROGRAM = ("import hashlib,json,pathlib; print(json.dumps({k:hashlib.sha256("
                "pathlib.Path(v).read_bytes()).hexdigest() for k,v in " + repr(RUNTIME) + ".items()}))")
DETECTION_PROGRAM = '''import base64,json,pathlib,subprocess,sys
sys.path.insert(0,"/opt/document")
import processor
payload=pathlib.Path("/tmp/request.json").read_bytes()
kind,content,sha256=processor.decode_request(payload)
target=pathlib.Path("/tmp/antivirus-test."+kind);target.write_bytes(content)
r=subprocess.run(["/usr/bin/clamscan","--stdout","--database=/var/lib/clamav",
 "--alert-exceeds-max=yes","--max-filesize=6M","--max-scansize=20M",
 "--max-files=500","--max-recursion=16","--max-scantime=20000","--",str(target)],
 capture_output=True,timeout=30)
found=r.returncode==1 and len(r.stdout)<=65536 and not r.stderr and b"EICAR" in r.stdout.upper() and b" FOUND" in r.stdout
p=subprocess.run([sys.executable,"-I","/opt/document/processor.py","/tmp/request.json"],
 capture_output=True,timeout=40)
expected={"version":1,"sha256":sha256,"size_bytes":len(content),"source_format":kind,"error":"document_inspection_refused"}
refused=p.returncode==1 and len(p.stdout)<=4096 and json.loads(p.stdout)==expected and not p.stderr
print(json.dumps({"eicar_detected":found,"processor_refused":refused}))
'''


class DocumentImageError(RuntimeError):
    def __init__(self):
        super().__init__(ERROR)


def command(args: list[str], *, timeout: int = 20, limit: int = 1024 * 1024) -> tuple[int, bytes]:
    child = None
    try:
        child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        assert child.stdout is not None
        deadline = time.monotonic() + timeout
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                left = deadline - time.monotonic()
                if left <= 0:
                    raise DocumentImageError()
                for key, _ in selector.select(min(left, 1)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(chunk)
                        if len(output) > limit:
                            raise DocumentImageError()
            left = deadline - time.monotonic()
            if left <= 0:
                raise DocumentImageError()
            return child.wait(timeout=left), bytes(output)
    except Exception:
        raise DocumentImageError() from None
    finally:
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            if child.stdout:
                child.stdout.close()


def successful(args: list[str], *, timeout: int = 20) -> bytes:
    code, output = command(args, timeout=timeout)
    if code:
        raise DocumentImageError()
    return output


def docker_command() -> list[str]:
    # Refuse a remote or caller-routed daemon. No context/account is changed.
    if os.environ.get("DOCKER_HOST") or os.environ.get("DOCKER_CONTEXT"):
        raise DocumentImageError()
    contexts = json.loads(successful(["docker", "context", "inspect"]))
    if not isinstance(contexts, list) or len(contexts) != 1:
        raise DocumentImageError()
    host = contexts[0].get("Endpoints", {}).get("docker", {}).get("Host", "")
    if not isinstance(host, str) or not re.fullmatch(r"unix:///[^\x00\r\n?#]+", host):
        raise DocumentImageError()
    return ["docker", "--host=" + host]


def source_hashes() -> dict[str, str]:
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in SOURCES}


def build(docker: list[str], platform: str, hashes: dict[str, str]) -> str:
    with tempfile.TemporaryDirectory(prefix="hirewiz-document-build-") as directory:
        context = Path(directory) / "context"
        context.mkdir()
        for path in SOURCES:
            content = (ROOT / path).read_bytes()
            if hashlib.sha256(content).hexdigest() != hashes[path]:
                raise DocumentImageError()
            copied = context / path
            copied.parent.mkdir(parents=True, exist_ok=True)
            copied.write_bytes(content)
            copied.chmod(0o644)
        iid = Path(directory) / "image.id"
        args = docker + ["build", "--pull=false", "--platform", platform, "--iidfile", str(iid),
                         "--file", str(context / SOURCES[0]),
                         "--build-arg", "DOCUMENT_DEFINITIONS_NONCE=" + uuid.uuid4().hex]
        for path, digest in hashes.items():
            args += ["--label", LABEL + path + "=" + digest]
        args.append(str(context))
        successful(args, timeout=600)
        image = iid.read_text().strip()
    if not IMAGE_ID.fullmatch(image) or source_hashes() != hashes:
        raise DocumentImageError()
    return image


def image_metadata(docker: list[str], image: str, platform: str, hashes: dict[str, str]) -> None:
    if not IMAGE_ID.fullmatch(image):
        raise DocumentImageError()
    entries = json.loads(successful(docker + ["image", "inspect", image]))
    if not isinstance(entries, list) or len(entries) != 1:
        raise DocumentImageError()
    value = entries[0]
    config = value.get("Config", {})
    labels = config.get("Labels", {})
    if (value.get("Id") != image or platform != value.get("Os", "") + "/" + value.get("Architecture", "")
            or config.get("User") != "65534:65534"
            or config.get("Entrypoint") != ["/usr/local/bin/python3", "-I", "/opt/document/service.py"]
            or any(labels.get(LABEL + path) != digest for path, digest in hashes.items())):
        raise DocumentImageError()


def check_container(value: dict, image: str, owned: str, mounts: list[str], arguments: list[str], *, terminal: bool) -> None:
    config, host, state = value.get("Config", {}), value.get("HostConfig", {}), value.get("State", {})
    observed_mounts = value.get("Mounts", [])
    if (value.get("Name") != "/" + owned or value.get("Image") != image
            or config.get("Image") != image or config.get("User") != "65534:65534"
            or config.get("Entrypoint") != ["/usr/local/bin/python3"] or config.get("Cmd") != ["-I"] + arguments
            or host.get("NetworkMode") != "none" or host.get("ReadonlyRootfs") is not True
            or host.get("CapDrop") != ["ALL"] or host.get("CapAdd")
            or host.get("SecurityOpt") != ["no-new-privileges"]
            or host.get("Memory") != MEMORY or host.get("MemorySwap") != MEMORY
            or host.get("NanoCpus") != 1000000000 or host.get("PidsLimit") != 64
            or host.get("Tmpfs") != {"/tmp": TMPFS} or host.get("Privileged") is not False
            or len(observed_mounts) != len(mounts) or any(m.get("Type") != "bind" or m.get("RW") is not False
               or m.get("Source") + ":" + m.get("Destination") not in mounts for m in observed_mounts)
            or state.get("Running") is not False or state.get("OOMKilled") is not False
            or state.get("Dead") is not False or state.get("Error") != ""
            or state.get("Status") != ("exited" if terminal else "created")):
        raise DocumentImageError()


def container(docker: list[str], image: str, platform: str, arguments: list[str],
              mounts: list[tuple[Path, str]]) -> tuple[int, bytes]:
    owned = "hirewiz-document-test-" + uuid.uuid4().hex
    args = docker + ["create", "--pull=never", "--platform", platform, "--name", owned,
        "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--user=65534:65534", "--cpus=1", "--memory=2g", "--memory-swap=2g", "--pids-limit=64",
        "--tmpfs=/tmp:" + TMPFS, "--entrypoint=/usr/local/bin/python3"]
    expected = []
    for source, target in mounts:
        source = source.resolve(strict=True)
        args += ["--mount", "type=bind,source=" + str(source) + ",target=" + target + ",readonly"]
        expected.append(str(source) + ":" + target)
    args += [image, "-I"] + arguments
    try:
        successful(args)
        before = json.loads(successful(docker + ["inspect", owned]))
        if not isinstance(before, list) or len(before) != 1:
            raise DocumentImageError()
        check_container(before[0], image, owned, expected, arguments, terminal=False)
        code, output = command(docker + ["start", "--attach", owned], timeout=65)
        after = json.loads(successful(docker + ["inspect", owned]))
        if not isinstance(after, list) or len(after) != 1:
            raise DocumentImageError()
        check_container(after[0], image, owned, expected, arguments, terminal=True)
        if after[0]["State"].get("ExitCode") != code:
            raise DocumentImageError()
        return code, output
    finally:
        # Covers client timeout/unknown state; never remove somebody else's name.
        successful(docker + ["rm", "--force", owned], timeout=10)


def corpus() -> list[tuple[str, str, bytes, str]]:
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from pypdf import PdfWriter

    def pdf(active=False):
        writer = PdfWriter()
        writer.add_blank_page(200, 200)
        if active:
            writer.add_js("this.print();")
        stream = io.BytesIO()
        writer.write(stream)
        return stream.getvalue()
    def docx(field=None):
        document = Document()
        document.add_paragraph("Candidate Example")
        document.add_paragraph("SKILLS")
        document.add_paragraph("Python, SQL")
        if field:
            node = OxmlElement("w:fldSimple")
            node.set(qn("w:instr"), field)
            document.add_paragraph()._p.append(node)
        stream = io.BytesIO()
        document.save(stream)
        return stream.getvalue()
    clean_docx = docx()
    infected = io.BytesIO()
    with ZipFile(io.BytesIO(clean_docx)) as original, ZipFile(infected, "w", ZIP_DEFLATED) as output:
        for entry in original.infolist():
            output.writestr(entry, original.read(entry))
        output.writestr("word/standalone-antivirus-test.txt", EICAR)
    if len(EICAR) != 68:
        raise DocumentImageError()
    return [("clean_pdf", "pdf", pdf(), "clean"), ("clean_docx", "docx", clean_docx, "clean"),
            ("page_counter", "docx", docx("PAGE"), "clean"),
            ("active_pdf", "pdf", pdf(True), "refused"),
            ("includetext", "docx", docx('INCLUDETEXT "https://fixture.invalid/candidate.docx"'), "refused"),
            ("eicar_exact_file", "pdf", EICAR, "detected"),
            ("eicar_zip_member", "docx", infected.getvalue(), "detected"),
            ("missing_definitions", "pdf", pdf(), "missing")]


def check(platform: str, *, image: str | None = None) -> dict:
    docker = docker_command()
    hashes = source_hashes()
    image = image or build(docker, platform, hashes)
    image_metadata(docker, image, platform, hashes)
    code, raw = container(docker, image, platform, ["-c", HASH_PROGRAM], [])
    if code or json.loads(raw) != {path: hashes[path] for path in RUNTIME}:
        raise DocumentImageError()
    observations = []
    with tempfile.TemporaryDirectory(prefix="hirewiz-document-corpus-") as temporary:
        directory = Path(temporary)
        directory.chmod(0o755)
        empty = directory / "empty-definitions"
        empty.mkdir(mode=0o755)
        for name, kind, content, outcome in corpus():
            envelope = {"version": 1, "source_format": kind, "content_base64": base64.b64encode(content).decode(),
                        "sha256": hashlib.sha256(content).hexdigest()}
            request = directory / (name + ".json")
            request.write_text(json.dumps(envelope))
            request.chmod(0o444)
            mounts = [(request, "/tmp/request.json")]
            if outcome == "missing":
                mounts.append((empty, "/var/lib/clamav"))
            arguments = ["-c", DETECTION_PROGRAM] if outcome == "detected" else ["/opt/document/processor.py", "/tmp/request.json"]
            code, raw = container(docker, image, platform, arguments, mounts)
            result = json.loads(raw)
            if outcome == "detected":
                if code or result != {"eicar_detected": True, "processor_refused": True}:
                    raise DocumentImageError()
            elif outcome in {"refused", "missing"}:
                expected = {"version": 1, "sha256": envelope["sha256"], "size_bytes": len(content),
                            "source_format": kind, "error": "document_inspection_unavailable" if outcome == "missing" else "document_inspection_refused"}
                if code != (2 if outcome == "missing" else 1) or result != expected:
                    raise DocumentImageError()
            elif (code or result.get("sha256") != envelope["sha256"] or result.get("size_bytes") != len(content)
                  or result.get("source_format") != kind or result.get("scan", {}).get("engine") != "ClamAV"
                  or (kind == "docx" and "Python" not in result.get("parsed", {}).get("skills", []))):
                raise DocumentImageError()
            observations.append({"case": name, "outcome": outcome, "passed": True,
                                 **({"scan": result["scan"]} if outcome == "clean" else {})})
    if source_hashes() != hashes:
        raise DocumentImageError()
    return {"scope": "local_native_docker_only", "cloud_run_isolation_proven": False,
            "image_id": image, "platform": platform, "source_sha256": hashes,
            "runtime_source_bytes_verified": True, "restricted_container_metadata_verified": True,
            "corpus": observations}


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("linux/amd64", "linux/arm64"), required=True)
    parser.add_argument("--image-id", help="Reuse only an immutable ID with exact current source labels/bytes.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = json.dumps(check(args.platform, image=args.image_id), sort_keys=True, indent=2) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(report)
        print(report, end="")
    except Exception:
        print(json.dumps({"error": ERROR}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
