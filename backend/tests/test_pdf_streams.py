from __future__ import annotations

from contextlib import closing
from io import BytesIO

import pypdfium2 as pdfium
import pytest
from backend.app.services.pdf_streams import PdfStreamError, splice_pdf_text_streams
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    ByteStringObject,
    ContentStream,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen.canvas import Canvas

ORIGINAL = b"Created robust services with secure access."
REPLACEMENT = b"Built robust services with secure access."


def _source(*, fractional: bool = False, leading: bool = False) -> bytes:
    output = BytesIO()
    canvas = Canvas(output, pagesize=(612, 792), invariant=True)
    canvas.setFont("Times-Roman", 11)
    canvas.drawString(50, 650, ORIGINAL.decode())
    canvas.save()
    writer = PdfWriter(clone_from=PdfReader(BytesIO(output.getvalue())))
    page = writer.pages[0]
    content = ContentStream(page.get_contents(), writer)
    index = next(i for i, (_, op) in enumerate(content.operations) if op == b"Tj")
    first = ArrayObject([ByteStringObject(ORIGINAL)])
    if leading:
        first.insert(0, FloatObject(-500))
    content.operations[index] = ([first], b"TJ")
    content.operations.insert(index + 1, ([ByteStringObject(b"NEXT")], b"Tj"))
    if fractional:
        font_name = next(
            operands[0] for operands, op in reversed(content.operations[:index]) if op == b"Tf"
        )
        font = page["/Resources"]["/Font"][font_name].get_object()
        font[NameObject("/FirstChar")] = NumberObject(32)
        font[NameObject("/LastChar")] = NumberObject(126)
        font[NameObject("/Widths")] = ArrayObject(
            [
                FloatObject(pdfmetrics.stringWidth(chr(code), "Times-Roman", 1000) + 0.5)
                for code in range(32, 127)
            ]
        )
    page.replace_contents(content)
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


def _generated(source: bytes, *, leading: bool = False, size_delta: float = 0) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(BytesIO(source)))
    page = writer.pages[0]
    content = ContentStream(page.get_contents(), writer)
    shows = [i for i, (_, op) in enumerate(content.operations) if op in {b"Tj", b"TJ"}]
    content.operations[shows[0]] = ([ByteStringObject(REPLACEMENT)], b"Tj")
    # This unwanted native change must never enter the original document.
    content.operations[shows[1]] = ([ByteStringObject(b"BAD")], b"Tj")
    for operands, operator in content.operations[: shows[0]]:
        if operator == b"Tm" and leading:
            operands[4] = FloatObject(float(operands[4]) + 5.5)
        if operator == b"Tf" and size_delta:
            operands[1] = FloatObject(float(operands[1]) + size_delta)
    page.replace_contents(content)
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


def _text_objects(data: bytes) -> list[tuple[str, tuple]]:
    with (
        pdfium.PdfDocument(data) as document,
        closing(document[0]) as page,
        closing(page.get_textpage()) as textpage,
    ):
        return [
            (obj.extract(), obj.get_matrix().get())
            for obj in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_TEXT], textpage=textpage)
        ]


@pytest.mark.parametrize("leading", [False, True])
@pytest.mark.parametrize("fractional", [False, True])
def test_splice_keeps_original_resources_and_following_cursor(leading, fractional):
    source = _source(leading=leading, fractional=fractional)
    generated = _generated(source, leading=leading)
    result = splice_pdf_text_streams(source, generated, {0: {0: "selected"}})
    old_objects, new_objects = _text_objects(source), _text_objects(result)
    assert new_objects[0][0].rstrip() == REPLACEMENT.decode()
    assert new_objects[0][1] == pytest.approx(old_objects[0][1], abs=0.0001)
    assert new_objects[1][0] == "NEXT"
    assert new_objects[1][1] == pytest.approx(old_objects[1][1], abs=0.0001)
    old_reader, new_reader = PdfReader(BytesIO(source)), PdfReader(BytesIO(result))
    assert (
        old_reader.pages[0]["/Resources"]["/Font"].keys()
        == new_reader.pages[0]["/Resources"]["/Font"].keys()
    )
    old_font = old_reader.pages[0]["/Resources"]["/Font"]["/F2"].get_object()
    new_font = new_reader.pages[0]["/Resources"]["/Font"]["/F2"].get_object()
    assert old_font == new_font


