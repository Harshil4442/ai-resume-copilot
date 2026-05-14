import re
from typing import Dict, List, Set

from .security import sanitize_job_description
from .skill_taxonomy import all_search_terms, canonical_skill, skill_category


def _term_pattern(term: str) -> re.Pattern:
    escaped = re.escape(term)
    # Keep symbols like C++, C#, Node.js, CI/CD usable while avoiding substring hits.
    return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)


SEARCH_TERMS = {
    canonical: sorted(set(terms), key=len, reverse=True)
    for canonical, terms in all_search_terms().items()
}


def extract_skills_from_text(text: str) -> tuple[Set[str], List[str]]:
    clean, warnings = sanitize_job_description(text)
    found: Set[str] = set()
    for canonical, terms in SEARCH_TERMS.items():
        for term in terms:
            if _term_pattern(term).search(clean):
                found.add(canonical_skill(canonical))
                break
    return found, warnings


def categorize_skills(skills: List[str]) -> Dict[str, List[str]]:
    categories: Dict[str, List[str]] = {}
    for skill in skills:
        categories.setdefault(skill_category(skill), []).append(skill)
    return {
        category: sorted(set(items), key=str.lower)
        for category, items in sorted(categories.items())
    }

