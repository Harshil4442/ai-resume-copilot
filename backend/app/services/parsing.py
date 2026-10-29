import datetime
import io
import re
from functools import lru_cache

import pdfplumber
from rapidfuzz import fuzz

try:
    from docx import Document as DocxDocument
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False


@lru_cache(maxsize=1)
def get_nlp():
    import spacy

    try:
        return spacy.load("en_core_web_sm")
    except Exception:
        return spacy.blank("en")


# ---------------------------------------------------------------------------
# Section classification via rapidfuzz (no hardcoded exact matches)
# ---------------------------------------------------------------------------

CANONICAL_SECTIONS: dict[str, list[str]] = {
    "summary":        ["summary", "profile", "objective", "about", "overview",
                       "professional summary", "career objective", "introduction"],
    "experience":     ["experience", "work experience", "employment", "work history",
                       "professional experience", "career history", "positions held",
                       "professional background"],
    "education":      ["education", "academic background", "qualifications",
                       "academic credentials", "degrees", "schooling"],
    "skills":         ["skills", "technical skills", "core competencies", "competencies",
                       "expertise", "technologies", "tools", "tech stack"],
    "projects":       ["projects", "personal projects", "side projects", "open source",
                       "portfolio", "notable projects", "key projects"],
    "certifications": ["certifications", "certificates", "credentials",
                       "licenses", "accreditations"],
    "awards":         ["awards", "honors", "achievements", "recognition", "accomplishments"],
    "publications":   ["publications", "papers", "research", "articles"],
    "volunteer":      ["volunteer", "volunteering", "community service"],
    "languages":      ["languages", "language skills"],
    "interests":      ["interests", "hobbies", "activities"],
}


def classify_section_header(header_text: str) -> str:
    """Fuzzy-match a header line to a canonical section name. Returns 'other' if no good match."""
    header_lower = header_text.lower().strip()
    best_match, best_score = "other", 0.0

    for canonical, aliases in CANONICAL_SECTIONS.items():
        for alias in aliases:
            score = fuzz.ratio(header_lower, alias)
            if score > best_score:
                best_score = score
                best_match = canonical

    return best_match if best_score >= 65 else "other"


# ---------------------------------------------------------------------------
# PDF extraction with layout-aware header detection
# ---------------------------------------------------------------------------

# Geometry only: native typesetting may encode word spaces as ~0.3em gaps,
# below pdfplumber's default absolute3pt threshold at small body sizes. A
# font-relative threshold keeps positive letter kerning within large-font words.
PDF_WORD_X_TOLERANCE_RATIO = 0.15


class PDFReadingOrderError(ValueError):
    """A recognized independent-column layout cannot be read without mixing facts."""


def _pdf_header_candidate(text: str, words: list[dict], median_size: float) -> bool:
    average = sum(word.get("size", 10) for word in words) / len(words)
    bold = any("bold" in word.get("fontname", "").lower() for word in words)
    short = len(text.split()) <= 6
    return short and (average >= median_size * 1.15 or bold or text.isupper())


def _pdf_physical_rows(words: list[dict], median_size: float) -> list[list[dict]]:
    """Group nearby line tops without quantization boundaries or chained drift.

    A one-point change must not put same-baseline words into different five-point
    buckets. Each row keeps its first physical top as its fixed anchor, so a run
    of successively lower words cannot bridge separate lines transitively.
    """
    tolerance = max(2.0, min(3.0, median_size * .25))
    rows: list[list[dict]] = []
    for word in sorted(words, key=lambda item: (item.get("top", 0), item["x0"])):
        if not rows or word.get("top", 0) - rows[-1][0].get("top", 0) > tolerance:
            rows.append([])
        rows[-1].append(word)
    return rows


