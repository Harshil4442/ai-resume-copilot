"""Independent synthetic geometry fixtures, never frozen corpus/qualification substitutes."""
from hashlib import sha256
from io import BytesIO

import pytest
from backend.app.services import parsing
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas


def _document(draw):
    stream = BytesIO()
    canvas = Canvas(stream, pagesize=(612, 792), invariant=True)
    draw(canvas)
    canvas.save()
    return stream.getvalue()


def _line(canvas, x, y, text, font="Helvetica", size=11):
    canvas.setFont(font, size)
    canvas.drawString(x, y, text)


def _columns(canvas, order, *, overlap=False):
    _line(canvas, 48, 752, "Morgan River", "Helvetica-Bold", 20)
    _line(canvas, 48, 732, "morgan@example.test | https://portfolio.example.test/morgan", size=10)
    canvas.linkURL("https://portfolio.example.test/morgan", (48, 728, 460, 741))
    left = [(48, 690, "SUMMARY", "Helvetica-Bold", 14), (48, 670, "Python engineer with documented work.", "Helvetica", 10),
        (48, 630, "EXPERIENCE", "Helvetica-Bold", 14), (48, 610, "Harbor Labs", "Helvetica-Bold", 11),
        (185, 610, "2020 - 2022", "Helvetica-Oblique", 10), (48, 590, "Reduced queue delays by 17%.", "Helvetica", 11)]
    right = [(330, 690, "PROJECTS", "Helvetica-Bold", 14), (330, 670, "Built 3 billing API checks.", "Helvetica", 11),
        (330, 630, "EDUCATION", "Helvetica-Bold", 14), (330, 610, "North College 2015 - 2019", "Helvetica", 11),
        (330, 570, "SKILLS", "Helvetica-Bold", 14), (330, 550, "Python, SQL, Docker", "Helvetica", 11)]
    sequence = left + right if order == "left-first" else right + left
    if order == "row-painted":
        sequence = sorted(left + right, key=lambda row: (-row[1], row[0]))
    for row in sequence:
        _line(canvas, *row)
    if overlap:
        _line(canvas, 48, 520, "Spanning context crosses the two section columns and cannot safely belong to either one.", size=11)


@pytest.mark.parametrize("order", ["left-first", "right-first", "row-painted"])
def test_two_independent_sections_do_not_mix_regardless_of_paint_order(order, monkeypatch):
    monkeypatch.setattr(parsing, "enrich_resume_skills", lambda *_: pytest.fail("No model enrichment"))
    source = _document(lambda canvas: _columns(canvas, order))
    before = sha256(source).hexdigest()
    raw, sections, skills, years, contact = parsing.parse_resume_file(source, "resume.pdf")
    assert raw.index("SUMMARY") < raw.index("EXPERIENCE") < raw.index("PROJECTS") < raw.index("EDUCATION")
    assert "Harbor Labs 2020 - 2022" in sections["experience"]
    assert "Reduced queue delays by 17%." in sections["experience"]
    assert "billing" not in sections["experience"] and "Harbor" not in sections["projects"]
    assert "North College 2015 - 2019" in sections["education"]
    assert years == 2 and {"Python", "SQL", "Docker"} <= set(skills)
    assert contact["name"] == "Morgan River" and contact["email"] == "morgan@example.test"
    assert sha256(source).hexdigest() == before
    assert PdfReader(BytesIO(source)).pages[0]["/Annots"][0].get_object()["/A"]["/URI"] == "https://portfolio.example.test/morgan"


def test_single_column_late_painted_dates_stay_with_their_employer():
    def draw(canvas):
        _line(canvas, 48, 750, "Avery Lake", "Helvetica-Bold", 20)
        _line(canvas, 48, 710, "EXPERIENCE", "Helvetica-Bold", 14)
        _line(canvas, 48, 686, "Example Company", "Helvetica-Bold", 11)
        _line(canvas, 48, 666, "Delivered 4 Python APIs.")
        _line(canvas, 48, 626, "EDUCATION", "Helvetica-Bold", 14)
        _line(canvas, 48, 606, "Degree 2015 - 2019")
        _line(canvas, 480, 686, "2020 - 2022", "Helvetica-Oblique", 10)
    raw, sections = parsing.extract_text_and_sections_from_pdf(_document(draw))
    assert sections["experience"].splitlines() == ["Example Company", "2020 - 2022", "Delivered 4 Python APIs."]
    assert parsing.estimate_experience_years(raw, sections) == 2
    assert "2020 - 2022" not in sections["education"]


