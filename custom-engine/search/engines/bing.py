"""Bing HTML scraping engine.

Scrapes ``https://www.bing.com/search`` and parses organic results. Ads are
skipped, ``/ck/a`` click-tracking redirects are unwrapped to the real
destination, and the query is never logged.
"""
from __future__ import annotations

import base64
import binascii
import logging
import re
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup
from bs4.element import Tag

from search.engines import base
from search.types import Result

logger = logging.getLogger(__name__)

NAME = "bing"
_URL = "https://www.bing.com/search"
_LANG_RE = re.compile(r"^[a-z]{2}$")
_CHALLENGE_MARKERS = (
    "captcha",
    "verify you are human",
    "unusual traffic",
    "are you a robot",
)


def _is_http(url: str) -> bool:
    """Return True if ``url`` is an absolute http(s) URL with a host."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def _is_bing_host(url: str) -> bool:
    """Return True if the URL points at a bing.com host."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host == "bing.com" or host.endswith(".bing.com")


def _decode_u(value: str) -> Optional[str]:
    """Decode the ``u`` parameter of a Bing ``/ck/a`` link, or None."""
    if not value.startswith("a1"):
        return None
    payload = value[2:]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8")
    except (binascii.Error, UnicodeError, ValueError):
        return None
    return decoded if _is_http(decoded) else None


def _unwrap(href: str) -> Optional[str]:
    """Return the real destination for ``href`` or None if unusable."""
    href = (href or "").strip()
    if not _is_http(href):
        return None
    try:
        parts = urlsplit(href)
    except ValueError:
        return None
    if _is_bing_host(href) and parts.path.startswith("/ck/a"):
        values = parse_qs(parts.query).get("u")
        if values:
            return _decode_u(values[0])
        return None
    return href


def _snippet(item: Tag) -> str:
    """Extract the snippet text from an organic result element."""
    for selector in (".b_caption p", "p", ".b_lineclamp2", ".b_algoSlug"):
        node = item.select_one(selector)
        if node is not None:
            text = base.clean_text(node.get_text(" "))
            if text:
                return text
    return ""


def _is_challenge(soup: BeautifulSoup) -> bool:
    """Detect a captcha / bot-challenge page (no results container)."""
    if soup.select_one("#b_results") is not None:
        return False
    text = soup.get_text(" ").lower()
    return any(marker in text for marker in _CHALLENGE_MARKERS)


def _parse(html: str) -> list[Result]:
    """Parse a Bing results page into at most MAX_RESULTS_PER_ENGINE results."""
    soup = BeautifulSoup(html, "html.parser")
    if _is_challenge(soup):
        logger.warning("bing: challenge page, skipping")
        return []
    results: list[Result] = []
    for item in soup.select("#b_results li.b_algo"):
        if "b_ad" in (item.get("class") or []) or item.find_parent(class_="b_ad"):
            continue
        link = item.select_one("h2 a")
        if link is None:
            continue
        url = _unwrap(str(link.get("href", "")))
        title = base.clean_text(link.get_text(" "))
        if not url or not title:
            continue
        results.append(
            {"title": title, "url": url, "snippet": _snippet(item), "engine": NAME}
        )
        if len(results) >= base.MAX_RESULTS_PER_ENGINE:
            break
    return results


def search(query: str, *, timeout: float = 3.0, safe_search: bool = True,
           language: str = "en") -> list[Result]:
    """Search Bing and return organic results.

    Args:
        query: Search text; empty/blank returns ``[]`` without any request.
        timeout: HTTP timeout in seconds.
        safe_search: ``adlt=strict`` when True, ``adlt=off`` otherwise.
        language: 2-letter code, falls back to ``"en"`` if invalid.

    Returns:
        Up to ``base.MAX_RESULTS_PER_ENGINE`` results; ``[]`` on any failure.
    """
    try:
        if not query or not query.strip():
            return []
        lang = language if isinstance(language, str) and _LANG_RE.match(language) else "en"
        params = {
            "q": query,
            "adlt": "strict" if safe_search else "off",
            # setlang is best-effort: Bing may override it from other signals.
            "setlang": lang,
        }
        resp = base.fetch(_URL, params=params, timeout=timeout)
        if resp is None:
            return []
        return _parse(resp.text or "")
    except Exception as exc:  # noqa: BLE001 - engines must never raise
        logger.warning("bing: unexpected error (%s)", type(exc).__name__)
        return []
