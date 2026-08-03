import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Any

import httpx

from .catalog import CatalogProduct


RAZORPAY_ORDERS_URL = "https://api.razorpay.com/v1/orders"


def _env_true(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() == "true"


@dataclass(frozen=True)
class RazorpaySettings:
    checkout_requested: bool
    account_approved: bool
    go_live_review_complete: bool
    key_id: str
    key_secret: str
    webhook_secret: str
    webhook_secret_previous: str
    mode: str
    app_env: str

    @classmethod
    def from_env(cls) -> "RazorpaySettings":
        return cls(
            checkout_requested=_env_true("RAZORPAY_CHECKOUT_ENABLED"),
            account_approved=_env_true("RAZORPAY_ACCOUNT_APPROVED"),
            go_live_review_complete=_env_true("PAYMENTS_GO_LIVE_REVIEW_COMPLETE"),
            key_id=(os.getenv("RAZORPAY_KEY_ID") or "").strip(),
            key_secret=(os.getenv("RAZORPAY_KEY_SECRET") or "").strip(),
            webhook_secret=(os.getenv("RAZORPAY_WEBHOOK_SECRET") or "").strip(),
            webhook_secret_previous=(
                os.getenv("RAZORPAY_WEBHOOK_SECRET_PREVIOUS") or ""
            ).strip(),
            mode=(os.getenv("RAZORPAY_MODE") or "live").strip().lower(),
            app_env=(os.getenv("APP_ENV") or "production").strip().lower(),
        )

    @property
    def mode_allowed_for_environment(self) -> bool:
        if self.app_env in {"production", "prod"}:
            return self.mode == "live"
        if self.app_env in {"development", "dev", "local", "test", "staging"}:
            return self.mode == "test"
        return False

    @property
    def key_matches_mode(self) -> bool:
        if self.mode == "live":
            return self.key_id.startswith("rzp_live_")
        if self.mode == "test":
            return self.key_id.startswith("rzp_test_")
        return False

    @property
    def credentials_complete(self) -> bool:
        return bool(
            self.key_id
            and self.key_secret
            and self.webhook_secret
            and len(self.webhook_secret) >= 32
            and self.key_matches_mode
        )

    @property
    def checkout_enabled(self) -> bool:
        return (
            self.checkout_requested
            and self.account_approved
            and self.go_live_review_complete
            and self.credentials_complete
            and self.mode_allowed_for_environment
        )

    @property
    def webhook_ready(self) -> bool:
        # Keep accepting signed callbacks for already-created orders if checkout
        # is intentionally switched off during an incident.
        return bool(
            self.webhook_secret
            and len(self.webhook_secret) >= 32
            and self.mode_allowed_for_environment
        )


class RazorpayProviderError(RuntimeError):
    pass


class RazorpayAdapter:
    def __init__(self, settings: RazorpaySettings):
        self.settings = settings

    def create_order(
        self,
        *,
        product: CatalogProduct,
        local_order_id: str,
        receipt: str,
    ) -> dict[str, Any]:
        if not self.settings.checkout_enabled:
            raise RazorpayProviderError("Razorpay checkout is disabled")
        if len(receipt) > 40:
            raise RazorpayProviderError("Receipt exceeds Razorpay's length limit")

        notes = {
            "hirewiz_order_id": local_order_id,
            "sku": product.sku,
            "billing_country": "IN",
        }
        request_body = {
            "amount": product.amount_minor,
            "currency": product.currency,
            "receipt": receipt,
            "notes": notes,
            "partial_payment": False,
        }
        try:
            response = httpx.post(
                RAZORPAY_ORDERS_URL,
                auth=(self.settings.key_id, self.settings.key_secret),
                json=request_body,
                timeout=20,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            raise RazorpayProviderError(
                f"Razorpay order request failed (status={status_code or 'network'})"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise RazorpayProviderError("Razorpay returned invalid JSON") from exc

        expected = {
            "entity": "order",
            "amount": product.amount_minor,
            "currency": product.currency,
            "receipt": receipt,
            "status": "created",
        }
        if any(data.get(key) != value for key, value in expected.items()):
            raise RazorpayProviderError("Razorpay returned an unexpected order")
        if data.get("partial_payment") not in (False, None):
            raise RazorpayProviderError("Razorpay unexpectedly enabled partial payment")
        provider_order_id = data.get("id")
        if not isinstance(provider_order_id, str) or not provider_order_id.startswith("order_"):
            raise RazorpayProviderError("Razorpay returned an invalid order identifier")

        returned_notes = data.get("notes")
        if returned_notes not in (None, []) and returned_notes != notes:
            raise RazorpayProviderError("Razorpay returned mismatched order metadata")
        return {"provider_order_id": provider_order_id, "notes": notes}

    def verify_checkout_signature(
        self,
        *,
        provider_order_id: str,
        provider_payment_id: str,
        signature: str,
    ) -> bool:
        if not self.settings.key_secret or not signature:
            return False
        message = f"{provider_order_id}|{provider_payment_id}".encode("utf-8")
        expected = hmac.new(
            self.settings.key_secret.encode("utf-8"), message, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def verify_webhook_signature(self, *, raw_body: bytes, signature: str) -> bool:
        if not self.settings.webhook_ready or not signature:
            return False
        secrets = [self.settings.webhook_secret]
        if len(self.settings.webhook_secret_previous) >= 32:
            secrets.append(self.settings.webhook_secret_previous)
        return any(
            hmac.compare_digest(
                hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest(),
                signature,
            )
            for secret in secrets
        )
