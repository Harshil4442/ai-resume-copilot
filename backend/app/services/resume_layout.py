"""Conservative edits inside an uploaded document, without rebuilding its layout.

PDF units are native text objects. DOCX units are paragraphs whose existing text
nodes receive local diffs; runs, properties, relationships and other ZIP parts
are retained. Unsupported edits fail rather than falling back to an overlay or
a new resume template.
"""

from __future__ import annotations

import ctypes
import difflib
import hashlib
import math
import re
import statistics
import threading
from contextlib import closing
from io import BytesIO
from typing import Any
from zipfile import BadZipFile, ZipFile

import pypdfium2 as pdfium
import pypdfium2.raw as raw
from lxml import etree
from PIL import ImageChops, ImageDraw


class ResumeLayoutError(ValueError):
    """The source or proposed edit cannot safely retain the original document."""

    def __init__(self, message: str, *, unit_id: str | None = None) -> None:
        super().__init__(message)
        self.unit_id = unit_id


# PDFium must not be called simultaneously from multiple threads, even for
# different documents. All PDF operations in this module share this lock.
_PDF_LOCK = threading.RLock()
_MAX_BYTES = 20 * 1024 * 1024
_MAX_PAGES = 20
_MAX_OBJECTS = 10_000
_MAX_XML_BYTES = 10 * 1024 * 1024
_MAX_EDITS = 100
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_NUMBER = re.compile(r"(?<!\w)\d+(?:[.,]\d+)*(?:%|\+)?")
_CONTACT = re.compile(r"@|https?://|www\.|linkedin\.|github\.", re.I)
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_HEADINGS = {
    "summary",
    "profile",
    "experience",
    "professional experience",
    "work experience",
    "education",
    "skills",
    "technical skills",
    "projects",
    "certifications",
    "awards",
    "publications",
    "interests",
    "languages",
    "objective",
    "achievements",
}
_ACTION = re.compile(
    r"^(?:built|developed|designed|implemented|delivered|led|managed|created|improved|"
    r"reduced|increased|achieved|optimized|maintained|supported|automated|collaborated|"
    r"worked|contributed|architected|deployed|integrated|owned|engineered|launched|"
    r"analyzed|tested|drove|established|migrated|mentored|resolved|streamlined)\b",
    re.I,
)
_ROLE = re.compile(
    r"\b(?:engineer|developer|manager|analyst|intern|consultant|architect|director|lead|specialist|officer)\b",
    re.I,
)
_EMPLOYER = re.compile(r"\b(?:pvt\.?|ltd\.?|inc\.?|llc|corporation|limited)\b", re.I)
_DATE = re.compile(
    r"\b(?:19|20)\d{2}\b|\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|present)\b",
    re.I,
)


def _identity_line(text: str) -> bool:
    stripped = text.strip().lstrip("•●▪◦-* ")
    if len(stripped) > 160 or _ACTION.search(stripped):
        return False
    return bool(_ROLE.search(stripped) or _EMPLOYER.search(stripped) or _DATE.search(stripped))


def _format(source_bytes: bytes, source_format: str) -> str:
    if not isinstance(source_bytes, bytes) or not source_bytes or len(source_bytes) > _MAX_BYTES:
        raise ResumeLayoutError("The original resume document is missing or too large.")
    kind = source_format.lower().lstrip(".") if isinstance(source_format, str) else ""
    if kind not in {"pdf", "docx"}:
        raise ResumeLayoutError("Layout-preserving tailoring supports PDF and DOCX sources.")
    return kind


def _candidate(text: str) -> bool:
    stripped = text.strip()
    return (
        30 <= len(stripped) <= 2000
        and len(re.findall(r"\w+", stripped)) >= 5
        and not _CONTROL.search(text)
        and not _CONTACT.search(text)
        and stripped.lower().rstrip(":") not in _HEADINGS
        and not stripped.isupper()
        and len(re.findall(r"[A-Za-z]", stripped)) >= 20
        and not _identity_line(stripped)
    )


def _unit_id(source_bytes: bytes, kind: str, location: str, text: str) -> str:
    digest = hashlib.sha256(source_bytes).hexdigest()[:16]
    text_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"{kind}:{digest}:{location}:{text_digest}"


