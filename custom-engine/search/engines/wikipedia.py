"""Wikipedia engine using the public MediaWiki search API.

Endpoint: ``https://{lang}.wikipedia.org/w/api.php`` with ``list=search``.

Privacy / design notes:
* ``safe_search`` is not applicable: Wikipedia has no safe-search switch, so
  the argument is accepted for contract compatibility and ignored.
* Unlike the HTML-scraping engines, this one deliberately does NOT use
  User-Agent rotation. Wikimedia's User-Agent policy asks API clients to send
  a descriptive, stable User-Agent; generic browser UAs are more likely to be
  throttled. The UA below contains no per-user information.
* The query is never logged.
"""
from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import quote

from bs4 import BeautifulSoup

from search.engines import base
from search.types import Result

NAME = "wikipedia"

_LANG_RE = re.compile(r"^[a-z]{2}$")
_USER_AGENT = "PrivateSearch/1.0 (self-hosted privacy meta-search)"


def _valid_language(language: Any) -> str:
    """Return ``language`` if it is a 2-letter lowercase code, else ``"en"``."""
    if isinstance(language, str) and _LANG_RE.match(language):
        return language
    return "en"


def _strip_html(raw: Any) -> str:
    """Remove HTML tags, unescape entities and normalise whitespace."""
    if not isinstance(raw, str) or not raw:
        return ""
    text = BeautifulSoup(raw, "html.parser").get_text()  # inline <span> highlights: no separator, or "word ." gaps appear
    return base.clean_text(html.unescape(text))


def _article_url(lang: str, title: str) -> str:
    """Build the canonical article URL for ``title`` on the ``lang`` wiki."""
    slug = quote(title.replace(" ", "_"), safe="/:()_,'-")
    return f"https://{lang}.wikipedia.org/wiki/{slug}"


def _extract_hits(payload: Any) -> list[Any]:
    """Return the list of search hits from either formatversion shape."""
    if not isinstance(payload, dict) or "error" in payload:
        return []
    query = payload.get("query")
    if not isinstance(query, dict):
        return []
    hits = query.get("search")
    if isinstance(hits, dict):  # legacy shape: values may be keyed by index
        hits = list(hits.values())
    return hits if isinstance(hits, list) else []


def search(query: str, *, timeout: float = 3.0, safe_search: bool = True,
           language: str = "en") -> list[Result]:
    """Search Wikipedia and return up to ``MAX_RESULTS_PER_ENGINE`` results.

    Args:
        query: Search terms. Empty/blank queries return ``[]`` without any request.
        timeout: Per-request timeout in seconds.
        safe_search: Ignored (not applicable to Wikipedia).
        language: 2-letter wiki language code; invalid values fall back to ``"en"``.

    Returns:
        Results in API rank order; ``[]`` on any failure. Never raises.
    """
    try:
        if not isinstance(query, str) or not query.strip():
            return []
        lang = _valid_language(language)
        resp = base.fetch(
            f"https://{lang}.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "srlimit": base.MAX_RESULTS_PER_ENGINE,
                "utf8": 1,
                "formatversion": 2,
            },
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
        )
        if resp is None:
            return []
        try:
            payload = resp.json()
        except ValueError:
            return []
        results: list[Result] = []
        for hit in _extract_hits(payload):
            if not isinstance(hit, dict):
                continue
            title = hit.get("title")
            if not isinstance(title, str) or not title.strip():
                continue
            results.append(
                {
                    "title": base.clean_text(title),
                    "url": _article_url(lang, title.strip()),
                    "snippet": _strip_html(hit.get("snippet")),
                    "engine": NAME,
                }
            )
            if len(results) >= base.MAX_RESULTS_PER_ENGINE:
                break
        return results
    except Exception:  # noqa: BLE001 - engines must never raise
        return []
