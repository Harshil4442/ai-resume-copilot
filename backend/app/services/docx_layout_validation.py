"""Pure, fail-closed comparison of a native DOCX edit and its rendered PDFs.

This module does not render, scan, fetch, or persist documents. A trusted caller
must scan/validate the DOCX input, apply approved edits, and render these exact
bytes in its pinned isolated renderer. The receipt binds those inputs and the
renderer/font/policy identity; it does not attest an unobserved renderer run.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import math
import re
import threading
import unicodedata
from contextlib import closing
from dataclasses import asdict, dataclass
from io import BytesIO
from typing import Any, TypedDict
from urllib.parse import urlsplit
from zipfile import ZipFile

import pdfplumber
import pypdfium2 as pdfium
from lxml import etree
from PIL import ImageChops, ImageDraw
from pypdf import PdfReader
from pypdf.generic import (
    ArrayObject,
    ContentStream,
    DictionaryObject,
    FloatObject,
    IndirectObject,
    NullObject,
    NumberObject,
)

VALIDATOR = "hirewiz-docx-layout-v1"
MAX_DOCX_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_GLYPHS = 200_000
GEOMETRY_TOLERANCE = 0.2
RENDER_SCALE = 1.5
INK_MARGIN_POINTS = 4
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_IMAGE = re.compile(r"[A-Za-z0-9._/:-]+@sha256:[a-f0-9]{64}\Z")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_PDF_LOCK = threading.Lock()
LAYOUT_REASONS = frozenset({
    "renderer_identity", "document_size", "document_package", "package_changed", "edit_binding",
    "hyperlink_changed", "no_changed_paragraphs", "undeclared_native_change", "pdf_active_content",
    "pdf_link", "unsupported_graphics", "pdf_complexity", "unsupported_paint", "pdf_size", "pdf_pages",
    "pdf_geometry", "font_mapping", "pdf_text", "outside_region_pixels", "page_geometry_changed",
    "nontext_changed", "ambiguous_rendered_span", "text_or_reading_order_changed", "paragraph_page_changed",
    "line_wrap_changed", "font_changed", "retained_style_changed", "paragraph_overflow",
    "unsupported_graphics_overlap", "protected_text_changed", "protected_geometry_changed", "malformed_input",
})


class _Glyph(TypedDict):
    text: str
    page: int
    font: str
    size: float
    box: tuple[float, ...]


class DocxLayoutError(ValueError):
    """Finite reason only; source text and renderer exceptions never escape."""

    def __init__(self, reason: str, *, unit_id: str | None = None):
        self.reason = reason
        self.unit_id = unit_id
        super().__init__("DOCX layout validation refused: " + reason)


@dataclass(frozen=True)
class RendererIdentity:
    image_digest: str
    policy_sha256: str
    renderer_version: str
    font_manifest_sha256: str


@dataclass(frozen=True)
class DocxLayoutReceipt:
    validator: str
    source_sha256: str
    source_size_bytes: int
    candidate_sha256: str
    candidate_size_bytes: int
    edits_sha256: str
    before_pdf_sha256: str
    after_pdf_sha256: str
    renderer: RendererIdentity
    rendered_fonts_sha256: str
    pages: int
    changed_paragraphs: int
    protected_glyphs: int
    max_protected_geometry_delta_points: float
    unchanged_links: int
    unchanged_graphics: int
    outside_changed_regions_pixel_delta: int = 0
    render_scale: float = RENDER_SCALE
    ink_margin_points: int = INK_MARGIN_POINTS

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode()


def _compact(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


def _identity(renderer: RendererIdentity) -> None:
    if (type(renderer) is not RendererIdentity
            or not all(isinstance(value, str) for value in (
                renderer.image_digest, renderer.policy_sha256, renderer.font_manifest_sha256))
            or len(renderer.image_digest) > 512
            or not _IMAGE.fullmatch(renderer.image_digest)
            or not _HASH.fullmatch(renderer.policy_sha256)
            or not _HASH.fullmatch(renderer.font_manifest_sha256)
            or not isinstance(renderer.renderer_version, str)
            or not 0 < len(renderer.renderer_version) <= 200
            or any(ord(c) < 32 or ord(c) > 126 for c in renderer.renderer_version)):
        raise DocxLayoutError("renderer_identity")


def check_layout_receipt(data: Any, *, source_sha256: str, source_size_bytes: int,
                         candidate_sha256: str, candidate_size_bytes: int, edits_sha256: str,
                         image: str, policy: str, renderer_version: str | None = None,
                         font_manifest_sha256: str | None = None) -> dict[str, Any]:
    """Strict bound protocol check, never renderer execution attestation.

    The HTTP adapter supplies its immutable image/policy configuration. The API
    additionally supplies renderer/font pins observed in native image acceptance.
    No document parser or renderer is invoked by this protocol-only function.
    """
    keys = {"validator", "source_sha256", "source_size_bytes", "candidate_sha256", "candidate_size_bytes",
            "edits_sha256", "before_pdf_sha256", "after_pdf_sha256", "renderer", "rendered_fonts_sha256",
            "pages", "changed_paragraphs", "protected_glyphs", "max_protected_geometry_delta_points",
            "unchanged_links", "unchanged_graphics", "outside_changed_regions_pixel_delta", "render_scale",
            "ink_margin_points"}
    if (type(data) is not dict or set(data) != keys or data["validator"] != VALIDATOR
            or data["source_sha256"] != source_sha256 or data["candidate_sha256"] != candidate_sha256
            or data["edits_sha256"] != edits_sha256
            or any(type(value) is not str or not _HASH.fullmatch(value)
                   for value in (source_sha256, candidate_sha256, edits_sha256))):
        raise DocxLayoutError("edit_binding")
    for key, value in (("source_size_bytes", source_size_bytes), ("candidate_size_bytes", candidate_size_bytes)):
        if type(value) is not int or type(data[key]) is not int or data[key] != value or not 0 < value <= MAX_DOCX_BYTES:
            raise DocxLayoutError("document_size")
    for key in ("before_pdf_sha256", "after_pdf_sha256", "rendered_fonts_sha256"):
        if type(data[key]) is not str or not _HASH.fullmatch(data[key]):
            raise DocxLayoutError("edit_binding")
    identity = data["renderer"]
    if type(identity) is not dict or set(identity) != {
        "image_digest", "policy_sha256", "renderer_version", "font_manifest_sha256"
    }:
        raise DocxLayoutError("renderer_identity")
    _identity(RendererIdentity(**identity))
    if (identity["image_digest"] != image or identity["policy_sha256"] != policy
            or (renderer_version is not None and identity["renderer_version"] != renderer_version)
            or (font_manifest_sha256 is not None and identity["font_manifest_sha256"] != font_manifest_sha256)):
        raise DocxLayoutError("renderer_identity")
    for key, lower, upper in (("pages", 1, 20), ("changed_paragraphs", 1, 100),
                              ("protected_glyphs", 0, MAX_GLYPHS), ("unchanged_links", 0, 20_000),
                              ("unchanged_graphics", 0, 20_020)):
        if type(data[key]) is not int or not lower <= data[key] <= upper:
            raise DocxLayoutError("edit_binding")
    if (type(data["outside_changed_regions_pixel_delta"]) is not int
            or data["outside_changed_regions_pixel_delta"] != 0
            or type(data["render_scale"]) is not float or data["render_scale"] != RENDER_SCALE
            or type(data["ink_margin_points"]) is not int or data["ink_margin_points"] != INK_MARGIN_POINTS
            or type(data["max_protected_geometry_delta_points"]) not in {int, float}
            or not math.isfinite(data["max_protected_geometry_delta_points"])
            or not 0 <= data["max_protected_geometry_delta_points"] <= GEOMETRY_TOLERANCE):
        raise DocxLayoutError("edit_binding")
    return data


def _document(data: bytes) -> tuple[dict[str, bytes], Any]:
    if type(data) is not bytes or not 0 < len(data) <= MAX_DOCX_BYTES:
        raise DocxLayoutError("document_size")
    with ZipFile(BytesIO(data)) as archive:
        infos = archive.infolist()
        names = [entry.filename for entry in infos]
        if (not 0 < len(names) <= 500 or len(names) != len(set(names))
                or "word/document.xml" not in names
                or sum(entry.file_size for entry in infos) > 20 * 1024 * 1024
                or any(entry.file_size > 10 * 1024 * 1024 or entry.flag_bits & 1
                       for entry in infos)):
            raise DocxLayoutError("document_package")
        parts = {entry.filename: archive.read(entry) for entry in infos}
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                             huge_tree=False)
    tree = etree.fromstring(parts["word/document.xml"], parser)
    if (tree.getroottree().docinfo.doctype or tree.tag != _W + "document"
            or tree.find(_W + "body") is None):
        raise DocxLayoutError("document_package")
    nodes = list(tree.iter())
    if len(nodes) > 100_000 or any(len(list(node.iterancestors())) > 64 for node in nodes):
        raise DocxLayoutError("document_package")
    return parts, tree


def _distribute(nodes: list[Any], original: str, replacement: str) -> list[str]:
    # Same source-run allocation as resume_layout._distribute_text. Verification
    # repeats that deterministic allocation; it does not introduce a fit heuristic.
    owners = [index for index, node in enumerate(nodes) for _ in (node.text or "")]
    output = ["" for _ in nodes]
    for opcode, old_start, old_end, new_start, new_end in difflib.SequenceMatcher(
        None, original, replacement, autojunk=False
    ).get_opcodes():
        value = replacement[new_start:new_end]
        if opcode == "equal":
            for offset, char in enumerate(value):
                output[owners[old_start + offset]] += char
        elif opcode == "insert":
            output[owners[min(old_start, len(owners) - 1)]] += value
        elif opcode == "replace":
            for offset, char in enumerate(value):
                old_offset = min(old_end - old_start - 1,
                                 offset * (old_end - old_start) // len(value))
                output[owners[old_start + old_offset]] += char
    return output


def _native_edits(source: bytes, candidate: bytes, edits: list[dict]) -> list[tuple[str, str, str]]:
    old_parts, old = _document(source)
    new_parts, new = _document(candidate)
    if (old_parts.keys() != new_parts.keys()
            or any(value != new_parts[name] for name, value in old_parts.items()
                   if name != "word/document.xml")):
        raise DocxLayoutError("package_changed")
    if type(edits) is not list or not 0 < len(edits) <= 100:
        raise DocxLayoutError("edit_binding")
    paragraphs = list(old.find(_W + "body").iter(_W + "p"))
    available = {}
    source_prefix = _sha(source)[:16]
    for index, paragraph in enumerate(paragraphs):
        text = "".join(node.text or "" for node in paragraph.iter(_W + "t"))
        unit = (f"docx:{source_prefix}:p{index}:"
                f"{_sha(text.encode())[:12]}")
        available[unit] = (paragraph, text)
    changes, seen = [], set()
    for edit in edits:
        if type(edit) is not dict or type(edit.get("unit_id")) is not str:
            raise DocxLayoutError("edit_binding")
        if (set(edit) - {"unit_id", "original_text", "replacement_text", "evidence_ids", "reason"}
                or ("reason" in edit and (not isinstance(edit["reason"], str)
                                         or len(edit["reason"]) > 2000))
                or ("evidence_ids" in edit and (type(edit["evidence_ids"]) is not list
                    or len(edit["evidence_ids"]) > 100
                    or any(not isinstance(value, str) or len(value) > 200
                           for value in edit["evidence_ids"])))):
            raise DocxLayoutError("edit_binding")
        unit = edit["unit_id"]
        if unit not in available or unit in seen:
            raise DocxLayoutError("edit_binding")
        seen.add(unit)
        paragraph, original = available[unit]
        replacement = edit.get("replacement_text")
        if (edit.get("original_text") != original or not 0 < len(original) <= 2000
                or not isinstance(replacement, str) or not replacement.strip()
                or len(replacement) > 2000 or _CONTROL.search(replacement)
                or any(0xD800 <= ord(c) <= 0xDFFF for c in replacement)):
            raise DocxLayoutError("edit_binding")
        nodes = list(paragraph.iter(_W + "t"))
        for node, value in zip(nodes, _distribute(nodes, original, replacement), strict=True):
            if value != (node.text or ""):
                if any(parent.tag == _W + "hyperlink" for parent in node.iterancestors()):
                    raise DocxLayoutError("hyperlink_changed", unit_id=unit)
                node.text = value
                if value.startswith(" ") or value.endswith(" "):
                    node.set(_SPACE, "preserve")
        if replacement != original:
            changes.append((original, replacement, unit))
    if not changes:
        raise DocxLayoutError("no_changed_paragraphs")
    if etree.tostring(old, method="c14n") != etree.tostring(new, method="c14n"):
        raise DocxLayoutError("undeclared_native_change")
    return changes


def _pdf_actions(reader: PdfReader) -> None:
    active = {"/AA", "/AcroForm", "/JavaScript", "/JS", "/EmbeddedFiles", "/EF",
              "/RichMediaContent", "/RichMediaSettings", "/XFA"}
    active_actions = {"/JavaScript", "/Launch", "/GoToR", "/GoToE", "/SubmitForm",
                      "/ResetForm", "/ImportData", "/Rendition", "/Movie", "/Sound",
                      "/Hide", "/Named", "/SetOCGState", "/Trans", "/GoTo3DView"}

    def passive(action: Any) -> bool:
        if isinstance(action, IndirectObject):
            action = action.get_object()
        if not isinstance(action, DictionaryObject):
            return False
        if action.get("/S") == "/GoTo":
            return "/D" in action and set(action) <= {"/S", "/D", "/Type"}
        if action.get("/S") != "/URI" or set(action) - {"/S", "/URI", "/Type"}:
            return False
        uri = action.get("/URI")
        if not isinstance(uri, str) or _CONTROL.search(uri):
            return False
        parsed = urlsplit(uri)
        return ((parsed.scheme in {"http", "https"} and bool(parsed.hostname))
                or (parsed.scheme == "mailto" and bool(parsed.path)))

    pending: list[tuple[Any, int]] = [(reader.trailer, 0)]
    seen: set[int] = set()
    references: set[tuple[int, int]] = set()
    while pending:
        obj, depth = pending.pop()
        if depth > 128 or len(seen) > 20_000:
            raise DocxLayoutError("pdf_active_content")
        if isinstance(obj, IndirectObject):
            ref = (obj.idnum, obj.generation)
            if ref in references:
                continue
            references.add(ref)
            obj = obj.get_object()
        if not isinstance(obj, (ArrayObject, DictionaryObject)) or id(obj) in seen:
            continue
        seen.add(id(obj))
        if isinstance(obj, DictionaryObject):
            if active & set(obj) or obj.get("/S") in active_actions:
                raise DocxLayoutError("pdf_active_content")
            if (obj.get("/S") in {"/URI", "/GoTo"} or obj.get("/Type") == "/Action") and not passive(obj):
                raise DocxLayoutError("pdf_active_content")
            if "/A" in obj and not passive(obj["/A"]):
                raise DocxLayoutError("pdf_active_content")
            if "/OpenAction" in obj:
                opening = obj["/OpenAction"]
                if isinstance(opening, IndirectObject):
                    opening = opening.get_object()
                if not isinstance(opening, ArrayObject) and not (
                    passive(opening) and opening.get("/S") == "/GoTo"
                ):
                    raise DocxLayoutError("pdf_active_content")
            pending.extend((value, depth + 1) for value in obj.values())
        else:
            pending.extend((value, depth + 1) for value in obj)


def _destination(reader: PdfReader, value: Any) -> tuple:
    if isinstance(value, str):
        value = reader.named_destinations.get(value)
        if value is None:
            raise DocxLayoutError("pdf_link")
        value = value.dest_array
    if not isinstance(value, ArrayObject) or not 2 <= len(value) <= 6:
        raise DocxLayoutError("pdf_link")
    reference = value[0]
    index = next((i for i, page in enumerate(reader.pages)
                  if page.indirect_reference == reference), None)
    if index is None or value[1] not in {
        "/XYZ", "/Fit", "/FitH", "/FitV", "/FitR", "/FitB", "/FitBH", "/FitBV"
    }:
        raise DocxLayoutError("pdf_link")
    coordinates = tuple(None if item is None or isinstance(item, NullObject) else float(item)
                        for item in value[2:])
    if any(number is not None and not math.isfinite(number) for number in coordinates):
        raise DocxLayoutError("pdf_link")
    return (index, str(value[1]), coordinates)


def _graphic_value(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        raise DocxLayoutError("unsupported_graphics")
    if value is None or type(value) in {bool, int}:
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, str) and len(value) <= 200 and not _CONTROL.search(value):
        return value
    if isinstance(value, (list, tuple)) and len(value) <= 5000:
        return tuple(_graphic_value(item, depth + 1) for item in value)
    raise DocxLayoutError("unsupported_graphics")


def _graphics(page: Any, index: int) -> list[tuple]:
    """Bind exposed vector geometry/paint; images are checked outside edit masks.

    Image content/color-space interpretation inside a masked paragraph is not
    certified by this core. Such overlap refuses instead of masking the image.
    """
    result: list[tuple] = []
    fields = ("x0", "y0", "x1", "y1", "top", "bottom", "linewidth", "stroke", "fill",
              "evenodd", "stroking_color", "non_stroking_color", "pts", "path", "dash")
    for kind in ("line", "rect", "curve", "image"):
        for obj in page.objects.get(kind, []):
            if len(result) >= 20_000:
                raise DocxLayoutError("pdf_complexity")
            signature: tuple[Any, ...]
            if kind == "image":
                signature = tuple(float(obj[key]) for key in ("x0", "top", "x1", "bottom"))
                if any(not math.isfinite(value) for value in signature):
                    raise DocxLayoutError("unsupported_graphics")
            else:
                signature = tuple((key, _graphic_value(obj.get(key))) for key in fields)
            result.append((index, kind, signature))
    return result


def _pdf_paint(reader: PdfReader) -> list[tuple]:
    """Refuse paint state the masked comparison does not model.

    Writer's initial page-edge rectangle clip is supported, including its
    observed 0.028-point inset. Other clips, nonneutral graphics states,
    shading/patterns and Form/Type3 content need a richer comparator and refuse.
    This checks presence and neutral defaults; it is not a PDF interpreter.
    """
    clips: list[tuple] = []
    neutral = {"/Type", "/CA", "/ca", "/BM", "/SMask", "/AIS", "/OP", "/op", "/OPM", "/TK"}
    for index, page in enumerate(reader.pages):
        resources = page.get("/Resources", DictionaryObject()).get_object()
        if not isinstance(resources, DictionaryObject):
            raise DocxLayoutError("unsupported_paint")
        for key in ("/Shading", "/Pattern"):
            if resources.get(key):
                raise DocxLayoutError("unsupported_paint")
        states = resources.get("/ExtGState", DictionaryObject()).get_object()
        if not isinstance(states, DictionaryObject) or len(states) > 100:
            raise DocxLayoutError("unsupported_paint")
        for state in states.values():
            state = state.get_object()
            if (not isinstance(state, DictionaryObject) or set(state) - neutral
                    or state.get("/Type", "/ExtGState") != "/ExtGState"
                    or state.get("/BM", "/Normal") != "/Normal"
                    or state.get("/SMask", "/None") != "/None"):
                raise DocxLayoutError("unsupported_paint")
            for key, default in (("/CA", 1), ("/ca", 1), ("/OPM", 0)):
                if key in state and (not isinstance(state[key], (NumberObject, FloatObject))
                                     or float(state[key]) != default):
                    raise DocxLayoutError("unsupported_paint")
            for key, default in (("/AIS", False), ("/OP", False), ("/op", False), ("/TK", True)):
                if key in state and getattr(state[key], "value", None) is not default:
                    raise DocxLayoutError("unsupported_paint")
        xobjects = resources.get("/XObject", DictionaryObject()).get_object()
        fonts = resources.get("/Font", DictionaryObject()).get_object()
        if (not isinstance(xobjects, DictionaryObject) or len(xobjects) > 1000
                or any(obj.get_object().get("/Subtype") != "/Image" for obj in xobjects.values())
                or not isinstance(fonts, DictionaryObject) or len(fonts) > 1000
                or any(font.get_object().get("/Subtype") == "/Type3" for font in fonts.values())):
            raise DocxLayoutError("unsupported_paint")
        operations = ContentStream(page.get_contents(), reader).operations
        if len(operations) > 100_000:
            raise DocxLayoutError("pdf_complexity")
        clipped = painted = transformed = False
        pending_path = 0
        for offset, (operands, operator) in enumerate(operations):
            if operator in {b"m", b"l", b"c", b"v", b"y", b"h", b"re"}:
                pending_path += 1
            if operator in {b"sh", b"scn", b"SCN"}:
                raise DocxLayoutError("unsupported_paint")
            if operator == b"gs" and (len(operands) != 1 or operands[0] not in states):
                raise DocxLayoutError("unsupported_paint")
            if operator == b"Tr" and (len(operands) != 1 or float(operands[0]) != 0):
                raise DocxLayoutError("unsupported_paint")
            if operator in {b"W", b"W*"}:
                if (clipped or painted or transformed or pending_path != 1 or offset < 2
                        or operations[offset - 1][1] != b"re"
                        or operations[offset - 2][1] != b"q"
                        or offset + 1 >= len(operations) or operations[offset + 1][1] != b"n"):
                    raise DocxLayoutError("unsupported_paint")
                rect = tuple(float(value) for value in operations[offset - 1][0])
                bounds = tuple(float(value) for value in page.mediabox)
                if (len(rect) != 4 or any(not math.isfinite(value) for value in rect)
                        or any(abs(a - b) > 0.05 for a, b in zip(
                            (rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3]),
                            bounds, strict=True))):
                    raise DocxLayoutError("unsupported_paint")
                clips.append((index, "page_clip", (operator.decode(), rect)))
                clipped = True
            if operator in {b"n", b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"}:
                pending_path = 0
            painted |= operator in {b"BT", b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"Do"}
            transformed |= operator == b"cm"
    return clips


def _view(data: bytes) -> tuple[list[_Glyph], list[tuple], list[tuple], list[tuple]]:
    if type(data) is not bytes or not 0 < len(data) <= MAX_PDF_BYTES:
        raise DocxLayoutError("pdf_size")
    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted or not 0 < len(reader.pages) <= 20:
        raise DocxLayoutError("pdf_pages")
    _pdf_actions(reader)
    chars: list[_Glyph] = []
    pages: list[tuple] = []
    links: list[tuple] = []
    graphics: list[tuple] = _pdf_paint(reader)
    with pdfplumber.open(BytesIO(data)) as document:
        for index, page in enumerate(document.pages):
            if not 200 <= page.width <= 900 or not 200 <= page.height <= 1500:
                raise DocxLayoutError("pdf_geometry")
            pages.append((float(page.width), float(page.height)))
            graphics.extend(_graphics(page, index))
            if len(graphics) > 20_000:
                raise DocxLayoutError("pdf_complexity")
            page_chars: list[_Glyph] = []
            for char in page.chars:
                font = re.sub(r"^[A-Z]{6}\+", "", char["fontname"])
                if not font or font == "unknown" or "(cid:" in char["text"]:
                    raise DocxLayoutError("font_mapping")
                box = tuple(float(char[key]) for key in ("x0", "top", "x1", "bottom"))
                size = float(char["size"])
                if (not math.isfinite(size) or size <= 0
                        or any(not math.isfinite(number) for number in box)):
                    raise DocxLayoutError("pdf_geometry")
                for letter in _compact(char["text"]):
                    item: _Glyph = {"text": letter, "page": index, "font": font,
                                    "size": round(size, 3), "box": box}
                    chars.append(item)
                    page_chars.append(item)
                if len(chars) > MAX_GLYPHS:
                    raise DocxLayoutError("pdf_complexity")
            for annotation in reader.pages[index].get("/Annots", []):
                obj = annotation.get_object()
                if obj.get("/Subtype") != "/Link":
                    raise DocxLayoutError("pdf_link")
                action = obj.get("/A")
                action = action.get_object() if action is not None else {}
                target: tuple[Any, ...]
                if action.get("/S") == "/URI":
                    target = ("uri", str(action["/URI"]))
                else:
                    target = ("destination", _destination(reader, action.get("/D", obj.get("/Dest"))))
                rect = tuple(float(number) for number in obj.get("/Rect", []))
                if len(rect) != 4 or any(not math.isfinite(number) for number in rect):
                    raise DocxLayoutError("pdf_link")
                left, bottom, right, top = rect
                covered = "".join(c["text"] for c in page_chars if
                                  c["box"][0] < right and c["box"][2] > left
                                  and c["box"][1] < page.height - bottom
                                  and c["box"][3] > page.height - top)
                links.append((index, target, rect, covered))
    if not chars:
        raise DocxLayoutError("pdf_text")
    return chars, pages, links, graphics


def _bounds(chars: list[_Glyph]) -> tuple[float, float, float, float]:
    return (min(c["box"][0] for c in chars), min(c["box"][1] for c in chars),
            max(c["box"][2] for c in chars), max(c["box"][3] for c in chars))


def _lines(chars: list[_Glyph]) -> list[tuple[float, float]]:
    lines = sorted({(c["box"][1], c["box"][3]) for c in chars})
    bands: list[tuple[float, float]] = []
    for top, bottom in lines:
        if not bands or abs(top - bands[-1][0]) > GEOMETRY_TOLERANCE:
            bands.append((top, bottom))
    return bands


def _compare_pixels(before: bytes, after: bytes, pages: int, regions: list[tuple]) -> None:
    with _PDF_LOCK, pdfium.PdfDocument(before) as old, pdfium.PdfDocument(after) as new:
        for index in range(pages):
            with closing(old[index]) as a, closing(new[index]) as b:
                with closing(a.render(scale=RENDER_SCALE)) as ab, closing(b.render(scale=RENDER_SCALE)) as bb:
                    difference = ImageChops.difference(ab.to_pil().convert("RGB"), bb.to_pil().convert("RGB"))
                draw = ImageDraw.Draw(difference)
                margin = INK_MARGIN_POINTS * RENDER_SCALE
                for page, (left, top, right, bottom) in regions:
                    if page == index:
                        draw.rectangle((left * RENDER_SCALE - margin, top * RENDER_SCALE - margin,
                                        right * RENDER_SCALE + margin, bottom * RENDER_SCALE + margin),
                                       fill=(0, 0, 0))
                if difference.getbbox() is not None:
                    raise DocxLayoutError("outside_region_pixels")


def validate_docx_layout(*, source_docx: bytes, candidate_docx: bytes, edits: list[dict],
                         before_pdf: bytes, after_pdf: bytes,
                         renderer: RendererIdentity) -> DocxLayoutReceipt:
    """Validate actual renders, preserving native structure and unrelated layout.

    Changed paragraph spans must be uniquely extractable and retain page, line
    bands, fonts/sizes and their original envelope. Unsupported/ambiguous text,
    missing mappings, reflow, substitutions between renders, or visual changes
    outside these spans refuse. Truth/evidence admission remains the caller's job.
    """
    try:
        _identity(renderer)
        changes = _native_edits(source_docx, candidate_docx, edits)
        old, old_pages, old_links, old_graphics = _view(before_pdf)
        new, pages, links, graphics = _view(after_pdf)
        if pages != old_pages:
            raise DocxLayoutError("page_geometry_changed")
        if links != old_links:
            raise DocxLayoutError("hyperlink_changed")
        if graphics != old_graphics:
            raise DocxLayoutError("nontext_changed")
        old_text = "".join(c["text"] for c in old)
        regions = []
        for original, replacement, unit_id in changes:
            text, replaced = _compact(original), _compact(replacement)
            if not text or not replaced or old_text.count(text) != 1:
                raise DocxLayoutError("ambiguous_rendered_span")
            start = old_text.index(text)
            regions.append((start, start + len(text), replaced, unit_id))
        regions.sort()
        if any(a[1] > b[0] for a, b in zip(regions, regions[1:], strict=False)):
            raise DocxLayoutError("ambiguous_rendered_span")
        expected = old_text
        for start, end, replacement, _unit in reversed(regions):
            expected = expected[:start] + replacement + expected[end:]
        if expected != "".join(c["text"] for c in new):
            raise DocxLayoutError("text_or_reading_order_changed")
        protected_old, protected_new, boxes = [], [], []
        cursor = new_cursor = 0
        for start, end, replacement, unit_id in regions:
            new_start = new_cursor + start - cursor
            protected_old.extend(old[cursor:start])
            protected_new.extend(new[new_cursor:new_start])
            a, b = old[start:end], new[new_start:new_start + len(replacement)]
            if len({c["page"] for c in a}) != 1 or any(c["page"] != a[0]["page"] for c in b):
                raise DocxLayoutError("paragraph_page_changed", unit_id=unit_id)
            old_lines, new_lines = _lines(a), _lines(b)
            if len(old_lines) != len(new_lines) or any(
                abs(x - y) > GEOMETRY_TOLERANCE for first, second in zip(old_lines, new_lines, strict=True)
                for x, y in zip(first, second, strict=True)
            ):
                raise DocxLayoutError("line_wrap_changed", unit_id=unit_id)
            fonts = {(c["font"], c["size"]) for c in a}
            if any((c["font"], c["size"]) not in fonts for c in b):
                raise DocxLayoutError("font_changed", unit_id=unit_id)
            # Retained text may shift horizontally within its paragraph, but
            # cannot silently change font/style or move to another line.
            for block in difflib.SequenceMatcher(
                None, [c["text"] for c in a], [c["text"] for c in b], autojunk=False
            ).get_matching_blocks():
                for old_char, new_char in zip(a[block.a:block.a + block.size],
                                              b[block.b:block.b + block.size], strict=True):
                    if (old_char["font"] != new_char["font"]
                            or old_char["size"] != new_char["size"]
                            or any(abs(x - y) > GEOMETRY_TOLERANCE for x, y in zip(
                                old_char["box"][1::2], new_char["box"][1::2], strict=True))):
                        raise DocxLayoutError("retained_style_changed", unit_id=unit_id)
            bounds = _bounds(a)
            if any(c["box"][0] < bounds[0] - GEOMETRY_TOLERANCE
                   or c["box"][1] < bounds[1] - GEOMETRY_TOLERANCE
                   or c["box"][2] > bounds[2] + GEOMETRY_TOLERANCE
                   or c["box"][3] > bounds[3] + GEOMETRY_TOLERANCE for c in b):
                raise DocxLayoutError("paragraph_overflow", unit_id=unit_id)
            boxes.append((a[0]["page"], bounds))
            cursor, new_cursor = end, new_start + len(replacement)
        for page, kind, bounds in graphics:
            if kind != "image":
                continue
            left, top, right, bottom = bounds
            if any(page == changed_page and left < box[2] + INK_MARGIN_POINTS
                   and right > box[0] - INK_MARGIN_POINTS
                   and top < box[3] + INK_MARGIN_POINTS
                   and bottom > box[1] - INK_MARGIN_POINTS for changed_page, box in boxes):
                raise DocxLayoutError("unsupported_graphics_overlap")
        protected_old.extend(old[cursor:])
        protected_new.extend(new[new_cursor:])
        if len(protected_old) != len(protected_new):
            raise DocxLayoutError("protected_text_changed")
        delta = 0.0
        for old_char, new_char in zip(protected_old, protected_new, strict=True):
            if (old_char["text"] != new_char["text"] or old_char["page"] != new_char["page"]
                    or old_char["font"] != new_char["font"] or old_char["size"] != new_char["size"]):
                raise DocxLayoutError("protected_text_changed")
            difference = max(abs(x - y) for x, y in zip(old_char["box"], new_char["box"], strict=True))
            delta = max(delta, difference)
            if difference > GEOMETRY_TOLERANCE:
                raise DocxLayoutError("protected_geometry_changed")
        _compare_pixels(before_pdf, after_pdf, len(pages), boxes)
        return DocxLayoutReceipt(
            validator=VALIDATOR, source_sha256=_sha(source_docx), source_size_bytes=len(source_docx),
            candidate_sha256=_sha(candidate_docx), candidate_size_bytes=len(candidate_docx),
            edits_sha256=_sha(_canonical(edits)), before_pdf_sha256=_sha(before_pdf),
            after_pdf_sha256=_sha(after_pdf), renderer=renderer,
            rendered_fonts_sha256=_sha(_canonical(sorted({(c["font"], c["size"]) for c in new}))),
            pages=len(pages), changed_paragraphs=len(changes), protected_glyphs=len(protected_old),
            max_protected_geometry_delta_points=round(delta, 6), unchanged_links=len(links),
            unchanged_graphics=len(graphics),
        )
    except DocxLayoutError:
        raise
    except Exception:
        raise DocxLayoutError("malformed_input") from None