def _validated_edits(units: list[dict[str, Any]], edits: list[dict]) -> dict[str, str]:
    if not isinstance(edits, list) or len(edits) > _MAX_EDITS:
        raise ResumeLayoutError("The source edit list is invalid or too large.")
    available = {unit["unit_id"]: unit for unit in units}
    replacements: dict[str, str] = {}
    seen: set[str] = set()
    for edit in edits:
        if not isinstance(edit, dict):
            raise ResumeLayoutError("Each source edit must identify an original text unit.")
        unit_id = edit.get("unit_id")
        if not isinstance(unit_id, str) or unit_id not in available or unit_id in seen:
            raise ResumeLayoutError(
                "An edit refers to an unknown, protected or repeated source unit."
            )
        seen.add(unit_id)
        original = available[unit_id]["text"]
        replacement = edit.get("replacement_text")
        if edit.get("original_text") != original:
            raise ResumeLayoutError("The original source text no longer matches the proposed edit.")
        if (
            not isinstance(replacement, str)
            or not replacement.strip()
            or len(replacement) > 2000
            or _CONTROL.search(replacement)
            or any(0xD800 <= ord(char) <= 0xDFFF for char in replacement)
        ):
            raise ResumeLayoutError(
                "Replacement text must be a nonempty single line of valid text."
            )
        if _NUMBER.findall(original) != _NUMBER.findall(replacement):
            raise ResumeLayoutError("Source edits must preserve all original numbers and dates.")
        if _CONTACT.search(replacement):
            raise ResumeLayoutError("Source edits cannot add or change contact information.")
        if replacement != original:
            replacements[unit_id] = replacement
    return replacements


def extract_source_units(source_bytes: bytes, source_format: str) -> list[dict]:
    """Return eligible source text and stable identities for concise local edits."""
    kind = _format(source_bytes, source_format)
    if kind == "pdf":
        with _PDF_LOCK:
            try:
                with pdfium.PdfDocument(source_bytes) as document:
                    _check_pdf(document)
                    return _pdf_units(document, source_bytes)
            except ResumeLayoutError:
                raise
            except Exception as exc:
                raise ResumeLayoutError("The original PDF cannot be read safely.") from exc
    try:
        with ZipFile(BytesIO(source_bytes)) as archive:
            tree = _docx_tree(archive)
            return _docx_units(tree, source_bytes)
    except ResumeLayoutError:
        raise
    except (BadZipFile, KeyError, etree.XMLSyntaxError, OSError) as exc:
        raise ResumeLayoutError("The original DOCX cannot be read safely.") from exc


def apply_source_edits(source_bytes: bytes, source_format: str, edits: list[dict]) -> bytes:
    """Return a native-format edited copy, rejecting unsafe edits atomically.

    The source bytes are never mutated. Empty or unchanged edits return the
    original byte sequence exactly.
    """
    kind = _format(source_bytes, source_format)
    if not isinstance(edits, list):
        raise ResumeLayoutError("The source edit list is invalid.")
    if not edits:
        return source_bytes
    if kind == "pdf":
        with _PDF_LOCK:
            try:
                return _apply_pdf(source_bytes, edits)
            except ResumeLayoutError:
                raise
            except Exception as exc:
                raise ResumeLayoutError(
                    "The PDF edit could not preserve the original layout."
                ) from exc
    try:
        return _apply_docx(source_bytes, edits)
    except ResumeLayoutError:
        raise
    except (BadZipFile, KeyError, etree.XMLSyntaxError, OSError) as exc:
        raise ResumeLayoutError("The DOCX edit could not preserve the original document.") from exc


def _check_pdf(document: Any) -> None:
    if not 0 < len(document) <= _MAX_PAGES:
        raise ResumeLayoutError("The PDF has too many pages for safe source editing.")
    if raw.FPDF_GetSecurityHandlerRevision(document) >= 0:
        raise ResumeLayoutError("Encrypted PDFs cannot be tailored in place.")
    if raw.FPDF_GetSignatureCount(document) > 0:
        raise ResumeLayoutError(
            "Signed PDFs cannot be tailored without invalidating the signature."
        )
    if raw.FPDF_GetFormType(document) != raw.FORMTYPE_NONE:
        raise ResumeLayoutError("PDF forms cannot be tailored in place.")


def _pdf_objects(page: Any, textpage: Any) -> list[Any]:
    objects = list(page.get_objects(textpage=textpage))
    if len(objects) > _MAX_OBJECTS:
        raise ResumeLayoutError("The PDF page is too complex for safe source editing.")
    return objects


def _pdf_eligible(obj: Any, text: str, page: Any) -> bool:
    if obj.level != 0 or not _candidate(text):
        return False
    matrix = obj.get_matrix()
    # Nested, rotated, skewed and vertical text need a different slot model.
    if abs(matrix.b) > 0.0001 or abs(matrix.c) > 0.0001 or matrix.a <= 0 or matrix.d <= 0:
        return False
    bounds = obj.get_bounds()
    font = obj.get_font()
    clip = raw.FPDFPageObj_GetClipPath(obj)
    if clip and raw.FPDFClipPath_CountPaths(clip) > 0:
        return False
    # Company/title lines are often short bold or italic objects. A body line
    # with an occasional emphasized keyword remains a separate normal object.
    if len(text.strip()) <= 120 and (
        font.get_weight() >= 600
        or re.search(r"bold|italic|oblique|^CMB(?:X|\d)|^CMTI|^CMSL", font.get_base_name(), re.I)
    ):
        return False
    if not all(math.isfinite(value) for value in bounds):
        return False
    if bounds[3] > page.get_height() - max(54, page.get_height() * 0.08):
        return False
    return (
        bounds[2] > bounds[0]
        and bounds[3] > bounds[1]
        and 5 <= obj.get_font_size() <= 13.5
        and raw.FPDFTextObj_GetTextRenderMode(obj) == raw.FPDF_TEXTRENDERMODE_FILL
    )


