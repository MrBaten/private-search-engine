"""Meta-search aggregator: parallel fan-out, dedup, scoring, caching.

Design notes (see PLAN.md section 5.7):

* Engines run in a ``ThreadPoolExecutor`` and are given a hard wall-clock
  budget.  The pool is shut down with ``wait=False`` so a hung engine can
  never delay the response (its thread is abandoned, not joined).
* Results are deduplicated by a normalised URL key, but the URL that is
  returned to the caller is the *original* URL with tracking parameters
  removed, so we never rewrite links more than necessary.
* The query is never logged, printed or placed in an exception message.
  Only engine names and exception *types* are logged.
* ``privacy`` and ``cache`` are imported as modules (not functions) so tests
  can monkeypatch them.
"""
from __future__ import annotations

import copy
import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Any, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit

from search import cache, privacy
from search.engines import base as engines_base
from search.types import EngineFn, RankedResult, Result, SearchResponse

logger = logging.getLogger(__name__)

#: Per-engine time budget in seconds.
DEFAULT_TIMEOUT = 3.0

_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(url: str) -> str:
    """Return a dedup key for ``url`` (not meant to be displayed or fetched).

    Tracking parameters are removed, scheme/host are lowercased, a leading
    ``www.`` is dropped, ``http`` is folded into ``https``, the fragment is
    dropped, a trailing ``/`` on the path is removed (so the root path
    becomes empty), remaining query parameters are sorted and default ports
    are dropped.  Never raises: on a parse error the stripped input is
    returned unchanged.
    """
    try:
        cleaned = privacy.strip_tracking_params(url.strip())
        parts = urlsplit(cleaned)
        host = (parts.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if ":" in host:  # IPv6 literal: urlsplit strips the brackets
            host = f"[{host}]"
        scheme = parts.scheme.lower()
        port = parts.port
        if port is not None and _DEFAULT_PORTS.get(scheme) != port:
            host = f"{host}:{port}"
        # http and https are the same page for dedup purposes.
        if scheme == "http":
            scheme = "https"
        path = parts.path.rstrip("/")
        pairs = sorted(parse_qsl(parts.query, keep_blank_values=True))
        query = urlencode(pairs)
        key = f"{scheme}://{host}{path}"
        if query:
            key += f"?{query}"
        return key
    except Exception:  # noqa: BLE001 - dedup key must never raise
        return url


def _empty_response(elapsed_ms: float) -> SearchResponse:
    return SearchResponse(results=[], engines_ok=[], engines_failed=[],
                          cached=False, elapsed_ms=elapsed_ms)


def _is_http_url(url: Any) -> bool:
    return isinstance(url, str) and url.lower().startswith(("http://", "https://"))


def _call_engine(fn: EngineFn, query: str, timeout: float, safe_search: bool,
                 language: str) -> tuple[list[Result], Optional[str]]:
    """Run one engine in the current worker thread.

    Returns its results plus the "unavailable" reason it (or ``base.fetch``)
    recorded, so a blocked engine that returned ``[]`` is reported as failed
    instead of looking like "no matches".
    """
    engines_base.reset_unavailable()
    res = fn(query, timeout=timeout, safe_search=safe_search, language=language)
    reason = None if res else engines_base.unavailable_reason()
    return res, reason


def _run_engines(
    query: str,
    engines: Mapping[str, EngineFn],
    timeout: float,
    safe_search: bool,
    language: str,
) -> tuple[dict[str, list[Result]], list[str]]:
    """Run all engines in parallel; return (results per ok engine, failed names)."""
    executor = ThreadPoolExecutor(max_workers=len(engines),
                                  thread_name_prefix="pse-engine")
    futures: dict[Future, str] = {}
    try:
        for name, fn in engines.items():
            fut = executor.submit(_call_engine, fn, query, timeout,
                                  safe_search, language)
            futures[fut] = name
        done, not_done = wait(futures, timeout=timeout)
    finally:
        # wait=False: never block on a hung engine; cancel anything queued.
        executor.shutdown(wait=False, cancel_futures=True)

    ok: dict[str, list[Result]] = {}
    failed: list[str] = []
    for fut in not_done:
        fut.cancel()
        failed.append(futures[fut])
    for fut in done:
        name = futures[fut]
        try:
            res, reason = fut.result()
        except Exception as exc:  # noqa: BLE001 - engines shouldn't raise; be defensive
            # Log only the exception type: its message could contain the query.
            logger.warning("%s: engine raised %s", name, type(exc).__name__)
            failed.append(name)
            continue
        if reason:
            # Blocked / rate limited / challenge page: report as unavailable.
            logger.warning("%s: unavailable (%s)", name, reason)
            failed.append(name)
            continue
        ok[name] = list(res) if isinstance(res, (list, tuple)) else []
    return ok, failed


def _merge(per_engine: Mapping[str, list[Result]]) -> list[RankedResult]:
    """Dedup, merge and score results from all successful engines."""
    # key -> {"occ": {engine: (position, result)}, "snippet": str}
    groups: dict[str, dict[str, Any]] = {}
    # Iterate engines by name so ties resolve deterministically.
    for engine in sorted(per_engine):
        for position, item in enumerate(per_engine[engine]):
            try:
                url = item["url"]
            except (KeyError, TypeError):
                continue
            if not _is_http_url(url):
                continue
            key = normalize_url(url)
            group = groups.setdefault(key, {"occ": {}, "snippet": ""})
            # If an engine lists the same page twice keep its best rank only,
            # so a single engine cannot inflate a result's score.
            if engine not in group["occ"]:
                group["occ"][engine] = (position, item)
            snippet = str(item.get("snippet") or "").strip()
            if len(snippet) > len(group["snippet"]):
                group["snippet"] = snippet

    ranked: list[tuple[float, int, str, RankedResult]] = []
    for group in groups.values():
        occ: dict[str, tuple[int, Result]] = group["occ"]
        # Best position wins; ties go to the alphabetically first engine
        # (the iteration order above), which keeps output deterministic.
        best_engine, (best_pos, best_item) = min(
            occ.items(), key=lambda kv: (kv[1][0], kv[0]))
        score = 10 * len(occ) + sum(max(0, 10 - pos) * 0.5 for pos, _ in occ.values())
        url = privacy.strip_tracking_params(best_item["url"])
        result = RankedResult(
            title=str(best_item.get("title") or "").strip(),
            url=url,
            snippet=group["snippet"],
            engine=best_engine,
            engines=sorted(occ),
            score=round(score, 2),
        )
        ranked.append((result["score"], best_pos, url, result))

    # Score desc, then best position asc, then url asc.
    ranked.sort(key=lambda t: (-t[0], t[1], t[2]))
    return [r for _, _, _, r in ranked]


def search_with_meta(
    query: str,
    *,
    engines: Mapping[str, EngineFn] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    safe_search: bool = True,
    language: str = "en",
    use_cache: bool = True,
) -> SearchResponse:
    """Search all engines in parallel and return merged, ranked results.

    Args:
        query: User query. Empty/whitespace returns an empty response and
            calls no engine.
        engines: Name -> engine callable; defaults to the enabled engines.
        timeout: Per-engine wall-clock budget in seconds.
        safe_search: Passed through to engines and part of the cache key.
        language: 2-letter language code, passed through, part of the cache key.
        use_cache: Read from / write to the in-memory cache.

    Returns:
        A ``SearchResponse`` with ``engines_ok`` / ``engines_failed`` sorted.
        Only exceptions and timeouts count as failures; an engine returning
        ``[]`` is ``ok`` with zero results.
    """
    start = time.monotonic()

    def elapsed() -> float:
        return round((time.monotonic() - start) * 1000.0, 2)

    if not isinstance(query, str) or not query.strip():
        return _empty_response(elapsed())

    key = cache.make_key(query, safe_search, language) if use_cache else ""
    if use_cache:
        hit = cache.get(key)
        if hit is not None:
            # Deep copy so callers cannot mutate the cached entry.
            response = copy.deepcopy(hit)
            response["cached"] = True
            response["elapsed_ms"] = elapsed()
            return response

    if engines is None:
        from search.engines import get_enabled_engines  # lazy: keeps import light
        engines = get_enabled_engines()
    if not engines:
        return _empty_response(elapsed())

    ok, failed = _run_engines(query, engines, timeout, safe_search, language)
    results = _merge(ok)
    response = SearchResponse(
        results=results,
        engines_ok=sorted(ok),
        engines_failed=sorted(failed),
        cached=False,
        elapsed_ms=elapsed(),
    )

    # Never cache total failures or empty answers: they are likely transient
    # (rate limits, network) and caching them would pin the outage for the TTL.
    if use_cache and response["engines_ok"] and results:
        cache.set(key, copy.deepcopy(response))
    return response


def search(query: str, **kwargs: Any) -> list[RankedResult]:
    """Convenience wrapper returning only ``search_with_meta(...)["results"]``."""
    return search_with_meta(query, **kwargs)["results"]
