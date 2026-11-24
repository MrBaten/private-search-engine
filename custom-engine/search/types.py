"""Shared data types for the PrivateSearch custom aggregator.

These TypedDicts are the interface contract between engines, the aggregator,
the cache and the web app (see PLAN.md §5.1).
"""
from __future__ import annotations

from typing import Callable, List, TypedDict


class Result(TypedDict):
    """A single result as returned by one search engine module."""

    title: str
    url: str
    snippet: str
    engine: str


class RankedResult(TypedDict):
    """A deduplicated, scored result as returned by the aggregator."""

    title: str
    url: str
    snippet: str
    engine: str
    engines: List[str]
    score: float


class SearchResponse(TypedDict):
    """Full aggregator response including per-engine status metadata."""

    results: List[RankedResult]
    engines_ok: List[str]
    engines_failed: List[str]
    cached: bool
    elapsed_ms: float


#: Signature every engine's ``search`` function satisfies:
#: ``search(query, *, timeout=3.0, safe_search=True, language="en") -> list[Result]``
EngineFn = Callable[..., List[Result]]
