"""Developer tool: which search engines answer from THIS machine right now?

Runs one fixed, generic sample query through every real engine using the
project's normal code path (no special clients) and prints, per engine, how
many results came back or why it was unavailable (HTTP 429, challenge page,
off-topic results, ...). Useful after moving the app to a new network or
server, or after adding API keys.

Usage (from the repo root):
    .venv/bin/python scripts/diagnose_engines.py
    BRAVE_API_KEY=... MARGINALIA_API_KEY=... .venv/bin/python scripts/diagnose_engines.py

Privacy: the sample query is fixed; nothing a user typed is sent, and nothing
is written to disk.
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom-engine"))

from search.engines import ALL_ENGINES, base  # noqa: E402

QUERY = "climate change effects"


def main() -> int:
    """Query each engine once and print a status table; exit 0 if any works."""
    logging.disable(logging.CRITICAL)  # keep the table readable
    print(f"{'engine':<12} {'results':>7} {'time':>7}  status")
    working = 0
    for name, fn in ALL_ENGINES.items():
        base.reset_unavailable()
        t0 = time.monotonic()
        results = fn(QUERY, timeout=8.0)
        ms = (time.monotonic() - t0) * 1000
        reason = None if results else base.unavailable_reason()
        status = "ok" if results else (f"unavailable: {reason}" if reason else "no results")
        working += bool(results)
        first = f"  e.g. {results[0]['title'][:40]}" if results else ""
        print(f"{name:<12} {len(results):>7} {ms:>6.0f}ms  {status}{first}")
    print(f"\n{working} of {len(ALL_ENGINES)} engines returned results.")
    return 0 if working else 1


if __name__ == "__main__":
    raise SystemExit(main())
