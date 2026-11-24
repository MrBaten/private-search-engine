"""Engine registry.

Each engine module exposes ``NAME`` and ``search(query, *, timeout,
safe_search, language) -> list[Result]`` and never raises.
"""
from __future__ import annotations

import os

from search.engines import bing, brave, demo, duckduckgo, marginalia, wikipedia
from search.types import EngineFn

ALL_ENGINES: dict[str, EngineFn] = {
    duckduckgo.NAME: duckduckgo.search,
    wikipedia.NAME: wikipedia.search,
    marginalia.NAME: marginalia.search,
    brave.NAME: brave.search,
    bing.NAME: bing.search,
}

DEMO_ENGINES: dict[str, EngineFn] = {
    demo.NAME_A: demo.search_a,
    demo.NAME_B: demo.search_b,
}

#: Fault-injection engine; only enabled when named explicitly in PSE_ENGINES.
FAULT_ENGINES: dict[str, EngineFn] = {
    demo.NAME_DOWN: demo.search_down,
}

_TRUTHY = {"1", "true", "yes", "on"}


def demo_mode_enabled() -> bool:
    """True when ``PSE_DEMO_MODE`` is set to a truthy value."""
    return os.environ.get("PSE_DEMO_MODE", "").strip().lower() in _TRUTHY


def get_enabled_engines() -> dict[str, EngineFn]:
    """Return the engines to query, honouring environment configuration.

    * ``PSE_ENGINES``: comma-separated subset of real engine names
      (default: all of :data:`ALL_ENGINES`: DuckDuckGo, Wikipedia,
      Marginalia, Brave, Bing). Demo names (``demo_a``,
      ``demo_b``) and the fault-injection engine ``demo_down`` may also be
      listed. Unknown names are ignored.
    * ``PSE_DEMO_MODE=1``: additionally enable the offline demo engines.
    """
    raw = os.environ.get("PSE_ENGINES", "").strip()
    if raw:
        wanted = [n.strip().lower() for n in raw.split(",") if n.strip()]
        enabled = {n: ALL_ENGINES[n] for n in wanted if n in ALL_ENGINES}
        enabled.update({n: DEMO_ENGINES[n] for n in wanted if n in DEMO_ENGINES})
        enabled.update({n: FAULT_ENGINES[n] for n in wanted if n in FAULT_ENGINES})
    else:
        enabled = dict(ALL_ENGINES)
    if demo_mode_enabled():
        enabled.update(DEMO_ENGINES)
    return enabled
