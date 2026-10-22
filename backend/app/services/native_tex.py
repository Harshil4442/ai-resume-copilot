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
from dataclasses import dataclass, field
from io import BytesIO
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from .native_project import CLASSES as CLASSES
from .native_project import DANGEROUS as DANGEROUS
from .native_project import DEPENDENCY as DEPENDENCY
from .native_project import DYNAMIC_COMMANDS as DYNAMIC_COMMANDS
from .native_project import DYNAMIC_DATES as DYNAMIC_DATES
from .native_project import EXTENSIONS as EXTENSIONS
from .native_project import INCLUDE as INCLUDE
from .native_project import MAX_FILES as MAX_FILES
from .native_project import MAX_PROJECT_BYTES as MAX_PROJECT_BYTES
from .native_project import NATIVE_FORMATS as NATIVE_FORMATS
from .native_project import PACKAGES as PACKAGES
from .native_project import PATH as PATH
from .native_project import Project as Project
from .native_project import _path as _path
from .native_project import read_project as read_project
from .resume_layout import ResumeLayoutError, ResumeProcessingUnavailable

if TYPE_CHECKING:
    from .document_ingestion import InspectedResume, WorkerConfig

MAX_PDF_BYTES = 10 * 1024 * 1024
VALIDATOR = "hirewiz-native-tex-v2"
SPAN = re.compile(r"\\resume(?:Item|Bullet)\{([^{}\\\n]+)\}|\\item(?:\[[^{}\\\n\]]{1,16}\])?\s+([^{}\\\n]+)|(?m:^([A-Za-z][^{}\\\n%]+)$)")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


@dataclass(frozen=True)
class InspectedNativeCompilation:
    """Exact compiled bytes and private admission evidence; no candidate repr."""

    pdf: bytes = field(repr=False)
    renderer_image: str
    renderer_policy_sha256: str
    source_inspection: InspectedResume = field(repr=False)
    pdf_inspection: InspectedResume = field(repr=False)


def _inspected(source: bytes, kind: str, config: WorkerConfig) -> InspectedResume:
    from .document_ingestion import (
        DocumentInspectionError,
        InspectedResume,
        _decode_receipt,
        inspect_resume_document_with_receipt,
    )

    try:
        receipt = inspect_resume_document_with_receipt(source, source_format=kind)
    except DocumentInspectionError as exc:
        if exc.status_code == 422:
            raise ResumeLayoutError("The TeX project or compiled PDF could not pass private resume safety checks. Use a plain original/custom PDF or supported source project.") from None
        raise ResumeProcessingUnavailable("Private TeX resume safety checks are unavailable. Keep the original/custom file and try again later.") from None
    if (type(receipt) is not InspectedResume or receipt.sha256 != digest(source)
            or type(receipt.size_bytes) is not int or receipt.size_bytes != len(source)
            or receipt.source_format != kind or receipt.worker_image != config.image
            or receipt.policy_sha256 != config.policy or receipt.scan_engine != "ClamAV"
            or any(not isinstance(value, str) or not 0 < len(value) <= 200
                   or any(ord(char) < 32 or ord(char) > 126 for char in value)
                   for value in (receipt.scan_version, receipt.scan_definitions))):
        raise ResumeProcessingUnavailable("The private TeX safety receipt could not be verified. Keep the original/custom file and try again later.")
    parsed = receipt.parsed
    try:
        if not isinstance(parsed, tuple) or len(parsed) != 5:
            raise ValueError
        # Reuse the worker contract's complete parsed/receipt validator even if
        # an unexpected helper result bypassed the transport decoder.
        receipt = _decode_receipt(json.dumps({
            "version": 1, "sha256": receipt.sha256, "size_bytes": receipt.size_bytes,
            "source_format": receipt.source_format, "worker_image": receipt.worker_image,
            "policy_sha256": receipt.policy_sha256,
            "scan": {"engine": receipt.scan_engine, "version": receipt.scan_version,
                     "definitions": receipt.scan_definitions},
            "parsed": dict(zip(("raw_text", "sections", "skills", "experience_years", "contact_info"), parsed, strict=True)),
        }).encode(), source, kind, config)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        raise ResumeProcessingUnavailable("The private TeX safety receipt could not be verified. Keep the original/custom file and try again later.") from None
    parsed = receipt.parsed
    if kind in NATIVE_FORMATS and (
                parsed[0] != "" or parsed[1] != {} or parsed[2] != []
                or type(parsed[3]) not in (int, float) or parsed[3] != 0
                or not isinstance(parsed[4], dict)
                or set(parsed[4]) - {"name", "email", "phone", "linkedin", "github"}
                or any(value is not None for value in parsed[4].values())):
        raise ResumeProcessingUnavailable("The private TeX safety receipt could not be verified. Keep the original/custom file and try again later.")
    return receipt


