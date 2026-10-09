"""Tiny in-process cache: TTL, single-flight per key, serve-stale when the portal is down.

Thread-safe, because FastAPI runs the sync routes in a thread pool. However many threads
ask for the same key at once, the loader runs once and they all share its outcome. If the
load fails with one of `stale_on`, an older entry is served instead of the error, flagged
"STALE", for up to `max_stale_seconds` past the TTL.

A load that failed that way is not tried again for `failure_seconds`. During an outage the
next callers get the stale entry (or the same error) at once, instead of each waiting out its
own doomed attempt, which would hold a worker thread per request for as long as the portal
takes to time out. It is a fail-fast window, not a full circuit breaker.

Bounded, least recently used out first: the keys every request needs (the meter list) stay,
the many per-meter ones come and go.
"""

from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any, Literal, TypeVar

CacheStatus = Literal["HIT", "MISS", "STALE"]
T = TypeVar("T")
_ABSENT: Any = object()

#: How long a failed load is remembered (see the module docstring).
FAILURE_MEMO_SECONDS = 15.0


class TTLCache:
    def __init__(
        self,
        max_stale_seconds: float,
        stale_on: tuple[type[BaseException], ...] = (),
        max_entries: int = 1024,
        failure_seconds: float = FAILURE_MEMO_SECONDS,
    ) -> None:
        self._max_stale = max_stale_seconds
        self._stale_on = stale_on
        self._max_entries = max_entries
        self._failure_seconds = failure_seconds
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._inflight: dict[str, Future[tuple[Any, CacheStatus]]] = {}
        self._failures: dict[str, tuple[float, BaseException]] = {}  # key -> (not before, what went wrong)

    def get_or_load(self, key: str, ttl: float, loader: Callable[[], T]) -> tuple[T, CacheStatus]:
        with self._lock:
            fresh = self._lookup(key, ttl)
            if fresh is not _ABSENT:
                return fresh, "HIT"
            failure = self._recent_failure(key)
            if failure is not None:  # the portal just failed this same load: do not make another caller wait for it
                stale = self._lookup(key, ttl + self._max_stale)
                if stale is not _ABSENT:
                    return stale, "STALE"
                raise copy.copy(failure)
            pending = self._inflight.get(key)
            leader = pending is None
            if pending is None:
                pending = self._inflight[key] = Future()
        if not leader:
            return pending.result()  # waits for the leader and re-raises its failure, if any

        try:
            outcome = self._load(key, ttl, loader)
        except BaseException as exc:
            with self._lock:
                del self._inflight[key]
            pending.set_exception(exc)
            raise
        with self._lock:
            del self._inflight[key]
        pending.set_result(outcome)
        return outcome

    def _load(self, key: str, ttl: float, loader: Callable[[], T]) -> tuple[T, CacheStatus]:
        try:
            value = loader()
        except self._stale_on as exc:
            with self._lock:
                self._remember_failure(key, exc)
                stale = self._lookup(key, ttl + self._max_stale)
            if stale is not _ABSENT:
                return stale, "STALE"
            raise
        with self._lock:
            self._failures.pop(key, None)
            self._store(key, value)
        return value, "MISS"

    # The helpers below expect the caller to hold `self._lock`.

    def _store(self, key: str, value: Any) -> None:
        self._entries[key] = (time.monotonic(), value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def _lookup(self, key: str, max_age: float) -> Any:
        entry = self._entries.get(key)
        if entry is None or time.monotonic() - entry[0] > max_age:
            return _ABSENT
        self._entries.move_to_end(key)  # used lately: the last to be evicted
        return entry[1]

    def _remember_failure(self, key: str, error: BaseException) -> None:
        if self._failure_seconds <= 0:
            return
        self._failures[key] = (time.monotonic() + self._failure_seconds, error)
        while len(self._failures) > self._max_entries:
            del self._failures[next(iter(self._failures))]

    def _recent_failure(self, key: str) -> BaseException | None:
        failure = self._failures.get(key)
        if failure is None:
            return None
        if time.monotonic() >= failure[0]:
            del self._failures[key]
            return None
        return failure[1]
