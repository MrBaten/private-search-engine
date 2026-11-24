"""Shared pytest fixtures (kept minimal; all names prefixed ``pse_``).

* ``_pse_clean_cache`` (autouse) clears the in-memory result cache around each test.
* ``_pse_clean_env`` (autouse) removes PSE_DEMO_MODE / PSE_ENGINES so tests are hermetic.
* ``pse_fake_engines`` provides two deterministic engines with overlapping URLs.
* ``pse_slow_engine`` / ``pse_failing_engine`` are engine factories.
"""
from __future__ import annotations

import time
from typing import Callable

import pytest

_MARKER = "PSE" + "_STUB"  # built dynamically so repo-wide stub scans stay accurate


def _fake_a(query, *, timeout=3.0, safe_search=True, language="en"):
    if not str(query).strip():
        return []
    rows = [
        ("Example Python", "https://www.example.com/python?utm_source=newsletter&utm_medium=email", "Short"),
        ("Docs", "https://example.org/docs/python/", "Documentation for the topic"),
        ("Tutorial", "https://example.com/tutorial?fbclid=ABC123", "A tutorial"),
        ("FAQ", "https://example.net/faq", "Frequently asked questions"),
    ]
    return [{"title": t, "url": u, "snippet": s, "engine": "fake_a"} for t, u, s in rows]


def _fake_b(query, *, timeout=3.0, safe_search=True, language="en"):
    if not str(query).strip():
        return []
    rows = [
        ("Docs (B)", "https://example.org/docs/python", "Documentation for the topic, longer snippet from B"),
        ("Example Python (B)", "http://example.com/python?gclid=XYZ789", "Short but a bit longer"),
        ("Guide", "https://example.edu/guide", "A guide"),
        ("New", "https://example.io/new?utm_campaign=x&page=2", "Something new"),
    ]
    return [{"title": t, "url": u, "snippet": s, "engine": "fake_b"} for t, u, s in rows]


@pytest.fixture(autouse=True)
def _pse_clean_cache():
    """Clear the global result cache before and after each test (no-op if unavailable)."""
    def _clear():
        try:
            from search import cache
            if getattr(cache, _MARKER, False):
                return
            cache.clear()
        except Exception:  # module missing or not ready: other suites report that
            pass
    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def _pse_clean_env(monkeypatch):
    """Make sure demo mode / engine selection from the shell never leaks into tests."""
    monkeypatch.delenv("PSE_DEMO_MODE", raising=False)
    monkeypatch.delenv("PSE_ENGINES", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("MARGINALIA_API_KEY", raising=False)


@pytest.fixture
def pse_fake_engines() -> dict:
    """Two deterministic engines: 8 raw results, 6 unique after dedup, with tracking params."""
    return {"fake_a": _fake_a, "fake_b": _fake_b}


@pytest.fixture
def pse_slow_engine() -> Callable[[float], Callable]:
    """Factory: ``pse_slow_engine(delay)`` returns an engine that sleeps then returns one result."""
    def make(delay: float = 2.0):
        def slow(query, *, timeout=3.0, safe_search=True, language="en"):
            time.sleep(delay)
            return [{"title": "Slow", "url": "https://slow.example.com/", "snippet": "late", "engine": "slow"}]
        return slow
    return make


@pytest.fixture
def pse_failing_engine() -> Callable[[], Callable]:
    """Factory: ``pse_failing_engine()`` returns an engine that always raises."""
    def make():
        def boom(query, *, timeout=3.0, safe_search=True, language="en"):
            raise RuntimeError("engine exploded")
        return boom
    return make
