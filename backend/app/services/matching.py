import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Domain map for LLM pre-filter
# ---------------------------------------------------------------------------

SKILL_DOMAINS: Dict[str, str] = {
    "c": "systems", "c++": "systems", "rust": "systems", "go": "systems", "assembly": "systems",
    "java": "jvm", "kotlin": "jvm", "scala": "jvm", "groovy": "jvm",
    "python": "scripting", "ruby": "scripting", "perl": "scripting", "bash": "scripting",
    "javascript": "web_fe", "typescript": "web_fe", "react": "web_fe", "vue": "web_fe",
    "angular": "web_fe", "svelte": "web_fe", "html": "web_fe", "css": "web_fe", "next.js": "web_fe",
    "node.js": "web_be", "express": "web_be", "fastapi": "web_be", "django": "web_be",
    "flask": "web_be", "spring": "web_be", "rails": "web_be",
    "pytorch": "ml", "tensorflow": "ml", "keras": "ml", "sklearn": "ml",
    "scikit-learn": "ml", "pandas": "ml", "numpy": "ml",
    "machine learning": "ml", "deep learning": "ml", "nlp": "ml", "langchain": "ml",
    "aws": "cloud", "gcp": "cloud", "azure": "cloud",
    "docker": "devops", "kubernetes": "devops", "terraform": "devops",
    "ansible": "devops", "jenkins": "devops", "github actions": "devops", "ci/cd": "devops",
    "postgresql": "database", "mysql": "database", "sqlite": "database",
    "mongodb": "database", "redis": "database", "elasticsearch": "database",
    "spark": "data", "kafka": "data", "airflow": "data", "dbt": "data",
}

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
    da, db_ = SKILL_DOMAINS.get(skill_a), SKILL_DOMAINS.get(skill_b)
    if da is None or db_ is None:
        return True
    if da == db_:
        return True
    return tuple(sorted([da, db_])) in ADJACENT_DOMAINS


MAX_NEW_LLM_CALLS = 12
PARTIAL_THRESHOLD = 0.30


# ---------------------------------------------------------------------------
# Skill confidence map — evidence-based (no LLM needed)
# ---------------------------------------------------------------------------

# Confidence levels
CONFIDENCE_PRODUCTION = 1.00   # Skill mentioned in work experience
CONFIDENCE_PROJECT    = 0.70   # Skill mentioned in projects only
CONFIDENCE_CLAIMED    = 0.30   # Skill only in skills section — not evidenced


def build_skill_confidence_map(
    resume_skills: List[str],
    sections: Dict[str, str],
) -> Dict[str, float]:
    """
    Classify each resume skill by evidence level:
      1.0 → appears in work experience (production use)
      0.7 → appears in projects (demonstrated use)
      0.3 → skills section only (claimed, unverified)
    """
    exp_text  = (sections.get("experience", "") or "").lower()
    proj_text = (sections.get("projects", "")   or "").lower()
    confidence: Dict[str, float] = {}

    for skill in resume_skills:
        s = skill.lower()
        if s in exp_text:
            confidence[s] = CONFIDENCE_PRODUCTION
        elif s in proj_text:
            confidence[s] = CONFIDENCE_PROJECT
        else:
            confidence[s] = CONFIDENCE_CLAIMED

    return confidence


# ---------------------------------------------------------------------------
# DB + LLM coverage lookup
# ---------------------------------------------------------------------------

def _batch_get_coverages(
    resume_skills: List[str],
    jd_skills: List[str],
    db: Optional[Session],
) -> Dict[Tuple[str, str], float]:
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

    rs_set = list({p[0] for p in pairs_to_lookup})
    jd_set = list({p[1] for p in pairs_to_lookup})

    rows = (
        db.query(SkillCoverage)
        .filter(SkillCoverage.skill_from.in_(rs_set), SkillCoverage.skill_to.in_(jd_set))
        .all()
    )
    for row in rows:
        coverage[(row.skill_from, row.skill_to)] = row.weight

    unknown = [p for p in pairs_to_lookup if p not in coverage]
    new_records = []
    llm_calls = 0

    for (rs_skill, jd_skill) in unknown:
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
        if weight > 0.0:
            new_records.append(SkillCoverage(
                skill_from=rs_skill, skill_to=jd_skill, weight=weight, source="llm",
            ))

    if new_records:
        try:
            for rec in new_records:
                db.merge(rec)
            db.commit()
        except Exception:
            db.rollback()

    return coverage


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _to_text(x: Any) -> str:
    if x is None:             return ""
    if isinstance(x, str):    return x
    if isinstance(x, bytes):
        try:    return x.decode("utf-8", errors="ignore")
        except: return ""
    if isinstance(x, dict):                return " ".join(_to_text(v) for v in x.values())
    if isinstance(x, (list, tuple, set)):  return " ".join(_to_text(v) for v in x)
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