def _pdf_header_anchors(headers: list[list[dict]], median_size: float) -> list[list[list[dict]]]:
    """Cluster repeated section starts, not body words or a guessed page midpoint."""
    tolerance = max(2.0, min(5.0, median_size * .3))
    anchors: list[list[list[dict]]] = []
    for header in sorted(headers, key=lambda chunk: min(word["x0"] for word in chunk)):
        x0 = min(word["x0"] for word in header)
        if not anchors or x0 - min(word["x0"] for word in anchors[-1][0]) > tolerance:
            anchors.append([])
        anchors[-1].append(header)
    return anchors


def _pdf_reading_groups(page, words: list[dict], median_size: float) -> list[list[dict]]:
    """Split only demonstrably independent two-column sections, never date rows.

    pdfplumber supplies words/fonts and table geometry. Source paint order and
    pdfminer's box-flow ordering are not reliable column-reading contracts.
    Every admitted word remains in exactly one original-coordinate group.
    """
    rows = _pdf_physical_rows(words, median_size)
    tables = None
    header_rows: list[list[list[dict]]] = []
    for row in rows:
        chunks: list[list[dict]] = []
        for word in sorted(row, key=lambda item: item["x0"]):
            if not chunks or word["x0"] - chunks[-1][-1]["x1"] >= max(24, median_size * 3):
                chunks.append([])
            chunks[-1].append(word)
        headers = []
        for chunk in chunks:
            text = " ".join(word["text"] for word in chunk)
            if _pdf_header_candidate(text, chunk, median_size) and classify_section_header(text) != "other":
                headers.append(chunk)
        if headers:
            header_rows.append(headers)

    all_headers = [header for row in header_rows for header in row]
    anchors = _pdf_header_anchors(all_headers, median_size)
    # One left-aligned section stream, including right-aligned dates, keeps its
    # existing row semantics. Widely separated styled section streams need a
    # physical ownership proof even when none of their headings share a row.
    separated = (len(anchors) >= 2 and
                 min(word["x0"] for word in anchors[-1][0]) -
                 min(word["x0"] for word in anchors[0][0]) >= max(72, median_size * 8))
    paired_rows = [headers for headers in header_rows if len(headers) >= 2]
    if not paired_rows and not separated:
        return [words]

    try:
        # A failed geometry probe after independent streams are recognized must
        # never fall back to a mixed plaintext reading.
        try:
            tables = page.find_tables()
        except Exception as error:
            raise PDFReadingOrderError("The PDF table and column flow could not be verified; use the original or an editable source.") from error

        def shared_table(headers):
            left = min(word["x0"] for header in headers for word in header)
            right = max(word["x1"] for header in headers for word in header)
            top = min(word["top"] for header in headers for word in header)
            bottom = max(word["bottom"] for header in headers for word in header)
            # A shared ruled table has row semantics, including labels/dates painted
            # in separate passes. Keep its existing row extraction rather than
            # reading each cell column as an unrelated resume section.
            return any(len(table.rows) >= 2 and len(table.columns) >= 2
                       and table.bbox[0] <= left and table.bbox[2] >= right
                       and table.bbox[1] <= top and table.bbox[3] >= bottom
                       for table in tables)

        # Headers inside one shared row-oriented table are not independent
        # streams, including when the rest of the page has ordinary sections.
        shared_headers = set()
        for table in tables:
            if len(table.rows) < 2 or len(table.columns) < 2:
                continue
            inside = [header for header in all_headers
                      if table.bbox[0] <= min(word["x0"] for word in header)
                      and max(word["x1"] for word in header) <= table.bbox[2]
                      and table.bbox[1] <= min(word["top"] for word in header)
                      and max(word["bottom"] for word in header) <= table.bbox[3]]
            if (inside and max(min(word["x0"] for word in header) for header in inside) -
                    min(min(word["x0"] for word in header) for header in inside) >= max(72, median_size * 8)):
                shared_headers.update(id(header) for header in inside)
        header_rows = [[header for header in row if id(header) not in shared_headers] for row in header_rows]
        all_headers = [header for row in header_rows for header in row]
        anchors = _pdf_header_anchors(all_headers, median_size)
        paired_rows = [headers for headers in header_rows if len(headers) >= 2]
        separated = (len(anchors) >= 2 and
                     min(word["x0"] for word in anchors[-1][0]) -
                     min(word["x0"] for word in anchors[0][0]) >= max(72, median_size * 8))
        if not paired_rows and not separated:
            return [words]

        paired_rows = [headers for headers in paired_rows if not shared_table(headers)]
        if paired_rows:
            headers = paired_rows[0]
            if len(headers) != 2:
                raise PDFReadingOrderError("This independent-column PDF layout needs manual review; use the original or an editable source.")
            top = min(word["top"] for header in headers for word in header)
            right_start = min(word["x0"] for word in headers[1])
            earlier = [header for header in all_headers if max(word["bottom"] for word in header) < top]
            earlier_sides = {"right" if min(word["x0"] for word in header) >= right_start else "left"
                             for header in earlier}
            if len(earlier_sides) == 2:
                # Include the complete earlier independent flow in the proof;
                # a later aligned pair cannot turn it into shared header text.
                top = min(word["top"] for header in earlier for word in header)
        else:
            if shared_table(all_headers):
                return [words]
            if not separated or len(anchors) != 2 or any(len(anchor) < 2 for anchor in anchors):
                raise PDFReadingOrderError("This independent-column PDF layout needs manual review; use the original or an editable source.")
            # Repeated, styled canonical starts establish candidate streams;
            # only the full-body gutter and rule checks below establish ownership.
            top = min(word["top"] for header in all_headers for word in header)
            right_start = min(word["x0"] for header in anchors[1] for word in header)

        body_headers = [header for header in all_headers if min(word["top"] for word in header) >= top]
        body_anchors = _pdf_header_anchors(body_headers, median_size)
        if len(body_anchors) != 2:
            raise PDFReadingOrderError("This independent-column PDF layout needs manual review; use the original or an editable source.")
        prelude = [word for word in words if word["top"] < top]
        body = [word for word in words if word["top"] >= top]
        left_words = [word for word in body if word["x0"] < right_start]
        right_words = [word for word in body if word["x0"] >= right_start]
        left_end = max(word["x1"] for word in left_words)
        # Local table borders/underlines can extend beyond their text. Keep the
        # cut in the actual gutter rather than slicing such a left-column box.
        left_edges = [edge["x1"] for edge in page.edges if edge.get("orientation") == "h"
                      and edge["top"] > top and edge["x0"] < right_start and edge["x1"] < right_start]
        left_end = max(left_end, *left_edges) if left_edges else left_end
        gap = min(word["x0"] for word in right_words) - left_end
        if gap < max(12, median_size):
            raise PDFReadingOrderError("The PDF columns overlap or span their gutter; use the original or an editable source.")
        cut = left_end + gap / 2
        if any(edge.get("orientation") == "h" and edge["x0"] < cut < edge["x1"]
               and edge["top"] > top for edge in page.edges):
            raise PDFReadingOrderError("The PDF mixes independent columns with spanning rules; use the original or an editable source.")
        if min(len(_pdf_physical_rows(side, median_size)) for side in (left_words, right_words)) < 2:
            raise PDFReadingOrderError("The PDF column flow could not be verified; use the original or an editable source.")
        return [prelude, left_words, right_words]
    except PDFReadingOrderError:
        raise
    except Exception as error:
        raise PDFReadingOrderError("The PDF column geometry could not be verified; use the original or an editable source.") from error


