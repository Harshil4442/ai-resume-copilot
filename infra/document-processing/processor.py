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
import subprocess
import sys
import tempfile
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


class InspectionDenied(ValueError):
    def __init__(self):
        super().__init__(ERROR)


class InspectionUnavailable(ValueError):
    def __init__(self):
        super().__init__(UNAVAILABLE)


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


def _command(arguments: list[str], timeout: int, *, allow_detection: bool = False) -> tuple[int, str]:
    try:
        result = subprocess.run(arguments, stdin=subprocess.DEVNULL, capture_output=True,
            timeout=timeout, check=False, env={"PATH": "/usr/bin:/bin", "LANG": "C", "TZ": "UTC",
                                             "HOME": "/nonexistent"})
        if (len(result.stdout) > 65536 or result.stderr
                or result.returncode not in ((0, 1) if allow_detection else (0,))):
            raise InspectionUnavailable()
        return result.returncode, result.stdout.decode("ascii", errors="strict")
    except Exception:
        raise InspectionUnavailable() from None


def scan_original(path: Path, *, now: datetime | None = None) -> dict:
    """Require fresh definitions and a coherent exact-one-file scanner outcome.

    Genuine detection is a permanent document refusal. Any missing, stale,
    incomplete, inconsistent or errored scan remains service unavailability.
    Scanner names/output are private and never cross the processor protocol.
    """
    _, version = _command(["/usr/bin/clamscan", "--database=" + DATABASE, "--version"], 5)
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
    code, output = _command(["/usr/bin/clamscan", "--stdout", "--database=" + DATABASE,
        "--official-db-only=yes", "--fail-if-cvd-older-than=3", "--disable-cache",
        "--alert-encrypted=yes", "--alert-exceeds-max=yes", "--heuristic-alerts=yes",
        "--scan-archive=yes", "--scan-pdf=yes", "--scan-ole2=yes",
        "--max-filesize=6M", "--max-scansize=20M", "--max-files=500",
        "--max-recursion=16", "--max-scantime=20000", "--bytecode-timeout=1000",
        "--tempdir=" + str(path.parent), "--", str(path)], 30, allow_detection=True)
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
    try:
        if sys.argv[1:] != ["/tmp/request.json"]:
            raise InspectionUnavailable()
        with open("/tmp/request.json", "rb") as stream:
            payload = stream.read(MAX_REQUEST + 1)
        kind, content, sha256 = decode_request(payload)
        binding = {"sha256": sha256, "size_bytes": len(content), "source_format": kind}
        result = inspect(payload)
        print(json.dumps(result, allow_nan=False))
    except InspectionDenied:
        print(json.dumps({"version": 1, **binding, "error": ERROR}))
        raise SystemExit(1) from None
    except Exception:
        print(json.dumps({"version": 1, **binding, "error": UNAVAILABLE}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