def test_a_shared_ruled_table_keeps_row_association_even_when_cells_are_painted_in_columns():
    def draw(canvas):
        _line(canvas, 48, 750, "Avery Lake", "Helvetica-Bold", 20)
        canvas.rect(48, 590, 510, 120)
        canvas.line(300, 590, 300, 710)
        canvas.line(48, 670, 558, 670)
        canvas.line(48, 630, 558, 630)
        for y, text in ((688, "EXPERIENCE"), (648, "Harbor Labs"), (608, "Hill Systems")):
            _line(canvas, 58, y, text, "Helvetica-Bold", 12)
        for y, text in ((688, "EDUCATION"), (648, "2020 - 2022"), (608, "2023 - 2025")):
            _line(canvas, 310, y, text, "Helvetica-Bold", 12)
    raw, _ = parsing.extract_text_and_sections_from_pdf(_document(draw))
    assert "Harbor Labs 2020 - 2022" in raw and "Hill Systems 2023 - 2025" in raw
    assert raw.index("Harbor Labs") < raw.index("2020 - 2022") < raw.index("Hill Systems")


def test_ambiguous_spanning_body_refuses_instead_of_using_interleaved_plaintext():
    source = _document(lambda canvas: _columns(canvas, "row-painted", overlap=True))
    with pytest.raises(parsing.PDFReadingOrderError, match="overlap|span"):
        parsing.extract_text_and_sections_from_pdf(source)


def test_ambiguous_layout_cannot_reach_optional_enrichment(monkeypatch):
    source = _document(lambda canvas: _columns(canvas, "left-first", overlap=True))
    monkeypatch.setattr(parsing, "enrich_resume_skills", lambda *_: pytest.fail("No model enrichment"))
    with pytest.raises(parsing.PDFReadingOrderError):
        parsing.parse_resume_file(source, "resume.pdf", use_llm=True)


def test_multipage_sections_continue_in_page_order_without_dropping_repeated_headings():
    def draw(canvas):
        _columns(canvas, "left-first")
        canvas.showPage()
        _line(canvas, 48, 730, "PROJECTS", "Helvetica-Bold", 14)
        _line(canvas, 48, 710, "Delivered another 2 test tools.")
    raw, sections = parsing.extract_text_and_sections_from_pdf(_document(draw))
    assert raw.count("PROJECTS") == 2
    assert sections["projects"].splitlines() == ["Built 3 billing API checks.", "Delivered another 2 test tools."]



def test_column_local_tables_and_mixed_bold_labels_keep_facts_in_their_section():
    def draw(canvas):
        _columns(canvas, "right-first")
        canvas.rect(48, 515, 252, 109)
        canvas.rect(325, 510, 250, 114)
        _line(canvas, 390, 650, "built a queue inspector.", size=11)
        _line(canvas, 330, 650, "Project 9:", "Helvetica-Bold", 11)
    raw, sections = parsing.extract_text_and_sections_from_pdf(_document(draw))
    assert "Project 9: built a queue inspector." in sections["projects"]
    assert "queue inspector" not in sections["experience"]
    assert raw.index("EXPERIENCE") < raw.index("PROJECTS")


def test_three_independent_columns_are_explicitly_unsupported():
    def draw(canvas):
        for x, heading, fact in ((48, "SUMMARY", "Python engineer"), (240, "PROJECTS", "Built 3 APIs"),
                                  (430, "SKILLS", "Python, SQL")):
            _line(canvas, x, 700, heading, "Helvetica-Bold", 14)
            _line(canvas, x, 680, fact)
    with pytest.raises(parsing.PDFReadingOrderError, match="independent-column"):
        parsing.extract_text_and_sections_from_pdf(_document(draw))


def test_failed_column_geometry_probe_cannot_fall_back_to_interleaved_plaintext(monkeypatch):
    import pdfplumber
    source = _document(lambda canvas: _columns(canvas, "row-painted"))
    def unavailable(*_):
        raise RuntimeError("Synthetic geometry probe unavailable")
    monkeypatch.setattr(pdfplumber.page.Page, "find_tables", unavailable)
    with pytest.raises(parsing.PDFReadingOrderError, match="could not be verified"):
        parsing.extract_text_and_sections_from_pdf(source)


