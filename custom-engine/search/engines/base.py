"""Shared HTTP helpers for engine modules.

Every engine calls :func:`fetch` as ``base.fetch(...)`` (module attribute
lookup) so tests can monkeypatch ``search.engines.base.fetch`` and run fully
offline.

Privacy decisions in this module:
* A fresh ``requests.Session`` per call with a cookie policy that rejects
  everything, so upstream engines cannot correlate our requests via cookies.
* A rotating, common desktop User-Agent so the instance does not stand out
  with a unique fingerprint (and to reduce trivial bot blocking).
* ``DNT: 1`` and no ``Referer`` header are sent upstream.
* Log lines contain only the host and HTTP status, never the URL or query.
"""
from __future__ import annotations

import http.cookiejar
import logging
import random
import re
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

MAX_RESULTS_PER_ENGINE = 10

# Current stable desktop browsers as of 2026-11 (Chrome 157: Chrome Releases
# blog, 2026-11-03; Firefox 160: Mozilla, 2026-11-10). Chrome uses the reduced
# UA format ("157.0.0.0", frozen macOS 10_15_7). Refresh these when browsers
# move on: stale versions make requests stand out and get blocked more often.
USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/157.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/157.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/157.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:160.0) Gecko/20100101 Firefox/160.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:160.0) Gecko/20100101 Firefox/160.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:160.0) Gecko/20100101 Firefox/160.0",
)

_WS_RE = re.compile(r"\s+")


class _RejectAllCookies(http.cookiejar.DefaultCookiePolicy):
    """Cookie policy that never stores or returns cookies."""

    def set_ok(self, cookie: http.cookiejar.Cookie, request: Any) -> bool:  # noqa: D401
        return False

    def return_ok(self, cookie: http.cookiejar.Cookie, request: Any) -> bool:  # noqa: D401
        return False


def random_user_agent() -> str:
    """Return a random common browser User-Agent string."""
    return random.choice(USER_AGENTS)


def default_headers(user_agent: Optional[str] = None) -> dict[str, str]:
    """Browser-like request headers with no cookies and no referrer.

    Args:
        user_agent: Explicit UA; a random one from :data:`USER_AGENTS` if None.
    """
    return {
        "User-Agent": user_agent or random_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.7",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
    }


def fetch(
    url: str,
    *,
    params: Optional[Mapping[str, Any]] = None,
    data: Optional[Mapping[str, Any]] = None,
    method: str = "GET",
    headers: Optional[Mapping[str, str]] = None,
    timeout: float = 3.0,
) -> Optional[requests.Response]:
    """Perform one HTTP request and return the response, or ``None``.

    Never raises. Returns ``None`` on network errors, timeouts, HTTP 403/429
    (blocked / rate limited) and any other non-2xx status, so engines can
    simply ``return []`` when they get ``None``.
    """
    host = urlsplit(url).hostname or "?"
    merged = default_headers()
    if headers:
        merged.update(headers)
    try:
        with requests.Session() as session:
            session.cookies.set_policy(_RejectAllCookies())
            resp = session.request(
                method.upper(),
                url,
                params=params,
                data=data,
                headers=merged,
                timeout=timeout,
                allow_redirects=True,
            )
    except Exception as exc:  # noqa: BLE001 - engines must never crash the aggregator
        logger.warning("%s: request failed (%s)", host, type(exc).__name__)
        return None
    if resp.status_code in (403, 429):
        logger.warning("%s: HTTP %s (blocked or rate limited), skipping", host, resp.status_code)
        return None
    if not 200 <= resp.status_code < 300:
        logger.warning("%s: HTTP %s, skipping", host, resp.status_code)
        return None
    return resp


def clean_text(s: Optional[str]) -> str:
    """Collapse internal whitespace and strip; ``None`` becomes ``""``."""
    if not s:
        return ""
    return _WS_RE.sub(" ", s).strip()
