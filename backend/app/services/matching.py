import re
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Skill alias families for weak-skill detection (no ML model needed)
# ---------------------------------------------------------------------------
SKILL_ALIASES: Dict[str, List[str]] = {
    "machine learning": ["ml", "deep learning", "neural networks", "ai", "artificial intelligence", "tensorflow", "pytorch", "keras"],
    "javascript":       ["js", "node.js", "nodejs", "typescript", "ecmascript"],
    "postgresql":       ["postgres", "sql", "rdbms", "mysql"],
    "kubernetes":       ["k8s", "container orchestration", "helm"],
    "golang":           ["go"],
    "aws":              ["amazon web services", "ec2", "s3", "lambda", "cloudformation"],
    "gcp":              ["google cloud", "bigquery", "cloud run", "firebase"],
    "azure":            ["microsoft azure", "az"],
    "react":            ["reactjs", "react.js", "next.js", "nextjs"],
    "docker":           ["containerization", "containers", "compose"],
    "ci/cd":            ["continuous integration", "continuous deployment", "github actions", "jenkins", "gitlab ci", "circleci"],
    "nlp":              ["natural language processing", "text processing", "llm", "large language models", "transformers", "langchain"],
    "data analysis":    ["data analytics", "business intelligence", "bi", "tableau", "power bi", "pandas", "numpy"],
    "rest":             ["rest api", "restful", "http api", "api design"],
}

# Section markers to distinguish required vs preferred skills in JD
REQUIRED_MARKERS  = ["required", "must have", "must-have", "mandatory", "essential", "requirements", "minimum qualifications"]
PREFERRED_MARKERS = ["preferred", "nice to have", "nice-to-have", "bonus", "plus", "desired", "advantage", "ideally", "a plus"]

# Heuristic fallback skill vocabulary (extended)
SKILL_KEYWORDS = [
    "python", "java", "javascript", "typescript", "c", "c++", "go", "golang", "rust", "sql",
    "react", "next.js", "nextjs", "node", "node.js", "express", "fastapi", "django", "flask",
    "pandas", "numpy", "scikit-learn", "sklearn", "tensorflow", "pytorch", "spacy", "langchain",
    "docker", "kubernetes", "k8s", "ci/cd", "git", "github actions", "terraform",
    "gcp", "google cloud", "aws", "azure",
    "postgres", "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
    "machine learning", "deep learning", "nlp", "llm", "rag",
    "kafka", "rabbitmq", "graphql", "rest", "grpc", "microservices",
]
SKILL_REGEX = re.compile(
    r"(?i)\b(" + "|".join(re.escape(s) for s in SKILL_KEYWORDS) + r")\b"
)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _to_text(x: Any) -> str:
    if x is None:           return ""
    if isinstance(x, str):  return x
    if isinstance(x, bytes):
        try:    return x.decode("utf-8", errors="ignore")
        except: return ""
    if isinstance(x, dict):              return " ".join(_to_text(v) for v in x.values())
    if isinstance(x, (list, tuple, set)): return " ".join(_to_text(v) for v in x)
    return str(x)


def _normalize_skill_list(skills: Any) -> List[str]:
    if skills is None:
        return []
    items = [_to_text(s) for s in skills] if isinstance(skills, (list, tuple, set)) \
            else re.split(r"[,|\n]+", _to_text(skills))
    seen, out = set(), []
    for it in items:
        v = it.strip().lower()
        if v and v not in seen:
            seen.add(v); out.append(v)
    return out


# ---------------------------------------------------------------------------
# JD skill extraction (heuristic fallback when LLM unavailable)
# ---------------------------------------------------------------------------

def extract_required_skills_from_jd(job_description: Any) -> List[str]:
    """Regex-based heuristic fallback for JD skill extraction."""
    text = _to_text(job_description)
    if not text.strip():
        return []
    found = [m.group(1).strip().lower() for m in SKILL_REGEX.finditer(text)]
    seen, out = set(), []
    for f in found:
        if f not in seen:
            seen.add(f); out.append(f)
    return out


# ---------------------------------------------------------------------------
# Required vs Preferred skill distinction
# ---------------------------------------------------------------------------

def parse_required_vs_preferred(
    jd_text: str, skills: List[str]
) -> Tuple[List[str], List[str]]:
    """
    Split JD skills into required and preferred lists based on
    proximity to section markers in the JD text.
    Falls back to treating all skills as required when no preferred section exists.
    """
    text_lower = jd_text.lower()

    preferred_positions = [
        m.start()
        for marker in PREFERRED_MARKERS
        for m in re.finditer(re.escape(marker), text_lower)
    ]
    if not preferred_positions:
        return skills, []  # No preferred section — all required

    required_positions = [
        m.start()
        for marker in REQUIRED_MARKERS
        for m in re.finditer(re.escape(marker), text_lower)
    ]

    required_skills, preferred_skills = [], []
    for skill in skills:
        positions = [m.start() for m in re.finditer(re.escape(skill), text_lower)]
        if not positions:
            required_skills.append(skill)
            continue
        pos = positions[0]
        nearest_req  = min((abs(pos - p) for p in required_positions),  default=float("inf"))
        nearest_pref = min((abs(pos - p) for p in preferred_positions), default=float("inf"))
        (preferred_skills if nearest_pref < nearest_req else required_skills).append(skill)

    return required_skills, preferred_skills


# ---------------------------------------------------------------------------
# Weak skill detection (alias family matching)
# ---------------------------------------------------------------------------

def find_weak_skills(resume_skills: List[str], missing_skills: List[str]) -> List[str]:
    """
    Find missing JD skills for which the candidate has a related/alias skill.
    E.g. JD needs 'machine learning', resume has 'pytorch' → weak match.
    """
    resume_set = set(resume_skills)
    weak = []
    for missing in missing_skills:
        for canonical, aliases in SKILL_ALIASES.items():
            family = {canonical} | set(aliases)
            if missing in family and family & resume_set:
                weak.append(missing)
                break
    return sorted(set(weak))


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------

def compute_match_score(
    resume_skills: Any,
    job_description: Any,
    required_skills: Optional[List[str]] = None,
) -> Tuple[float, List[str], List[str], List[str]]:
    """
    Compute match score between resume skills and a job description.

    Returns:
        (score 0-100, all_jd_skills, missing_skills, weak_skills)

    Scoring logic:
        - If JD has required/preferred sections: 70% weight on required, 30% preferred
        - Otherwise: pure overlap on all skills
    """
    jd_text = _to_text(job_description)
    req_all = required_skills or extract_required_skills_from_jd(job_description)

    if not req_all:
        return 0.0, [], [], []

    rs     = _normalize_skill_list(resume_skills)
    rs_set = set(rs)

    req_required, req_preferred = parse_required_vs_preferred(jd_text, req_all)
    req_set  = set(req_required)
    pref_set = set(req_preferred)

    matched_req  = req_set  & rs_set
    matched_pref = pref_set & rs_set
    missing      = sorted((req_set | pref_set) - rs_set)

    req_score  = len(matched_req)  / max(len(req_set),  1)
    pref_score = len(matched_pref) / max(len(pref_set), 1) if pref_set else 0.0

    score = ((0.7 * req_score + 0.3 * pref_score) if req_preferred else req_score) * 100
    weak  = find_weak_skills(rs, missing)

    return round(score, 2), req_all, missing, weak