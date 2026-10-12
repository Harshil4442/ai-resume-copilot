"""Standalone pure-loader checks; no application or global-conftest imports.

Run directly with the backend venv Python. PDFs here are synthetic comparison
fixtures, not claims that LibreOffice rendered these DOCX files.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import unittest
from dataclasses import FrozenInstanceError, replace
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from lxml import etree
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NullObject,
    TextStringObject,
)
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

SOURCE = Path(__file__).resolve().parents[1] / "app/services/docx_layout_validation.py"
SPEC = importlib.util.spec_from_file_location("hirewiz_docx_layout_core_test", SOURCE)
assert SPEC is not None and SPEC.loader is not None
core = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = core
SPEC.loader.exec_module(core)

ORIGINAL = "Built scalable systems with Python and SQL."
REPLACEMENT = "Built systems with Python and SQL."
PROTECTED = "Reduced API latency by 40% through focused profiling."
IDENTITY = core.RendererIdentity(
    "registry.example/worker@sha256:" + "a" * 64,
    "b" * 64, "Synthetic renderer fixture 1", "c" * 64,
)
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx(text=ORIGINAL, *, protected=PROTECTED, style="Times New Roman", styled=False):
    root = etree.Element(W + "document", nsmap={"w": W[1:-1]})
    body = etree.SubElement(root, W + "body")
    for value in ("Taylor Example", text, protected):
        paragraph = etree.SubElement(body, W + "p")
        pieces = value.partition("Python") if styled and value == text else (value,)
        for piece in pieces:
            run = etree.SubElement(paragraph, W + "r")
            if piece == "Python":
                etree.SubElement(etree.SubElement(run, W + "rPr"), W + "b")
            node = etree.SubElement(run, W + "t")
            node.text = piece
            if piece.startswith(" ") or piece.endswith(" "):
                node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("word/document.xml", etree.tostring(root))
        archive.writestr("word/styles.xml", ("<font>" + style + "</font>").encode())
    return stream.getvalue()


def edit(source, replacement=REPLACEMENT):
    return {"unit_id": f"docx:{hashlib.sha256(source).hexdigest()[:16]}:p1:"
            + hashlib.sha256(ORIGINAL.encode()).hexdigest()[:12],
            "original_text": ORIGINAL, "replacement_text": replacement,
            "evidence_ids": ["approved-original"], "reason": "Clearer original wording"}


def pdf(text=ORIGINAL, *, protected=PROTECTED, protected_y=650, font="Times-Roman",
        extra_graphic=False, two_pages=True, link="https://example.test/portfolio",
        internal=False, wrap=False, styled=False, swap_faces=False, border=None,
        image=False, image_overlap=False, image_color="blue"):
    stream = BytesIO()
    canvas = Canvas(stream, pagesize=(612, 792), invariant=True)
    canvas.bookmarkPage("first")
    canvas.setFont("Times-Bold", 18)
    canvas.drawString(48, 740, "Taylor Example")
    canvas.setFont(font, 11)
    if styled:
        prefix, word, suffix = text.partition("Python")
        pieces = [(prefix, "Times-Roman"), (word, "Times-Bold"), (suffix, "Times-Roman")]
        if swap_faces:
            pieces = [("Built", "Times-Bold"), (prefix[5:], "Times-Roman"),
                      (word, "Times-Roman"), (suffix, "Times-Roman")]
        x = 48
        for value, face in pieces:
            canvas.setFont(face, 11)
            canvas.drawString(x, 690, value)
            x += canvas.stringWidth(value, face, 11)
    elif wrap:
        canvas.drawString(48, 690, "Built systems with")
        canvas.drawString(48, 675, "Python and SQL.")
    else:
        canvas.drawString(48, 690, text)
    canvas.setFont("Times-Roman", 11)
    canvas.drawString(48, protected_y, protected)
    canvas.drawString(48, 610, "Portfolio")
    if internal:
        canvas.linkAbsolute("Portfolio", "first", Rect=(48, 608, 95, 623))
    else:
        canvas.linkURL(link, (48, 608, 95, 623), thickness=0)
    if extra_graphic:
        canvas.setFillColorRGB(1, 0, 0)
        canvas.rect(400, 500, 10, 10, fill=1, stroke=0)
    if border:
        canvas.setStrokeColorRGB(1 if border == "red" else 0, 0, 0)
        canvas.line(60, 688, 110, 688)
    if image:
        position = (70, 690) if image_overlap else (400, 500)
        canvas.drawImage(ImageReader(Image.new("RGB", (10, 10), image_color)),
                         *position, width=10, height=10)
    if two_pages:
        canvas.showPage()
        canvas.setFont("Times-Roman", 11)
        canvas.drawString(48, 740, "Original running footer and second page.")
    canvas.save()
    return stream.getvalue()


class DocxLayoutValidationTests(unittest.TestCase):
    def setUp(self):
        self.source = docx()
        self.candidate = docx(REPLACEMENT)
        self.edits = [edit(self.source)]
        self.before = pdf()
        self.after = pdf(REPLACEMENT)

    def validate(self, **changes):
        values = dict(source_docx=self.source, candidate_docx=self.candidate,
                      edits=self.edits, before_pdf=self.before, after_pdf=self.after,
                      renderer=IDENTITY)
        values.update(changes)
        return core.validate_docx_layout(**values)

    def refuses(self, reason, **changes):
        with self.assertRaises(core.DocxLayoutError) as raised:
            self.validate(**changes)
        self.assertEqual(raised.exception.reason, reason)
        self.assertNotIn(ORIGINAL, str(raised.exception))

    def test_real_pdf_comparison_binds_all_input_and_renderer_hashes(self):
        result = self.validate()
        self.assertEqual((result.pages, result.changed_paragraphs, result.unchanged_links), (2, 1, 1))
        self.assertGreater(result.protected_glyphs, 50)
        self.assertEqual(result.outside_changed_regions_pixel_delta, 0)
        self.assertEqual(result.max_protected_geometry_delta_points, 0)
        for field, value in (("source_sha256", self.source), ("candidate_sha256", self.candidate),
                             ("before_pdf_sha256", self.before), ("after_pdf_sha256", self.after)):
            self.assertEqual(getattr(result, field), hashlib.sha256(value).hexdigest())
        canonical = json.dumps(self.edits, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True, allow_nan=False).encode()
        self.assertEqual(result.edits_sha256, hashlib.sha256(canonical).hexdigest())
        self.assertEqual(result.renderer, IDENTITY)
        self.assertNotIn(ORIGINAL, json.dumps(result.to_dict()))
        with self.assertRaises(FrozenInstanceError):
            result.pages = 100

    def test_reflow_new_page_and_protected_geometry_are_refused(self):
        self.refuses("line_wrap_changed", after_pdf=pdf(REPLACEMENT, wrap=True))
        self.refuses("page_geometry_changed", after_pdf=pdf(REPLACEMENT, two_pages=False))
        self.refuses("protected_geometry_changed", after_pdf=pdf(REPLACEMENT, protected_y=645))

    def test_font_change_and_changed_reading_order_are_refused(self):
        self.refuses("font_changed", after_pdf=pdf(REPLACEMENT, font="Helvetica"))
        self.refuses("text_or_reading_order_changed", after_pdf=pdf(REPLACEMENT, protected="Different private text"))

    def test_actual_raster_change_outside_paragraph_is_refused(self):
        self.refuses("outside_region_pixels", before_pdf=pdf(image=True),
                     after_pdf=pdf(REPLACEMENT, image=True, image_color="red"))

    def test_link_target_change_is_refused_and_internal_links_are_supported(self):
        self.refuses("hyperlink_changed", after_pdf=pdf(REPLACEMENT, link="https://example.test/other"))
        result = self.validate(before_pdf=pdf(internal=True), after_pdf=pdf(REPLACEMENT, internal=True))
        self.assertEqual(result.unchanged_links, 1)

    def test_hidden_native_changes_stale_edits_and_duplicates_are_refused(self):
        self.refuses("package_changed", candidate_docx=docx(REPLACEMENT, style="Arial"))
        self.refuses("undeclared_native_change", candidate_docx=docx(REPLACEMENT, protected="Changed claims"))
        self.refuses("edit_binding", edits=[{**self.edits[0], "unit_id": "stale"}])
        self.refuses("edit_binding", edits=self.edits * 2)
        self.refuses("edit_binding", edits=[{**self.edits[0], "original_text": "stale"}])

    def test_renderer_identity_missing_font_manifest_and_size_are_refused(self):
        for renderer in (replace(IDENTITY, image_digest="worker:latest"),
                         replace(IDENTITY, font_manifest_sha256=""),
                         replace(IDENTITY, policy_sha256="invalid")):
            with self.subTest(renderer=renderer):
                self.refuses("renderer_identity", renderer=renderer)
        self.refuses("document_size", source_docx=b"x" * (core.MAX_DOCX_BYTES + 1))
        self.refuses("pdf_size", after_pdf=b"x" * (core.MAX_PDF_BYTES + 1))

    def test_active_pdf_is_refused_before_native_raster(self):
        writer = PdfWriter()
        writer.clone_document_from_reader(PdfReader(BytesIO(self.after)))
        writer.add_js("this.print();")
        stream = BytesIO()
        writer.write(stream)
        self.refuses("pdf_active_content", after_pdf=stream.getvalue())

    def test_neutral_state_and_initial_page_clip_supported_advanced_paint_refused(self):
        def paint(data, *, opacity=1, clip=b"q 0 .028 611.972 791.972 re W* n\n", pattern=False):
            writer = PdfWriter()
            writer.clone_document_from_reader(PdfReader(BytesIO(data)))
            page = writer.pages[0]
            page["/Resources"][NameObject("/ExtGState")] = DictionaryObject({
                NameObject("/GS0"): DictionaryObject({
                    NameObject("/Type"): NameObject("/ExtGState"),
                    NameObject("/CA"): FloatObject(opacity),
                    NameObject("/ca"): FloatObject(1),
                    NameObject("/SMask"): NameObject("/None"),
                    NameObject("/AIS"): BooleanObject(False),
                }),
            })
            if pattern:
                page["/Resources"][NameObject("/Pattern")] = DictionaryObject({
                    NameObject("/P0"): DictionaryObject(),
                })
            contents = DecodedStreamObject()
            contents.set_data(b"/GS0 gs\n" + clip + page.get_contents().get_data())
            page[NameObject("/Contents")] = writer._add_object(contents)
            output = BytesIO()
            writer.write(output)
            return output.getvalue()
        result = self.validate(before_pdf=paint(self.before), after_pdf=paint(self.after))
        self.assertEqual(result.pages, 2)
        # Nonopaque paint can hide a changed border inside the text mask while
        # preserving its exposed geometry and color. It cannot earn a receipt.
        self.refuses("unsupported_paint", after_pdf=paint(self.after, opacity=.5))
        self.refuses("unsupported_paint", after_pdf=paint(self.after, clip=b"q 48 680 200 30 re W* n\n"))
        self.refuses("unsupported_paint", after_pdf=paint(self.after, pattern=True))

    def test_passive_tagged_pdf_is_supported(self):
        def tagged(data):
            writer = PdfWriter()
            writer.clone_document_from_reader(PdfReader(BytesIO(data)))
            writer._root_object[NameObject("/StructTreeRoot")] = DictionaryObject({
                NameObject("/K"): ArrayObject([DictionaryObject({
                    NameObject("/Type"): NameObject("/StructElem"),
                    NameObject("/S"): NameObject("/Document"),
                })]),
            })
            stream = BytesIO()
            writer.write(stream)
            return stream.getvalue()
        result = self.validate(before_pdf=tagged(self.before), after_pdf=tagged(self.after))
        self.assertEqual(result.pages, 2)

    def test_malformed_annotations_and_destinations_emit_only_finite_errors(self):
        for field in ("annotation", "action", "destination"):
            with self.subTest(field=field):
                writer = PdfWriter()
                writer.clone_document_from_reader(PdfReader(BytesIO(self.after)))
                annotation = writer.pages[0]["/Annots"][0].get_object()
                if field == "annotation":
                    writer.pages[0][NameObject("/Annots")] = ArrayObject([TextStringObject("private marker")])
                elif field == "action":
                    annotation[NameObject("/A")] = ArrayObject([])
                else:
                    annotation.pop("/A")
                    annotation[NameObject("/Dest")] = ArrayObject([NullObject(), NameObject("/Fit")])
                stream = BytesIO()
                writer.write(stream)
                with self.assertRaises(core.DocxLayoutError) as raised:
                    self.validate(after_pdf=stream.getvalue())
                self.assertIn(raised.exception.reason, {"malformed_input", "pdf_active_content", "pdf_link"})
                self.assertNotIn("private marker", str(raised.exception))

    def test_indirect_opening_and_null_internal_coordinates_are_passive(self):
        def normalized(data):
            writer = PdfWriter()
            writer.clone_document_from_reader(PdfReader(BytesIO(data)))
            destination = ArrayObject([writer.pages[0].indirect_reference, NameObject("/XYZ"),
                                       NullObject(), NullObject(), NullObject()])
            annotation = writer.pages[0]["/Annots"][0].get_object()
            annotation.pop("/A", None)
            annotation[NameObject("/Dest")] = destination
            writer._root_object[NameObject("/OpenAction")] = writer._add_object(destination)
            stream = BytesIO()
            writer.write(stream)
            return stream.getvalue()
        result = self.validate(before_pdf=normalized(self.before), after_pdf=normalized(self.after))
        self.assertEqual(result.unchanged_links, 1)

    def test_retained_styled_word_keeps_face_while_moving_horizontally(self):
        source, candidate = docx(styled=True), docx(REPLACEMENT, styled=True)
        arguments = dict(source_docx=source, candidate_docx=candidate, edits=[edit(source)],
                         before_pdf=pdf(styled=True), after_pdf=pdf(REPLACEMENT, styled=True))
        self.assertEqual(self.validate(**arguments).changed_paragraphs, 1)
        arguments["after_pdf"] = pdf(REPLACEMENT, styled=True, swap_faces=True)
        with self.assertRaises(core.DocxLayoutError) as raised:
            self.validate(**arguments)
        self.assertEqual(raised.exception.reason, "retained_style_changed")
        self.assertEqual(raised.exception.unit_id, edit(source)["unit_id"])

    def test_masked_border_appearance_is_bound_and_masked_images_refuse(self):
        self.assertGreater(self.validate(before_pdf=pdf(border="black"),
                                         after_pdf=pdf(REPLACEMENT, border="black")).unchanged_graphics, 0)
        self.refuses("nontext_changed", before_pdf=pdf(border="black"),
                     after_pdf=pdf(REPLACEMENT, border="red"))
        self.assertGreater(self.validate(before_pdf=pdf(image=True),
                                         after_pdf=pdf(REPLACEMENT, image=True)).unchanged_graphics, 0)
        self.refuses("unsupported_graphics_overlap", before_pdf=pdf(image=True, image_overlap=True),
                     after_pdf=pdf(REPLACEMENT, image=True, image_overlap=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
