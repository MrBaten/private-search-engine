"""Brave Search engine module (HTML scraping).

Brave Search has no free keyless API, so this module parses the public HTML
results page. Brave's markup is undocumented and changes often, therefore the
parser is layered:

0. Embedded page data (current layout, verified against live Brave results in
   Nov 2026). Result links in the markup are opaque ``/go/v1/<token>``
   redirects, but the page embeds its results as a JavaScript object literal
   (``web:{type:"search",...,results:[{title:..,url:..,description:..,
   meta_url:{scheme,netloc,hostname,favicon,path}},...]}``). The destination
   is rebuilt from ``meta_url``: scheme + hostname + the breadcrumb ``path``
   (``"› python  › python_intro.asp"`` -> ``/python/python_intro.asp``).
   Breadcrumbs that are human-readable labels rather than URL path segments
   (e.g. ``"› google for education"``) cannot be turned into a real URL, so
   those results are dropped instead of guessed.
1. Result containers (``div[data-type="web"]``) with direct external links.
2. A generic fallback that harvests external links inside ``#results``.

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


# --- Layer 0: embedded results data -------------------------------------

_DATA_MARKER = 'web:{type:"search"'
_JS_STR = r'"((?:[^"\\]|\\.)*)"'
_TITLE_RE = re.compile(r"(?:^\{|,)title:" + _JS_STR)
_URL_RE = re.compile(r",url:" + _JS_STR)
_DESC_RE = re.compile(r",description:" + _JS_STR)
_META_URL_RE = re.compile(
    r"meta_url:\{scheme:" + _JS_STR + r",netloc:" + _JS_STR + r",hostname:" + _JS_STR
    + r",favicon:(?:" + _JS_STR + r"|[\w$]+),path:" + _JS_STR + r"\}"
)
# One URL path segment: unreserved / sub-delims / pct-encoded / ":" "@".
_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._~%!$&'()*+,;=:@-]+$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")


def _js_unescape(raw: str) -> str:
    """Decode a JavaScript double-quoted string body (\\uXXXX, \\", \\\\ ...)."""
    import json

    try:
        return json.loads('"' + raw + '"')
    except ValueError:
        return raw


def _split_js_objects(text: str, start: int) -> list[str]:
    """Split the top-level ``{...}`` objects of the JS array starting at ``start``.

    ``start`` points just after the opening ``[``. String literals are skipped so
    braces inside titles or descriptions do not confuse the depth count.
    """
    objs: list[str] = []
    depth = 0
    in_str = False
    esc = False
    obj_start = start
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            if depth == 0 and c == "{":
                obj_start = i
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0 and c == "}":
                objs.append(text[obj_start:i + 1])
            if depth < 0:
                break
    return objs


def _rebuild_url(obj: str) -> Optional[str]:
    """Destination URL for one embedded result, or None if it can't be trusted."""
    m = _URL_RE.search(obj)
    if m:
        direct = _external_url(_js_unescape(m.group(1)))
        if direct:
            return direct
    meta = _META_URL_RE.search(obj)
    if not meta:
        return None
    scheme = _js_unescape(meta.group(1)).lower()
    host = _js_unescape(meta.group(3)).lower()
    path_label = _js_unescape(meta.group(5))
    if scheme not in ("http", "https") or not host or not _HOST_RE.match(host):
        return None
    if _is_brave_host(host):
        return None
    segments = [seg.strip() for seg in path_label.split("\u203a")]
    segments = [seg for seg in segments if seg]
    if any(not _PATH_SEGMENT_RE.match(seg) for seg in segments):
        # Human-readable breadcrumb ("google for education"), not a real path.
        return None
    return f"{scheme}://{host}/" + "/".join(segments)


def _parse_embedded(html: str) -> list[Result]:
    """Layer 0: results from the page's embedded ``web.results`` data."""
    pos = html.find(_DATA_MARKER)
    if pos < 0:
        return []
    arr = html.find("results:[", pos)
    if arr < 0 or arr - pos > 500:
        return []
    out: list[Result] = []
    seen: set[str] = set()
    for obj in _split_js_objects(html, arr + len("results:[")):
        if 'type:"search_result"' not in obj:
            continue
        t = _TITLE_RE.search(obj)
        url = _rebuild_url(obj)
        if not t or not url or url in seen:
            continue
        title = base.clean_text(_js_unescape(t.group(1)))
        if not title:
            continue
        d = _DESC_RE.search(obj)
        snippet = ""
        if d:
            snippet = base.clean_text(
                BeautifulSoup(_js_unescape(d.group(1)), "html.parser").get_text()
            )
        seen.add(url)
        out.append(Result(title=title, url=url, snippet=snippet, engine=NAME))
        if len(out) >= base.MAX_RESULTS_PER_ENGINE:
            break
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
    results = _parse_embedded(html) or _parse_primary(soup) or _parse_fallback(soup)
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
