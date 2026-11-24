"""Offline demo engines (opt-in via ``PSE_DEMO_MODE=1``).

Used for demos and CI where outbound access to real search engines is not
available. They return deterministic fixture results on ``example.com`` /
``example.org`` (domains reserved for documentation by RFC 2606), every title
is prefixed ``[Demo]`` so they can never be mistaken for real results.

The two engines deliberately overlap and include tracking parameters so the
aggregator's deduplication and tracking-parameter stripping are exercised
end-to-end.
"""
from __future__ import annotations

from urllib.parse import quote

from search.engines import base
from search.types import Result

NAME_A = "demo_a"
NAME_B = "demo_b"


def _slug(query: str) -> str:
    return quote(base.clean_text(query).lower().replace(" ", "-")[:60], safe="-")


def search_a(query: str, *, timeout: float = 3.0, safe_search: bool = True,
             language: str = "en") -> list[Result]:
    """Demo engine A: six fixture results, some with tracking params."""
    q = base.clean_text(query)
    if not q:
        return []
    s = _slug(q)
    rows = [
        (f"https://www.example.com/{s}?utm_source=demo&utm_medium=cpc", "Overview"),
        (f"https://example.org/docs/{s}/", "Documentation"),
        (f"https://example.com/tutorial/{s}?fbclid=DEMO123", "Tutorial"),
        (f"https://example.net/{s}/faq", "FAQ"),
        (f"https://example.org/news/{s}?gclid=DEMO456&page=1", "News"),
        (f"https://example.com/community/{s}", "Community"),
    ]
    return [
        Result(title=f"[Demo] {q} — {label}", url=url,
               snippet=f"Offline demo result A{i + 1} for this query ({label.lower()}).",
               engine=NAME_A)
        for i, (url, label) in enumerate(rows)
    ]


def search_b(query: str, *, timeout: float = 3.0, safe_search: bool = True,
             language: str = "en") -> list[Result]:
    """Demo engine B: overlaps with A (same pages, different URL forms)."""
    q = base.clean_text(query)
    if not q:
        return []
    s = _slug(q)
    rows = [
        (f"https://example.org/docs/{s}", "Documentation"),        # dup of A#2 (trailing slash)
        (f"http://example.com/{s}?utm_campaign=demo", "Overview"),  # dup of A#1 (http, www, utm)
        (f"https://example.edu/{s}/guide", "Guide"),
        (f"https://example.com/tutorial/{s}", "Tutorial"),          # dup of A#3 (fbclid)
    ]
    return [
        Result(title=f"[Demo] {q} — {label}", url=url,
               snippet=f"Offline demo result B{i + 1}: a slightly longer snippet for {label.lower()}.",
               engine=NAME_B)
        for i, (url, label) in enumerate(rows)
    ]


NAME_DOWN = "demo_down"


def search_down(query: str, *, timeout: float = 3.0, safe_search: bool = True,
                language: str = "en") -> list[Result]:
    """Simulated outage: always raises.

    Opt-in only (``PSE_ENGINES=...,demo_down``). ``test.sh`` enables it to prove
    end-to-end that one engine going down never breaks a search; real engines
    swallow their own errors and return ``[]``, so the aggregator's exception
    path needs a deliberate fault to be exercised against the running app.
    """
    raise RuntimeError("simulated engine outage")