def _pdf_units(document: Any, source_bytes: bytes) -> list[dict[str, Any]]:
    units = []
    for page_index in range(len(document)):
        with closing(document[page_index]) as page, closing(page.get_textpage()) as textpage:
            objects = _pdf_objects(page, textpage)
            texts = {
                index: obj.extract()
                for index, obj in enumerate(objects)
                if obj.type == raw.FPDF_PAGEOBJ_TEXT
            }
            for object_index, obj in enumerate(objects):
                if obj.type != raw.FPDF_PAGEOBJ_TEXT:
                    continue
                text = texts[object_index]
                if _pdf_eligible(obj, text, page):
                    matrix = obj.get_matrix()
                    line = sorted(
                        (
                            (other.get_bounds()[0], texts[index].strip())
                            for index, other in enumerate(objects)
                            if index in texts
                            and other.level == 0
                            and abs(other.get_matrix().f - matrix.f) <= obj.get_font_size() * 0.3
                        ),
                        key=lambda entry: entry[0],
                    )
                    units.append(
                        {
                            "unit_id": _unit_id(
                                source_bytes, "pdf", f"p{page_index}:o{object_index}", text
                            ),
                            "text": text,
                            "kind": "text_object",
                            "page_index": page_index,
                            "object_index": object_index,
                            "bounds": list(obj.get_bounds()),
                            "font": obj.get_font().get_base_name(),
                            "font_size": obj.get_font_size(),
                            "width": obj.get_bounds()[2] - obj.get_bounds()[0],
                            "height": obj.get_bounds()[3] - obj.get_bounds()[1],
                            "max_chars": len(text),
                            "fit_policy": "original_font_and_fixed_slot",
                            "preserve_numbers": True,
                            "line_context": " ".join(value for _, value in line)[:1000],
                            "allowed_characters": _pdf_allowed_characters(obj, text),
                        }
                    )
    return units


def _pdf_allowed_characters(obj: Any, text: str) -> str:
    # A subset font may contain only the letters used in the source. Tell the
    # model what can be written natively; the renderer checks this again.
    candidates = set(chr(value) for value in range(32, 127)) | set(text)
    allowed = []
    font = obj.get_font()
    for char in sorted(candidates):
        if char == " ":
            allowed.append(char)
            continue
        if ord(char) > 0xFFFF or _CONTROL.search(char):
            continue
        width = ctypes.c_float()
        if raw.FPDFFont_GetGlyphWidth(
            font, ord(char), obj.get_font_size(), ctypes.byref(width)
        ) and raw.FPDFFont_GetGlyphPath(font, ord(char), obj.get_font_size()):
            allowed.append(char)
    return "".join(allowed)


def _color(obj: Any, getter: Any) -> tuple[int, ...]:
    values = [ctypes.c_uint() for _ in range(4)]
    if not getter(obj, *(ctypes.byref(value) for value in values)):
        raise ResumeLayoutError("The PDF text style cannot be verified.")
    return tuple(value.value for value in values)


def _style(obj: Any) -> tuple[Any, ...]:
    return (
        obj.get_font().get_base_name(),
        obj.get_font_size(),
        obj.get_matrix().get(),
        _color(obj, raw.FPDFPageObj_GetFillColor),
        _color(obj, raw.FPDFPageObj_GetStrokeColor),
        raw.FPDFTextObj_GetTextRenderMode(obj),
    )


def _pdf_line_slot(obj: Any, page: Any) -> tuple[float, ...]:
    bounds = obj.get_bounds()
    matrix = obj.get_matrix()
    ascent, descent = ctypes.c_float(), ctypes.c_float()
    font = obj.get_font()
    if not (
        raw.FPDFFont_GetAscent(font, obj.get_font_size(), ctypes.byref(ascent))
        and raw.FPDFFont_GetDescent(font, obj.get_font_size(), ctypes.byref(descent))
        and math.isfinite(ascent.value)
        and math.isfinite(descent.value)
        and ascent.value > 0
        and descent.value <= 0
    ):
        return bounds
    # Keep the original baseline and right boundary. Ink bearings/descenders
    # vary with letters, so they are not the font's available line height.
    return (
        max(0, min(bounds[0], matrix.e)),
        max(0, min(bounds[1], matrix.f + descent.value * matrix.d)),
        min(page.get_width(), bounds[2]),
        min(page.get_height(), max(bounds[3], matrix.f + ascent.value * matrix.d)),
    )


