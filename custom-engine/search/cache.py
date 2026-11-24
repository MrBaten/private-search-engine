"""In-memory TTL + LRU cache for search results.

Memory only: nothing here ever touches disk, and keys / queries are never
logged or printed.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from typing import Any, Callable


class TTLLRUCache:
    """Thread-safe cache with a per-entry time-to-live and LRU eviction."""

    def __init__(
        self,
        maxsize: int = 100,
        ttl: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create a cache holding at most ``maxsize`` entries for ``ttl`` seconds.

        Raises ValueError if ``maxsize < 1`` or ``ttl <= 0``.
        """
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        if ttl <= 0:
            raise ValueError("ttl must be > 0")
        self._maxsize = maxsize
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        # key -> (stored_at, value); order = least to most recently used.
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def _expired(self, stored_at: float, now: float) -> bool:
        return now - stored_at >= self._ttl

    def _purge_expired(self, now: float) -> None:
        """Remove all expired entries. Caller must hold the lock."""
        stale = [k for k, (ts, _) in self._data.items() if self._expired(ts, now)]
        for k in stale:
            del self._data[k]

    def get(self, key: str) -> Any | None:
        """Return the cached value, or None on miss/expiry.

        An expired entry is removed. A hit moves the entry to the most
        recently used position but does not refresh its TTL.
        """
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            stored_at, value = item
            if self._expired(stored_at, self._clock()):
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: Any) -> None:
        """Store ``value``, resetting its timestamp; evict the LRU entry if full."""
        with self._lock:
            now = self._clock()
            self._purge_expired(now)
            self._data[key] = (now, value)
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        """Remove every entry."""
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        """Number of stored entries (expired ones are purged first)."""
        with self._lock:
            self._purge_expired(self._clock())
            return len(self._data)

    def __contains__(self, key: object) -> bool:
        """True if ``key`` is present and not expired (does not alter LRU order)."""
        with self._lock:
            item = self._data.get(key)  # type: ignore[arg-type]
            if item is None:
                return False
            if self._expired(item[0], self._clock()):
                del self._data[key]  # type: ignore[arg-type]
                return False
            return True


default_cache: TTLLRUCache = TTLLRUCache(maxsize=100, ttl=600.0)


def make_key(query: str, safe_search: bool, language: str) -> str:
    """Return a sha256 hex cache key for a normalised query and its flags.

    Privacy: raw queries are never held as dict keys; only this one-way hash
    is. The cached values do contain results, which is unavoidable for a
    results cache; they live only in RAM and expire after 10 minutes.
    """
    normalised = " ".join(query.casefold().split())
    material = f"{normalised}\x00{int(safe_search)}\x00{language.lower()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def get(key: str) -> Any | None:
    """Look up ``key`` in the default cache."""
    return default_cache.get(key)


def set(key: str, value: Any) -> None:  # noqa: A001 - public API name per plan
    """Store ``value`` under ``key`` in the default cache."""
    default_cache.set(key, value)


def clear() -> None:
    """Empty the default cache."""
    default_cache.clear()
