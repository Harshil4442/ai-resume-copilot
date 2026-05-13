import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Skill classification for domain pre-filter (avoids LLM calls for obviously
# unrelated pairs like "cooking" vs "kubernetes")
# ---------------------------------------------------------------------------

SKILL_DOMAINS: Dict[str, str] = {
    # Systems / low-level
    "c": "systems", "c++": "systems", "rust": "systems", "go": "systems",
    "assembly": "systems",
    # JVM
    "java": "jvm", "kotlin": "jvm", "scala": "jvm", "groovy": "jvm",
    # Scripting
    "python": "scripting", "ruby": "scripting", "perl": "scripting",
    "bash": "scripting", "shell": "scripting",
    # Web frontend
    "javascript": "web_fe", "typescript": "web_fe", "react": "web_fe",
    "vue": "web_fe", "angular": "web_fe", "svelte": "web_fe",
    "html": "web_fe", "css": "web_fe", "next.js": "web_fe",
    # Web backend
    "node.js": "web_be", "express": "web_be", "fastapi": "web_be",
    "django": "web_be", "flask": "web_be", "spring": "web_be",
    "rails": "web_be", "laravel": "web_be",
    # ML / AI
    "pytorch": "ml", "tensorflow": "ml", "keras": "ml", "sklearn": "ml",
    "scikit-learn": "ml", "pandas": "ml", "numpy": "ml",
    "machine learning": "ml", "deep learning": "ml", "nlp": "ml",
    "langchain": "ml", "llm": "ml", "rag": "ml",
    # Cloud / DevOps
    "aws": "cloud", "gcp": "cloud", "azure": "cloud",
    "docker": "devops", "kubernetes": "devops", "terraform": "devops",
    "ansible": "devops", "jenkins": "devops", "github actions": "devops",
    "ci/cd": "devops",
    # Databases
    "postgresql": "database", "mysql": "database", "sqlite": "database",
    "mongodb": "database", "redis": "database", "elasticsearch": "database",
    "dynamodb": "database", "cassandra": "database",
    # Data engineering
    "spark": "data", "kafka": "data", "airflow": "data",
    "dbt": "data", "hadoop": "data", "rabbitmq": "data",
}

# Domains that are adjacent enough to warrant a coverage check
ADJACENT_DOMAINS = {
    ("systems", "jvm"), ("systems", "scripting"),
    ("jvm", "scripting"), ("jvm", "web_be"),
    ("scripting", "web_be"), ("scripting", "ml"),
    ("web_fe", "web_be"),
    ("ml", "data"), ("ml", "scripting"),
    ("cloud", "devops"),
    ("database", "data"), ("database", "scripting"),
}


def _may_have_coverage(skill_a: str, skill_b: str) -> bool:
    """
    Quick pre-filter: return False only when skills are in completely
    different, non-adjacent domains. Avoids wasting LLM tokens on pairs
    like (python, kubernetes) or (react, spark).
    """
    domain_a = SKILL_DOMAINS.get(skill_a)
    domain_b = SKILL_DOMAINS.get(skill_b)

    # Unknown domain → allow the LLM to decide
    if domain_a is None or domain_b is None:
        return True

    if domain_a == domain_b:
        return True

    pair = tuple(sorted([domain_a, domain_b]))
    return pair in ADJACENT_DOMAINS


# ---------------------------------------------------------------------------
# DB + LLM lookup chain
# ---------------------------------------------------------------------------

MAX_NEW_LLM_CALLS = 12   # cap per match request to keep latency acceptable
PARTIAL_THRESHOLD = 0.30  # below this, treat as a true gap


def _batch_get_coverages(
    resume_skills: List[str],
    jd_skills: List[str],
    db: Optional[Session],
) -> Dict[Tuple[str, str], float]:
    """
    Return coverage weights for all (resume_skill, jd_skill) pairs.
    Flow per pair:
      1. Same skill         → 1.0 (no DB hit)
      2. Neon DB cache      → stored weight
      3. Domain pre-filter  → 0.0 if obviously unrelated
      4. LLM query          → computed weight, stored in DB
    """
    from ..models import SkillCoverage

    coverage: Dict[Tuple[str, str], float] = {}
    pairs_to_lookup: List[Tuple[str, str]] = []

    for rs in resume_skills:
        for jd in jd_skills:
            if rs == jd:
                coverage[(rs, jd)] = 1.0
            else:
                pairs_to_lookup.append((rs, jd))

    if not pairs_to_lookup or db is None:
        return coverage

    # --- Batch DB lookup (one round-trip for all pairs) ---
    rs_set = list({p[0] for p in pairs_to_lookup})
    jd_set = list({p[1] for p in pairs_to_lookup})

    rows = (
        db.query(SkillCoverage)
        .filter(
            SkillCoverage.skill_from.in_(rs_set),
            SkillCoverage.skill_to.in_(jd_set),
        )
        .all()
    )
    for row in rows:
        coverage[(row.skill_from, row.skill_to)] = row.weight

    # --- LLM for remaining unknown pairs ---
    unknown = [p for p in pairs_to_lookup if p not in coverage]
    new_records: List[SkillCoverage] = []
    llm_calls = 0

    for (rs_skill, jd_skill) in unknown:
        # Domain pre-filter
        if not _may_have_coverage(rs_skill, jd_skill):
            coverage[(rs_skill, jd_skill)] = 0.0
            continue

        if llm_calls >= MAX_NEW_LLM_CALLS:
            coverage[(rs_skill, jd_skill)] = 0.0
            continue

        try:
            from .llm_client import get_skill_coverage_llm
            weight = get_skill_coverage_llm(rs_skill, jd_skill)
            llm_calls += 1
        except Exception:
            weight = 0.0

        coverage[(rs_skill, jd_skill)] = weight

        # Only persist non-zero results to save DB space
        if weight > 0.0:
            new_records.append(SkillCoverage(
                skill_from=rs_skill,
                skill_to=jd_skill,
                weight=weight,
                source="llm",
            ))

    # Bulk persist newly discovered coverages
    if new_records:
        try:
            for rec in new_records:
                db.merge(rec)   # merge handles PK conflicts gracefully
            db.commit()
        except Exception:
            db.rollback()

    return coverage


# ---------------------------------------------------------------------------
# Utility helpers (kept from previous version)
# ---------------------------------------------------------------------------

def _to_text(x: Any) -> str:
    if x is None:            return ""
    if isinstance(x, str):   return x
    if isinstance(x, bytes):
        try:    return x.decode("utf-8", errors="ignore")
        except: return ""
    if isinstance(x, dict):               return " ".join(_to_text(v) for v in x.values())
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
# JD skill extraction (heuristic fallback)
# ---------------------------------------------------------------------------

SKILL_KEYWORDS = [
    "python", "java", "javascript", "typescript", "c", "c++", "go", "golang",
    "rust", "sql", "react", "next.js", "nextjs", "node", "node.js", "express",
    "fastapi", "django", "flask", "pandas", "numpy", "scikit-learn", "sklearn",
    "tensorflow", "pytorch", "spacy", "langchain", "docker", "kubernetes", "k8s",
    "ci/cd", "git", "github actions", "terraform", "gcp", "google cloud",
    "aws", "azure", "postgres", "postgresql", "mysql", "mongodb", "redis",
    "elasticsearch", "machine learning", "deep learning", "nlp", "llm", "rag",
    "kafka", "rabbitmq", "graphql", "rest", "grpc", "microservices",
]
_SKILL_RE = re.compile(
    r"(?i)\b(" + "|".join(re.escape(s) for s in SKILL_KEYWORDS) + r")\b"
)

REQUIRED_MARKERS  = ["required", "must have", "must-have", "mandatory", "essential",
                     "requirements", "minimum qualifications"]
PREFERRED_MARKERS = ["preferred", "nice to have", "nice-to-have", "bonus", "plus",
                     "desired", "advantage", "ideally", "a plus"]


def extract_required_skills_from_jd(job_description: Any) -> List[str]:
    text = _to_text(job_description)
    if not text.strip():
        return []
    found = [m.group(1).strip().lower() for m in _SKILL_RE.finditer(text)]
    seen, out = set(), []
    for f in found:
        if f not in seen:
            seen.add(f); out.append(f)
    return out


def parse_required_vs_preferred(
    jd_text: str, skills: List[str]
) -> Tuple[List[str], List[str]]:
    text_lower = jd_text.lower()
    preferred_positions = [
        m.start() for marker in PREFERRED_MARKERS
        for m in re.finditer(re.escape(marker), text_lower)
    ]
    if not preferred_positions:
        return skills, []

    required_positions = [
        m.start() for marker in REQUIRED_MARKERS
        for m in re.finditer(re.escape(marker), text_lower)
    ]

    req, pref = [], []
    for skill in skills:
        positions = [m.start() for m in re.finditer(re.escape(skill), text_lower)]
        if not positions:
            req.append(skill); continue
        pos = positions[0]
        nr = min((abs(pos - p) for p in required_positions),  default=float("inf"))
        np = min((abs(pos - p) for p in preferred_positions), default=float("inf"))
        (pref if np < nr else req).append(skill)
    return req, pref


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------

def compute_match_score(
    resume_skills: Any,
    job_description: Any,
    required_skills: Optional[List[str]] = None,
    db: Optional[Session] = None,
) -> Tuple[float, List[str], List[str], List[Dict], List[str]]:
    """
    Coverage-weighted match score using DB→LLM skill coverage lookup.

    Returns:
        score          – 0–100 float
        all_jd_skills  – every skill extracted from JD
        full_matches   – skills with coverage = 1.0
        partial_matches– list of {skill, coverage (int %), via (str)}
        true_gaps      – skills with coverage < PARTIAL_THRESHOLD
    """
    jd_text  = _to_text(job_description)
    req_all  = required_skills or extract_required_skills_from_jd(job_description)

    if not req_all:
        return 0.0, [], [], [], []

    rs = _normalize_skill_list(resume_skills)
    if not rs:
        return 0.0, req_all, [], [], req_all

    rs_norm  = [s.lower() for s in rs]
    req_norm = [s.lower() for s in req_all]

    req_required, req_preferred = parse_required_vs_preferred(jd_text, req_norm)
    req_set  = set(req_required)
    pref_set = set(req_preferred)

    # --- Batch coverage lookup (DB → LLM) ---
    coverage_map = _batch_get_coverages(rs_norm, req_norm, db)

    # --- Weighted score computation ---
    full_matches:    List[str]  = []
    partial_matches: List[Dict] = []
    true_gaps:       List[str]  = []
    total_weighted = 0.0
    total_weight   = 0.0

    for jd_skill in req_norm:
        importance = 1.0 if jd_skill in req_set else 0.5
        total_weight += importance

        # Best coverage across all resume skills
        best_w   = 0.0
        best_via = None
        for rs_skill in rs_norm:
            w = coverage_map.get((rs_skill, jd_skill), 0.0)
            if w > best_w:
                best_w   = w
                best_via = rs_skill

        # Apply threshold
        effective = best_w if best_w >= PARTIAL_THRESHOLD else 0.0
        total_weighted += effective * importance

        if best_w == 1.0:
            full_matches.append(jd_skill)
        elif best_w >= PARTIAL_THRESHOLD:
            partial_matches.append({
                "skill":    jd_skill,
                "coverage": round(best_w * 100),
                "via":      best_via or "",
            })
        else:
            true_gaps.append(jd_skill)

    score = (total_weighted / max(total_weight, 1)) * 100
    return round(score, 1), req_norm, full_matches, partial_matches, true_gaps