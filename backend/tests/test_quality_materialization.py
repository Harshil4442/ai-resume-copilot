from __future__ import annotations

import copy
import io
import json
import re
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest
import reportlab
from pypdf import PdfReader

from app.domains.analysis import quality_materialization as native
from app.domains.analysis import quality_packet as packet

ROOT = Path(__file__).resolve().parents[1] / "evals/unified_credit_v1"
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def case_matching(source_format, *risks):
    return copy.deepcopy(next(c for c in packet.load_packet(ROOT)[2]
        if c["source_format"] == source_format and all("risk:" + risk in c["cohorts"] for risk in risks)))


def normalized(text):
    return re.sub(r"\s+", " ", text).strip()


def test_docx_preserves_all_text_tables_columns_fonts_and_links():
    case = case_matching("docx", "tables", "multicolumn")
    source = native._docx(case)
    assert native._docx(case) == source
    with ZipFile(io.BytesIO(source)) as archive:
        xml = ElementTree.fromstring(archive.read("word/document.xml"))
        text = ["".join(p.itertext()) for p in xml.findall(".//w:t", NS)]
        expected = [case["resume"]["name"], case["resume"]["email"], *case["source_recipe"]["required_links"]]
        for section in case["resume"]["sections"]:
            expected += [section["heading"], *section["text"].split("\n")]
        assert text == expected
        assert len(xml.findall(".//w:tbl", NS)) == len(case["source_recipe"]["table_sections"])
        assert any(cols.get("{" + NS["w"] + "}num") == "2" for cols in xml.findall(".//w:cols", NS))
        assert any(br.get("{" + NS["w"] + "}type") == "column" for br in xml.findall(".//w:br", NS))
        assert case["source_recipe"]["font"].encode() in archive.read("word/styles.xml")
        styles = ElementTree.fromstring(archive.read("word/styles.xml"))
        for style in styles.findall("w:style", NS):
            if style.get("{" + NS["w"] + "}styleId") in {"Normal", "Title", "Heading1"}:
                assert style.find(".//w:pBdr", NS) is None
                assert not any("theme" in key.lower() for item in style.findall(".//w:rPr/*", NS) for key in item.attrib)
        for link in case["source_recipe"]["required_links"]:
            assert link.encode() in archive.read("word/_rels/document.xml.rels")
        assert all(info.date_time == native.EPOCH for info in archive.infolist())


def test_tex_preserves_complete_source_and_requested_layout():
    case = case_matching("tex", "tables", "multicolumn")
    tex = native._tex(case).decode()
    assert r"\begin{multicols}{2}" in tex
    assert r"\columnbreak" in tex
    assert tex.count(r"\begin{tabularx}") == len(case["source_recipe"]["table_sections"])
    assert r"\setmainfont{" + case["source_recipe"]["font"] + "}" in tex
    for section in case["resume"]["sections"]:
        for line in section["text"].split("\n"):
            assert native._tex_escape(line) in tex
    assert r"\%" in tex
    assert case["invariants"]["unclaimed_skill"] not in tex


def test_pdf_genuine_multipage_text_and_links_without_font_substitution():
    # A unit-test-only recipe uses ReportLab's bundled actual font. The frozen
    # production packet remains unchanged and cannot use this substitution.
    case = case_matching("pdf", "long_documents", "tables")
    case["source_recipe"]["font"] = "Bitstream Vera Sans"
    fonts = {"Bitstream Vera Sans": Path(reportlab.__file__).parent / "fonts/Vera.ttf"}
    source = native._pdf(case, fonts)
    assert source == native._pdf(case, fonts)
    parsed = PdfReader(io.BytesIO(source))
    assert len(parsed.pages) >= 2
    text = normalized(" ".join(page.extract_text() for page in parsed.pages))
    for section in case["resume"]["sections"]:
        for line in section["text"].split("\n"):
            assert normalized(line) in text
    assert case["resume"]["name"] in text
    links = [annotation.get_object()["/A"]["/URI"] for page in parsed.pages
             for annotation in page.get("/Annots", [])]
    assert links == case["source_recipe"]["required_links"]


def test_pdf_refuses_wrong_named_font_file():
    case = case_matching("pdf")
    with pytest.raises(native.UnsupportedRecipe, match="font_mismatch"):
        native._pdf(case, {case["source_recipe"]["font"]: Path(reportlab.__file__).parent / "fonts/Vera.ttf"})


def test_pinned_official_courier_prime_license_and_font_bytes_are_unchanged():
    assets = ROOT.parent / "assets/courier_prime"
    provenance = json.loads((assets / "provenance.json").read_text())
    assert provenance["source_repository"] == "https://github.com/google/fonts"
    assert provenance["source_commit"] == "7204f5a9b1e530633e00c67bd4954ceb7d8c2483"
    assert provenance["modified"] is False
    for row in provenance["files"]:
        path = assets / row["path"]
        assert path.stat().st_size == row["size_bytes"]
        assert packet.file_digest(path) == row["sha256"]
        assert row["source_url"] == f"https://raw.githubusercontent.com/google/fonts/{provenance['source_commit']}/ofl/courierprime/{path.name}"
    assert packet.file_digest(assets / "OFL.txt") == "9a755af092b494944c99f471be6fddd19b006a448fefdc4717e4ee0aa09a97b0"
    assert packet.file_digest(assets / "CourierPrime-Regular.ttf") == "72f793376f8e2841656bf21d77a5de010f2929bd6956a22ee848ad0c7eb978af"


