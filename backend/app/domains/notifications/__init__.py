"""Notification service exports resolved only when a caller requests them."""
from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .service import (
        DeliverySummary,
        deliver_pending_notifications,
        enqueue_notification,
        lifecycle_email_enabled,
        queue_due_reminder_emails,
    )

__all__ = [
    "DeliverySummary",
    "deliver_pending_notifications",
    "enqueue_notification",
    "lifecycle_email_enabled",
    "queue_due_reminder_emails",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(".service", __name__), name)
    globals()[name] = value
    return value