def _same_pdf_style(first: tuple[Any, ...], second: tuple[Any, ...]) -> bool:
    # Original operators/resources are retained. Different TJ segmentation
    # can change accumulated translations by one float32 ULP in PDFium.
    # Keep font, size, colors, render mode and matrix scales exact; permit
    # only subpixel translation precision, with an independent raster proof.
    return (
        first[:2] == second[:2]
        and first[3:] == second[3:]
        and first[2][:4] == second[2][:4]
        and all(abs(a - b) <= 0.0001 for a, b in zip(first[2][4:], second[2][4:], strict=True))
    )


def _fits(slot: tuple[float, ...], new: tuple[float, ...]) -> bool:
    tolerance = 0.1  # float precision in PDFium's generated content
    return (
        all(math.isfinite(value) for value in new)
        and 0 < new[2] - new[0] <= slot[2] - slot[0] + tolerance
        and 0 < new[3] - new[1] <= slot[3] - slot[1] + tolerance
        and new[0] >= slot[0] - tolerance
        and new[1] >= slot[1] - tolerance
        and new[2] <= slot[2] + tolerance
        and new[3] <= slot[3] + tolerance
    )


def _intersection(first: tuple[float, ...], second: tuple[float, ...]) -> tuple[float, ...] | None:
    area = (
        max(first[0], second[0]),
        max(first[1], second[1]),
        min(first[2], second[2]),
        min(first[3], second[3]),
    )
    return area if area[2] - area[0] > 0.05 and area[3] - area[1] > 0.05 else None


def _pdf_collision_bounds(obj: Any) -> tuple[float, ...]:
    bounds = obj.get_bounds()
    if obj.type == raw.FPDF_PAGEOBJ_PATH:
        fill, stroke = ctypes.c_int(), ctypes.c_int()
        width = ctypes.c_float()
        if raw.FPDFPath_GetDrawMode(obj, ctypes.byref(fill), ctypes.byref(stroke)) and stroke.value:
            if not raw.FPDFPageObj_GetStrokeWidth(obj, ctypes.byref(width)):
                raise ResumeLayoutError("The PDF path stroke cannot be verified.")
            matrix = obj.get_matrix()
            scale = max(math.hypot(matrix.a, matrix.b), math.hypot(matrix.c, matrix.d))
            padding = max(0.05, width.value * scale / 2)
            bounds = (
                bounds[0] - padding,
                bounds[1] - padding,
                bounds[2] + padding,
                bounds[3] + padding,
            )
    return bounds


def _check_pdf_collisions(
    objects: list[Any], old_bounds: list[tuple[float, ...]], changed: dict[int, str]
) -> None:
    for index, unit_id in changed.items():
        for other_index, other in enumerate(objects):
            if index == other_index or other.level != 0:
                continue
            overlap = _intersection(objects[index].get_bounds(), _pdf_collision_bounds(other))
            if overlap is None:
                continue
            previous = _intersection(old_bounds[index], old_bounds[other_index])
            if previous is None or not _fits(previous, overlap):
                raise ResumeLayoutError(
                    "The replacement overlaps neighboring PDF content.", unit_id=unit_id
                )


def _pdf_text_cores(objects: list[Any], textpage: Any) -> dict[int, tuple[str, str]]:
    """Exclude only a proven, extractor-generated trailing boundary space.

    PDFium can assign the inferred gap between two text objects to the first
    object's extracted string. A shorter adjacent edit can change that gap
    without changing any source glyph. Actual encoded whitespace remains part
    of the exact comparison, as do all internal generated word gaps.
    """
    ranges: dict[int, list[int]] = {}
    for index in range(textpage.count_chars()):
        identity = ctypes.cast(raw.FPDFText_GetTextObject(textpage, index), ctypes.c_void_p).value
        if identity is not None:
            ranges.setdefault(identity, []).append(index)
    result = {}
    for object_index, obj in enumerate(objects):
        if obj.type != raw.FPDF_PAGEOBJ_TEXT:
            continue
        text = obj.extract()
        identity = ctypes.cast(obj.raw, ctypes.c_void_p).value
        indices = ranges.get(identity, []) if identity is not None else []
        suffix = ""
        if indices:
            first, last = indices[0], indices[-1]
            core = textpage.get_text_range(first, last - first + 1)
            following = last + 1
            if (
                text == core + " "
                and following < textpage.count_chars()
                and raw.FPDFText_IsGenerated(textpage, following) == 1
                and not raw.FPDFText_GetTextObject(textpage, following)
                and textpage.get_text_range(following, 1) == " "
            ):
                text, suffix = core, " "
        result[object_index] = (text, suffix)
    return result


