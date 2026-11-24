"""Brave Search engine module (HTML scraping).

Brave Search has no free keyless API, so this module parses the public HTML
results page. Brave's markup is undocumented and changes often, therefore the
parser is layered: a primary parser for the known result containers and a
generic fallback that harvests external links inside ``#results``.

Brave frequently serves a captcha or rate-limit page to scraping clients. In
that case this module deliberately keeps the engine interface and returns an
empty list instead of fabricating or mocking results: the aggregator treats
``[]`` as "engine produced nothing" and relies on the other engines.

Privacy: no cookies are sent (``base.fetch`` rejects them), the query is never
logged, and only ``base.fetch`` is used for HTTP.
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

from search.engines import base
from search.types import Result

logger = logging.getLogger(__name__)

NAME = "brave"
SEARCH_URL = "https://search.brave.com/search"

_LANG_RE = re.compile(r"^[a-z]{2}$")
_AD_TOKENS = frozenset({"ad", "ads", "sponsored"})
_AD_LABELS = frozenset({"ad", "sponsored"})
_BLOCK_MARKERS = ("captcha", "rate limit", "too many requests", "unusual traffic")

_CONTAINER_SELECTOR = "div.snippet[data-type], div[data-type]"
_TITLE_SELECTOR = ".title, .snippet-title"
_SNIPPET_SELECTOR = (
    ".snippet-description, .snippet-content, .description, .generic-snippet .content"
)


def _is_brave_host(host: str) -> bool:
    """Return True for brave.com and any of its subdomains."""
    host = host.lower()
    return host == "brave.com" or host.endswith(".brave.com")


def _external_url(href: Optional[str]) -> Optional[str]:
    """Return ``href`` if it is an absolute http(s) URL outside brave.com."""
    if not href or not href.lower().startswith(("http://", "https://")):
        return None
    try:
        host = urlsplit(href).hostname or ""
    except ValueError:
        return None
    if not host or _is_brave_host(host):
        return None
    return href


def _class_tokens(tag: Tag) -> set[str]:
    """Return the lowercase CSS class tokens of ``tag``."""
    classes = tag.get("class") or []
    if isinstance(classes, str):
        classes = classes.split()
    return {c.lower() for c in classes}


def _is_ad(tag: Tag) -> bool:
    """Heuristically detect a sponsored block (class token, label text)."""
    node: Optional[Tag] = tag
    depth = 0
    while node is not None and depth < 3 and node.name not in ("body", "html", "[document]"):
        if _class_tokens(node) & _AD_TOKENS:
            return True
        node = node.parent if isinstance(node.parent, Tag) else None
        depth += 1
    for el in tag.find_all(True):
        if _class_tokens(el) & _AD_TOKENS:
            return True
        if not el.find(True) and base.clean_text(el.get_text()).lower() in _AD_LABELS:
            return True
    return False


def _text_of(tag: Optional[Tag]) -> str:
    """Whitespace-normalised text of ``tag`` (empty string for None)."""
    return base.clean_text(tag.get_text(" ")) if tag is not None else ""


def _first_external_link(tag: Tag) -> Optional[Tag]:
    """First ``a[href^=http]`` under ``tag`` pointing outside brave.com."""
    for a in tag.select('a[href^="http"]'):
        if _external_url(a.get("href")):
            return a
    return None


def _parse_primary(soup: BeautifulSoup) -> list[Result]:
    """Layer 1: parse ``div[data-type="web"]`` style result containers."""
    out: list[Result] = []
    seen: set[str] = set()
    for box in soup.select(_CONTAINER_SELECTOR):
        if (box.get("data-type") or "").strip().lower() != "web":
            continue
        if _is_ad(box):
            continue
        link = _first_external_link(box)
        if link is None:
            continue
        url = str(link.get("href"))
        if url in seen:
            continue
        title = _text_of(box.select_one(_TITLE_SELECTOR)) or _text_of(link)
        if not title:
            continue
        seen.add(url)
        out.append(
            Result(
                title=title,
                url=url,
                snippet=_text_of(box.select_one(_SNIPPET_SELECTOR)),
                engine=NAME,
            )
        )
    return out


def _parse_fallback(soup: BeautifulSoup) -> list[Result]:
    """Layer 2: external anchors with heading/title text inside ``#results``."""
    root = soup.select_one("#results")
    if root is None:
        return []
    out: list[Result] = []
    seen: set[str] = set()
    for a in root.select('a[href^="http"]'):
        url = _external_url(a.get("href"))
        if not url or url in seen:
            continue
        heading = a.select_one("h1, h2, h3, h4, h5, h6, .title, .snippet-title")
        title = _text_of(heading)
        if not title:
            continue
        if _is_ad(a):
            continue
        snippet = ""
        parent = a.parent if isinstance(a.parent, Tag) else None
        for _ in range(3):
            if parent is None:
                break
            if len([x for x in parent.select('a[href^="http"]') if _external_url(x.get("href"))]) > 1:
                break  # parent spans several results; do not borrow a neighbour's text
            snippet = _text_of(parent.select_one(_SNIPPET_SELECTOR))
            if snippet:
                break
            parent = parent.parent if isinstance(parent.parent, Tag) else None
        seen.add(url)
        out.append(Result(title=title, url=url, snippet=snippet, engine=NAME))
    return out


def _looks_blocked(soup: BeautifulSoup) -> bool:
    """True for a captcha / rate-limit page (markers and no results container)."""
    if soup.select_one("#results") or soup.select_one("div[data-type]"):
        return False
    text = _text_of(soup).lower()
    return any(marker in text for marker in _BLOCK_MARKERS)


def _parse(html: str) -> list[Result]:
    """Parse a Brave results page into at most MAX_RESULTS_PER_ENGINE results."""
    soup = BeautifulSoup(html, "html.parser")
    if _looks_blocked(soup):
        logger.warning("brave: captcha or rate-limit page, skipping")
        return []
    results = _parse_primary(soup) or _parse_fallback(soup)
    return results[: base.MAX_RESULTS_PER_ENGINE]


def search(query: str, *, timeout: float = 3.0, safe_search: bool = True,
           language: str = "en") -> list[Result]:
    """Search Brave and return up to ``MAX_RESULTS_PER_ENGINE`` results.

    Args:
        query: Search text; empty/blank returns ``[]`` without any request.
        timeout: HTTP timeout in seconds.
        safe_search: Maps to Brave's ``safesearch`` parameter (strict / off).
        language: 2-letter code; sent only as ``Accept-Language`` because no
            Brave URL parameter for it is known with confidence.

    Returns:
        Results in page order; ``[]`` when blocked, on errors, or on parse
        failure. Never raises.
    """
    try:
        q = (query or "").strip()
        if not q:
            return []
        lang = language if isinstance(language, str) and _LANG_RE.match(language) else "en"
        params = {
            "q": q,
            "source": "web",
            # Best-effort: Brave may instead read this preference from a cookie,
            # which we deliberately never send.
            "safesearch": "strict" if safe_search else "off",
        }
        resp = base.fetch(
            SEARCH_URL,
            params=params,
            headers={"Accept-Language": f"{lang},{lang};q=0.8,en;q=0.5"},
            timeout=timeout,
        )
        if resp is None:
            return []
        return _parse(resp.text or "")
    except Exception as exc:  # noqa: BLE001 - engines must never raise
        logger.warning("brave: unexpected %s, skipping", type(exc).__name__)
        return []
