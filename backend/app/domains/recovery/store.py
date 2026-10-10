"""Authority storage boundary. Production adapter deliberately absent."""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar

T = TypeVar("T")


class GuardDenied(ValueError):
    pass


class GuardUnavailable(RuntimeError):
    pass


class Transaction(Protocol):
    def get(self, namespace: str, key: str) -> dict | None: ...
    def put(self, namespace: str, key: str, value: dict, *, immutable: bool = False) -> None: ...
    def event(self, event_id: str) -> dict | None: ...
    def append(self, event_id: str, kind: str, payload: dict) -> dict: ...
    def head(self) -> tuple[int, str]: ...


class AuthorityStore(Protocol):
    def transact(self, operation: Callable[[Transaction], T]) -> T:
        """Return only after confirmed durable commit; callback performs no IO.

        Adapters must serialize affected records and retain epochs, tombstones,
        claims and events independently of application restoration. Missing or
        inconsistent authority evidence is unavailable, never an empty registry.
        A production adapter also needs independent protected-journal/restore
        and authentication guarantees; the test adapter does not provide them.
        """
        ...


class UnavailableStore:
    def transact(self, operation: Callable[[Transaction], T]) -> T:
        raise GuardUnavailable("Production recovery authority is not implemented")


def production_store() -> AuthorityStore:
    # No environment flag, application SQL, Redis or fixture SQLite fallback.
    return UnavailableStore()
