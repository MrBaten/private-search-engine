"""PrivateSearch web app: a Flask front end for the custom meta-search engine.

Privacy decisions are explained next to the code that implements them. In
short: queries only travel in POST bodies, are never logged, never echoed back
in errors or JSON, and no cookies or sessions are ever created.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

# Allow both `python custom-engine/app.py` (repo root) and `python app.py`
# (inside custom-engine/): make the `search` package importable either way.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from flask import Flask, Response, jsonify, redirect, render_template, request  # noqa: E402

from search import aggregator, privacy  # noqa: E402

#: (code, label) pairs offered in the settings panel.
LANGUAGES: list[tuple[str, str]] = [
    ("en", "English"),
    ("bn", "Bangla"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("fr", "Français"),
    ("hi", "Hindi"),
    ("ja", "日本語"),
    ("pt", "Português"),
]

#: (id, label) pairs for the filter tabs.
CATEGORIES: list[tuple[str, str]] = [
    ("all", "All"),
    ("images", "Images"),
    ("videos", "Videos"),
    ("news", "News"),
    ("wikipedia", "Wikipedia"),
]

_LANGUAGE_CODES = frozenset(code for code, _ in LANGUAGES)
_CATEGORY_IDS = frozenset(cid for cid, _ in CATEGORIES)

#: Request bodies are tiny (a query plus a few flags); cap them at 16 KiB.
MAX_BODY_BYTES = 16 * 1024


def _valid_language(value: object) -> str:
    """Return ``value`` if it is a supported language code, else ``"en"``."""
    return value if isinstance(value, str) and value in _LANGUAGE_CODES else "en"


def _valid_category(value: object) -> str:
    """Return ``value`` if it is a known category id, else ``"all"``."""
    return value if isinstance(value, str) and value in _CATEGORY_IDS else "all"


def _domain(url: str) -> str:
    """Return the hostname of ``url`` without a leading ``www.`` ("" on error)."""
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _with_domains(results: list[Any]) -> list[dict[str, Any]]:
    """Copy results and add a ``domain`` key (copies keep cached data pristine)."""
    out: list[dict[str, Any]] = []
    for item in results:
        row = dict(item)
        row["domain"] = _domain(str(row.get("url", "")))
        out.append(row)
    return out


def create_app(config: Mapping[str, Any] | None = None) -> Flask:
    """Build and configure the Flask application.

    Honoured config keys: ``TESTING``, ``ENGINES`` (engine mapping override
    passed to the aggregator) and ``USE_CACHE`` (default True).
    """
    # Silence request logging first: werkzeug's access line would otherwise
    # record request paths (and, for GET, query strings).
    privacy.no_log()

    flask_app = Flask(__name__)
    flask_app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES
    # Deliberately no SECRET_KEY and no use of `session`: nothing about a
    # visitor is ever stored, so there is nothing to sign.
    if config:
        flask_app.config.update(dict(config))

    def run_search(query: str, safe_search: bool, language: str) -> dict[str, Any]:
        """Run the aggregator with the app's engine/cache configuration."""
        return dict(
            aggregator.search_with_meta(
                query,
                engines=flask_app.config.get("ENGINES"),
                safe_search=safe_search,
                language=language,
                use_cache=flask_app.config.get("USE_CACHE", True),
            )
        )

    @flask_app.get("/")
    def index() -> str:
        """Render the landing page with default preferences."""
        return render_template(
            "index.html",
            safe_search=True,
            language="en",
            categories=CATEGORIES,
            languages=LANGUAGES,
        )

    @flask_app.get("/search")
    def search_get() -> Response:
        """Bounce GET /search to the home page without searching.

        Queries must never live in a URL (history, proxies, Referer headers),
        so a GET carrying ``?q=...`` is deliberately ignored. 303 makes sure
        clients follow with a plain GET and never re-send anything.
        """
        return redirect("/", code=303)

    @flask_app.post("/search")
    def search_post() -> Response | tuple[Response, int] | str:
        """Handle a search submitted as JSON (API) or as an HTML form."""
        if request.is_json:
            return _search_json()
        return _search_form()

    def _search_json() -> Response | tuple[Response, int]:
        """JSON mode: returns results without echoing the query back."""
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "query is required"}), 400
        query = privacy.sanitize_query(body.get("query"))
        if not query:
            return jsonify({"error": "query is required"}), 400
        safe = body.get("safe_search", True)
        safe_search = safe if isinstance(safe, bool) else True
        language = _valid_language(body.get("language", "en"))
        data = run_search(query, safe_search, language)
        results = _with_domains(list(data.get("results", [])))
        # The query is intentionally absent from the payload.
        return jsonify(
            {
                "results": results,
                "count": len(results),
                "engines_ok": data.get("engines_ok", []),
                "engines_failed": data.get("engines_failed", []),
                "cached": bool(data.get("cached", False)),
                "elapsed_ms": data.get("elapsed_ms", 0.0),
            }
        )

    def _search_form() -> Response | str:
        """Form mode: render results.html (query only in the POST body)."""
        query = privacy.sanitize_query(request.form.get("q"))
        if not query:
            return redirect("/", code=303)
        safe_search = request.form.get("safe", "1") != "0"
        language = _valid_language(request.form.get("lang", "en"))
        category = _valid_category(request.form.get("cat", "all"))
        data = run_search(query, safe_search, language)
        results = _with_domains(list(data.get("results", [])))
        if category == "wikipedia":
            results = [r for r in results if "wikipedia" in r.get("engines", [])]
        # Other categories: backend returns everything; the UI says so.
        return render_template(
            "results.html",
            query=query,
            results=results,
            count=len(results),
            engines_ok=data.get("engines_ok", []),
            engines_failed=data.get("engines_failed", []),
            cached=bool(data.get("cached", False)),
            elapsed_ms=data.get("elapsed_ms", 0.0),
            category=category,
            safe_search=safe_search,
            language=language,
            categories=CATEGORIES,
            languages=LANGUAGES,
        )

    @flask_app.get("/healthz")
    def healthz() -> Response:
        """Liveness probe."""
        return jsonify({"status": "ok"})

    @flask_app.get("/robots.txt")
    def robots() -> Response:
        """Ask crawlers to stay away from the search endpoint."""
        return Response("User-agent: *\nDisallow: /search\n", mimetype="text/plain")

    # Error handlers return fixed bodies only: never request data, since a
    # query could otherwise leak into responses, proxies or logs.
    @flask_app.errorhandler(404)
    def not_found(_exc: Exception) -> tuple[Response, int]:
        """Generic 404."""
        return jsonify({"error": "not found"}), 404

    @flask_app.errorhandler(405)
    def method_not_allowed(_exc: Exception) -> tuple[Response, int]:
        """Generic 405."""
        return jsonify({"error": "method not allowed"}), 405

    @flask_app.errorhandler(413)
    def too_large(_exc: Exception) -> tuple[Response, int]:
        """Generic 413 for oversized bodies."""
        return jsonify({"error": "request too large"}), 413

    @flask_app.errorhandler(500)
    def server_error(_exc: Exception) -> tuple[Response, int]:
        """Generic 500."""
        return jsonify({"error": "internal error"}), 500

    @flask_app.after_request
    def add_privacy_headers(response: Response) -> Response:
        """Apply security headers, strip cookies and disable caching."""
        return privacy.apply_security_headers(response)

    return flask_app


app = create_app()

if __name__ == "__main__":
    _host = os.environ.get("HOST", "127.0.0.1")
    _port = int(os.environ.get("PORT", "5000"))
    # Only the address is printed; never any query.
    print(f"PrivateSearch listening on http://{_host}:{_port}")
    app.run(host=_host, port=_port, debug=False)
