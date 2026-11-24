"""DuckDuckGo engine: scrapes the JavaScript-free HTML endpoint.

Privacy / design notes:
* The query is sent with ``POST`` form data, not in the URL. This is how the
  ``html.duckduckgo.com/html/`` form itself submits, and it keeps the query
  out of any URL that an intermediary or a log line could capture. (GET with
  ``params`` also works on this endpoint, but POST is the safer choice.)
* No cookies, no referrer; HTTP goes exclusively through ``base.fetch``.
* The query is never logged, printed or placed in an exception message.

Live markup could not be verified from the build workspace (no network access
to duckduckgo.com). The parser therefore uses several fallbacks and degrades
to an empty list instead of raising.
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import parse_qs, unquote, urlsplit

from bs4 import BeautifulSoup
from bs4.element import Tag

from search.engines import base
from search.types import Result

logger = logging.getLogger(__name__)

NAME = "duckduckgo"
ENDPOINT = "https://html.duckduckgo.com/html/"

_LANG_RE = re.compile(r"^[a-z]{2}$")

# DuckDuckGo ``kl`` is "<region>-<language>". Only combinations I am
# confident exist are mapped; for any other language ``kl`` is omitted and
# DuckDuckGo falls back to its default (no region/language bias).
_KL_BY_LANGUAGE: dict[str, str] = {
    "en": "us-en",
    "de": "de-de",
    "es": "es-es",
    "fr": "fr-fr",
    "ja": "jp-jp",
    "pt": "br-pt",
}

# Phrases seen on DuckDuckGo's bot-challenge / anomaly page. Used only when
# no result links were found at all.
_BLOCK_MARKERS: tuple[str, ...] = (
    "anomaly",
    "captcha",
    "unfortunately, bots use duckduckgo",
    "select all squares",
    "are you a human",
)


def _normalize_language(language: str) -> str:
    """Return a valid 2-letter lowercase language code, defaulting to ``en``."""
    code = (language or "").strip().lower() if isinstance(language, str) else ""
    return code if _LANG_RE.match(code) else "en"


def _build_form(query: str, safe_search: bool, language: str) -> dict[str, str]:
    """Build the POST form fields for the HTML endpoint."""
    form: dict[str, str] = {"q": query}
    # kp: "1" = strict safe search, "-2" = off ("-1" is the moderate default).
    form["kp"] = "1" if safe_search else "-2"
    kl = _KL_BY_LANGUAGE.get(language)
    if kl:
        form["kl"] = kl
    return form


def _unwrap_url(href: str) -> Optional[str]:
    """Resolve a result href to the real absolute http(s) destination.

    Handles ``//duckduckgo.com/l/?uddg=<encoded>`` redirects and plain
    absolute URLs. Returns ``None`` for ad redirects, internal DDG links and
    anything that is not an absolute http(s) URL.
    """
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = "https:" + href
    try:
        parts = urlsplit(href)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host == "duckduckgo.com" or host.endswith(".duckduckgo.com"):
        if parts.path.startswith("/l/"):
            values = parse_qs(parts.query).get("uddg")
            if not values:
                return None
            href = unquote(values[0])
            try:
                parts = urlsplit(href)
            except ValueError:
                return None
        else:
            # y.js ad clicks, settings, help pages etc. are not results.
            return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return href


def _is_ad(node: Tag) -> bool:
    """True if the container (or its link) is marked as an advertisement."""
    classes = " ".join(node.get("class") or [])
    if "result--ad" in classes or "badge--ad" in classes:
        return True
    return node.select_one(".result--ad, .badge--ad") is not None


def _containers(soup: BeautifulSoup) -> list[Tag]:
    """Find result containers, falling back to the parents of title links."""
    nodes = soup.select("div.result, div.web-result")
    if nodes:
        return nodes
    seen: list[Tag] = []
    for link in soup.select("a.result__a"):
        parent = link.find_parent("div")
        if parent is not None and parent not in seen:
            seen.append(parent)
    return seen


def _parse(html: str) -> list[Result]:
    """Parse a results page into at most ``MAX_RESULTS_PER_ENGINE`` results."""
    soup = BeautifulSoup(html, "html.parser")
    results: list[Result] = []
    for node in _containers(soup):
        if len(results) >= base.MAX_RESULTS_PER_ENGINE:
            break
        if _is_ad(node):
            continue
        link = node.select_one("a.result__a") or node.select_one("h2 a[href]")
        if link is None:
            continue
        url = _unwrap_url(link.get("href", ""))
        title = base.clean_text(link.get_text(" "))
        if not url or not title:
            continue
        snippet_node = node.select_one(".result__snippet")
        snippet = base.clean_text(snippet_node.get_text(" ")) if snippet_node else ""
        results.append({"title": title, "url": url, "snippet": snippet, "engine": NAME})
    return results


def _looks_blocked(html: str) -> bool:
    """Heuristic: does this page look like a captcha / anomaly challenge?"""
    lowered = html.lower()
    return any(marker in lowered for marker in _BLOCK_MARKERS)


def search(query: str, *, timeout: float = 3.0, safe_search: bool = True,
           language: str = "en") -> list[Result]:
    """Search DuckDuckGo's HTML endpoint.

    Args:
        query: User query (never logged).
        timeout: Per-request timeout in seconds.
        safe_search: Strict filtering if True, off if False.
        language: 2-letter language code; invalid values fall back to ``en``.

    Returns:
        Up to ``base.MAX_RESULTS_PER_ENGINE`` organic results in rank order,
        or ``[]`` on empty query, block/captcha, network or parse failure.
    """
    try:
        if not isinstance(query, str) or not query.strip():
            return []
        form = _build_form(query.strip(), safe_search, _normalize_language(language))
        resp = base.fetch(ENDPOINT, data=form, method="POST", timeout=timeout)
        if resp is None:
            return []
        html = resp.text
        if not isinstance(html, str) or not html.strip():
            return []
        results = _parse(html)
        if not results and _looks_blocked(html):
            logger.warning("duckduckgo: challenge page detected, skipping")
            base.mark_unavailable("bot challenge page")
        return results
    except Exception as exc:  # noqa: BLE001 - engines must never raise
        logger.warning("duckduckgo: unexpected %s", type(exc).__name__)
        return []
