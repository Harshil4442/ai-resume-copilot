# backend/app/services/matching.py

import os
import re
from functools import lru_cache
from typing import Any, Iterable, List, Optional, Tuple

USE_SENTENCE_TRANSFORMER = os.getenv("USE_SENTENCE_TRANSFORMER", "0") == "1"
SENTENCE_TRANSFORMER_MODEL = os.getenv(
    "SENTENCE_TRANSFORMER_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)

# A practical starter list (add more as needed)
SKILL_KEYWORDS = [
    # languages
    "python", "java", "javascript", "typescript", "c", "c++", "go", "golang", "rust", "sql",
    # web
    "react", "next.js", "nextjs", "node", "node.js", "express", "fastapi", "django", "flask",
    # data/ml
    "pandas", "numpy", "scikit-learn", "sklearn", "tensorflow", "pytorch", "spacy",
    # devops/cloud
    "docker", "kubernetes", "k8s", "ci/cd", "git", "github actions",
    "gcp", "google cloud", "aws", "azure",
    # db
    "postgres", "postgresql", "mysql", "mongodb", "redis",
]

# compile once; word-ish boundaries so we don't match inside other words
SKILL_REGEX = re.compile(
    r"(?i)\b(" + "|".join(re.escape(s) for s in SKILL_KEYWORDS) + r")\b"
)


def _to_text(x: Any) -> str:
    """
    Normalize any input (str/list/dict/None/other) into a safe string.
    This prevents errors like: 'list' object has no attribute 'lower'.
    """
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, bytes):
        try:
            return x.decode("utf-8", errors="ignore")
        except Exception:
            return ""
    if isinstance(x, dict):
        # join values
        return " ".join(_to_text(v) for v in x.values())
    if isinstance(x, (list, tuple, set)):
        return " ".join(_to_text(v) for v in x)
    return str(x)


def _normalize_skill_list(skills: Any) -> List[str]:
    """
    Accepts skills in many shapes (list[str], list[any], json-string-ish, comma string)
    and returns unique, lowercase skills.
    """
    if skills is None:
        return []

    # If it's already a list-like: flatten and stringify
    if isinstance(skills, (list, tuple, set)):
        items = []
        for s in skills:
            if isinstance(s, (list, tuple, set)):
                items.extend([_to_text(x) for x in s])
            else:
                items.append(_to_text(s))
    else:
        # string-ish: split by commas/newlines
        text = _to_text(skills)
        items = re.split(r"[,|\n]+", text)

    cleaned = []
    for it in items:
        v = it.strip().lower()
        if not v:
            continue
        cleaned.append(v)

    # unique while preserving order
    seen = set()
    out = []
    for v in cleaned:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def extract_required_skills_from_jd(job_description: Any) -> List[str]:
    """
    Extract skills from a job description (robust against non-string inputs).
    """
    text = _to_text(job_description)
    if not text.strip():
        return []

    found = [m.group(1).strip().lower() for m in SKILL_REGEX.finditer(text)]
    # unique preserve order
    seen = set()
    out = []
    for f in found:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


@lru_cache(maxsize=1)
def _get_sentence_transformer():
    """
    Lazy-load only if enabled. If it fails (rate limit / memory),
    we return None and the scorer falls back to skill overlap.
    """
    if not USE_SENTENCE_TRANSFORMER:
        return None

    try:
        from sentence_transformers import SentenceTransformer  # heavy import
        return SentenceTransformer(SENTENCE_TRANSFORMER_MODEL)
    except Exception:
        return None


def compute_match_score(
    resume_skills: Any,
    job_description: Any,
    required_skills: Optional[List[str]] = None,
) -> Tuple[float, List[str], List[str]]:
    """
    Returns: (score_0_to_100, required_skills, missing_skills)
    - Default: skill-overlap scoring (fast + stable for free tiers)
    - Optional: adds semantic similarity if USE_SENTENCE_TRANSFORMER=1 and model loads
    """
    req = required_skills or extract_required_skills_from_jd(job_description)
    req_set = set(req)

    rs = _normalize_skill_list(resume_skills)
    rs_set = set(rs)

    if not req:
        # If JD has no recognizable skills, return a safe neutral score
        return 0.0, [], []

    matched = sorted(req_set.intersection(rs_set))
    missing = sorted(req_set.difference(rs_set))

    overlap = len(matched) / max(len(req_set), 1)  # 0..1

    model = _get_sentence_transformer()
    if model is None:
        return round(overlap * 100.0, 2), req, missing

    # Semantic boost (best-effort). If anything fails, fall back to overlap.
    try:
        import numpy as np

        jd_text = _to_text(job_description)
        resume_text = " ".join(rs)  # lightweight summary

        emb = model.encode([resume_text, jd_text], normalize_embeddings=True)
        sim = float(np.dot(emb[0], emb[1]))  # cosine for normalized embeddings

        # Weighted blend: mostly skills (stable), small semantic boost
        score = (0.8 * overlap) + (0.2 * max(0.0, min(1.0, sim)))
        return round(score * 100.0, 2), req, missing
    except Exception:
        return round(overlap * 100.0, 2), req, missing