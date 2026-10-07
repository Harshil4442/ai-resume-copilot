"""Splice accepted native text operands into original PDF content streams.

PDFium may merge fonts with different encodings when regenerating a page. This
module retains the original document/resources and changes only selected
top-level Tj/TJ operations. It preserves their total text-cursor advance, so
subsequent relative positioning remains unchanged. The caller must still verify
saved Unicode, font/style, geometry and unchanged pixels before accepting output.

Only horizontal simple fonts and Identity-H CID fonts with provable widths are
supported. Targeted quote shorthand, nonzero character/word spacing, custom
CMaps, missing width tables and incompatible font encodings fail closed.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace
from io import BytesIO
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    ByteStringObject,
    ContentStream,
    FloatObject,
    TextStringObject,
)
from reportlab.pdfbase import pdfmetrics


class PdfStreamError(ValueError):
    """A text-stream splice cannot preserve the original PDF safely."""


_SHOW = {b"Tj", b"TJ", b"'", b'"'}
_STANDARD_FONTS = {
    "Courier",
    "Courier-Bold",
    "Courier-Oblique",
    "Courier-BoldOblique",
    "Helvetica",
    "Helvetica-Bold",
    "Helvetica-Oblique",
    "Helvetica-BoldOblique",
    "Times-Roman",
    "Times-Bold",
    "Times-Italic",
    "Times-BoldItalic",
}
_MAX_BYTES = 20 * 1024 * 1024
_MAX_OPERATIONS = 100_000


@dataclass
class _TextState:
    font: str | None = None
    size: float = 0
    char_space: float = 0
    word_space: float = 0
    horizontal_scale: float = 100


def _number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PdfStreamError("Invalid PDF text-state number.") from exc
    if not math.isfinite(result):
        raise PdfStreamError("Nonfinite PDF text-state number.")
    return result


def _resolve(value: Any) -> Any:
    return value.get_object() if hasattr(value, "get_object") else value


def _string_bytes(value: Any) -> bytes:
    if isinstance(value, ByteStringObject):
        return bytes(value)
    if isinstance(value, TextStringObject):
        try:
            return value.original_bytes
        except Exception as exc:
            raise PdfStreamError("PDF text operand has no original encoded bytes.") from exc
    raise PdfStreamError("Unsupported PDF text operand.")


def _show_array(operands: list, operator: bytes) -> ArrayObject:
    if operator == b"Tj" and len(operands) == 1:
        return ArrayObject([ByteStringObject(_string_bytes(operands[0]))])
    if operator == b"TJ" and len(operands) == 1 and isinstance(operands[0], ArrayObject):
        result = ArrayObject()
        for value in operands[0]:
            if isinstance(value, (ByteStringObject, TextStringObject)):
                result.append(ByteStringObject(_string_bytes(value)))
            else:
                result.append(FloatObject(_number(value)))
        return result
    raise PdfStreamError("Targeted PDF quote shorthand or text operand is unsupported.")


def _shows(stream: ContentStream) -> list[tuple[int, list, bytes, _TextState]]:
    if len(stream.operations) > _MAX_OPERATIONS:
        raise PdfStreamError("PDF content stream is too complex to splice safely.")
    state = _TextState()
    stack: list[_TextState] = []
    result = []
    in_text = False
    for operation_index, (operands, operator) in enumerate(stream.operations):
        if operator == b"q":
            stack.append(replace(state))
        elif operator == b"Q":
            if not stack:
                raise PdfStreamError("Unbalanced PDF graphics state.")
            state = stack.pop()
        elif operator == b"BT":
            if in_text:
                raise PdfStreamError("Nested PDF text objects are unsupported.")
            in_text = True
        elif operator == b"ET":
            if not in_text:
                raise PdfStreamError("Unbalanced PDF text object.")
            in_text = False
        elif operator == b"Tf":
            if len(operands) != 2:
                raise PdfStreamError("Invalid PDF font state.")
            state.font, state.size = str(operands[0]), _number(operands[1])
        elif operator in {b"Tc", b"Tw", b"Tz"}:
            if len(operands) != 1:
                raise PdfStreamError("Invalid PDF spacing state.")
            attribute = {b"Tc": "char_space", b"Tw": "word_space", b"Tz": "horizontal_scale"}[
                operator
            ]
            setattr(state, attribute, _number(operands[0]))
        elif operator in _SHOW:
            if not in_text:
                raise PdfStreamError("PDF text-show operation is outside a text object.")
            if operator == b'"':
                if len(operands) != 3:
                    raise PdfStreamError("Invalid PDF quote shorthand.")
                state.word_space = _number(operands[0])
                state.char_space = _number(operands[1])
            result.append((operation_index, operands, operator, replace(state)))
    if stack or in_text:
        raise PdfStreamError("Unbalanced PDF content state.")
    return result


def _font(page: Any, state: _TextState) -> Any:
    if not state.font or state.size <= 0 or state.horizontal_scale <= 0:
        raise PdfStreamError("PDF target has no supported horizontal font state.")
    if state.char_space != 0 or state.word_space != 0:
        raise PdfStreamError("PDF target uses unsupported character or word spacing.")
    try:
        return _resolve(page["/Resources"]["/Font"][state.font])
    except (KeyError, TypeError) as exc:
        raise PdfStreamError("PDF target font resource is unavailable.") from exc


def _fingerprint(value: Any) -> Any:
    value = _resolve(value)
    if hasattr(value, "get_data"):
        return ("stream", hashlib.sha256(value.get_data()).hexdigest())
    if isinstance(value, dict):
        return tuple(sorted((str(key), _fingerprint(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_fingerprint(item) for item in value)
    return str(value)


def _font_signature(font: Any) -> tuple:
    # Comparing exact encoding/CMap and embedded font bytes is conservative;
    # equal BaseFont names alone do not establish compatible character codes.
    keys = ("/Subtype", "/BaseFont", "/Encoding", "/ToUnicode")
    descriptor_owner = font
    descendants = _resolve(font.get("/DescendantFonts", []))
    if descendants:
        if len(descendants) != 1:
            raise PdfStreamError("Multiple PDF descendant fonts are unsupported.")
        descriptor_owner = _resolve(descendants[0])
    descriptor = _resolve(descriptor_owner.get("/FontDescriptor", {}))
    embedded = tuple(
        _fingerprint(descriptor.get(key)) for key in ("/FontFile", "/FontFile2", "/FontFile3")
    )
    cid_mapping = _fingerprint(descriptor_owner.get("/CIDToGIDMap"))
    return tuple(_fingerprint(font.get(key)) for key in keys) + (embedded, cid_mapping)


def _codes(font: Any, encoded: bytes) -> list[int]:
    subtype = str(font.get("/Subtype"))
    if subtype in {"/Type1", "/TrueType"}:
        return list(encoded)
    if subtype == "/Type0" and str(font.get("/Encoding")) == "/Identity-H":
        if len(encoded) % 2:
            raise PdfStreamError("Invalid Identity-H PDF character codes.")
        return [
            int.from_bytes(encoded[index : index + 2], "big") for index in range(0, len(encoded), 2)
        ]
    raise PdfStreamError("PDF target font uses an unsupported character mapping.")


def _cid_width(font: Any, code: int) -> float:
    descendants = _resolve(font.get("/DescendantFonts", []))
    if len(descendants) != 1:
        raise PdfStreamError("PDF CID font has no single descendant font.")
    descendant = _resolve(descendants[0])
    if str(descendant.get("/Subtype")) not in {"/CIDFontType0", "/CIDFontType2"}:
        raise PdfStreamError("Unsupported PDF descendant font.")
    widths = _resolve(descendant.get("/W", []))
    index = 0
    while index < len(widths):
        start = int(widths[index])
        index += 1
        if index >= len(widths):
            raise PdfStreamError("Invalid PDF CID width table.")
        item = _resolve(widths[index])
        index += 1
        if isinstance(item, list):
            if start <= code < start + len(item):
                return _number(item[code - start])
        else:
            end = int(item)
            if index >= len(widths) or end < start:
                raise PdfStreamError("Invalid PDF CID width range.")
            width = _number(widths[index])
            index += 1
            if start <= code <= end:
                return width
    return _number(descendant.get("/DW", 1000))


def _width(font: Any, code: int) -> float:
    if str(font.get("/Subtype")) == "/Type0":
        result = _cid_width(font, code)
    elif "/Widths" in font:
        widths = _resolve(font["/Widths"])
        first = int(font.get("/FirstChar", -1))
        last = int(font.get("/LastChar", first + len(widths) - 1))
        if not first <= code <= last or not 0 <= code - first < len(widths):
            raise PdfStreamError("PDF target character is outside the original width table.")
        result = _number(widths[code - first])
    else:
        name = str(font.get("/BaseFont", "")).lstrip("/")
        if name not in _STANDARD_FONTS or str(font.get("/Encoding")) != "/WinAnsiEncoding":
            raise PdfStreamError("PDF target font has no provable original character widths.")
        try:
            character = bytes([code]).decode("cp1252")
        except UnicodeDecodeError as exc:
            raise PdfStreamError("Undefined WinAnsi PDF character code.") from exc
        result = pdfmetrics.stringWidth(character, name, 1000)
    if result < 0:
        raise PdfStreamError("Negative PDF character width is unsupported.")
    return result


def _advance(font: Any, operands: ArrayObject, *, integer_widths: bool = True) -> float:
    advance = 0.0
    for value in operands:
        if isinstance(value, ByteStringObject):
            widths = (_width(font, code) for code in _codes(font, bytes(value)))
            # PDFium stores declared simple/CID widths as integers. Matching
            # that native cursor is necessary to retain following glyphs. See
            # CPDF_FaceBasedSimpleFont::LoadCharWidths/GetIntegerAt. The caller's
            # exact unchanged-glyph/raster proof remains required.
            advance += sum(int(width) if integer_widths else width for width in widths)
        else:
            advance -= _number(value)
    return advance


def _retain_leading_position(original: ArrayObject, generated: ArrayObject) -> ArrayObject:
    """Keep the source's shift before the first glyph, already baked into native Tm."""

    def prefix(array: ArrayObject) -> tuple[float, int]:
        offset = 0.0
        for index, value in enumerate(array):
            if isinstance(value, ByteStringObject):
                if value:
                    return offset, index
            else:
                offset += _number(value)
        raise PdfStreamError("PDF target contains no encoded glyphs.")

    old_offset, _ = prefix(original)
    _, new_start = prefix(generated)
    result = ArrayObject(generated[new_start:])
    if old_offset:
        result.insert(0, FloatObject(old_offset))
    return result