def _word_gaps(obj: Any, textpage: Any) -> list[float]:
    identity = ctypes.cast(obj.raw, ctypes.c_void_p).value
    chars = []
    for index in range(textpage.count_chars()):
        if (
            ctypes.cast(raw.FPDFText_GetTextObject(textpage, index), ctypes.c_void_p).value
            != identity
        ):
            continue
        char = textpage.get_text_range(index, 1)
        x, y = ctypes.c_double(), ctypes.c_double()
        if len(char) == 1 and raw.FPDFText_GetCharOrigin(
            textpage, index, ctypes.byref(x), ctypes.byref(y)
        ):
            chars.append((char, x.value))
    gaps = []
    scale = obj.get_matrix().a
    for index in range(1, len(chars) - 1):
        if (
            not chars[index][0].isspace()
            or chars[index - 1][0].isspace()
            or chars[index + 1][0].isspace()
        ):
            continue
        width = ctypes.c_float()
        if raw.FPDFFont_GetGlyphWidth(
            obj.get_font(), ord(chars[index - 1][0]), obj.get_font_size(), ctypes.byref(width)
        ):
            gaps.append((chars[index + 1][1] - chars[index - 1][1]) / scale - width.value)
    return gaps


def _set_pdf_text(obj: Any, replacement: str) -> float | None:
    font = obj.get_font()
    advances = {}
    for char in set(replacement):
        if char.isspace() and char != " ":
            raise ResumeLayoutError("The original PDF whitespace cannot be retained safely.")
        if ord(char) > 0xFFFF:
            raise ResumeLayoutError("The original PDF font cannot safely encode this character.")
        width = ctypes.c_float()
        if not raw.FPDFFont_GetGlyphWidth(
            font, ord(char), obj.get_font_size(), ctypes.byref(width)
        ):
            raise ResumeLayoutError("The original PDF font is missing a required glyph.")
        if not char.isspace() and not raw.FPDFFont_GetGlyphPath(
            font, ord(char), obj.get_font_size()
        ):
            raise ResumeLayoutError("The original PDF font is missing a required glyph.")
        advances[char] = width.value
    restored_space = None
    if " " in replacement and advances[" "] <= 0:
        # TeX Type1 subsets commonly omit the space glyph, placing words using
        # explicit TJ offsets. SetText alone would collapse those word gaps.
        # Retain measured source spacing with native character positions.
        gaps = _word_gaps(obj, obj.textpage)
        if not gaps or any(not math.isfinite(gap) or gap <= 0 for gap in gaps):
            raise ResumeLayoutError("The original PDF word spacing cannot be retained safely.")
        restored_space = statistics.median(gaps)
        advances[" "] = restored_space
    written_text = replacement
    if restored_space is not None:
        # A missing space code can map to another TeX glyph (e.g. ß), even
        # when its reported width is zero. Represent gaps with TJ positions,
        # without ever writing a missing glyph into the source font.
        written_text = replacement.replace(" ", "")
        if replacement.startswith(" "):
            raise ResumeLayoutError("The original PDF leading spacing cannot be retained safely.")
    encoded = written_text.encode("utf-16-le") + b"\0\0"
    buffer = (raw.FPDF_WCHAR * (len(encoded) // ctypes.sizeof(raw.FPDF_WCHAR))).from_buffer_copy(
        encoded
    )
    if not raw.FPDFText_SetText(obj, buffer):
        raise ResumeLayoutError("The PDF text could not be replaced using the original font.")
    if restored_space is not None:
        position = 0.0
        positions = []
        for char in replacement:
            if char != " ":
                positions.append(position)
            position += advances[char]
        positions = positions[1:]
        values = (ctypes.c_float * len(positions))(*positions)
        if not raw.FPDFText_SetPositions(obj, values, len(positions)):
            raise ResumeLayoutError("The original PDF word spacing cannot be retained safely.")
    return restored_space


def _render(page: Any) -> Any:
    if page.get_width() * page.get_height() > 4_000_000:
        raise ResumeLayoutError("The PDF page is too large to verify its layout safely.")
    with closing(page.render(scale=1)) as bitmap:
        return bitmap.to_pil().convert("RGB").copy()


def _apply_pdf(source_bytes: bytes, edits: list[dict]) -> bytes:
    from .pdf_streams import PdfStreamError, splice_pdf_text_streams

    with pdfium.PdfDocument(source_bytes) as document:
        _check_pdf(document)
        units = _pdf_units(document, source_bytes)
        replacements = _validated_edits(units, edits)
        if not replacements:
            return source_bytes
        targets = {
            (unit["page_index"], unit["object_index"]): replacements[unit["unit_id"]]
            for unit in units
            if unit["unit_id"] in replacements
        }
        target_ids = {
            (unit["page_index"], unit["object_index"]): unit["unit_id"]
            for unit in units
            if unit["unit_id"] in replacements
        }
        stream_targets: dict[int, dict[int, str]] = {}
        snapshots: dict[int, dict[str, Any]] = {}
        for page_index in sorted({key[0] for key in targets}):
            with closing(document[page_index]) as page, closing(page.get_textpage()) as textpage:
                objects = _pdf_objects(page, textpage)
                ordinal = 0
                stream_targets[page_index] = {}
                for index, obj in enumerate(objects):
                    if obj.type == raw.FPDF_PAGEOBJ_TEXT and obj.level == 0:
                        if (page_index, index) in targets:
                            stream_targets[page_index][ordinal] = target_ids[page_index, index]
                        ordinal += 1
                old_object_bounds = [_pdf_collision_bounds(obj) for obj in objects]
                text_snapshot = {
                    index: (obj.extract(), obj.get_bounds(), _style(obj))
                    for index, obj in enumerate(objects)
                    if obj.type == raw.FPDF_PAGEOBJ_TEXT
                }
                slots: list[tuple[float, ...]] = []
                snapshots[page_index] = {
                    "size": page.get_size(),
                    "types": [obj.type for obj in objects],
                    "texts": text_snapshot,
                    "slots": slots,
                    "spacing": {},
                    "line_slots": {},
                    "unit_ids": {},
                    "text_cores": _pdf_text_cores(objects, textpage),
                }
                for object_index, obj in enumerate(objects):
                    replacement = targets.get((page_index, object_index))
                    if replacement is None:
                        continue
                    _, bounds, style = text_snapshot[object_index]
                    unit_id = target_ids[page_index, object_index]
                    line_slot = _pdf_line_slot(obj, page)
                    snapshots[page_index]["line_slots"][object_index] = line_slot
                    snapshots[page_index]["unit_ids"][object_index] = unit_id
                    try:
                        restored_space = _set_pdf_text(obj, replacement)
                    except ResumeLayoutError as exc:
                        exc.unit_id = unit_id
                        raise
                    if restored_space is not None:
                        snapshots[page_index]["spacing"][object_index] = restored_space
                    new_bounds = obj.get_bounds()
                    if not _fits(line_slot, new_bounds) or _style(obj) != style:
                        raise ResumeLayoutError(
                            "The replacement does not fit its original PDF text slot.",
                            unit_id=unit_id,
                        )
                    slots.append(
                        (
                            min(bounds[0], new_bounds[0]),
                            min(bounds[1], new_bounds[1]),
                            max(bounds[2], new_bounds[2]),
                            max(bounds[3], new_bounds[3]),
                        )
                    )
                _check_pdf_collisions(objects, old_object_bounds, snapshots[page_index]["unit_ids"])
                page.gen_content()
        output = BytesIO()
        document.save(output)
        try:
            # PDFium's page serializer can coalesce fonts with the same name
            # but different encodings. Keep the source resources/content and
            # transplant only the verified target text-show operands.
            result = splice_pdf_text_streams(source_bytes, output.getvalue(), stream_targets)
        except PdfStreamError as exc:
            raise ResumeLayoutError(
                "The original PDF text stream cannot be updated safely."
            ) from exc
        _verify_pdf(source_bytes, result, len(document), snapshots, targets)
        return result


def _verify_pdf(
    source_bytes: bytes, result: bytes, page_count: int, snapshots: dict, targets: dict
) -> None:
    with pdfium.PdfDocument(source_bytes) as original, pdfium.PdfDocument(result) as document:
        if len(document) != page_count:
            raise ResumeLayoutError("The PDF page count changed during source editing.")
        for page_index, snapshot in snapshots.items():
            with closing(document[page_index]) as page, closing(page.get_textpage()) as textpage:
                objects = _pdf_objects(page, textpage)
                saved_cores = _pdf_text_cores(objects, textpage)
                if (
                    page.get_size() != snapshot["size"]
                    or [obj.type for obj in objects] != snapshot["types"]
                ):
                    raise ResumeLayoutError(
                        "The PDF object structure changed during source editing."
                    )
                for index, (_old_text, old_bounds, old_style) in snapshot["texts"].items():
                    obj = objects[index]
                    old_core, generated_suffix = snapshot["text_cores"][index]
                    expected = targets.get((page_index, index), old_core)
                    if (
                        (page_index, index) in targets
                        and generated_suffix
                        and expected.endswith(generated_suffix)
                    ):
                        expected = expected[: -len(generated_suffix)]
                    if saved_cores[index][0] != expected or not _same_pdf_style(
                        _style(obj), old_style
                    ):
                        raise ResumeLayoutError(
                            "Saved PDF text or font differs from the accepted source edit.",
                            unit_id=snapshot["unit_ids"].get(index),
                        )
                    bounds = obj.get_bounds()
                    if (page_index, index) in targets:
                        if not _fits(snapshot["line_slots"][index], bounds):
                            raise ResumeLayoutError(
                                "Saved PDF text exceeds its original slot.",
                                unit_id=snapshot["unit_ids"].get(index),
                            )
                        if index in snapshot["spacing"]:
                            gaps = _word_gaps(obj, textpage)
                            if not gaps or any(
                                gap < snapshot["spacing"][index] * 0.8 for gap in gaps
                            ):
                                raise ResumeLayoutError("Saved PDF word spacing is not readable.")
                    elif any(abs(a - b) > 0.1 for a, b in zip(old_bounds, bounds, strict=True)):
                        raise ResumeLayoutError("Unedited PDF text moved during source editing.")
                image = _render(page)
                with closing(original[page_index]) as source_page:
                    difference = ImageChops.difference(_render(source_page), image)
                draw = ImageDraw.Draw(difference)
                converter = pdfium.PdfPosConv(page, (0, 0, image.width, image.height, 0))
                # Hide only the original changed slots in the comparison image.
                # No masking or overlay is written into the exported document.
                for left, bottom, right, top in snapshot["slots"]:
                    x1, y1 = converter.to_bitmap(left, top)
                    x2, y2 = converter.to_bitmap(right, bottom)
                    draw.rectangle(
                        (min(x1, x2) - 2, min(y1, y2) - 2, max(x1, x2) + 2, max(y1, y2) + 2),
                        fill=(0, 0, 0),
                    )
                if difference.getbbox():
                    raise ResumeLayoutError(
                        "The PDF changed visually outside the accepted text slots."
                    )
        for page_index in range(page_count):
            with (
                closing(original[page_index]) as source_page,
                closing(document[page_index]) as saved_page,
            ):
                if _page_structure(source_page, original) != _page_structure(saved_page, document):
                    raise ResumeLayoutError(
                        "The PDF page geometry or link targets changed during editing."
                    )
                if page_index in snapshots:
                    continue
                with (
                    closing(source_page.get_textpage()) as source_text,
                    closing(saved_page.get_textpage()) as saved_text,
                ):
                    if source_text.get_text_range() != saved_text.get_text_range():
                        raise ResumeLayoutError(
                            "An untouched PDF page changed text during editing."
                        )
                if ImageChops.difference(_render(source_page), _render(saved_page)).getbbox():
                    raise ResumeLayoutError(
                        "An untouched PDF page changed visually during editing."
                    )


def _page_structure(page: Any, document: Any) -> tuple[Any, ...]:
    annotations = []
    for index in range(raw.FPDFPage_GetAnnotCount(page)):
        annotation = raw.FPDFPage_GetAnnot(page, index)
        if not annotation:
            raise ResumeLayoutError("The original PDF annotations cannot be verified.")
        try:
            rect = raw.FS_RECTF()
            if not raw.FPDFAnnot_GetRect(annotation, ctypes.byref(rect)):
                raise ResumeLayoutError("The PDF annotation bounds cannot be verified.")
            uri = b""
            link = raw.FPDFAnnot_GetLink(annotation)
            action = raw.FPDFLink_GetAction(link) if link else None
            action_type = raw.FPDFAction_GetType(action) if action else None
            if action and action_type == raw.PDFACTION_URI:
                size = raw.FPDFAction_GetURIPath(document, action, None, 0)
                buffer = ctypes.create_string_buffer(size)
                raw.FPDFAction_GetURIPath(document, action, buffer, size)
                uri = buffer.value
            annotations.append(
                (
                    raw.FPDFAnnot_GetSubtype(annotation),
                    raw.FPDFAnnot_GetFlags(annotation),
                    (rect.left, rect.bottom, rect.right, rect.top),
                    action_type,
                    uri,
                )
            )
        finally:
            raw.FPDFPage_CloseAnnot(annotation)
    images = []
    for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE]):
        images.append(
            (
                obj.get_bounds(),
                obj.get_matrix().get(),
                hashlib.sha256(bytes(obj.get_data())).hexdigest(),
            )
        )
    return (
        page.get_size(),
        page.get_rotation(),
        page.get_mediabox(),
        page.get_cropbox(),
        page.get_bleedbox(),
        page.get_trimbox(),
        page.get_artbox(),
        tuple(annotations),
        tuple(images),
    )


