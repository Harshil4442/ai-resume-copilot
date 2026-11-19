"""Finite new-controller diagnostics and typed exact-read retries only."""
from __future__ import annotations

import json
import re
import sys
import time

from infra.gcp import schema27_api_identity_readback as readback

OPERATIONS = frozenset({"capture", "promote", "rollback", "stage", "advance", "observe", "reconcile", "profile", "owner-profile", "refresh",
    "crm_project_read", "crm_project_policy_read", "run_service_read", "run_service_policy_read",
    "run_revision_read", "iam_account_read", "iam_keys_read", "iam_account_policy_read",
    "maintenance_identity_read", "document_binary_read", "run_job_read", "run_execution_read",
    "run_operation_read", "logging_entries_read", "document_routing_read"})
READ_OPERATIONS = frozenset(value for value in OPERATIONS if value.endswith("_read"))
REASONS = frozenset({"IAM_PERMISSION_DENIED", "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "SERVICE_DISABLED",
    "RATE_LIMIT_EXCEEDED", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "BACKEND_ERROR", "INTERNAL",
    "PERMISSION_DENIED", "ACCESS_DENIED", "PROJECT_DELETED", "ORG_RESTRICTION_VIOLATION"})


def status(error):
    try:
        value = getattr(error, "status_code", None)
    except Exception:
        return None
    return value if type(value) is int and 100 <= value <= 599 else None


def provider_reason(error):
    try:
        raw = getattr(error, "content", None)
    except Exception:
        return None
    if not isinstance(raw, (str, bytes)) or len(raw) > 65536:
        return None
    try:
        value = json.loads(raw)
        error_body = value.get("error", {})
        rows = [*error_body.get("errors", []), *error_body.get("details", [])]
        reasons = [row.get("reason") for row in rows if type(row) is dict]
        reasons.append(error_body.get("status"))
        return next((reason for reason in reasons if type(reason) is str and reason in REASONS), None)
    except (ValueError, TypeError, AttributeError):
        return None


class ForwardDenied(readback.rolling.RollingDenied):
    def __init__(self, code, operation, error=None):
        self.code, self.operation = code, operation
        self.exception_class = type(error).__name__ if error is not None else type(self).__name__
        self.http_status = status(error) if error is not None else None
        self.provider_reason = provider_reason(error) if error is not None else None
        super().__init__(code)


def diagnostic(error, operation):
    operation = operation if operation in OPERATIONS else "observe"
    code = "schema27_forward_operation_unavailable"
    name, http, reason = type(error).__name__, status(error), provider_reason(error)
    if type(error) is ForwardDenied:
        code, operation, name, http, reason = (error.code, error.operation, error.exception_class,
                                              error.http_status, error.provider_reason)
        if type(code) is not str or not re.fullmatch(r"schema27_forward_[a-z_]{1,80}", code):
            code = "schema27_forward_operation_unavailable"
    elif isinstance(error, readback.rolling.RollingDenied) and len(error.args) == 1:
        candidate = error.args[0]
        if type(candidate) is str and re.fullmatch(r"(?:schema27_identity_promotion|schema27_mixed_identity)_[a-z_]{1,90}", candidate):
            code = candidate
    elif type(error).__name__ in {"TransitionDenied", "RefreshDenied", "IdentityDenied", "NativeProofDenied", "SourceDenied", "ApplicationDenied"} and len(error.args) == 1:
        candidate = error.args[0]
        if type(candidate) is str and re.fullmatch(r"document_[a-z_]{1,100}", candidate):
            code = candidate
    return {"status": "schema27_forward_refused", "denial_code": code,
        "exception_class": name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name) else "Exception",
        "operation": operation if operation in OPERATIONS else "observe", "http_status": http,
        "provider_reason": reason, "native_approval": False, "replay_authorized": False}


class ReadBudget:
    """One deadline; only a typed 429/502/503/504 can repeat the same callable."""
    def __init__(self, *, seconds=120):
        self.deadline = time.monotonic() + seconds

    def read(self, operation, call, http_error_type=None):
        if operation not in READ_OPERATIONS:
            raise ForwardDenied("schema27_forward_read_route_refused", "observe")
        for attempt in range(3):
            if time.monotonic() >= self.deadline:
                raise ForwardDenied("schema27_forward_read_deadline", operation)
            try:
                result = call()
            except Exception as error:
                native_type = http_error_type
                if native_type is None:
                    sdk = sys.modules.get("apitools.base.py.exceptions")
                    native_type = getattr(sdk, "HttpError", ())
                if not isinstance(error, native_type) or status(error) not in {429, 502, 503, 504}:
                    raise ForwardDenied("schema27_forward_read_failed", operation, error) from None
                delay = 0.1 * (2 ** attempt)
                if attempt == 2 or time.monotonic() + delay >= self.deadline:
                    raise ForwardDenied("schema27_forward_read_transient_exhausted", operation, error) from None
                time.sleep(delay)
            else:
                if time.monotonic() >= self.deadline:
                    raise ForwardDenied("schema27_forward_read_deadline", operation)
                return result
        raise ForwardDenied("schema27_forward_read_deadline", operation)