def compile_project_inspected(data: bytes, kind: str) -> InspectedNativeCompilation:
    from .document_ingestion import MAX_SOURCE_BYTES, DocumentInspectionError, worker_config

    try:
        config = worker_config()
    except DocumentInspectionError:
        raise ResumeProcessingUnavailable("Private TeX resume safety checks are unavailable. Keep the original/custom file and try again later.") from None
    source_inspection = _inspected(data, kind, config)
    project = read_project(data, kind)
    pdf, image, policy = _compile_project(project)
    if len(pdf) > MAX_SOURCE_BYTES:
        raise ResumeLayoutError("The compiled TeX PDF exceeds the 5 MB private safety-check limit. Reduce the source or included assets, or use a smaller original/custom PDF.")
    pdf_inspection = _inspected(pdf, "pdf", config)
    return InspectedNativeCompilation(pdf, image, policy, source_inspection, pdf_inspection)


def compile_project(data: bytes, kind: str) -> tuple[bytes, str]:
    inspected = compile_project_inspected(data, kind)
    return inspected.pdf, inspected.renderer_image


def _compile_project(project: Project) -> tuple[bytes, str, str]:
    image = os.getenv("NATIVE_TEX_IMAGE", "")
    policy = os.getenv("NATIVE_TEX_ISOLATION_POLICY_SHA256", "")
    if not re.fullmatch(r"[a-f0-9]{64}", policy):
        raise ResumeProcessingUnavailable("The private TeX worker isolation contract is unavailable. Use your original/custom PDF until the reviewed worker is configured.")
    payload = json.dumps({"protocol": "hirewiz-tex-v1", "main": project.main,
        "files": [{"path": name, "data": base64.b64encode(value).decode()} for name, value in sorted(project.files.items())]}).encode()
    remote = os.getenv("NATIVE_TEX_COMPILER_URL", "")
    if remote:
        from .native_tex_transport import remote_compile
        image = os.getenv("NATIVE_TEX_IMAGE_DIGEST", "")
        response = remote_compile(payload, url=remote, image=image, policy=policy)
        if (type(response) is not dict or response.get("renderer_image") != image
                or response.get("isolation_policy_sha256") != policy
                or response.get("request_sha256") != digest(payload)
                or response.get("renderer") != "pdftex-no-shell-escape-v1"):
            raise ResumeProcessingUnavailable("The isolated TeX worker returned an invalid artifact. Use the original/custom PDF until the worker is checked.")
        return _compiler_pdf(response), image, policy
    # Docker is a local evidence driver, never Docker-in-Docker on Cloud Run.
    if (os.getenv("APP_ENV", "production").lower() not in {"test", "dev", "development", "local"}
            or not re.fullmatch(r"(?:[a-zA-Z0-9._/:-]+@)?sha256:[a-f0-9]{64}", image)):
        raise ResumeProcessingUnavailable("The isolated TeX compiler is unavailable. Upload the original compiled PDF or a custom PDF/DOCX; your source is not converted on the API host.")
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
        if len(result.stdout) > 15 * 1024 * 1024:
            raise ResumeProcessingUnavailable("The isolated TeX worker returned an invalid artifact. Use the original/custom PDF until the worker is checked.")
        try:
            response = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise ResumeProcessingUnavailable("The isolated TeX worker returned an invalid artifact. Use the original/custom PDF until the worker is checked.") from None
        if result.returncode:
            if result.returncode == 1 and response == {"error": "compile_refused"}:
                raise ValueError("compiler_refused")
            raise ResumeProcessingUnavailable("The isolated TeX compiler is unavailable. Upload the original compiled PDF or a custom PDF/DOCX; your source is not converted on the API host.")
        return _compiler_pdf(response), image, policy
    except ResumeLayoutError:
        raise
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ResumeProcessingUnavailable("The isolated TeX compiler is unavailable. Upload the original compiled PDF or a custom PDF/DOCX; your source is not converted on the API host.") from exc
    except (ValueError, KeyError) as exc:
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
        raise ResumeProcessingUnavailable("The isolated TeX worker returned an invalid artifact. Use the original/custom PDF until the worker is checked.") from exc


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
    inspected = compile_project_inspected(data, kind)
    units = source_units(data, kind, _candidate)
    replacements = _validated_edits(units, edits)
    validate_claims(edits, approved_evidence or [])
    native = patch_project(data, kind, units, replacements) if replacements else data
    candidate = compile_project_inspected(native, kind) if replacements else inspected
    if (inspected.renderer_image != candidate.renderer_image
            or inspected.renderer_policy_sha256 != candidate.renderer_policy_sha256):
        raise ResumeProcessingUnavailable("The private TeX renderer changed during preparation. Keep the original/custom file and try again later.")
    before, after = inspected.pdf, candidate.pdf
    seal: dict[str, Any] = {"validator": VALIDATOR, "renderer_image": inspected.renderer_image,
        "renderer_policy_sha256": inspected.renderer_policy_sha256, "source_sha256": digest(data),
        "edits_sha256": digest(json.dumps(edits, sort_keys=True, separators=(",", ":")).encode()),
        "pdf_sha256": digest(after), "size": len(after), "media_type": "application/pdf",
        "pdf_base64": base64.b64encode(after).decode(), "native_sha256": digest(native),
        "native_base64": base64.b64encode(native).decode(),
        "inspection_version": 1, "inspections": {
            "source": _inspection_metadata(inspected.source_inspection),
            "source_pdf": _inspection_metadata(inspected.pdf_inspection),
            "candidate": _inspection_metadata(candidate.source_inspection),
            "candidate_pdf": _inspection_metadata(candidate.pdf_inspection)},
        "classification": "tailored" if replacements else "unchanged_source_snapshot"}
    try:
        _validate_seal_inspections(seal, data, kind, native, after, original_pdf=before)
    except (KeyError, TypeError, ValueError):
        raise ResumeProcessingUnavailable("The private TeX safety receipts could not be verified. Keep the original/custom file and try again later.") from None
    seal["validation"] = validate_layout(before, after, units, replacements)
    return seal


