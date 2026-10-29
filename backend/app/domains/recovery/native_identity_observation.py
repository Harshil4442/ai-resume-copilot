"""Operation-local observations of immutable, explicitly pinned database identities.

Private bookkeeping may precede a later replacement detection. Authorization rows,
closures and witnesses remain fresh; signing, activation and export use fresh barriers.
Nothing is retained across operations, and a caught refresh failure poisons the scope.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from threading import current_thread
from time import monotonic
from typing import Any, NoReturn

from .gcp_contracts import RegistryPin
from .store import GuardUnavailable

FAILURE = "Native identity observation is unavailable"
_KEYS = ("name", "uid", "type")
MAX_OBSERVATION_SECONDS = 30.0
_current: ContextVar[_ObservationScope | None] = ContextVar("native_identity_observation", default=None)


def _task() -> object | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


@dataclass
class _Dependency:
    rpc: Any
    pin: RegistryPin
    fresh: Callable[[], dict] | None = None
    value: tuple[str, str, str] | None = None


class _ObservationScope:
    def __init__(self, bindings: tuple[tuple[Any, RegistryPin], ...], *, batch=None):
        if type(bindings) is not tuple or len(bindings) != 2:
            raise GuardUnavailable(FAILURE)
        self.dependencies: dict[int, _Dependency] = {}
        for rpc, pin in bindings:
            if (type(pin) is not RegistryPin or id(rpc) in self.dependencies
                    or getattr(rpc, "database_resource", None) != pin.database):
                raise GuardUnavailable(FAILURE)
            self.dependencies[id(rpc)] = _Dependency(rpc, pin)
        if batch is not None:
            from .gcp_rpc import FirestoreIdentityList

            if (type(batch) is not FirestoreIdentityList or tuple(d.rpc for d in self.dependencies.values()) != batch.rpcs):
                raise GuardUnavailable(FAILURE)
        self.batch = batch
        self.thread, self.task = current_thread(), _task()
        self.deadline = monotonic() + MAX_OBSERVATION_SECONDS
        self.token: Token[_ObservationScope | None] | None = None
        self.active = self.initializing = self.valid = self.poisoned = False
        self.callbacks: list[Callable[[], None]] = []

    def reject(self) -> NoReturn:
        self.poisoned, self.valid = True, False
        raise GuardUnavailable(FAILURE)

    def owner(self) -> None:
        if (not self.active or current_thread() is not self.thread or _task() is not self.task
                or _current.get() is not self or self.token is None
                or monotonic() >= self.deadline):
            self.reject()
        # Tokens are bound to the exact Context, unlike ContextVar values copied into
        # another Context on the same thread/task. Rotate without await or I/O.
        try:
            _current.reset(self.token)
        except (ValueError, RuntimeError):
            self.reject()
        self.token = _current.set(self)

    def checked(self, dependency: _Dependency, value: dict) -> tuple[str, str, str]:
        expected = (dependency.pin.database, str(dependency.pin.database_uid), "FIRESTORE_NATIVE")
        if (type(value) is not dict or set(value) != set(_KEYS)
                or any(type(value[key]) is not str for key in _KEYS)
                or tuple(value[key] for key in _KEYS) != expected
                or dependency.rpc.database_resource != dependency.pin.database):
            raise GuardUnavailable(FAILURE)
        return expected

    def refresh(self, *, exiting: bool = False, preserve_error: bool = False) -> None:
        self.owner()
        if self.poisoned and not exiting:
            self.reject()
        self.valid = False
        if self.batch is not None:
            try:
                values = self.batch.identities()
                if type(values) is not dict or set(values) != set(self.dependencies):
                    raise GuardUnavailable(FAILURE)
                for key, dependency in self.dependencies.items():
                    dependency.value = self.checked(dependency, values[key])
                if monotonic() >= self.deadline:
                    raise GuardUnavailable(FAILURE)
            except BaseException:
                self.poisoned = True
                for dependency in self.dependencies.values():
                    dependency.value = None
                raise
            if self.poisoned and not preserve_error:
                self.reject()
            self.valid = True
            return
        failure: Exception | None = None
        for dependency in self.dependencies.values():
            try:
                if monotonic() >= self.deadline:
                    raise GuardUnavailable(FAILURE)
                if dependency.fresh is None:
                    raise GuardUnavailable(FAILURE)
                dependency.value = self.checked(dependency, dependency.fresh())
            except BaseException as exc:
                self.poisoned = True
                dependency.value = None
                if not isinstance(exc, Exception):
                    raise
                if failure is None:
                    failure = exc
        if monotonic() >= self.deadline:
            self.poisoned = True
        if failure is not None:
            raise GuardUnavailable(FAILURE) from failure
        if self.poisoned and not preserve_error:
            self.reject()
        self.valid = True

    def observe(self, rpc: Any, fresh: Callable[[], dict]) -> dict:
        self.owner()
        if self.poisoned:
            self.reject()
        dependency = self.dependencies.get(id(rpc))
        if (dependency is None or dependency.rpc is not rpc
                or rpc.database_resource != dependency.pin.database
                or getattr(fresh, "__self__", None) is not rpc
                or (dependency.fresh is not None and
                    getattr(dependency.fresh, "__func__", None) is not getattr(fresh, "__func__", None))):
            self.reject()
        dependency.fresh = fresh
        if self.initializing and self.batch is None:
            try:
                dependency.value = self.checked(dependency, fresh())
            except Exception:
                self.poisoned, self.valid = True, False
                raise
        elif not self.valid:
            self.refresh()
        if dependency.value is None:
            self.reject()
        return dict(zip(_KEYS, dependency.value, strict=True))

    def quarantine(self) -> None:
        callbacks, self.callbacks = self.callbacks, []
        for callback in reversed(callbacks):
            try:
                callback()
            except BaseException:
                self.poisoned = True

    def enter(self) -> None:
        prior = _current.get()
        if prior is not None:
            prior.reject()
        self.active = self.initializing = True
        self.token = _current.set(self)
        try:
            if self.batch is not None:
                self.refresh()
            for dependency in self.dependencies.values():
                dependency.rpc.identity()
            self.owner()
            if any(d.fresh is None or d.value is None for d in self.dependencies.values()):
                self.reject()
            self.valid = True
        except BaseException:
            self.poisoned = True
            self.quarantine()
            assert self.token is not None
            _current.reset(self.token)
            self.release()
            raise
        finally:
            self.initializing = False

    def exit(self, error: BaseException | None) -> None:
        completed = False
        try:
            self.owner()
            prior_failure = self.poisoned
            if error is not None:
                self.poisoned, self.valid = True, False
            self.refresh(exiting=True, preserve_error=error is not None and not prior_failure)
            completed = True
        finally:
            if not completed:
                self.poisoned, self.valid = True, False
            try:
                if self.poisoned:
                    self.quarantine()
            finally:
                assert self.token is not None
                try:
                    _current.reset(self.token)
                finally:
                    self.release()

    def release(self) -> None:
        self.active = self.valid = False
        self.callbacks.clear()
        self.dependencies.clear()
        self.batch = None
        self.token = None


@contextmanager
def identity_observation_scope(bindings: tuple[tuple[Any, RegistryPin], ...], *, batch=None) -> Iterator[None]:
    scope = _ObservationScope(bindings, batch=batch)
    scope.enter()
    error: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        error = exc
        raise
    finally:
        scope.exit(error)


def observe_identity(rpc: Any, freshcall: Callable[[], dict]) -> dict:
    scope = _current.get()
    return freshcall() if scope is None else scope.observe(rpc, freshcall)


def identity_barrier() -> None:
    scope = _current.get()
    if scope is not None:
        scope.refresh()


def invalidate_identity_observations() -> None:
    scope = _current.get()
    if scope is not None:
        scope.owner()
        scope.valid = False


def quarantine_on_failure(callback: Callable[[], None]) -> None:
    scope = _current.get()
    if scope is not None:
        try:
            scope.owner()
        except GuardUnavailable:
            _discard(callback)
            raise
        if scope.poisoned or not callable(callback) or len(scope.callbacks) >= 32:
            _discard(callback)
            scope.reject()
        scope.callbacks.append(callback)


def _discard(callback: Callable[[], None]) -> None:
    if callable(callback):
        try:
            callback()
        except BaseException:
            pass
