"""Marginalia Search engine (official JSON API).

Marginalia is an independent, open-source web search engine. Unlike the HTML
scrapers in this package it offers an official API, documented at
https://about.marginalia-search.com/article/api/ :

* ``GET https://api2.marginalia-search.com/search?query=...&count=N``
* the key goes in an ``API-Key`` header;
* the response is ``{"query": ..., "license": ..., "results": [{"url",
  "title", "description"}, ...]}``.

Key: ``MARGINALIA_API_KEY`` from the environment (never hard-coded). Without
it the shared ``public`` key is used, which works but is often rate limited
(HTTP 429 "QPM Limit Exceeded"); a free non-commercial key is available by
emailing the operator. Results from the free key are licensed CC-BY-NC-SA 4.0.

Privacy: no cookies, the query is never logged, HTTP goes only through
``base.fetch``.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from search.engines import base
from search.types import Result

logger = logging.getLogger(__name__)

NAME = "marginalia"
ENDPOINT = "https://api2.marginalia-search.com/search"
PUBLIC_KEY = "public"


def api_key() -> str:
    """The configured API key, or the shared ``public`` key."""
    return os.environ.get("MARGINALIA_API_KEY", "").strip() or PUBLIC_KEY


def _parse(payload: Any) -> list[Result]:
    """Turn the API's JSON into results (tolerates missing or odd fields)."""
    if not isinstance(payload, dict):
        return []
    items = payload.get("results")
    if not isinstance(items, list):
        return []
    out: list[Result] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
            continue
        title = base.clean_text(item.get("title") if isinstance(item.get("title"), str) else "")
        snippet = item.get("description")
        snippet = base.clean_text(snippet if isinstance(snippet, str) else "")
        out.append(Result(title=title or url, url=url, snippet=snippet, engine=NAME))
        if len(out) >= base.MAX_RESULTS_PER_ENGINE:
            break
    return out


def search(query: str, *, timeout: float = 3.0, safe_search: bool = True,
           language: str = "en") -> list[Result]:
    """Search Marginalia and return up to ``MAX_RESULTS_PER_ENGINE`` results.

    Args:
        query: Search text; empty/blank returns ``[]`` without any request.
        timeout: HTTP timeout in seconds.
        safe_search: maps to the documented ``nsfw=1`` ("reduce extreme
            results", marked experimental by Marginalia) vs ``nsfw=0``.
        language: unused; Marginalia's index is mostly English.

    Returns:
        Results in API order; ``[]`` on errors or rate limiting (marked
        unavailable by ``base.fetch``). Never raises.
    """
    try:
        q = (query or "").strip()
        if not q:
            return []
        resp = base.fetch(
            ENDPOINT,
            params={
                "query": q,
                "count": base.MAX_RESULTS_PER_ENGINE,
                "nsfw": 1 if safe_search else 0,
            },
            headers={"API-Key": api_key(), "Accept": "application/json"},
            timeout=timeout,
        )
        if resp is None:
            return []
        try:
            payload = resp.json()
        except ValueError:
            base.mark_unavailable("invalid JSON response")
            return []
        return _parse(payload)
    except Exception as exc:  # noqa: BLE001 - engines must never raise
        logger.warning("marginalia: unexpected %s, skipping", type(exc).__name__)
        return []