def _inspection_metadata(inspection: InspectedResume) -> dict[str, Any]:
    from .document_ingestion import InspectedResume

    # Deliberately exclude parsed candidate facts from durable seal metadata.
    if type(inspection) is not InspectedResume:
        raise ResumeProcessingUnavailable("The private TeX safety receipts could not be verified. Keep the original/custom file and try again later.")
    return {"sha256": inspection.sha256, "size_bytes": inspection.size_bytes,
        "source_format": inspection.source_format, "worker_image": inspection.worker_image,
        "policy_sha256": inspection.policy_sha256, "scan_engine": inspection.scan_engine,
        "scan_version": inspection.scan_version, "scan_definitions": inspection.scan_definitions}


def _validate_inspection_metadata(record: Any, kind: str, source: bytes | None) -> None:
    if (not isinstance(record, dict) or set(record) != {
            "sha256", "size_bytes", "source_format", "worker_image", "policy_sha256",
            "scan_engine", "scan_version", "scan_definitions"}
            or not isinstance(record["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"])
            or type(record["size_bytes"]) is not int or not 0 < record["size_bytes"] <= MAX_PROJECT_BYTES
            or record["source_format"] != kind
            or not isinstance(record["worker_image"], str)
            or not re.fullmatch(r"[a-zA-Z0-9._/:-]+@sha256:[a-f0-9]{64}", record["worker_image"])
            or not isinstance(record["policy_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", record["policy_sha256"])
            or record["scan_engine"] != "ClamAV"
            or any(not isinstance(value, str) or not 0 < len(value) <= 200
                   or any(ord(char) < 32 or ord(char) > 126 for char in value)
                   for value in (record["scan_version"], record["scan_definitions"]))
            or source is not None and (record["sha256"] != digest(source) or record["size_bytes"] != len(source))):
        raise ValueError("invalid_inspection_metadata")


def _validate_seal_inspections(seal: dict, data: bytes, kind: str, native: bytes, pdf: bytes,
                               *, original_pdf: bytes | None = None) -> None:
    records = seal["inspections"]
    if (type(seal["inspection_version"]) is not int or seal["inspection_version"] != 1
            or kind not in NATIVE_FORMATS or not isinstance(records, dict)
            or set(records) != {"source", "source_pdf", "candidate", "candidate_pdf"}
            or not isinstance(seal["renderer_image"], str)
            or not re.fullmatch(r"(?:[a-zA-Z0-9._/:-]+@)?sha256:[a-f0-9]{64}", seal["renderer_image"])
            or not isinstance(seal["renderer_policy_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", seal["renderer_policy_sha256"])
            or type(seal["size"]) is not int or seal["size"] != len(pdf)
            or seal["native_sha256"] != digest(native) or seal["pdf_sha256"] != digest(pdf)
            or not 0 < len(native) <= MAX_PROJECT_BYTES or not 0 < len(pdf) <= MAX_PROJECT_BYTES):
        raise ValueError("invalid_inspection_seal")
    for name, source_kind, content in (("source", kind, data), ("source_pdf", "pdf", original_pdf),
                                       ("candidate", kind, native), ("candidate_pdf", "pdf", pdf)):
        _validate_inspection_metadata(records[name], source_kind, content)
    if any((record["worker_image"], record["policy_sha256"]) != (
            records["source"]["worker_image"], records["source"]["policy_sha256"]) for record in records.values()):
        raise ValueError("inspection_worker_changed")
    if native == data and (records["source"] != records["candidate"] or records["source_pdf"] != records["candidate_pdf"]):
        raise ValueError("unchanged_inspection_changed")


def sealed_bytes(seal: dict | None, data: bytes, kind: str, edits: list[dict], output: str) -> bytes:
    if not isinstance(seal, dict):
        raise ResumeLayoutError("This TeX version has no sealed review artifact. Prepare a new version in the isolated compiler before approval.")
    if (seal.get("validator") != VALIDATOR or seal.get("source_sha256") != digest(data)
            or seal.get("edits_sha256") != digest(json.dumps(edits, sort_keys=True, separators=(",", ":")).encode())):
        raise ResumeLayoutError("The TeX source or edits changed after review. Prepare a new version and review it again.")
    if "inspection_version" in seal:
        try:
            # New seals verify both retained outputs even when only one is requested.
            if not isinstance(seal["native_base64"], str) or not isinstance(seal["pdf_base64"], str):
                raise ValueError("invalid_artifact_encoding")
            native = base64.b64decode(seal["native_base64"], validate=True)
            pdf = base64.b64decode(seal["pdf_base64"], validate=True)
            _validate_seal_inspections(seal, data, kind, native, pdf)
            return pdf if output == "pdf" else native
        except (KeyError, TypeError, ValueError) as exc:
            raise ResumeLayoutError("The sealed TeX artifact failed integrity verification. Prepare a new version; do not use this file.") from exc
    # Historical v2 seals retain their original retrieval contract and policy.
    try:
        key = "pdf" if output == "pdf" else "native"
        result = base64.b64decode(seal[key + "_base64"], validate=True)
        if not result or len(result) > MAX_PDF_BYTES or digest(result) != seal[key + "_sha256"]:
            raise ValueError("artifact_changed")
        return result
    except (KeyError, ValueError) as exc:
        raise ResumeLayoutError("The sealed TeX artifact failed integrity verification. Prepare a new version; do not use this file.") from exc
