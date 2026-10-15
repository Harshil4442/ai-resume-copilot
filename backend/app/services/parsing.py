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
                words = page.extract_words(extra_attrs=["size", "fontname"])
                if not words:
                    raise ValueError("no words")

                sizes = sorted(w.get("size", 10) for w in words if w.get("size"))
                median_size = sizes[len(sizes) // 2] if sizes else 10
                header_threshold = median_size * 1.15

                # Group words into lines by rounded y-position
                lines_map: dict[int, list[dict]] = {}
                for w in words:
                    y = round(w.get("top", 0) / 5) * 5
                    lines_map.setdefault(y, []).append(w)

                for y in sorted(lines_map):
                    lw = lines_map[y]
                    line_text = " ".join(w["text"] for w in lw).strip()
                    if not line_text:
                        continue
                    avg_size = sum(w.get("size", 10) for w in lw) / len(lw)
                    is_bold = any("bold" in w.get("fontname", "").lower() for w in lw)
                    is_large = avg_size >= header_threshold
                    is_caps = line_text.isupper() and len(line_text.split()) <= 6
                    is_short = len(line_text.split()) <= 6
                    tagged_lines.append((line_text, (is_large or is_bold or is_caps) and is_short))

            except Exception:
                # Fallback: plain text, use ALL-CAPS heuristic
                for line in (page.extract_text() or "").splitlines():
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
        from .native_tex import compile_project
        pdf, _image = compile_project(file_bytes, "tex" if filename.lower().endswith(".tex") else "texzip")
        raw_text, sections = extract_text_and_sections_from_pdf(pdf)
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
