import re


SUSPICIOUS_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"disregard\s+(the\s+)?system\s+prompt",
    r"you\s+are\s+now\s+",
    r"developer\s+message",
    r"system\s+message",
    r"return\s+only\s+",
    r"do\s+not\s+follow\s+",
]


def sanitize_job_description(text: str) -> tuple[str, list[str]]:
    """Treat external job text as untrusted data before optional LLM use."""
    warnings = []
    clean = text or ""
    for pattern in SUSPICIOUS_PATTERNS:
        if re.search(pattern, clean, flags=re.IGNORECASE):
            clean = re.sub(pattern, "[removed untrusted instruction]", clean, flags=re.IGNORECASE)
            warnings.append("Removed suspicious instruction-like text from one or more job descriptions.")
    return clean[:12000], sorted(set(warnings))