def _docx_tree(archive: ZipFile) -> Any:
    names = archive.namelist()
    if len(names) != len(set(names)) or len(names) > 2000:
        raise ResumeLayoutError("The DOCX archive structure is unsupported.")
    if any(name.startswith("_xmlsignatures/") for name in names):
        raise ResumeLayoutError(
            "Signed DOCX files cannot be tailored without invalidating the signature."
        )
    if sum(info.file_size for info in archive.infolist()) > 50 * 1024 * 1024:
        raise ResumeLayoutError("The DOCX content is too large for safe source editing.")
    data = archive.read("word/document.xml")
    if len(data) > _MAX_XML_BYTES or b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ResumeLayoutError("The DOCX XML content is unsupported.")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=False)
    tree = etree.fromstring(data, parser)
    if tree.tag != f"{_W}document":
        raise ResumeLayoutError("The DOCX document structure is unsupported.")
    return tree


def _paragraph_nodes(paragraph: Any) -> list[Any]:
    return list(paragraph.iter(f"{_W}t"))


def _paragraph_eligible(paragraph: Any, text: str) -> bool:
    if not _candidate(text):
        return False
    if any(parent.tag == f"{_W}txbxContent" for parent in paragraph.iterancestors()):
        return False
    protected = {
        f"{_W}{name}"
        for name in (
            "fldChar",
            "instrText",
            "fldSimple",
            "drawing",
            "pict",
            "del",
            "ins",
            "tab",
            "br",
            "cr",
        )
    }
    if any(node.tag in protected for node in paragraph.iter()):
        return False
    text_runs = [
        run
        for run in paragraph.iter(f"{_W}r")
        if any((node.text or "").strip() for node in run.iter(f"{_W}t"))
    ]

    def emphasized(run: Any) -> bool:
        properties = run.find(f"{_W}rPr")
        if properties is None:
            return False
        return any(
            node is not None and node.get(f"{_W}val", "true") not in {"false", "0", "off"}
            for node in (properties.find(f"{_W}b"), properties.find(f"{_W}i"))
        )

    if len(text.strip()) <= 120 and text_runs and all(emphasized(run) for run in text_runs):
        return False
    style = paragraph.find(f"{_W}pPr/{_W}pStyle")
    if style is not None and re.search(r"heading|title|subtitle", style.get(f"{_W}val", ""), re.I):
        return False
    return paragraph.find(f"{_W}pPr/{_W}outlineLvl") is None