@pytest.mark.parametrize("failure", ["edges", "table_bbox"])
def test_failed_geometry_after_recognition_cannot_reach_plaintext_or_enrichment(monkeypatch, failure):
    import pdfplumber

    source = _document(lambda canvas: _columns(canvas, "row-painted"))
    def unavailable(_):
        raise RuntimeError("Synthetic geometry unavailable")
    if failure == "edges":
        monkeypatch.setattr(pdfplumber.page.Page, "find_tables", lambda _: [])
        monkeypatch.setattr(pdfplumber.page.Page, "edges", property(unavailable))
    else:
        class UnavailableTable:
            rows = columns = (None, None)
            bbox = property(unavailable)
        monkeypatch.setattr(pdfplumber.page.Page, "find_tables", lambda _: [UnavailableTable()])
    monkeypatch.setattr(pdfplumber.page.Page, "extract_text", lambda *_: pytest.fail("No plaintext fallback"))
    monkeypatch.setattr(parsing, "enrich_resume_skills", lambda *_: pytest.fail("No model enrichment"))
    with pytest.raises(parsing.PDFReadingOrderError, match="geometry could not be verified"):
        parsing.parse_resume_file(source, "resume.pdf", use_llm=True)


def test_later_heading_pair_cannot_hide_earlier_unaligned_independent_sections():
    def draw(canvas):
        _line(canvas, 48, 752, "Morgan River", "Helvetica-Bold", 20)
        _line(canvas, 48, 710, "EDUCATION", "Helvetica-Bold", 14)
        _line(canvas, 330, 700, "EXPERIENCE", "Helvetica-Bold", 14)
        _line(canvas, 48, 680, "North College 2015 - 2019")
        _line(canvas, 330, 680, "Harbor Labs 2020 - 2022")
        _line(canvas, 48, 660, "Degree in Computing")
        _line(canvas, 330, 660, "Built 3 Python APIs")
        _line(canvas, 48, 620, "PROJECTS", "Helvetica-Bold", 14)
        _line(canvas, 330, 620, "SKILLS", "Helvetica-Bold", 14)
        _line(canvas, 48, 600, "Built 2 billing checks")
        _line(canvas, 330, 600, "Python, SQL, Docker")
    source = _document(draw)
    with pytest.raises(parsing.PDFReadingOrderError, match="earlier independent sections"):
        parsing.extract_text_and_sections_from_pdf(source)


def test_shared_contact_and_single_spanning_section_before_columns_remain_readable():
    def draw(canvas):
        _columns(canvas, "row-painted")
        _line(canvas, 48, 715, "PROFILE", "Helvetica-Bold", 12)
        _line(canvas, 48, 702, "Engineer focused on clear records, accountable work and reliable delivery.", size=10)
    raw, sections, _, years, contact = parsing.parse_resume_file(_document(draw), "resume.pdf")
    assert "morgan@example.test | https://portfolio.example.test/morgan" in raw
    assert "Engineer focused on clear records, accountable work and reliable delivery." in raw
    assert contact["name"] == "Morgan River" and contact["email"] == "morgan@example.test"
    assert "Harbor Labs" in sections["experience"] and years == 2


@pytest.mark.parametrize("font", ["Helvetica", "Times-Roman"])
@pytest.mark.parametrize("size", [6, 10, 24, 40])
def test_relative_word_spacing_preserves_small_spaces_and_large_font_kerning(font, size):
    from reportlab.pdfbase.pdfmetrics import stringWidth

    text = "Delivered 14 API checks."
    def draw(canvas):
        _line(canvas, 48, 750, "EXPERIENCE", "Helvetica-Bold", 14)
        x = 48.0
        for character in text:
            if character == " ":
                x += size * .24
            else:
                _line(canvas, x, 680, character, font, size)
                x += stringWidth(character, font, size) + size * .04
    source = _document(draw)
    before = sha256(source).hexdigest()
    raw, sections = parsing.extract_text_and_sections_from_pdf(source)
    assert sections["experience"] == text
    assert "Delivered 14 API checks." in raw
    assert sha256(source).hexdigest() == before


def test_visible_compound_and_line_end_hyphens_are_not_dictionary_reconstructed():
    def draw(canvas):
        _line(canvas, 48, 750, "EXPERIENCE", "Helvetica-Bold", 14)
        _line(canvas, 48, 720, "Built event-driven API-14 tools.", size=10)
        _line(canvas, 48, 700, "docu-", size=10)
        _line(canvas, 48, 686, "mented result", size=10)
    raw, sections = parsing.extract_text_and_sections_from_pdf(_document(draw))
    assert sections["experience"].splitlines() == ["Built event-driven API-14 tools.", "docu-", "mented result"]
    assert "documented" not in raw