def test_splice_accepts_native_float_precision_but_rejects_font_size_change():
    source = _source()
    assert splice_pdf_text_streams(
        source, _generated(source, size_delta=-0.0000002), {0: {0: "selected"}}
    )
    with pytest.raises(PdfStreamError, match="font/encoding or text state changed"):
        splice_pdf_text_streams(source, _generated(source, size_delta=-0.01), {0: {0: "selected"}})


@pytest.mark.parametrize("operator,value", [(b"Tc", 1), (b"Tw", 2)])
def test_splice_rejects_nonzero_spacing(operator, value):
    source = _source()
    writer = PdfWriter(clone_from=PdfReader(BytesIO(source)))
    page = writer.pages[0]
    content = ContentStream(page.get_contents(), writer)
    index = next(i for i, (_, op) in enumerate(content.operations) if op == b"TJ")
    content.operations.insert(index, ([FloatObject(value)], operator))
    page.replace_contents(content)
    result = BytesIO()
    writer.write(result)
    changed = result.getvalue()
    with pytest.raises(PdfStreamError, match="character or word spacing"):
        splice_pdf_text_streams(changed, changed, {0: {0: "selected"}})


def test_splice_rejects_changed_encoding_and_invalid_target():
    source = _source()
    writer = PdfWriter(clone_from=PdfReader(BytesIO(source)))
    writer.pages[0]["/Resources"]["/Font"]["/F2"].get_object()[NameObject("/Encoding")] = (
        NameObject("/MacRomanEncoding")
    )
    output = BytesIO()
    writer.write(output)
    with pytest.raises(PdfStreamError, match="font/encoding or text state changed"):
        splice_pdf_text_streams(source, output.getvalue(), {0: {0: "selected"}})
    with pytest.raises(PdfStreamError, match="ordinal"):
        splice_pdf_text_streams(source, source, {0: {2: "invalid"}})
    assert splice_pdf_text_streams(source, b"unused", {}) is source


def test_splice_rejects_bullet_font_merged_with_same_name_body_font():
    source = _source()
    writer = PdfWriter(clone_from=PdfReader(BytesIO(source)))
    page = writer.pages[0]
    fonts = page["/Resources"]["/Font"]
    bullet_font = DictionaryObject(fonts["/F2"].get_object())
    bullet_font[NameObject("/Encoding")] = DictionaryObject(
        {
            NameObject("/BaseEncoding"): NameObject("/WinAnsiEncoding"),
            NameObject("/Differences"): ArrayObject([NumberObject(65), NameObject("/bullet")]),
        }
    )
    fonts[NameObject("/F3")] = bullet_font
    content = ContentStream(page.get_contents(), writer)
    index = next(i for i, (_, op) in enumerate(content.operations) if op == b"TJ")
    content.operations[index:index] = [
        ([NameObject("/F3"), NumberObject(11)], b"Tf"),
        ([ByteStringObject(b"A")], b"Tj"),
        ([NameObject("/F2"), NumberObject(11)], b"Tf"),
    ]
    page.replace_contents(content)
    output = BytesIO()
    writer.write(output)
    source = output.getvalue()
    native = PdfWriter(clone_from=PdfReader(BytesIO(source)))
    native_page = native.pages[0]
    content = ContentStream(native_page.get_contents(), native)
    # Simulate PDFium choosing the first font by BaseFont alone: the body is
    # now interpreted through the bullet font's incompatible encoding.
    for operands, operator in content.operations:
        if operator == b"Tf" and operands[0] == "/F2":
            operands[0] = NameObject("/F3")
    native_page.replace_contents(content)
    output = BytesIO()
    native.write(output)
    with pytest.raises(PdfStreamError, match="font/encoding or text state changed"):
        splice_pdf_text_streams(source, output.getvalue(), {0: {1: "body"}})