def _docx_units(tree: Any, source_bytes: bytes) -> list[dict[str, Any]]:
    units = []
    body = tree.find(f"{_W}body")
    if body is None:
        raise ResumeLayoutError("The DOCX main document body is missing.")
    for index, paragraph in enumerate(body.iter(f"{_W}p")):
        text = "".join(node.text or "" for node in _paragraph_nodes(paragraph))
        if _paragraph_eligible(paragraph, text):
            units.append(
                {
                    "unit_id": _unit_id(source_bytes, "docx", f"p{index}", text),
                    "text": text,
                    "kind": "paragraph",
                    "paragraph_index": index,
                    "text_node_count": len(_paragraph_nodes(paragraph)),
                    "max_chars": len(text),
                    "fit_policy": "concise_local_run_edits",
                    "preserve_numbers": True,
                }
            )
    return units


def _distribute_text(nodes: list[Any], original: str, replacement: str) -> list[str]:
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
            owner = owners[min(old_start, len(owners) - 1)]
            output[owner] += value
        elif opcode == "replace":
            # Project replacements across the original run boundaries. Equal
            # spans stay with their original node, including bold/link spans.
            for offset, char in enumerate(value):
                old_offset = min(
                    old_end - old_start - 1, offset * (old_end - old_start) // len(value)
                )
                output[owners[old_start + old_offset]] += char
    if "".join(output) != replacement:
        raise ResumeLayoutError("The DOCX edit cannot preserve its original text run order.")
    return output


