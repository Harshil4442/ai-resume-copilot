"""Bounded, positive-only Razorpay reads; no model or credential-state imports."""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from .purchase_quote import BillingOrderStatusResponse, order_quote

MAX_PAGES = 2
PAGE_SIZE = 100
MAX_RESPONSE_BYTES = 512_000
READ_BUDGET_SECONDS = 20


class ReconciliationResponse(BaseModel):
    resolution: Literal["unresolved", "checkout_ready", "settled"]
    message: str
    order: BillingOrderStatusResponse


class ReconciliationUnavailable(ValueError):
    """Missing, incomplete or conflicting proof never establishes absence."""


@dataclass(frozen=True)
class OriginalOrder:
    public_id: str
    owner_id: int
    provider_mode: str
    provider_key_id: str
    sku: str
    billing_country: str
    quote_json: str

    @classmethod
    def from_order(cls, order: Any) -> OriginalOrder:
        if order.entitlement_kind == "credit_pass":
            from fastapi import HTTPException

            from ..domains.credits.payments import order_quote as unified_order_quote
            from ..domains.credits.pricing import CreditContractError
            try:
                snapshot = unified_order_quote(order).model_dump_json(exclude={"server_time"})
            except CreditContractError:
                raise HTTPException(409, "The original payment terms require reconciliation.") from None
            return cls(order.public_id, order.user_id, order.provider_mode,
                       order.provider_key_id, order.sku, order.billing_country, snapshot)
        return cls(order.public_id, order.user_id, order.provider_mode,
                   order.provider_key_id, order.sku, order.billing_country,
                   order_quote(order).model_dump_json())

    @property
    def receipt(self) -> str:
        return f"hw_{self.public_id}"

    @property
    def quote(self) -> dict:
        return json.loads(self.quote_json)


