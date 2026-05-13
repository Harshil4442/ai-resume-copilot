import io
import re
import datetime
from typing import List, Dict, Tuple, Optional
from functools import lru_cache

import pdfplumber
import spacy
from rapidfuzz import fuzz

try:
    from docx import Document as DocxDocument
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False


@lru_cache(maxsize=1)
def get_nlp():
    try:
        return spacy.load("en_core_web_sm")
    except Exception:
        return spacy.blank("en")


# ---------------------------------------------------------------------------
# Section classification via rapidfuzz (no hardcoded exact matches)
# ---------------------------------------------------------------------------

CANONICAL_SECTIONS: Dict[str, List[str]] = {
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
    best_match, best_score = "other", 0

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

def extract_text_and_sections_from_pdf(file_bytes: bytes) -> Tuple[str, Dict[str, str]]:
    """
    Extract raw text and classify sections from a PDF.
    Uses pdfplumber character-level font-size data to identify section headers,
    then rapidfuzz to classify them — no hardcoded section names required.
    """
    tagged_lines: List[Tuple[str, bool]] = []  # (line_text, is_header_candidate)

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
                lines_map: Dict[int, List[dict]] = {}
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


def _build_sections(tagged_lines: List[Tuple[str, bool]]) -> Tuple[str, Dict[str, str]]:
    raw_parts: List[str] = []
    sections: Dict[str, List[str]] = {"other": []}
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
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _heuristic_sections_fuzzy(text: str) -> Dict[str, str]:
    """Section detection for plain text / DOCX (no font-size data)."""
    sections: Dict[str, List[str]] = {"other": []}
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

def extract_contact_info(text: str) -> Dict[str, Optional[str]]:
    """Extract name, email, phone, LinkedIn, GitHub using regex + spaCy NER."""
    header = text[:2000]

    email = next(iter(re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", header)), None)
    phone_matches = re.findall(r"[\+]?[(]?[0-9]{1,4}[)]?[-\s\.]?[(]?[0-9]{1,3}[)]?[-\s\.]?[0-9]{3,4}[-\s\.]?[0-9]{3,4}", header[:500])
    phone = phone_matches[0].strip() if phone_matches else None

    li = re.search(r"linkedin\.com/in/([a-zA-Z0-9\-]+)", header, re.I)
    linkedin = f"linkedin.com/in/{li.group(1)}" if li else None

    gh = re.search(r"github\.com/([a-zA-Z0-9\-]+)", header, re.I)
    github = f"github.com/{gh.group(1)}" if gh else None

    # Name: spaCy PERSON entity first, then first short line without digits/@
    name = None
    nlp = get_nlp()
    doc = nlp(header)
    for ent in doc.ents:
        if ent.label_ == "PERSON":
            name = ent.text
            break
    if not name:
        for line in text.splitlines()[:5]:
            s = line.strip()
            if s and 1 < len(s.split()) <= 4 and not re.search(r"[@\d]", s):
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
_YEAR_PAT   = r"(20\d{2}|19\d{2})"
_END_PAT    = r"(20\d{2}|19\d{2}|present|current|now)"
_DATE_RANGE = re.compile(
    rf"(?:{_MONTH_PAT}\s+)?{_YEAR_PAT}\s*[-\u2013\u2014/to]+\s*(?:{_MONTH_PAT}\s+)?{_END_PAT}",
    re.IGNORECASE,
)


def _extract_intervals(text: str) -> List[Tuple[int, int]]:
    """Pull (start_year, end_year) pairs from a text block."""
    current_year = datetime.datetime.now().year
    intervals: List[Tuple[int, int]] = []
    for m in _DATE_RANGE.finditer(text):
        start_raw, end_raw = m.group(1), m.group(2)
        try:
            start = int(start_raw)
            end   = current_year if end_raw.lower() in ("present", "current", "now") else int(end_raw)
        except (ValueError, AttributeError):
            continue
        if 1970 <= start <= end <= current_year + 1:
            intervals.append((start, end))
    return intervals


def _merge_and_sum(intervals: List[Tuple[int, int]]) -> float:
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
    return min(float(sum(e - s for s, e in merged)), 40.0)


# Work-context phrases for explicit "X years" fallback
_WORK_YEAR_RE = re.compile(
    r"(\d+)\+?\s+years?\s+(?:of\s+)?(?:professional\s+|industry\s+|work\s+|total\s+)?experience",
    re.IGNORECASE,
)


def estimate_experience_years(
    text: str,
    sections: Optional[Dict[str, str]] = None,
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
# Skill extraction — heuristic fallback (LLM preferred, see llm_client.py)
# ---------------------------------------------------------------------------

SKILL_VOCAB = {
    "python", "java", "javascript", "typescript", "c++", "c#", "go", "rust",
    "swift", "kotlin", "ruby", "php", "scala", "r",
    "react", "vue", "angular", "next.js", "svelte", "html", "css", "tailwind",
    "fastapi", "django", "flask", "node.js", "express", "spring boot",
    "pytorch", "tensorflow", "keras", "scikit-learn", "pandas", "numpy",
    "machine learning", "deep learning", "nlp", "computer vision", "llm",
    "langchain", "rag", "fine-tuning", "hugging face", "openai",
    "aws", "gcp", "azure", "docker", "kubernetes", "terraform", "ci/cd",
    "github actions", "jenkins", "linux", "bash",
    "postgresql", "mysql", "mongodb", "redis", "elasticsearch", "sqlite",
    "dynamodb", "supabase", "neon",
    "git", "rest", "graphql", "grpc", "kafka", "rabbitmq", "spark",
    "sql", "data analysis", "api", "microservices", "agile", "scrum",
}


def extract_skills_heuristic(text: str) -> List[str]:
    text_lower = text.lower()
    return sorted({s for s in SKILL_VOCAB if s in text_lower})


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def parse_resume_file(
    file_bytes: bytes,
    filename: str = "",
    use_llm: bool = True,
) -> Tuple[str, Dict[str, str], List[str], float, Dict]:
    """
    Parse a resume file (PDF or DOCX).

    Returns:
        raw_text, sections, skills, experience_years, contact_info
    """
    # Step 1: Text + sections
    if filename.lower().endswith(".docx"):
        raw_text = extract_text_from_docx(file_bytes)
        sections = _heuristic_sections_fuzzy(raw_text)
    else:
        raw_text, sections = extract_text_and_sections_from_pdf(file_bytes)

    # Step 2: Contact info
    contact_info = extract_contact_info(raw_text)

    # Step 3: Skills — LLM preferred, heuristic fallback
    skills = extract_skills_heuristic(raw_text)  # default
    if use_llm:
        try:
            from .llm_client import extract_skills_llm
            skills = extract_skills_llm(raw_text)
        except Exception:
            pass  # silently use heuristic

    # Step 4: Experience years — pass sections so we only count work dates
    exp_years = estimate_experience_years(raw_text, sections=sections)

    return raw_text, sections, skills, exp_years, contact_info