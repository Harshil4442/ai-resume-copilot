"""Small reviewed deterministic aliases; retain all non-role qualifier tokens."""

from __future__ import annotations

import re

VERSION = "role-title-intent-v2"
GROUPS = (
    (
        "software engineer",
        "software developer",
        "software engineering",
        "software development engineer",
        "sde",
    ),
    ("backend engineer", "backend developer", "back-end engineer", "back-end developer"),
    ("frontend engineer", "frontend developer", "front-end engineer", "front-end developer"),
    (
        "full stack engineer",
        "full stack developer",
        "full-stack engineer",
        "full-stack developer",
        "fullstack engineer",
        "fullstack developer",
    ),
)
SOFTWARE_SPECIALTIES = (
    *GROUPS[1],
    *GROUPS[2],
    *GROUPS[3],
    "platform engineer",
    "web developer",
    "mobile engineer",
    "ios engineer",
    "android engineer",
    "member of technical staff",
)
_CONFLICTING_SOFTWARE_TITLES = (
    r"\b(?:software(?: development| engineering| engineer)?|engineering|sde|product|program|project) (?:manager|director)\b",
    r"\b(?:manager|director|head|chief) (?:of )?(?:software|engineering|technology|product)\b",
    r"\b(?:gtm|go to market|sales|marketing|revenue) engineer\b",
    r"\bcustomer (?:success|support) engineer\b",
)


def _variants(role: str, *, related: bool) -> list[str]:
    normalized = " ".join(role.lower().split())
    for group in GROUPS:
        for term in group:
            pattern = r"(?<![\w])" + re.escape(term) + r"(?![\w])"
            if re.search(pattern, normalized):
                aliases = (
                    (*group, *SOFTWARE_SPECIALTIES) if related and group is GROUPS[0] else group
                )
                return list(
                    dict.fromkeys(
                        [normalized, *(re.sub(pattern, alias, normalized) for alias in aliases)]
                    )
                )
    return [normalized]


def variants(role: str) -> list[str]:
    """Explicit core synonyms/specialties, preserving every query qualifier."""
    return _variants(role, related=True)


def title_relevance(role: str, title: str) -> int:
    """Require role evidence in the title; description mentions cannot replace it.

    Two means the requested role or its direct synonym, one a reviewed related
    software specialty. Token matching allows reordered words and extra title
    qualifiers; it is not equality with a single exact job title.
    """

    def normalized(value: str) -> str:
        return " ".join(re.findall(r"[\w+#]+", value.casefold()))

    target, actual = normalized(role), normalized(title)
    software = any(
        re.search(r"(?<!\w)" + re.escape(normalized(term)) + r"(?!\w)", target)
        for term in GROUPS[0]
    )
    if software and any(
        re.search(pattern, actual) and not re.search(pattern, target)
        for pattern in _CONFLICTING_SOFTWARE_TITLES
    ):
        return 0
    words = set(actual.split())
    for rank, choices in ((2, _variants(role, related=False)), (1, variants(role))):
        if any((tokens := set(normalized(value).split())) and tokens <= words for value in choices):
            return rank
    return 0