def extract_text_and_sections_from_pdf(file_bytes: bytes) -> tuple[str, dict[str, str]]:
    """
    Extract raw text and classify sections from a PDF.
    Uses pdfplumber character-level font-size data to identify section headers,
    then rapidfuzz to classify them — no hardcoded section names required.
    """
    tagged_lines: list[tuple[str, bool]] = []  # (line_text, is_header_candidate)

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            try:
                words = page.extract_words(x_tolerance_ratio=PDF_WORD_X_TOLERANCE_RATIO, extra_attrs=["size", "fontname"])
                if not words:
                    raise ValueError("no words")

                sizes = sorted(w.get("size", 10) for w in words if w.get("size"))
                median_size = sizes[len(sizes) // 2] if sizes else 10
                # Preserve the existing single-column/date-row behavior. Only
                # verified independent columns receive separate Y grouping.
                try:
                    for group in _pdf_reading_groups(page, words, median_size):
                        for row in _pdf_physical_rows(group, median_size):
                            line_words = sorted(row, key=lambda word: word["x0"])
                            line_text = " ".join(word["text"] for word in line_words).strip()
                            if line_text:
                                tagged_lines.append((line_text, _pdf_header_candidate(line_text, line_words, median_size)))
                except PDFReadingOrderError:
                    raise
                except Exception as error:
                    raise PDFReadingOrderError("The PDF column geometry could not be verified; use the original or an editable source.") from error

            except PDFReadingOrderError:
                raise  # A known ambiguous flow must never become mixed plaintext.
            except Exception:
                # Fallback: plain text, use ALL-CAPS heuristic
                for line in (page.extract_text(x_tolerance_ratio=PDF_WORD_X_TOLERANCE_RATIO) or "").splitlines():
                    s = line.strip()
                    if s:
                        tagged_lines.append((s, s.isupper() and len(s.split()) <= 6))

    return _build_sections(tagged_lines)


def _build_sections(tagged_lines: list[tuple[str, bool]]) -> tuple[str, dict[str, str]]:
    raw_parts: list[str] = []
    sections: dict[str, list[str]] = {"other": []}
    current = "other"

    for line_text, is_header_candidate in tagged_lines:
        raw_parts.append(line_text)
        if is_header_candidate:
            classified = classify_section_header(line_text)
            if classified != "other":
                current = classified
                sections.setdefault(current, [])
                continue
        sections.setdefault(current, []).append(line_text)

    raw_text = "\n".join(raw_parts)
    sections_text = {k: "\n".join(v).strip() for k, v in sections.items() if any(v)}
    return raw_text, sections_text


# ---------------------------------------------------------------------------
# DOCX extraction
# ---------------------------------------------------------------------------

def extract_text_from_docx(file_bytes: bytes) -> str:
    if not DOCX_AVAILABLE:
        raise RuntimeError("python-docx not installed.")
    doc = DocxDocument(io.BytesIO(file_bytes))

    def block_text(container) -> list[str]:
        parts = []
        for block in container.iter_inner_content():
            if isinstance(block, Paragraph):
                if block.text.strip():
                    parts.append(block.text)
            elif isinstance(block, Table):
                seen_cells = set()
                for row in block.rows:
                    values = []
                    for cell in row.cells:
                        if cell._tc in seen_cells:
                            continue
                        seen_cells.add(cell._tc)
                        text = "\n".join(block_text(cell)).strip()
                        if text:
                            values.append(text)
                    if values:
                        parts.append("\t".join(values))
        return parts

    return "\n".join(block_text(doc))


def _heuristic_sections_fuzzy(text: str) -> dict[str, str]:
    """Section detection for plain text / DOCX (no font-size data)."""
    sections: dict[str, list[str]] = {"other": []}
    current = "other"
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if len(s.split()) <= 6 and (s.isupper() or s.istitle()):
            classified = classify_section_header(s)
            if classified != "other":
                current = classified
                sections.setdefault(current, [])
                continue
        sections.setdefault(current, []).append(s)
    return {k: "\n".join(v).strip() for k, v in sections.items() if any(v)}


# ---------------------------------------------------------------------------
# Contact info extraction
# ---------------------------------------------------------------------------

def extract_contact_info(text: str) -> dict[str, str | None]:
    """Extract name, email, phone, LinkedIn, GitHub using regex + spaCy NER."""
    header = text[:2000]

    email = next(iter(re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", header)), None)
    phone_matches = re.findall(r"[\+]?[(]?[0-9]{1,4}[)]?[-\s\.]?[(]?[0-9]{1,3}[)]?[-\s\.]?[0-9]{3,4}[-\s\.]?[0-9]{3,4}", header[:500])
    phone = phone_matches[0].strip() if phone_matches else None

    li = re.search(r"linkedin\.com/in/([a-zA-Z0-9\-]+)", header, re.I)
    linkedin = f"linkedin.com/in/{li.group(1)}" if li else None

    gh = re.search(r"github\.com/([a-zA-Z0-9\-]+)", header, re.I)
    github = f"github.com/{gh.group(1)}" if gh else None

    # Deterministic header candidate. Ambiguous/missing names remain reviewable
    # rather than loading a learned model during routine extraction.
    name = None
    for line in text.splitlines()[:3]:
        s = line.strip()
        if (s and 1 < len(s.split()) <= 4 and not re.search(r"[@\d|:]", s)
                and classify_section_header(s) == "other"
                and s.casefold() not in {"curriculum vitae", "software engineer", "software developer"}):
            name = s
            break

    return {"name": name, "email": email, "phone": phone, "linkedin": linkedin, "github": github}


# ---------------------------------------------------------------------------
# Experience year estimation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Experience year estimation — section-aware + deduplication
# ---------------------------------------------------------------------------

_MONTH_PAT = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?"
    r"|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?"
    r"|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_DATE_RANGE = re.compile(
    rf"(?:(?P<start_month>{_MONTH_PAT})\s+)?(?P<start_year>20\d{{2}}|19\d{{2}})"
    rf"\s*(?:[-\u2013\u2014]|to)\s*"
    rf"(?:(?P<end_month>{_MONTH_PAT})\s+)?(?P<end_year>20\d{{2}}|19\d{{2}}|present|current|now)",
    re.IGNORECASE,
)
_MONTHS = {name: index for index, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
)}


def _extract_intervals(text: str) -> list[tuple[int, int]]:
    """Pull half-open month intervals; retain conservative year-only estimates."""
    today = datetime.datetime.now()
    current_year = today.year
    intervals: list[tuple[int, int]] = []
    for m in _DATE_RANGE.finditer(text):
        start_raw, end_raw = m.group("start_year"), m.group("end_year")
        try:
            start_year = int(start_raw)
            ongoing = end_raw.lower() in ("present", "current", "now")
            end_year = current_year if ongoing else int(end_raw)
        except (ValueError, AttributeError):
            continue
        if not 1970 <= start_year <= end_year <= current_year + 1:
            continue
        start_month = m.group("start_month")
        end_month = m.group("end_month")
        start = start_year * 12 + (_MONTHS[start_month[:3].lower()] - 1 if start_month else 0)
        if ongoing:
            end = current_year * 12 + today.month
        elif end_month:
            end = end_year * 12 + _MONTHS[end_month[:3].lower()]
        else:
            end = end_year * 12
        if end >= start:
            intervals.append((start, end))
    return intervals


def _merge_and_sum(intervals: list[tuple[int, int]]) -> float:
    """Merge overlapping job periods so concurrent jobs aren't double-counted."""
    if not intervals:
        return 0.0
    intervals = sorted(set(intervals))
    merged = [list(intervals[0])]
    for start, end in intervals[1:]:
        if start <= merged[-1][1]:          # overlapping — extend
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return min(round(sum(e - s for s, e in merged) / 12, 2), 40.0)


# Work-context phrases for explicit "X years" fallback
_WORK_YEAR_RE = re.compile(
    r"(\d+)\+?\s+years?\s+(?:of\s+)?(?:professional\s+|industry\s+|work\s+|total\s+)?experience",
    re.IGNORECASE,
)


def estimate_experience_years(
    text: str,
    sections: dict[str, str] | None = None,
) -> float:
    """
    Estimate WORK experience years only.

    Priority:
    1. Date ranges extracted solely from the 'experience' section
       → overlapping periods merged (no double-counting concurrent jobs)
    2. Date ranges from full text minus any dates found in 'education' section
    3. Explicit 'X years of experience' phrases (work-context filtered)
    """
    # --- 1. Experience section only (best signal) ---
    if sections:
        exp_text = sections.get("experience", "")
        if exp_text:
            intervals = _extract_intervals(exp_text)
            if intervals:
                return _merge_and_sum(intervals)

    # --- 2. Full text minus education dates ---
    edu_intervals: set = set()
    if sections:
        edu_text = sections.get("education", "")
        if edu_text:
            edu_intervals = set(_extract_intervals(edu_text))

    all_intervals = _extract_intervals(text)
    work_intervals = [iv for iv in all_intervals if iv not in edu_intervals]
    if work_intervals:
        return _merge_and_sum(work_intervals)

    # --- 3. Explicit mention (context-filtered, last resort) ---
    matches = _WORK_YEAR_RE.findall(text)
    if matches:
        return float(max(int(m) for m in matches))

    return 0.0



# ---------------------------------------------------------------------------
# Skill extraction — bounded catalog by default, explicit optional enrichment
# ---------------------------------------------------------------------------


def extract_skills_heuristic(text: str) -> list[str]:
    from .market.skill_extractor import extract_skill_mentions

    return sorted({item["skill"] for item in extract_skill_mentions(text)}, key=str.casefold)


def enrich_resume_skills(raw_text: str, skills: list[str]) -> list[str]:
    """Explicit enrichment only adds source-present, non-negated terminology."""
    from .llm_client import extract_skills_llm
    from .market.skill_extractor import _NEGATION, _term_pattern
    from .market.skill_taxonomy import canonical_skill

    enriched = extract_skills_llm(raw_text)
    supported = set(skills)
    for skill in enriched:
        if not isinstance(skill, str) or not skill.strip():
            continue
        for match in _term_pattern(skill).finditer(raw_text):
            prefix = raw_text[max(0, match.start() - 80):match.start()]
            if not _NEGATION.search(prefix):
                supported.add(canonical_skill(skill))
                break
    return sorted(supported, key=str.casefold)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def parse_resume_file(
    file_bytes: bytes,
    filename: str = "",
    use_llm: bool = False,
) -> tuple[str, dict[str, str], list[str], float, dict]:
    """
    Parse a resume file (PDF or DOCX).

    Returns:
        raw_text, sections, skills, experience_years, contact_info
    """
    # Step 1: Text + sections
    if filename.lower().endswith((".tex", ".zip")):
        from .native_tex import compile_project_inspected
        inspected = compile_project_inspected(file_bytes, "tex" if filename.lower().endswith(".tex") else "texzip")
        return inspected.pdf_inspection.parsed
    elif filename.lower().endswith(".docx"):
        raw_text = extract_text_from_docx(file_bytes)
        sections = _heuristic_sections_fuzzy(raw_text)
    else:
        raw_text, sections = extract_text_and_sections_from_pdf(file_bytes)

    # Step 2: Contact info
    contact_info = extract_contact_info(raw_text)

    # Step 3: Catalog mentions first. Optional enrichment cannot remove locally
    # found skills and is constrained to terms actually present in the source.
    skills = extract_skills_heuristic(raw_text)  # default
    if use_llm:
        try:
            skills = enrich_resume_skills(raw_text, skills)
        except Exception:
            pass  # silently use heuristic

    # Step 4: Experience years — pass sections so we only count work dates
    exp_years = estimate_experience_years(raw_text, sections=sections)

    return raw_text, sections, skills, exp_years, contact_info
