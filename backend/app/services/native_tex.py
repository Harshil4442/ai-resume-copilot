"""Source-preserving native TeX projects and fail-closed isolated compilation.

The compiler must be a separately isolated, secretless job. No host TeX, shell
commands from the source, package installation or network fallback is allowed.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import unicodedata
from contextlib import closing
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

from .resume_layout import ResumeLayoutError

NATIVE_FORMATS = {"tex", "texzip"}
MAX_PROJECT_BYTES = 5 * 1024 * 1024
MAX_FILES = 64
MAX_PDF_BYTES = 10 * 1024 * 1024
VALIDATOR = "hirewiz-native-tex-v2"
# Deliberately small supported dependency set, all supplied in the pinned image.
PACKAGES = {"fontenc", "inputenc", "lmodern", "geometry", "hyperref", "xcolor", "color",
    "array", "tabularx", "graphicx", "url", "amsmath", "amssymb", "textcomp", "babel",
    "multicol", "fancyhdr", "parskip"}
CLASSES = {"article", "report", "book"}
EXTENSIONS = {".tex", ".sty", ".cls", ".png", ".jpg", ".jpeg", ".pdf"}
PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,159}\Z")
DANGEROUS = re.compile(r"\\(?:write18|openin|openout|read|write|directlua|pdfobj|pdfcatalog|pdfannot|special|catcode)\b")
DYNAMIC_COMMANDS = re.compile(r"\\(?:csname|endcsname|scantokens)\b|\^\^")
DYNAMIC_DATES = re.compile(r"\\(?:today|year|month|day|time|pdfcreationdate|pdffilemoddate|maketitle)\b")
DEPENDENCY = re.compile(r"\\(?:usepackage|RequirePackage|documentclass|LoadClass)(?:\[[^\]\n]*\])?\s*\{([^{}]+)\}")
INCLUDE = re.compile(r"\\(?:input|include|includegraphics)(?:\[[^\]\n]*\])?\s*\{([^{}]+)\}")
SPAN = re.compile(r"\\resume(?:Item|Bullet)\{([^{}\\\n]+)\}|\\item(?:\[[^{}\\\n\]]{1,16}\])?\s+([^{}\\\n]+)|(?m:^([A-Za-z][^{}\\\n%]+)$)")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _path(name: str) -> str:
    path = PurePosixPath(name)
    if (not PATH.fullmatch(name) or path.is_absolute() or str(path) != name
            or any(part in {".", ".."} or part.startswith(".") for part in path.parts)
            or path.suffix not in EXTENSIONS):
        raise ResumeLayoutError("The TeX project contains an unsafe path or unsupported file. Upload a flat, path-safe source project or a PDF.")
    return name


@dataclass(frozen=True)
class Project:
    files: dict[str, bytes]
    main: str
    kind: str

    def bytes(self) -> bytes:
        if self.kind == "tex":
            return self.files[self.main]
        output = BytesIO()
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
            for name, data in sorted(self.files.items()):
                info = ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100600 << 16
                archive.writestr(info, data)
        return output.getvalue()


def read_project(data: bytes, kind: str) -> Project:
    if kind not in NATIVE_FORMATS or not 0 < len(data) <= MAX_PROJECT_BYTES:
        raise ResumeLayoutError("Native TeX source must be nonempty and at most 5 MB. Upload PDF or DOCX instead.")
    if kind == "tex":
        files = {"resume.tex": data}
    else:
        files = {}
        try:
            with ZipFile(BytesIO(data)) as archive:
                entries = archive.infolist()
                if not 0 < len(entries) <= MAX_FILES:
                    raise ResumeLayoutError("TeX projects support at most 64 source/assets files. Remove generated files or upload PDF.")
                total = 0
                for entry in entries:
                    if entry.is_dir():
                        continue
                    name = _path(entry.filename)
                    mode = entry.external_attr >> 16
                    if name in files or entry.flag_bits & 1 or (mode & 0o170000) not in {0, 0o100000}:
                        raise ResumeLayoutError("Encrypted, duplicate or linked TeX archive entries are unsupported. Upload regular source files only.")
                    total += entry.file_size
                    if total > MAX_PROJECT_BYTES:
                        raise ResumeLayoutError("The TeX project expands beyond 5 MB. Remove generated assets or upload PDF.")
                    files[name] = archive.read(entry)
        except (BadZipFile, RuntimeError, OSError) as exc:
            raise ResumeLayoutError("The TeX ZIP project is malformed. Upload a regular source ZIP or the original PDF.") from exc
    mains = []
    for name, data in files.items():
        if PurePosixPath(name).suffix not in {".tex", ".sty", ".cls"}:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ResumeLayoutError("TeX source must use UTF-8 text. Export UTF-8 source or upload PDF.") from exc
        text = re.sub(r"(?<!\\)%[^\n]*", "", text)
        if "\x00" in text or DANGEROUS.search(text):
            raise ResumeLayoutError("The TeX project uses unsupported executable/file primitives. Remove them or upload the original PDF.")
        if DYNAMIC_COMMANDS.search(text):
            raise ResumeLayoutError("The TeX project uses dynamically constructed commands that may contain an unsupported interactive action. Use literal supported commands or upload the original/custom PDF.")
        if DYNAMIC_DATES.search(text):
            raise ResumeLayoutError("The TeX project uses a dynamic date or time whose original rendered value cannot be preserved. Replace it with the intended literal date in your source, or upload the original/custom PDF.")
        if re.search(r"\\documentclass\b", text):
            mains.append(name)
        for match in DEPENDENCY.finditer(text):
            for dep in match[1].split(","):
                dep = dep.strip()
                if dep not in PACKAGES | CLASSES and dep + ".sty" not in files and dep + ".cls" not in files:
                    raise ResumeLayoutError("The TeX project needs an unsupported dependency. Use the supported package list or upload the original PDF.")
        for match in INCLUDE.finditer(text):
            target = match[1].strip()
            candidates = {target, target + ".tex", target + ".pdf", target + ".png", target + ".jpg"}
            if not any(candidate in files for candidate in candidates):
                raise ResumeLayoutError("An included TeX source or asset is missing. Include every project file in the ZIP or upload PDF.")
    if len(mains) != 1:
        raise ResumeLayoutError("The TeX project needs exactly one documentclass entry file. Remove extra main files or upload PDF.")
    if "/" in mains[0]:
        raise ResumeLayoutError("Put the main TeX file at the ZIP root and use root-relative includes, or upload PDF.")
    return Project(files, mains[0], kind)


def source_units(data: bytes, kind: str, eligible) -> list[dict]:
    project = read_project(data, kind)
    units = []
    for name, content in sorted(project.files.items()):
        if not name.endswith(".tex"):
            continue
        text = content.decode("utf-8")
        for match in SPAN.finditer(text):
            group = next(i for i in (1, 2, 3) if match[i] is not None)
            value = match[group]
            if not eligible(value):
                continue
            # Commented text and preamble definitions are not candidate facts.
            line_start = text.rfind("\n", 0, match.start()) + 1
            if "%" in text[line_start:match.start()] or (name == project.main and match.start() < text.find(r"\begin{document}")):
                continue
            start, end = match.span(group)
            identity = f"tex:{digest(data)[:16]}:{name}:{start}:{digest(value.encode())[:12]}"
            units.append({"unit_id": identity, "text": value, "file": name, "start": start, "end": end,
                "kind": "native_tex", "max_characters": len(value), "preserve_numbers": True,
                "generation_priority": 1})
    return units


def patch_project(data: bytes, kind: str, units: list[dict], replacements: dict[str, str]) -> bytes:
    project = read_project(data, kind)
    files = dict(project.files)
    for name, content in files.items():
        if not name.endswith(".tex"):
            continue
        text = content.decode("utf-8")
        for unit in sorted((u for u in units if u["file"] == name and u["unit_id"] in replacements), key=lambda u: u["start"], reverse=True):
            replacement = replacements[unit["unit_id"]]
            if re.search(r"[\\{}%$&#_^~]", replacement):
                raise ResumeLayoutError("TeX replacements must be plain text without commands or special characters. Keep the source notation or edit it manually.", unit_id=unit["unit_id"])
            text = text[:unit["start"]] + replacement + text[unit["end"]:]
        files[name] = text.encode()
    return Project(files, project.main, kind).bytes()


def compile_project(data: bytes, kind: str) -> tuple[bytes, str]:
    project = read_project(data, kind)
    image = os.getenv("NATIVE_TEX_IMAGE", "")
    payload = json.dumps({"protocol": "hirewiz-tex-v1", "main": project.main,
        "files": [{"path": name, "data": base64.b64encode(value).decode()} for name, value in sorted(project.files.items())]}).encode()
    remote = os.getenv("NATIVE_TEX_COMPILER_URL", "")
    if remote:
        from .native_tex_transport import remote_compile
        image = os.getenv("NATIVE_TEX_IMAGE_DIGEST", "")
        response = remote_compile(payload, url=remote, image=image,
            policy=os.getenv("NATIVE_TEX_ISOLATION_POLICY_SHA256", ""))
        return _compiler_pdf(response), image
    # Docker is a local evidence driver, never Docker-in-Docker on Cloud Run.
    if (os.getenv("APP_ENV", "production").lower() not in {"test", "dev", "development", "local"}
            or not re.fullmatch(r"(?:[a-zA-Z0-9._/:-]+@)?sha256:[a-f0-9]{64}", image)):
        raise ResumeLayoutError("The isolated TeX compiler is unavailable. Upload the original compiled PDF or a custom PDF/DOCX; your source is not converted on the API host.")
    import uuid
    container = "hirewiz-tex-" + uuid.uuid4().hex
    command = ["docker", "run", "--rm", "--name", container, "--pull=never", "--network=none",
        "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--user=65534:65534",
        "--cpus=1", "--memory=256m", "--memory-swap=256m", "--pids-limit=32",
        "--ulimit", "cpu=12:12", "--ulimit", "fsize=10485760:10485760",
        "--tmpfs", "/work:rw,noexec,nosuid,nodev,size=64m,uid=65534,gid=65534",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m,uid=65534,gid=65534", "-i", image]
    try:
        result = subprocess.run(command, input=payload, capture_output=True, timeout=30, check=False)
        if result.returncode or len(result.stdout) > 15 * 1024 * 1024:
            raise ValueError("compiler_refused")
        response = json.loads(result.stdout)
        return _compiler_pdf(response), image
    except ResumeLayoutError:
        raise
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError) as exc:
        raise ResumeLayoutError("The TeX source did not compile safely or overflowed its layout. Check missing assets, unsupported fonts/dependencies and text length, or use the original/custom PDF.") from exc
    finally:
        # subprocess timeout does not stop Docker's daemon-side container.
        try:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _compiler_pdf(response: Any) -> bytes:
    try:
        if (not isinstance(response, dict) or not isinstance(response.get("pdf"), str)
                or not isinstance(response.get("sha256"), str)
                or type(response.get("size")) is not int):
            raise ValueError("invalid_compiler_contract")
        pdf = base64.b64decode(response["pdf"], validate=True)
        if (response.get("protocol") != "hirewiz-tex-v1" or not pdf.startswith(b"%PDF-")
                or not 0 < len(pdf) <= MAX_PDF_BYTES or response["sha256"] != digest(pdf)
                or response["size"] != len(pdf)):
            raise ValueError("invalid_compiler_artifact")
        return pdf
    except (KeyError, TypeError, ValueError) as exc:
        raise ResumeLayoutError("The isolated TeX worker returned an invalid artifact. Use the original/custom PDF until the worker is checked.") from exc


def _compact(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def _validate_pdf_actions(reader) -> None:
    """Inspect the reachable object graph, including indirect/chained actions.

    Passive URI/GoTo links and hyperref's opening destination are supported.
    Action chains and ambiguous action dictionaries are deliberately refused.
    Cycles are visited once; graph depth/work are bounded independently of size.
    """
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

    message = "The compiled TeX document contains unsupported active content or an interactive action. Remove it or upload a plain PDF."
    active_keys = {"/AA", "/AcroForm", "/JavaScript", "/JS", "/EmbeddedFiles", "/EF",
        "/RichMediaContent", "/RichMediaSettings", "/XFA"}
    active_actions = {"/JavaScript", "/Launch", "/GoToR", "/GoToE", "/SubmitForm",
        "/ResetForm", "/ImportData", "/Rendition", "/Movie", "/Sound", "/Hide",
        "/Named", "/SetOCGState", "/Trans", "/GoTo3DView"}

    def passive_action(action) -> bool:
        if isinstance(action, IndirectObject):
            action = action.get_object()
        if not isinstance(action, DictionaryObject):
            return False
        if action.get("/S") == "/GoTo":
            return "/D" in action and set(action) <= {"/S", "/D", "/Type"}
        if action.get("/S") != "/URI" or set(action) - {"/S", "/URI", "/Type"}:
            return False
        uri = action.get("/URI")
        if not isinstance(uri, str) or any(ord(c) < 32 for c in uri):
            return False
        parsed = urlsplit(uri)
        return (parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)) or (
            parsed.scheme.lower() == "mailto" and bool(parsed.path))

    pending = [(reader.trailer, 0)]
    seen_objects: set[int] = set()
    seen_references: set[tuple[int, int]] = set()
    visited = 0
    try:
        while pending:
            obj, depth = pending.pop()
            if depth > 128 or visited > 20_000:
                raise ResumeLayoutError(message)
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
                    raise ResumeLayoutError(message)
                if obj.get("/S") in {"/URI", "/GoTo"} and not passive_action(obj):
                    raise ResumeLayoutError(message)
                if "/A" in obj and not passive_action(obj["/A"]):
                    raise ResumeLayoutError(message)
                if "/OpenAction" in obj:
                    opening = obj["/OpenAction"]
                    if not isinstance(opening, ArrayObject) and (
                            not passive_action(opening) or opening.get("/S") != "/GoTo"):
                        raise ResumeLayoutError(message)
                pending.extend((value, depth + 1) for value in obj.values())
            else:
                pending.extend((value, depth + 1) for value in obj)
    except ResumeLayoutError:
        raise
    except Exception as exc:
        raise ResumeLayoutError(message) from exc


def _pdf_view(data: bytes) -> tuple[list[dict], list, list]:
    try:
        return _checked_pdf_view(data)
    except ResumeLayoutError:
        raise
    except Exception as exc:
        raise ResumeLayoutError("The compiled TeX PDF is malformed or has unsupported interactive action data. Use the original/custom PDF until the worker is checked.") from exc


def _checked_pdf_view(data: bytes) -> tuple[list[dict], list, list]:
    import pdfplumber
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted or not 0 < len(reader.pages) <= 20:
        raise ResumeLayoutError("The compiled TeX document has unsupported encryption or page count. Use a simpler source or original PDF.")
    _validate_pdf_actions(reader)
    links = []
    for page_number, page in enumerate(reader.pages):
        if "/AA" in page:
            raise ResumeLayoutError("The compiled TeX document has an unsupported interactive action. Remove it or upload a plain PDF.")
        for annot in page.get("/Annots", []):
            obj = annot.get_object()
            action_ref = obj.get("/A")
            action = action_ref.get_object() if action_ref is not None else {}
            uri = str(action.get("/URI", ""))
            if (obj.get("/Subtype") != "/Link" or action.get("/S") != "/URI" or "/AA" in obj
                    or not re.match(r"^(?:https?://|mailto:)", uri, re.I)):
                raise ResumeLayoutError("The compiled TeX document has an unsupported interactive action. Remove it or upload a plain PDF.")
            links.append((page_number, uri, tuple(float(v) for v in obj.get("/Rect", []))))
    chars, pages = [], []
    with pdfplumber.open(BytesIO(data)) as document:
        for i, page in enumerate(document.pages):
            if not 200 <= page.width <= 900 or not 200 <= page.height <= 1500:
                raise ResumeLayoutError("The compiled TeX page size is unsupported. Use a standard resume page size or the original PDF.")
            pages.append((page.width, page.height))
            for c in page.chars:
                if c["fontname"] == "unknown" or "(cid:" in c["text"]:
                    raise ResumeLayoutError("The compiled TeX PDF contains glyphs without readable font mappings. Use a Unicode-mapped font such as Latin Modern or a readable original/custom PDF.")
                normalized = _compact(c["text"])
                for letter in normalized:
                    chars.append({"text": letter, "page": i, "font": re.sub(r"^[A-Z]{6}\+", "", c["fontname"]),
                        "size": round(c["size"], 3), "box": tuple(float(c[k]) for k in ("x0", "top", "x1", "bottom"))})
                if len(chars) > 200_000:
                    raise ResumeLayoutError("The compiled TeX document is too complex for layout validation. Use a simpler source or original PDF.")
    if not chars:
        raise ResumeLayoutError("The compiled TeX PDF has no readable text. Upload an editable source or a readable original PDF.")
    return chars, pages, links


def validate_layout(before: bytes, after: bytes, units: list[dict], replacements: dict[str, str]) -> dict:
    old, old_pages, old_links = _pdf_view(before)
    new, pages, links = _pdf_view(after)
    if old_pages != pages or old_links != links:
        raise ResumeLayoutError("The TeX edit changed pages, page geometry or links. Shorten the replacement or keep the original/custom resume.")
    old_text = "".join(c["text"] for c in old)
    regions = []
    for unit in units:
        if unit["unit_id"] not in replacements:
            continue
        text = _compact(unit["text"])
        if old_text.count(text) != 1:
            raise ResumeLayoutError("The TeX source span is not uniquely readable in the compiled PDF. Edit this source manually or use the original PDF.", unit_id=unit["unit_id"])
        start = old_text.index(text)
        regions.append((start, start + len(text), _compact(replacements[unit["unit_id"]]), unit))
    regions.sort()
    expected = old_text
    for start, end, replacement, _unit in reversed(regions):
        expected = expected[:start] + replacement + expected[end:]
    if expected != "".join(c["text"] for c in new):
        raise ResumeLayoutError("The TeX edit changed unexpected text or reading order. Keep the original/custom resume or edit the source manually.")
    old_protected, new_protected = [], []
    changed_boxes = []
    cursor = new_cursor = 0
    for start, end, replacement, unit in regions:
        new_start = new_cursor + start - cursor
        old_protected.extend(old[cursor:start])
        new_protected.extend(new[new_cursor:new_start])
        old_region = old[start:end]
        new_region = new[new_start:new_start + len(replacement)]
        fonts = {(c["font"], c["size"]) for c in old_region}
        bounds = (min(c["box"][0] for c in old_region), min(c["box"][1] for c in old_region),
            max(c["box"][2] for c in old_region), max(c["box"][3] for c in old_region))
        changed_boxes.append((old_region[0]["page"], bounds))
        for char in new_region:
            box = char["box"]
            if ((char["font"], char["size"]) not in fonts or char["page"] != old_region[0]["page"]
                    or box[0] < bounds[0] - .5 or box[1] < bounds[1] - .5
                    or box[2] > bounds[2] + .5 or box[3] > bounds[3] + .5):
                raise ResumeLayoutError("The TeX replacement cannot fit the original font and region. Shorten it or keep the original/custom resume.", unit_id=unit["unit_id"])
        cursor = end
        new_cursor = new_start + len(replacement)
    old_protected.extend(old[cursor:])
    new_protected.extend(new[new_cursor:])
    if len(old_protected) != len(new_protected) or any(
            a["text"] != b["text"] or a["page"] != b["page"] or a["font"] != b["font"] or a["size"] != b["size"]
            or any(abs(x - y) > .2 for x, y in zip(a["box"], b["box"], strict=True))
            for a, b in zip(old_protected, new_protected, strict=True)):
        raise ResumeLayoutError("The TeX edit moved protected text or changed typography. Shorten the edit or keep the original/custom resume.")
    # Native TeX macros can draw rules/icons conditionally. Protect all visible
    # pixels outside declared source-text regions, not only extracted glyphs.
    import pypdfium2 as pdfium
    from PIL import ImageChops, ImageDraw

    from .resume_layout import _PDF_LOCK
    with _PDF_LOCK, pdfium.PdfDocument(before) as old_document, pdfium.PdfDocument(after) as new_document:
        for page_index in range(len(pages)):
            with closing(old_document[page_index]) as old_page, closing(new_document[page_index]) as new_page:
                with closing(old_page.render(scale=1.5)) as old_bitmap, closing(new_page.render(scale=1.5)) as new_bitmap:
                    difference = ImageChops.difference(old_bitmap.to_pil().convert("RGB"), new_bitmap.to_pil().convert("RGB"))
                # Font advance boxes can omit descender ink; include a bounded
                # four-point ink/antialias margin, recorded in the validator.
                draw = ImageDraw.Draw(difference)
                for changed_page, box in changed_boxes:
                    if changed_page == page_index:
                        draw.rectangle((int(box[0] * 1.5) - 6, int(box[1] * 1.5) - 6,
                            int(box[2] * 1.5) + 6, int(box[3] * 1.5) + 6), fill=(0, 0, 0))
                if difference.getbbox() is not None:
                    raise ResumeLayoutError("The TeX edit changed visible layout outside its text span. Keep the original/custom resume or edit the source manually.")
    return {"validator": VALIDATOR, "pages": len(pages), "links": len(links), "changed_spans": len(regions),
        "protected_glyphs": len(old_protected), "fonts": sorted({c["font"] for c in new}),
        "reading_order": "unchanged_except_declared_spans", "geometry": "protected_glyphs_within_0.2pt", "render_scale": 1.5, "outside_changed_regions_pixel_delta": 0, "changed_region_ink_padding_points": 4}


def validate_claims(edits: list[dict], evidence: list[dict]) -> None:
    """Permit narrow ordered rewrites or complete cited approved statements.

    An unordered vocabulary cannot establish which action a qualifier limits.
    Qualified statements therefore retain their complete ordered structure;
    uncertain recombinations require manual review instead of a sealed claim.
    """
    allowed = {str(item["id"]): str(item.get("text") or "") + " " + " ".join(item.get("skills") or []) for item in evidence}
    grammar = {"a", "an", "the", "and", "for", "with", "using", "to", "of", "in", "on", "by", "from", "through", "that", "at"}
    creation = {"built", "developed", "created", "implemented"}
    qualifiers = {"not", "never", "no", "without", "only", "assisted", "supported", "contributed", "helped",
        "neither", "nor", "except", "partially", "primarily", "approximately", "solely", "jointly"}
    def words(text: str) -> set[str]:
        return set(re.findall(r"[^\W\d_]+", text.lower()))
    def statement(text: str) -> tuple[str, ...]:
        # Keep word order and clause punctuation: neither association nor scope
        # is inferred from a bag of supported words. The only automatic semantic
        # substitution is the existing narrow creation-verb family.
        tokens = re.findall(r"\w+|[^\w\s]", unicodedata.normalize("NFKC", text).lower())
        return tuple("<creation>" if token in creation else token for token in tokens)
    approved_statements = {str(item["id"]): str(item.get("text") or "") for item in evidence}
    for edit in edits:
        original = words(edit["original_text"])
        replacement = words(edit["replacement_text"])
        cited = " ".join(allowed.get(identity, "") for identity in edit.get("evidence_ids", []))
        support = original | words(cited) | grammar
        if original & creation:
            support |= creation
        if replacement - support or (original & qualifiers) - replacement:
            raise ResumeLayoutError("The TeX edit adds unsupported responsibility or achievement wording, or removes a source qualifier. Use wording supported by cited approved evidence or keep the original.", unit_id=edit.get("unit_id"))
        if statement(edit["original_text"]) == statement(edit["replacement_text"]):
            continue
        if (original | replacement) & qualifiers:
            raise ResumeLayoutError("The TeX edit changes a source qualifier or its claim scope. Keep the original qualified statement or edit and verify the source manually.", unit_id=edit.get("unit_id"))
        cited_statements = [approved_statements.get(identity, "") for identity in edit.get("evidence_ids", [])]
        if not any(statement(edit["replacement_text"]) == statement(value) for value in cited_statements if value):
            raise ResumeLayoutError("The TeX edit recombines supported words into an unsupported responsibility or achievement. Use a complete cited approved statement or keep the original.", unit_id=edit.get("unit_id"))


def prepare_artifact(data: bytes, kind: str, edits: list[dict], approved_evidence: list[dict] | None = None) -> dict[str, Any]:
    from .resume_layout import _candidate, _validated_edits
    units = source_units(data, kind, _candidate)
    replacements = _validated_edits(units, edits)
    validate_claims(edits, approved_evidence or [])
    native = patch_project(data, kind, units, replacements) if replacements else data
    before, image = compile_project(data, kind)
    after, _ = compile_project(native, kind) if replacements else (before, image)
    validation = validate_layout(before, after, units, replacements)
    return {"validator": VALIDATOR, "renderer_image": image, "source_sha256": digest(data),
        "edits_sha256": digest(json.dumps(edits, sort_keys=True, separators=(",", ":")).encode()),
        "pdf_sha256": digest(after), "size": len(after), "media_type": "application/pdf",
        "pdf_base64": base64.b64encode(after).decode(), "native_sha256": digest(native),
        "native_base64": base64.b64encode(native).decode(), "validation": validation,
        "classification": "tailored" if replacements else "unchanged_source_snapshot"}


def sealed_bytes(seal: dict | None, data: bytes, kind: str, edits: list[dict], output: str) -> bytes:
    if not isinstance(seal, dict):
        raise ResumeLayoutError("This TeX version has no sealed review artifact. Prepare a new version in the isolated compiler before approval.")
    if (seal.get("validator") != VALIDATOR or seal.get("source_sha256") != digest(data)
            or seal.get("edits_sha256") != digest(json.dumps(edits, sort_keys=True, separators=(",", ":")).encode())):
        raise ResumeLayoutError("The TeX source or edits changed after review. Prepare a new version and review it again.")
    try:
        key = "pdf" if output == "pdf" else "native"
        result = base64.b64decode(seal[key + "_base64"], validate=True)
        if not result or len(result) > MAX_PDF_BYTES or digest(result) != seal[key + "_sha256"]:
            raise ValueError("artifact_changed")
        return result
    except (KeyError, ValueError) as exc:
        raise ResumeLayoutError("The sealed TeX artifact failed integrity verification. Prepare a new version; do not use this file.") from exc
