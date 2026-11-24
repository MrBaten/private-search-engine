"""Privacy primitives for PrivateSearch.

Privacy is the selling point of this project, so every decision in this module
is deliberate and explained.  The module is intentionally small and has no
third-party dependencies so it can be audited in a few minutes.

Threat model, in plain words
----------------------------
1. **The search provider / destination site learns who you are.**
   Result URLs frequently carry click-tracking identifiers (``fbclid``,
   ``gclid``, ``utm_*`` ...).  Those identifiers let advertisers link a click
   to a profile.  :func:`strip_tracking_params` removes them from every URL
   before it is shown to the user, so clicking a result does not hand a
   tracking token to the destination.

2. **The browser leaks data on its own.**  Referrer headers, cookies, caches,
   embedded third-party resources and "interest cohort" APIs can all leak what
   you searched for.  :data:`SECURITY_HEADERS` and
   :func:`apply_security_headers` switch these channels off at the HTTP
   layer, independent of what the templates do.

3. **The server itself keeps a diary.**  Default web-server access logs record
   the request line; a careless setup would therefore store queries on disk.
   :func:`no_log` silences the request loggers, and :func:`sanitize_query`
   keeps hostile input (control characters, giant payloads) away from the
   rest of the code.  The application never logs a query, never sets a
   cookie and never writes anything to disk.

Nothing here talks to the network and nothing here records user input.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import unquote_plus, urlsplit, urlunsplit

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps Flask optional at import time
    from flask import Response

__all__ = [
    "TRACKING_PARAMS",
    "TRACKING_PREFIXES",
    "SECURITY_HEADERS",
    "strip_tracking_params",
    "sanitize_query",
    "apply_security_headers",
    "no_log",
]

# ---------------------------------------------------------------------------
# Tracking-parameter removal
# ---------------------------------------------------------------------------

# Exact parameter names (lowercase) that exist only to identify a click, a
# campaign or a person across sites.  They never change what the destination
# page shows, so removing them is safe and stops the destination (and anyone
# observing the URL) from correlating the visit with an ad/click profile.
TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        # Google (Ads, Analytics cross-domain linker, Shopping)
        "gclid",
        "gclsrc",
        "dclid",
        "gbraid",
        "wbraid",
        "gad_source",
        "srsltid",
        "_ga",
        "_gl",
        # Meta / Facebook / Instagram
        "fbclid",
        "fb_action_ids",
        "fb_action_types",
        "fb_source",
        "fb_ref",
        "mibextid",
        "igshid",
        "igsh",
        # Microsoft / Bing Ads
        "msclkid",
        # Yandex
        "yclid",
        "_openstat",
        # X / Twitter
        "twclid",
        "ref_src",
        "ref_url",
        # TikTok
        "ttclid",
        # LinkedIn
        "li_fat_id",
        # Pinterest
        "epik",
        # Snapchat
        "sccid",
        "scid",
        # Mailchimp
        "mc_cid",
        "mc_eid",
        # HubSpot
        "_hsenc",
        "_hsmi",
        "__hssc",
        "__hstc",
        "__hsfp",
        "hsctatracking",
        # Marketo / Vero / Olytics / RollWorks / Adobe
        "mkt_tok",
        "vero_id",
        "oly_anon_id",
        "oly_enc_id",
        "rb_clickid",
        "s_cid",
        # Misc. generic click / share trackers
        "spm",
        "__s",
        "wickedid",
        "soc_src",
        "soc_trk",
    }
)

# Parameter-name prefixes owned by analytics suites; every name starting with
# one of these is a campaign tag (e.g. utm_source, mtm_campaign).  A prefix
# list future-proofs the filter against new utm_* style tags.
TRACKING_PREFIXES: tuple[str, ...] = (
    "utm_",      # Google Analytics / the de-facto campaign standard
    "pk_",       # Piwik / Matomo (legacy)
    "mtm_",      # Matomo Tag Manager
    "matomo_",   # Matomo (legacy campaign names)
    "piwik_",    # Piwik (legacy campaign names)
    "hsa_",      # HubSpot Ads
)


def _is_tracking_name(name: str) -> bool:
    """Return True if a (decoded) query-parameter name is a tracker.

    Matching is case-insensitive because trackers are not consistent about
    capitalisation (``UTM_Source`` is common) and servers ignore case for
    these names anyway.
    """
    lowered = name.lower()
    return lowered in TRACKING_PARAMS or lowered.startswith(TRACKING_PREFIXES)


def strip_tracking_params(url: str) -> str:
    """Remove tracking parameters from ``url`` and return the cleaned URL.

    * Remaining parameters keep their original order, raw encoding and blank
      values (``a=&b``), because rewriting them could break the destination.
    * The fragment is kept untouched: it is never sent to the server, so it
      cannot leak anything, and removing it would break in-page anchors.
    * If nothing was removed, the input is returned byte-for-byte.
    * No dangling ``?`` is left when every parameter was a tracker.
    * Never raises: on any parse problem (or non-string input) the input is
      returned unchanged, so a malformed result can never crash a search.
    """
    if not isinstance(url, str) or "?" not in url:
        return url
    try:
        parts = urlsplit(url)
        if not parts.query:
            return url
        kept: list[str] = []
        removed = False
        for segment in parts.query.split("&"):
            if segment == "":
                # Empty segment from "&&"; harmless noise, drop it quietly
                # only if we are already rewriting the query.
                kept.append(segment)
                continue
            name = unquote_plus(segment.split("=", 1)[0])
            if _is_tracking_name(name):
                removed = True
            else:
                kept.append(segment)
        if not removed:
            return url
        new_query = "&".join(s for s in kept if s != "")
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, new_query, parts.fragment)
        )
    except Exception:  # noqa: BLE001 - contract: never raise, return input
        return url


# ---------------------------------------------------------------------------
# Query sanitising
# ---------------------------------------------------------------------------

# Control characters (C0, DEL, C1) plus Unicode line/paragraph separators.
# They have no place in a search query and can be used for log injection or
# to smuggle odd bytes into upstream requests.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


def sanitize_query(raw: object, max_len: int = 512) -> str:
    """Return a safe, normalised search query (``""`` for unusable input).

    * Non-strings (including ``None``) give ``""`` instead of being coerced:
      ``str(obj)`` on arbitrary objects could expose internals or be huge.
    * Control characters become spaces, and whitespace runs collapse to one
      space, so the same query always looks the same (good for cache keys).
    * The result is truncated to ``max_len`` characters so an attacker cannot
      make us forward or store megabytes of text.
    """
    if not isinstance(raw, str):
        return ""
    cleaned = _CONTROL_RE.sub(" ", raw)
    cleaned = " ".join(cleaned.split())
    if max_len >= 0 and len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip()
    return cleaned


# ---------------------------------------------------------------------------
# HTTP response hardening
# ---------------------------------------------------------------------------

SECURITY_HEADERS: dict[str, str] = {
    # Never send a Referer: the browser would otherwise tell the destination
    # site (and any resource host) which page - and therefore which
    # search - the visitor came from.
    "Referrer-Policy": "no-referrer",
    # Stop MIME sniffing so a response is only ever treated as its declared type.
    "X-Content-Type-Options": "nosniff",
    # Forbid framing: prevents clickjacking and embedding of the results page.
    "X-Frame-Options": "DENY",
    # Only same-origin resources: no third-party scripts, fonts, beacons or
    # trackers can load, even if a template were ever modified carelessly.
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; form-action 'self'; frame-ancestors 'none'; "
        "base-uri 'none'"
    ),
    # Opt out of FLoC / Topics interest profiling and powerful sensors.
    "Permissions-Policy": (
        "interest-cohort=(), browsing-topics=(), geolocation=(), "
        "camera=(), microphone=()"
    ),
    # Isolate the browsing context from cross-origin windows (no window.opener
    # leaks, no cross-window side channels).
    "Cross-Origin-Opener-Policy": "same-origin",
    # Other sites may not embed our responses as sub-resources.
    "Cross-Origin-Resource-Policy": "same-origin",
    # Block Flash/PDF cross-domain policy files from granting data access.
    "X-Permitted-Cross-Domain-Policies": "none",
}


def apply_security_headers(response: "Response") -> "Response":
    """Harden a Flask response in place and return it.

    * Sets every header in :data:`SECURITY_HEADERS`.
    * Deletes any ``Set-Cookie``: this service is stateless, so a cookie could
      only ever be a tracking vector (and would be a bug).
    * Sets ``Cache-Control: no-store, max-age=0`` and ``Pragma: no-cache`` so
      neither the browser nor an intermediary proxy keeps results pages (which
      reveal the query) in a cache or the back/forward store.
    """
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    # `del` removes every Set-Cookie header, not just the first one.
    if "Set-Cookie" in response.headers:
        del response.headers["Set-Cookie"]
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def no_log() -> None:
    """Silence request logging (idempotent, never prints).

    Werkzeug's dev server logs one line per request, including the request
    path; gunicorn's access log does the same.  Even though queries are sent
    by POST (so they are not in the path), the safest policy is to keep no
    request record at all.  Werkzeug is raised to ERROR so real failures stay
    visible while routine request lines disappear; the gunicorn access logger
    is disabled outright if it exists.
    """
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    # getLogger creates the logger if absent, which is harmless: a disabled
    # logger that is never used costs nothing.
    gunicorn_access = logging.getLogger("gunicorn.access")
    gunicorn_access.disabled = True
    gunicorn_access.setLevel(logging.CRITICAL + 1)
