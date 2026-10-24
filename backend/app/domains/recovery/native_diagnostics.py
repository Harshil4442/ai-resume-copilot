"""Finite failure labels only; observation never authorizes or retries native IO."""
from __future__ import annotations

import contextvars
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from .store import GuardUnavailable

_log = logging.getLogger("native_boundary")
_STAGES = {
    "candidate_account": frozenset({"configuration", "composition", "pre_witness", "service", "post_configuration", "post_witness"}),
    "browser_pairing": frozenset({"configuration", "service", "account_composition", "native_operation"}),
    "execution_witness": frozenset({"check", "identity", "closure"}),
    "protected_publication": frozenset({"identity", "root", "closure"}),
    "witness_bytes": frozenset({"live_metadata", "exact_metadata", "bounded_media"}),
    "firestore_rpc": frozenset({"identity"}),
}
_TYPES = frozenset({
    "Exception", "ValueError", "TypeError", "KeyError", "AssertionError", "RuntimeError",
    "TimeoutError", "OSError", "ConnectionError", "ImportError", "ModuleNotFoundError",
    "AttributeError", "ValidationError", "GuardDenied", "GuardUnavailable", "AmbiguousCommit",
    "PermissionDenied", "Forbidden", "Unauthenticated", "Unauthorized", "NotFound",
    "DeadlineExceeded", "Aborted", "FailedPrecondition", "ServiceUnavailable",
    "ResourceExhausted", "TooManyRequests",
    "InternalServerError", "GoogleAPICallError", "RetryError", "ConnectTimeout", "ReadTimeout",
    "SSLError", "TransportError",
})
_GUARDS = {
    "Dedicated Storage SDK metadata suppression is required": "storage_metadata_suppression",
    "A fresh dedicated pinned Storage SDK client is required": "storage_client_pin",
    "Live witness generation changed": "witness_generation",
    "Object metadata lacks the exact bounded byte size": "object_metadata",
    "Exact-generation metadata disagrees": "object_generation",
    "Generation-bound object bytes disagree": "object_bytes",
    "Transaction retry deadline exceeded": "transaction_deadline",
    "Transaction deadline exceeded before Commit": "transaction_commit_deadline",
    "Candidate account lifetime configuration is unavailable": "candidate_configuration",
    "Native browser pairing configuration is unavailable": "pairing_configuration",
}


@dataclass(frozen=True)
class NativeFailureEvent:
    component: str
    stage: str
    exception_type: str
    guard_code: str | None = None

    def payload(self) -> dict[str, str]:
        valid = self.stage in _STAGES.get(self.component, ())
        output = {"event": "native_boundary_failure_v1",
            "component": self.component if valid else "unclassified",
            "stage": self.stage if valid else "unclassified",
            "exception_type": self.exception_type if self.exception_type in _TYPES else "OtherError"}
        if self.guard_code is not None and self.guard_code in _GUARDS.values():
            output["guard_code"] = self.guard_code
        return output


@dataclass
class _FailureScope:
    reported: bool = False


_current: contextvars.ContextVar[_FailureScope | None] = contextvars.ContextVar("native_failure", default=None)


@contextmanager
def native_phase(component: str, stage: str, *, expected: tuple[type[Exception], ...] = ()) -> Iterator[None]:
    """Report the first nested failure; preserve even deliberately expected errors."""
    scope = _current.get()
    token = None
    if scope is None:
        scope = _FailureScope()
        token = _current.set(scope)
    try:
        yield
    except Exception as error:
        if not scope.reported and not isinstance(error, expected):
            scope.reported = True
            try:
                code = (_GUARDS.get(error.args[0]) if type(error) is GuardUnavailable
                    and len(error.args) == 1 and type(error.args[0]) is str else None)
                event = NativeFailureEvent(component, stage, type(error).__name__, code)
                _log.warning("native_boundary_failure_v1", extra={"native_failure": event})
            except Exception:
                pass  # Logging cannot replace the original failure or cause another native call.
        raise
    finally:
        if token is not None:
            _current.reset(token)
