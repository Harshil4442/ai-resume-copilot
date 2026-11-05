"""Lightweight provider result/error values; no SQL, sender or native authority.

Validation and receipt custody semantics are shared unchanged with mail funding.
Importing a pure authority census must not configure application ORM or senders.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


class MailBudgetError(RuntimeError):
    def __init__(self, outcome: Literal["unknown", "deferred", "permanent", "known_retryable"], *,
                 committed_result: ProviderResult | None = None):
        super().__init__("provider_mail_" + outcome)
        self.mail_outcome = outcome
        self._committed_result = committed_result

    @property
    def committed_result(self) -> ProviderResult | None:
        """Exact known provider result, only after its outcome commit returns.

        Missing/unknown commits and callback outcomes cannot supply a receipt.
        Existing exceptions and reconstructed historical failures default to None.
        """
        return self._committed_result


@dataclass(frozen=True)
class ProviderResult:
    outcome: Literal["sent", "unknown", "permanent", "known_retryable"]
    receipt_sha256: str
    status_code: int | None = None


def _result(value) -> ProviderResult:
    if (type(value) is not ProviderResult or value.outcome not in {"sent", "unknown", "permanent", "known_retryable"}
            or re.fullmatch(r"[a-f0-9]{64}", value.receipt_sha256) is None
            or value.status_code is not None and (type(value.status_code) is not int or not 100 <= value.status_code <= 599)):
        raise MailBudgetError("unknown")
    if (value.outcome == "sent" and (value.status_code is None or not 200 <= value.status_code < 300)
            or value.outcome == "known_retryable" and value.status_code != 429
            or value.outcome == "permanent" and (value.status_code is None or not 400 <= value.status_code < 500
                or value.status_code in {408, 429})):
        raise MailBudgetError("unknown")
    return value