def splice_pdf_text_streams(
    source_bytes: bytes,
    generated_bytes: bytes,
    targets: dict[int, dict[int, Any]],
) -> bytes:
    """Replace selected zero-based top-level text-show ordinals on each page.

    Mapping values are caller-owned edit metadata; only keys select operations.
    Source and native-generated pages must have the same text-show count/order.
    No resource from ``generated_bytes`` is copied into the output.
    """
    if not targets:
        return source_bytes
    if any(
        not isinstance(data, bytes) or not data or len(data) > _MAX_BYTES
        for data in (source_bytes, generated_bytes)
    ):
        raise PdfStreamError("PDF source or generated copy is missing or too large.")
    try:
        source = PdfReader(BytesIO(source_bytes), strict=True)
        generated = PdfReader(BytesIO(generated_bytes), strict=True)
        if source.is_encrypted or generated.is_encrypted:
            raise PdfStreamError("Encrypted PDF stream splicing is unsupported.")
        if len(source.pages) != len(generated.pages) or not 1 <= len(source.pages) <= 20:
            raise PdfStreamError("PDF stream-splice page count is unsupported.")
        writer = PdfWriter()
        writer.clone_document_from_reader(source)
        for page_index, selected in targets.items():
            if (
                type(page_index) is not int
                or not 0 <= page_index < len(source.pages)
                or not isinstance(selected, dict)
            ):
                raise PdfStreamError("Invalid PDF target page.")
            if not selected:
                continue
            original_page, native_page = writer.pages[page_index], generated.pages[page_index]
            original_stream = ContentStream(original_page.get_contents(), writer)
            native_stream = ContentStream(native_page.get_contents(), generated)
            original_shows, native_shows = _shows(original_stream), _shows(native_stream)
            if len(original_shows) != len(native_shows):
                raise PdfStreamError("Native PDF text-show order/count changed.")
            for ordinal in selected:
                if type(ordinal) is not int or not 0 <= ordinal < len(original_shows):
                    raise PdfStreamError("Invalid PDF target text-show ordinal.")
                operation_index, old_operands, old_operator, old_state = original_shows[ordinal]
                _, new_operands, new_operator, new_state = native_shows[ordinal]
                old_array, new_array = (
                    _show_array(old_operands, old_operator),
                    _show_array(new_operands, new_operator),
                )
                new_array = _retain_leading_position(old_array, new_array)
                old_font, new_font = _font(original_page, old_state), _font(native_page, new_state)
                if (
                    not math.isclose(old_state.size, new_state.size, rel_tol=0, abs_tol=0.00001)
                    or not math.isclose(
                        old_state.horizontal_scale,
                        new_state.horizontal_scale,
                        rel_tol=0,
                        abs_tol=0.00001,
                    )
                    or _font_signature(old_font) != _font_signature(new_font)
                ):
                    raise PdfStreamError("Native PDF target font/encoding or text state changed.")
                # Verify every transplanted code has the same declared width.
                for value in new_array:
                    if isinstance(value, ByteStringObject):
                        for code in _codes(old_font, bytes(value)):
                            if abs(_width(old_font, code) - _width(new_font, code)) > 0.0001:
                                raise PdfStreamError("Native PDF target character widths changed.")
                correction = _advance(old_font, new_array) - _advance(old_font, old_array)
                declared_correction = _advance(
                    old_font, new_array, integer_widths=False
                ) - _advance(old_font, old_array, integer_widths=False)
                precision_difference = (
                    abs(correction - declared_correction)
                    * old_state.size
                    * old_state.horizontal_scale
                    / 100_000
                )
                if precision_difference > 0.1:
                    raise PdfStreamError(
                        "PDF fractional widths exceed the safe native cursor precision limit."
                    )
                if abs(correction) > 0.000001:
                    new_array.append(FloatObject(correction))
                original_stream.operations[operation_index] = ([new_array], b"TJ")
            original_page.replace_contents(original_stream)
        output = BytesIO()
        writer.write(output)
        return output.getvalue()
    except PdfStreamError:
        raise
    except Exception as exc:
        raise PdfStreamError("PDF content streams could not be spliced safely.") from exc
