import re

from .security import sanitize_job_description
from .skill_taxonomy import all_search_terms, canonical_skill, skill_category


def _term_pattern(term: str) -> re.Pattern:
    escaped = re.escape(term)
    # Keep symbols like C++, C#, Node.js, CI/CD usable while avoiding substring hits.
    return re.compile(rf"(?<![\w+#]){escaped}(?![\w+#])", re.IGNORECASE)


SEARCH_TERMS = {
    canonical: sorted(set(terms), key=len, reverse=True)
    for canonical, terms in all_search_terms().items()
}
SKILL_EXTRACTOR_VERSION = "bounded-skills-v2"
_PATTERNS = {
    canonical: [(term, _term_pattern(term)) for term in terms]
    for canonical, terms in SEARCH_TERMS.items()
}
_AMBIGUOUS_TERMS = {"c", "r", "go", "node", "ts", "js", "lambda", "render", "express"}
_TECH_CONTEXT = re.compile(
    r"\b(?:skills?|languages?|programming|developer|development|backend|frontend|"
    r"framework|library|libraries|runtime|stack|code|coding|golang|statistical|analytics)\b",
    re.IGNORECASE,
)
_NEGATION = re.compile(
    r"\b(?:no(?:\s+prior)?|without|lack(?:ing)?|not|never)\s+"
    r"(?:(?:experience|knowledge|proficiency|familiarity|used|using|worked)\s+)?"
    r"(?:(?:in|with|of)\s+)?$",
    re.IGNORECASE,
)


def extract_skill_mentions(text: str) -> list[dict]:
    """Find catalog mentions with exact source offsets, without inferring skills.

    Ambiguous short names require a skill-list or technical sentence context.
    Negated mentions are excluded; unmatched vocabulary remains unknown. This
    operates on the original string so offsets remain usable for review.
    """
    mentions: list[dict] = []
    for canonical, patterns in _PATTERNS.items():
        spans: set[tuple[int, int]] = set()
        for term, pattern in patterns:
            for match in pattern.finditer(text or ""):
                start, end = match.span()
                span = (start, end)
                if span in spans:
                    continue
                line_start = text.rfind("\n", 0, start) + 1
                line_end = text.find("\n", end)
                if line_end < 0:
                    line_end = len(text)
                context = text[line_start:line_end]
                prefix = text[max(line_start, start - 80):start]
                if _NEGATION.search(prefix):
                    continue
                if term.lower() in _AMBIGUOUS_TERMS:
                    # A delimited skills list (Python, R, SQL) is also an
                    # explicit declaration, unlike ordinary prose "go".
                    explicit_list = bool(re.search(r"[,|;/]", context)) and (
                        term.lower() in {"c", "r", "go", "ts", "js"}
                        and (match.group() == term.upper() or term.lower() == "go" and match.group() == "Go")
                    )
                    if not _TECH_CONTEXT.search(context) and not explicit_list:
                        continue
                spans.add(span)
                mentions.append({
                    "skill": canonical_skill(canonical),
                    "start": start,
                    "end": end,
                    "text": text[start:end],
                    "provenance": "catalog_mention",
                })
    return sorted(mentions, key=lambda item: (item["start"], item["end"], item["skill"]))


def extract_skills_from_text(text: str) -> tuple[set[str], list[str]]:
    clean, warnings = sanitize_job_description(text)
    found: set[str] = set()
    found.update(item["skill"] for item in extract_skill_mentions(clean))
    return found, warnings


def categorize_skills(skills: list[str]) -> dict[str, list[str]]:
    categories: dict[str, list[str]] = {}
    for skill in skills:
        categories.setdefault(skill_category(skill), []).append(skill)
    return {
        category: sorted(set(items), key=str.lower)
        for category, items in sorted(categories.items())
    }