def test_exact_courier_prime_pdf_is_embedded_with_full_source_text():
    case = next(c for c in packet.load_packet(ROOT)[2]
        if c["source_format"] == "pdf" and c["source_recipe"]["font"] == "Courier Prime")
    font = ROOT.parent / "assets/courier_prime/CourierPrime-Regular.ttf"
    source = native._pdf(case, {"Courier Prime": font})
    reader = PdfReader(io.BytesIO(source))
    text = normalized(" ".join(page.extract_text() for page in reader.pages))
    for section in case["resume"]["sections"]:
        for line in section["text"].splitlines():
            assert normalized(line) in text
    embedded = [font.get_object() for page in reader.pages for font in page["/Resources"]["/Font"].values()
        if "CourierPrime" in font.get_object().get("/BaseFont", "")]
    assert embedded and all(font["/FontDescriptor"].get("/FontFile2") for font in embedded)


def test_multicolumn_pdf_contains_text_in_both_actual_columns():
    case = case_matching("pdf", "multicolumn", "tables")
    case["source_recipe"]["font"] = "Bitstream Vera Sans"
    fonts = {"Bitstream Vera Sans": Path(reportlab.__file__).parent / "fonts/Vera.ttf"}
    reader = PdfReader(io.BytesIO(native._pdf(case, fonts)))
    positions = {}

    def visit(text, cm, tm, font, size):
        if text.strip() in {"Experience", "Projects"}:
            positions[text.strip()] = cm[4] + tm[4]

    reader.pages[0].extract_text(visitor_text=visit)
    assert positions["Experience"] < 306 < positions["Projects"]


def test_unreadable_font_is_explicit_pending_native_source(tmp_path):
    case = case_matching("pdf")
    bad_font = tmp_path / "invalid.ttf"
    bad_font.write_bytes(b"not a font")
    result = native.materialize(ROOT, tmp_path / "out", fonts={case["source_recipe"]["font"]: bad_font}, case_ids=[case["case_id"]])
    assert result["materialized_count"] == 0
    assert result["cases"][0]["materialization_state"] == "native_source_pending"
    assert any("font_unreadable" in issue for issue in result["cases"][0]["issues"])


def test_materialization_binds_frozen_input_and_preserves_unrendered_source(tmp_path, monkeypatch):
    monkeypatch.setattr(native.shutil, "which", lambda name: None)
    cases = [case_matching(fmt) for fmt in packet.FORMATS]
    before = {p.name: packet.file_digest(p) for p in ROOT.iterdir() if p.is_file()}
    destination = tmp_path / "native"
    result = native.materialize(ROOT, destination, case_ids=[case["case_id"] for case in cases])
    assert result["materialized_count"] == 3 and result["case_count"] == 4
    assert result["authorizes_release"] is False and result["quality_status"] == "not_evaluated"
    assert before == {p.name: packet.file_digest(p) for p in ROOT.iterdir() if p.is_file()}
    for row in result["cases"]:
        folder = destination / row["case_id"]
        assert packet.file_digest(folder / "case.json") == row["input_sha256"]
        assert row["compiler"] == "unavailable" and row["rendered_proof"] == "pending"
        assert row["visual_review"] == "pending"
        if row["source_format"] == "pdf":
            assert row["input_artifact"] is None and not (folder / "source.pdf").exists()
            assert (folder / "source.tex").exists()
        else:
            artifact = destination / row["input_artifact"]["path"]
            assert packet.file_digest(artifact) == row["input_artifact"]["sha256"]
            if row["source_format"] == "texzip":
                with ZipFile(artifact) as archive:
                    assert archive.namelist() == ["main.tex"]
                    assert archive.read("main.tex") == (folder / "source.tex").read_bytes()
    with pytest.raises(FileExistsError):
        native.materialize(ROOT, destination)


def test_output_is_reproducible_without_machine_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(native.shutil, "which", lambda name: None)
    case = case_matching("docx", "long_documents")
    first = native.materialize(ROOT, tmp_path / "first", case_ids=[case["case_id"]])
    second = native.materialize(ROOT, tmp_path / "second", case_ids=[case["case_id"]])
    assert first == second


def test_frozen_packet_or_unsupported_layout_never_silently_changes(tmp_path):
    with pytest.raises(ValueError, match="separate"):
        native.materialize(ROOT, ROOT / "new")
    with pytest.raises(ValueError, match="distinct"):
        native.materialize(ROOT, tmp_path / "new", case_ids=["nonexistent"])
    case = case_matching("docx")
    case["source_recipe"]["columns"] = 3
    with pytest.raises(native.UnsupportedRecipe):
        native._check(case)
    case = case_matching("docx")
    case["resume"]["raw_text"] += " altered"
    with pytest.raises(native.UnsupportedRecipe, match="differs"):
        native._check(case)


def test_cli_reports_partial_materialization_without_claiming_success(tmp_path, monkeypatch, capsys):
    from scripts.materialize_quality_sources import main
    case = case_matching("pdf")
    monkeypatch.setattr("sys.argv", ["materialize_quality_sources.py", "--packet", str(ROOT),
        "--destination", str(tmp_path / "pending"), "--case", case["case_id"]])
    assert main() == 2
    assert '"materialized_count": 0' in capsys.readouterr().out