def _docx_fit(original: str, replacement: str) -> bool:
    # DOCX is a flowing format, not a fixed PDF slot. Restrict new glyph advance
    # conservatively and never add lines/tabs or grow a paragraph. Native Word
    # fonts resolve new letters; PDF subset encoding constraints do not apply.
    def advance(text: str) -> float:
        return sum(
            0.3 if char in " ilI.,'`:;!|" else 1.1 if char in "MW@%" else 0.65 for char in text
        )

    return len(replacement) <= len(original) and advance(replacement) <= advance(original)


def _apply_docx(source_bytes: bytes, edits: list[dict]) -> bytes:
    with ZipFile(BytesIO(source_bytes)) as archive:
        tree = _docx_tree(archive)
        units = _docx_units(tree, source_bytes)
        replacements = _validated_edits(units, edits)
        if not replacements:
            return source_bytes
        body = tree.find(f"{_W}body")
        paragraphs = list(body.iter(f"{_W}p"))
        for unit in units:
            replacement = replacements.get(unit["unit_id"])
            if replacement is None:
                continue
            if not _docx_fit(unit["text"], replacement):
                raise ResumeLayoutError(
                    "The replacement is too long for its original DOCX paragraph.",
                    unit_id=unit["unit_id"],
                )
            paragraph = paragraphs[unit["paragraph_index"]]
            nodes = _paragraph_nodes(paragraph)
            values = _distribute_text(nodes, unit["text"], replacement)
            for node, value in zip(nodes, values, strict=True):
                if value != (node.text or ""):
                    node.text = value
                    if value.startswith(" ") or value.endswith(" "):
                        node.set(_XML_SPACE, "preserve")
        xml = etree.tostring(tree, encoding="UTF-8", xml_declaration=True, standalone=True)
        output = BytesIO()
        with ZipFile(output, "w") as result:
            result.comment = archive.comment
            for info in archive.infolist():
                result.writestr(
                    info,
                    xml if info.filename == "word/document.xml" else archive.read(info.filename),
                )
        return output.getvalue()
