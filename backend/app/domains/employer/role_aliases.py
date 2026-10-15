"""Small reviewed deterministic aliases; retain all non-role qualifier tokens."""

from __future__ import annotations

import re

VERSION = "role-aliases-v1"
GROUPS = (
    ("software engineer", "software developer", "sde"),
    ("backend engineer", "backend developer", "back-end engineer", "back-end developer"),
    ("frontend engineer", "frontend developer", "front-end engineer", "front-end developer"),
)


def variants(role: str) -> list[str]:
    normalized = " ".join(role.lower().split())
    for group in GROUPS:
        for term in group:
            pattern = r"(?<![\w])" + re.escape(term) + r"(?![\w])"
            if re.search(pattern, normalized):
                return list(
                    dict.fromkeys(
                        [normalized, *(re.sub(pattern, alias, normalized) for alias in group)]
                    )
                )
    return [normalized]
