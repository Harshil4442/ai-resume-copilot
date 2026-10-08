"""Conservative disclosure minimization at the external-model boundary.

Original files and candidate records remain untouched. Structured sensitive
keys and explicit labeled declarations are omitted from model input. This is
not a general-purpose anonymizer; callers must still select relevant facts.
"""
from __future__ import annotations

import json
import re
from typing import Any

REDACTED = '[sensitive information removed]'
PROMPT_PRIVACY_VERSION = 'explicit-sensitive-fields-v1'
_EMAIL = re.compile(r'\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b', re.IGNORECASE)
_INTERNATIONAL_PHONE = re.compile(r'(?<!\w)\+\d[\d ()-]{6,}\d(?!\w)')
_LABELS = (
    r'password|passwd|access[ _-]?token|refresh[ _-]?token|session[ _-]?(?:token|cookie)|'
    r'api[ _-]?key|secret|ssn|social security(?: number)?|aadhaar|aadhar|'
    r'passport(?: number)?|pan(?: number| card)|government[ _-]?id|national[ _-]?id|'
    r'date of birth|dob|age|gender|sex|race|ethnicity|religion|caste|marital status|'
    r'citizenship|nationality|visa[ _-]?status|work[ _-]?authorization|'
    r'disability(?: status)?|medical history|health information|criminal history|'
    r'sexual orientation|consent(?: decision| status)?|phone(?: number)?|mobile(?: number)?'
)
_SENSITIVE_KEY = re.compile(rf'^(?:{_LABELS})$', re.IGNORECASE)
_JSON_LABELED = re.compile(rf'((?:"|\b)(?:{_LABELS})(?:"|\b)\s*[:=]\s*)("(?:\\.|[^"\\])*"|[^\n,;}}]+)', re.IGNORECASE)
_PLAIN_LABELED = re.compile(rf'\b(?:{_LABELS})\s*(?::|=|\bis\b)\s*[^\n;|]+', re.IGNORECASE)
_GOVERNMENT_NUMBER = re.compile(r'\b(?:\d{3}-\d{2}-\d{4}|\d{4}[ -]\d{4}[ -]\d{4})\b')


def redact_structured(value: Any) -> Any:
    if isinstance(value, dict):
        def normalized_key(key):
            return re.sub(r'([a-z])([A-Z])', r'\1 \2', str(key)).replace('_', ' ').replace('-', ' ')
        return {key: redact_structured(item) for key, item in value.items() if not _SENSITIVE_KEY.fullmatch(normalized_key(key))}
    if isinstance(value, list):
        return [redact_structured(item) for item in value]
    if isinstance(value, str):
        return redact_prompt_text(value)
    return value


def redact_prompt_text(text: str) -> str:
    # Preserve a JSON document's structure when possible. Narrative prompt
    # blocks fall through to local explicit-label and identifier redaction.
    clean = text or ''
    if clean.lstrip().startswith(('{', '[')):
        try:
            parsed = json.loads(clean)
        except (json.JSONDecodeError, ValueError):
            pass
        else:
            return json.dumps(redact_structured(parsed), ensure_ascii=False)
    clean = _JSON_LABELED.sub(lambda match: match.group(1) + json.dumps(REDACTED), clean)
    clean = _PLAIN_LABELED.sub(REDACTED, clean)
    clean = _EMAIL.sub(REDACTED, clean)
    clean = _INTERNATIONAL_PHONE.sub(REDACTED, clean)
    clean = _GOVERNMENT_NUMBER.sub(REDACTED, clean)
    return clean


def redact_messages(messages: list[dict]) -> list[dict]:
    return [{**message, 'content': redact_prompt_text(str(message.get('content', '')))} for message in messages]