@dataclass(frozen=True)
class ReconciliationProof:
    order: dict
    payment: dict | None
    refunds: tuple[dict, ...]
    checkout_ready: bool

    def digest(self, original: OriginalOrder) -> str:
        # Persist a digest of selected accounting facts, never raw customer JSON.
        order_keys = ("id", "entity", "amount", "currency", "receipt", "amount_paid",
                      "amount_due", "status", "attempts", "partial_payment", "notes")
        payment_keys = ("id", "entity", "order_id", "amount", "currency", "status", "captured",
                        "amount_refunded", "fee", "tax", "method", "international")
        refund_keys = ("id", "entity", "payment_id", "amount", "currency", "status")
        provider_order = {key: self.order.get(key) for key in order_keys}
        provider_order["notes"] = {key: self.order["notes"].get(key) for key in
                                  ("hirewiz_order_id", "sku", "billing_country", "hirewiz_user_id")}
        projection = {"source": "razorpay_api_get", "original": vars(original),
                      "order": provider_order,
                      "payment": {key: self.payment.get(key) for key in payment_keys} if self.payment else None,
                      "refunds": [{key: refund.get(key) for key in refund_keys} for refund in self.refunds]}
        return hashlib.sha256(json.dumps(projection, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _integer(entity: dict, name: str) -> int:
    value = entity.get(name)
    if type(value) is not int or value < 0:
        raise ReconciliationUnavailable("invalid_accounting_fact")
    return value


def _identifier(entity: dict, name: str, prefix: str) -> str:
    value = entity.get(name)
    if not isinstance(value, str) or not re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9]+", value):
        raise ReconciliationUnavailable("invalid_provider_identifier")
    if len(value) > 120:
        raise ReconciliationUnavailable("invalid_provider_identifier")
    return value


def _collection(data: Any) -> list[dict]:
    if not isinstance(data, dict) or data.get("entity") != "collection":
        raise ReconciliationUnavailable("invalid_collection")
    items = data.get("items")
    if not isinstance(items, list) or len(items) > PAGE_SIZE or _integer(data, "count") != len(items):
        raise ReconciliationUnavailable("incomplete_collection")
    if any(not isinstance(item, dict) for item in items):
        raise ReconciliationUnavailable("invalid_collection")
    return items


def _order_match(entity: dict, original: OriginalOrder) -> None:
    _identifier(entity, "id", "order_")
    quote = original.quote
    if (entity.get("entity") != "order" or entity.get("receipt") != original.receipt
            or _integer(entity, "amount") != quote["amount_minor"]
            or entity.get("currency") != quote["currency"]
            or (entity.get("partial_payment") is not False and entity.get("partial_payment") is not None)):
        raise ReconciliationUnavailable("original_order_mismatch")
    expected = {"hirewiz_order_id": original.public_id, "sku": original.sku,
                "billing_country": original.billing_country}
    notes = entity.get("notes")
    if not isinstance(notes, dict) or any(notes.get(key) != value for key, value in expected.items()):
        raise ReconciliationUnavailable("original_owner_mismatch")
    if "hirewiz_user_id" in notes and notes["hirewiz_user_id"] != str(original.owner_id):
        raise ReconciliationUnavailable("original_owner_mismatch")
    if entity.get("status") not in {"created", "attempted", "paid"}:
        raise ReconciliationUnavailable("unsupported_order_status")
    _integer(entity, "attempts")
    paid, due = _integer(entity, "amount_paid"), _integer(entity, "amount_due")
    expected_paid = quote["amount_minor"] if entity["status"] == "paid" else 0
    if paid != expected_paid or due != quote["amount_minor"] - expected_paid:
        raise ReconciliationUnavailable("order_amounts_mismatch")


def _payment_match(payment: dict, original: OriginalOrder, provider_id: str) -> None:
    _identifier(payment, "id", "pay_")
    quote = original.quote
    if (payment.get("entity") != "payment" or payment.get("order_id") != provider_id
            or _integer(payment, "amount") != quote["amount_minor"]
            or payment.get("currency") != quote["currency"]):
        raise ReconciliationUnavailable("payment_mismatch")


def _provider_json(settings: Any, path: str, params: dict, timeout: float) -> dict:
    # Only fixed GET paths built from validated provider identifiers reach here.
    deadline = time.monotonic() + timeout
    with httpx.stream("GET", "https://api.razorpay.com/v1/" + path,
                      auth=(settings.key_id, settings.key_secret), params=params, timeout=timeout) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            if time.monotonic() > deadline:
                raise ReconciliationUnavailable("read_budget")
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ReconciliationUnavailable("response_limit")
            chunks.append(chunk)
        data = json.loads(b"".join(chunks))
        if not isinstance(data, dict):
            raise ReconciliationUnavailable("invalid_response")
        return data


def fetch_reconciliation(original: OriginalOrder, settings: Any,
                         *, get_json: Callable | None = None) -> ReconciliationProof:
    """At most seven GETs. Empty/capped/failed reads leave the attempt held."""
    if (original.provider_mode != settings.mode or original.provider_key_id != settings.key_id
            or not settings.credentials_complete or not settings.mode_allowed_for_environment):
        raise ReconciliationUnavailable("original_credentials_unavailable")
    deadline = time.monotonic() + READ_BUDGET_SECONDS
    reader = get_json or _provider_json

    def read(path: str, params: dict | None = None) -> dict:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReconciliationUnavailable("read_budget")
        data = reader(settings, path, params or {}, min(5, remaining))
        if time.monotonic() > deadline:
            raise ReconciliationUnavailable("read_budget")
        return data

    def pages(path: str, params: dict) -> list[dict]:
        result = []
        for page in range(MAX_PAGES):
            items = _collection(read(path, params | {"count": PAGE_SIZE, "skip": page * PAGE_SIZE}))
            result.extend(items)
            if len(items) < PAGE_SIZE:
                return result
        raise ReconciliationUnavailable("pagination_limit")

    try:
        # Receipt filtering is documented as contains, not exact equality.
        candidates = [item for item in pages("orders", {"receipt": original.receipt})
                      if item.get("receipt") == original.receipt]
        if len(candidates) != 1:
            raise ReconciliationUnavailable("original_order_not_unique")
        _order_match(candidates[0], original)
        provider_id = candidates[0]["id"]
        provider_order = read("orders/" + provider_id)
        _order_match(provider_order, original)
        if provider_order["id"] != provider_id:
            raise ReconciliationUnavailable("provider_order_mismatch")
        payments = _collection(read("orders/" + provider_id + "/payments"))
        for payment in payments:
            _payment_match(payment, original, provider_id)
        successful = [payment for payment in payments if payment.get("captured") is True
                      and payment.get("status") in {"captured", "refunded"}]
        if provider_order["status"] != "paid":
            if successful or len(payments) == PAGE_SIZE:
                raise ReconciliationUnavailable("incomplete_payment_outcome")
            ready = provider_order["status"] == "created" and provider_order["attempts"] == 0 and not payments
            return ReconciliationProof(provider_order, None, (), ready)
        if len(successful) != 1 or len(payments) == PAGE_SIZE:
            raise ReconciliationUnavailable("successful_payment_not_unique")
        payment_id = _identifier(successful[0], "id", "pay_")
        payment = read("payments/" + payment_id)
        _payment_match(payment, original, provider_id)
        if (payment["id"] != payment_id or payment.get("captured") is not True
                or payment.get("status") not in {"captured", "refunded"}):
            raise ReconciliationUnavailable("capture_not_proven")
        refunded = _integer(payment, "amount_refunded")
        if refunded > original.quote["amount_minor"]:
            raise ReconciliationUnavailable("refund_amount_mismatch")
        refunds = []
        if refunded:
            seen = set()
            for refund in pages("payments/" + payment_id + "/refunds", {}):
                refund_id = _identifier(refund, "id", "rfnd_")
                if refund_id in seen or refund.get("entity") != "refund" or refund.get("payment_id") != payment_id:
                    raise ReconciliationUnavailable("refund_ownership_mismatch")
                seen.add(refund_id)
                if refund.get("currency") != original.quote["currency"] or refund.get("status") != "processed":
                    raise ReconciliationUnavailable("refund_not_final")
                if _integer(refund, "amount") <= 0:
                    raise ReconciliationUnavailable("invalid_refund_amount")
                refunds.append(refund)
            if sum(refund["amount"] for refund in refunds) != refunded:
                raise ReconciliationUnavailable("incomplete_refund_proof")
        elif payment["status"] != "captured":
            raise ReconciliationUnavailable("incomplete_refund_proof")
        return ReconciliationProof(provider_order, payment, tuple(refunds), False)
    except (httpx.HTTPError, TypeError, ValueError, KeyError) as exc:
        raise ReconciliationUnavailable("provider_proof_unavailable") from exc