SKILL_KEYWORDS = [
    "python", "java", "javascript", "typescript", "c", "c++", "go", "golang",
    "rust", "sql", "react", "next.js", "nextjs", "node", "node.js", "express",
    "fastapi", "django", "flask", "pandas", "numpy", "scikit-learn", "sklearn",
    "tensorflow", "pytorch", "docker", "kubernetes", "k8s", "ci/cd", "git",
    "github actions", "terraform", "gcp", "google cloud", "aws", "azure",
    "postgres", "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
    "machine learning", "deep learning", "nlp", "llm", "rag",
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


def parse_required_vs_preferred(jd_text: str, skills: List[str]) -> Tuple[List[str], List[str]]:
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
# Dimension weights (dynamic based on experience years)
# ---------------------------------------------------------------------------

HOLISTIC_DIMENSION_WEIGHTS = {
    "Experience Level Fit":    0.18,
    "Role Relevance":          0.12,
    "Domain / Industry Fit":   0.10,
    "Project Relevance":       0.10,
    "Achievement Quality":     0.10,
    "Career Trajectory":       0.05,
    "Education Fit":           0.03,
    "Employment Stability":    0.02,
}

SKILL_WEIGHTS = {
    "applied":      0.20,   # skills evidenced in work/projects
    "claimed":      0.05,   # skills only in skills section
    "verification": 0.05,   # bonus for high verification rate
}


def get_dynamic_weights(experience_years: float) -> Tuple[Dict, Dict]:
    """Adjust dimension weights based on seniority level."""
    holistic = dict(HOLISTIC_DIMENSION_WEIGHTS)
    skill_w  = dict(SKILL_WEIGHTS)

    if experience_years < 2:
        # Junior: projects matter more, experience level penalises less
        holistic["Project Relevance"]    = 0.20
        holistic["Experience Level Fit"] = 0.10
        holistic["Role Relevance"]       = 0.08
        skill_w["applied"]               = 0.22
    elif experience_years >= 5:
        # Senior: experience + domain matter most
        holistic["Experience Level Fit"]   = 0.22
        holistic["Domain / Industry Fit"]  = 0.13
        holistic["Project Relevance"]      = 0.05
        holistic["Achievement Quality"]    = 0.12

    return holistic, skill_w


# ---------------------------------------------------------------------------
# Confidence-weighted skill scoring
# ---------------------------------------------------------------------------

def compute_skill_scores(
    resume_skills: List[str],
    jd_skills: List[str],
    coverage_map: Dict[Tuple[str, str], float],
    confidence_map: Dict[str, float],
    jd_text: str,
) -> Tuple[float, float, int, List[str], List[Dict], List[str]]:
    """
    Compute evidence-weighted skill scores.

    Returns:
        applied_score       – 0-100, score from production/project-evidenced skills
        claimed_score       – 0-100, score from claimed-only skills
        verification_rate   – 0-100 integer, % of resume skills with evidence
        full_matches        – skills at coverage 1.0
        partial_matches     – [{skill, coverage, via}]
        true_gaps           – skills with no meaningful coverage
    """
    req_required, req_preferred = parse_required_vs_preferred(jd_text, jd_skills)
    req_set  = set(req_required)

    full_matches:    List[str]  = []
    partial_matches: List[Dict] = []
    true_gaps:       List[str]  = []

    applied_num = applied_den = 0.0
    claimed_num = claimed_den = 0.0

    for jd_skill in jd_skills:
        importance = 1.0 if jd_skill in req_set else 0.5

        # Best coverage + best-covering resume skill
        best_cov  = 0.0
        best_via  = None
        best_conf = 0.0

        for rs_skill in resume_skills:
            cov  = coverage_map.get((rs_skill, jd_skill), 0.0)
            conf = confidence_map.get(rs_skill, CONFIDENCE_CLAIMED)
            if cov > best_cov:
                best_cov  = cov
                best_via  = rs_skill
                best_conf = conf

        if best_cov < PARTIAL_THRESHOLD:
            best_cov = 0.0

        # Bucket into applied vs claimed based on confidence of best matching skill
        if best_conf >= CONFIDENCE_PROJECT:
            applied_den += importance
            applied_num += best_cov * importance
        else:
            claimed_den += importance
            claimed_num += best_cov * best_conf * importance

        # Categorise
        if best_cov == 1.0:
            full_matches.append(jd_skill)
        elif best_cov >= PARTIAL_THRESHOLD:
            partial_matches.append({
                "skill":    jd_skill,
                "coverage": round(best_cov * 100),
                "via":      best_via or "",
            })
        else:
            true_gaps.append(jd_skill)

    applied_score = (applied_num / applied_den * 100) if applied_den else 0.0
    claimed_score = (claimed_num / claimed_den * 100) if claimed_den else 0.0

    # Verification rate: % of resume skills evidenced in work or projects
    evidenced = sum(1 for v in confidence_map.values() if v >= CONFIDENCE_PROJECT)
    total     = max(len(confidence_map), 1)
    verification_rate = round(evidenced / total * 100)

    return (
        round(applied_score, 1),
        round(claimed_score, 1),
        verification_rate,
        full_matches,
        partial_matches,
        true_gaps,
    )


# ---------------------------------------------------------------------------
# Overall score combiner
# ---------------------------------------------------------------------------

def combine_scores(
    applied_score: float,
    claimed_score: float,
    verification_rate: int,
    holistic_dimensions: List[Dict],
    experience_years: float,
) -> float:
    """
    Compute final weighted overall score from skill scores + holistic dimensions.
    """
    holistic_weights, skill_weights = get_dynamic_weights(experience_years)

    # Skill contribution
    verification_score = verification_rate  # already 0-100
    skill_total = (
        applied_score      * skill_weights["applied"]     +
        claimed_score      * skill_weights["claimed"]     +
        verification_score * skill_weights["verification"]
    )

    # Holistic contribution
    holistic_total = 0.0
    for dim in holistic_dimensions:
        w = holistic_weights.get(dim.get("name", ""), 0.0)
        holistic_total += dim.get("score", 50) * w

    # Normalise: weights may not sum exactly to 1.0 due to dynamic adjustment
    total_weight = (
        skill_weights["applied"] + skill_weights["claimed"] + skill_weights["verification"] +
        sum(holistic_weights.values())
    )
    raw = (skill_total + holistic_total) / max(total_weight, 1)
    return round(min(raw, 100.0), 1)


def score_to_grade(score: float) -> str:
    if score >= 90: return "A+"
    if score >= 85: return "A"
    if score >= 80: return "A-"
    if score >= 75: return "B+"
    if score >= 70: return "B"
    if score >= 65: return "B-"
    if score >= 55: return "C+"
    if score >= 45: return "C"
    if score >= 35: return "D"
    return "F"


# ---------------------------------------------------------------------------
# Main entry — kept for backward compat; new flow uses compute_skill_scores
# ---------------------------------------------------------------------------

def compute_match_score(
    resume_skills: Any,
    job_description: Any,
    required_skills: Optional[List[str]] = None,
    db: Optional[Session] = None,
    confidence_map: Optional[Dict[str, float]] = None,
) -> Tuple[float, List[str], List[str], List[Dict], List[str]]:
    """
    Backward-compatible entry point.
    Returns: (score, all_jd_skills, full_matches, partial_matches, true_gaps)
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

    conf_map = confidence_map or {s: CONFIDENCE_CLAIMED for s in rs_norm}
    cov_map  = _batch_get_coverages(rs_norm, req_norm, db)

    applied, claimed, verif, full_m, partial_m, gaps = compute_skill_scores(
        rs_norm, req_norm, cov_map, conf_map, jd_text,
    )
    # Simple combined score for backward compat
    score = applied * 0.70 + claimed * 0.15 + verif * 0.15
    return round(score, 1), req_norm, full_m, partial_m, gaps