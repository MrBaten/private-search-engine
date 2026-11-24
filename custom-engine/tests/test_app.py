"""Offline tests for the Flask app (fake engines, no network)."""
from __future__ import annotations

import logging

import pytest

from app import CATEGORIES, LANGUAGES, create_app

UNIQUE = "test123unique"


def _make_engine(name: str, calls: list[str] | None = None, n: int = 4):
    def engine(query, *, timeout=3.0, safe_search=True, language="en"):
        if calls is not None:
            calls.append(name)
        host = "en.wikipedia.org" if name == "wikipedia" else f"{name}.example.com"
        return [
            {
                "title": f"{name} title {i}",
                "url": f"https://www.{host}/page{i}?utm_source=x",
                "snippet": f"snippet {i}",
                "engine": name,
            }
            for i in range(n)
        ]

    return engine


@pytest.fixture
def calls() -> list[str]:
    return []


@pytest.fixture
def client(calls):
    engines = {
        "wikipedia": _make_engine("wikipedia", calls),
        "alpha": _make_engine("alpha", calls),
        "beta": _make_engine("beta", calls),
    }
    app = create_app({"TESTING": True, "ENGINES": engines, "USE_CACHE": False})
    return app.test_client()


def test_constants() -> None:
    assert ("en", "English") in LANGUAGES
    assert [c for c, _ in CATEGORIES] == ["all", "images", "videos", "news", "wikipedia"]


def test_index(client) -> None:
    r = client.get("/")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "PrivateSearch" in body
    assert "google-analytics" not in body
    assert "fonts.googleapis" not in body


def test_json_search(client) -> None:
    r = client.post("/search", json={"query": "python"})
    assert r.status_code == 200
    data = r.get_json()
    assert data["count"] >= 5
    assert data["count"] == len(data["results"])
    assert {"engines_ok", "engines_failed", "cached", "elapsed_ms"} <= data.keys()
    assert all(x["domain"] and not x["domain"].startswith("www.") for x in data["results"])


def test_json_does_not_echo_query(client) -> None:
    r = client.post("/search", json={"query": UNIQUE})
    assert r.status_code == 200
    assert UNIQUE not in r.get_data(as_text=True)
    assert "query" not in r.get_json()


@pytest.mark.parametrize("payload", [{}, {"query": ""}, {"query": "   "}, {"query": 5}, [1]])
def test_json_missing_query(client, payload) -> None:
    r = client.post("/search", json=payload)
    assert r.status_code == 400
    assert r.get_json() == {"error": "query is required"}


def test_form_search_renders_html(client) -> None:
    r = client.post("/search", data={"q": "python"})
    assert r.status_code == 200
    assert r.mimetype == "text/html"
    body = r.get_data(as_text=True)
    assert "alpha.example.com" in body
    assert "utm_source" not in body


def test_form_empty_query_redirects(client) -> None:
    r = client.post("/search", data={"q": "   "})
    assert r.status_code == 303
    assert r.headers["Location"].rstrip("/").endswith("") and r.headers["Location"].endswith("/")


def test_get_search_redirects_without_searching(client, calls) -> None:
    r = client.get("/search?q=x")
    assert r.status_code == 303
    assert r.headers["Location"].endswith("/")
    assert "q=" not in r.headers["Location"]
    assert calls == []
    assert client.get("/search").status_code == 303


def test_category_wikipedia_filters(client) -> None:
    r = client.post("/search", data={"q": "python", "cat": "wikipedia"})
    body = r.get_data(as_text=True)
    assert "wikipedia.org" in body
    assert "alpha.example.com" not in body
    assert "beta.example.com" not in body


def test_invalid_language_and_category_fall_back(client) -> None:
    r = client.post("/search", data={"q": "python", "lang": "zz", "cat": "bogus"})
    assert r.status_code == 200
    r = client.post("/search", json={"query": "python", "language": "../x"})
    assert r.status_code == 200


def test_healthz_and_robots(client) -> None:
    assert client.get("/healthz").get_json() == {"status": "ok"}
    r = client.get("/robots.txt")
    assert r.get_data(as_text=True) == "User-agent: *\nDisallow: /search\n"


def test_error_handlers_do_not_echo(client) -> None:
    r = client.get(f"/{UNIQUE}")
    assert r.status_code == 404
    assert UNIQUE not in r.get_data(as_text=True)
    r = client.put("/search", data={"q": UNIQUE})
    assert r.status_code == 405
    assert UNIQUE not in r.get_data(as_text=True)
    r = client.post("/search", data={"q": UNIQUE * 2000})
    assert r.status_code == 413
    assert UNIQUE not in r.get_data(as_text=True)


@pytest.mark.parametrize(
    "method,path,kwargs",
    [
        ("get", "/", {}),
        ("get", "/healthz", {}),
        ("get", "/robots.txt", {}),
        ("get", "/search?q=x", {}),
        ("get", "/nope", {}),
        ("post", "/search", {"data": {"q": "python"}}),
        ("post", "/search", {"json": {"query": "python"}}),
        ("post", "/search", {"json": {}}),
    ],
)
def test_security_headers_and_no_cookies(client, method, path, kwargs) -> None:
    r = getattr(client, method)(path, **kwargs)
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert "Set-Cookie" not in r.headers


def test_query_not_in_response_headers(client) -> None:
    for r in (
        client.post("/search", data={"q": UNIQUE}),
        client.post("/search", json={"query": UNIQUE}),
        client.get(f"/search?q={UNIQUE}"),
    ):
        for k, v in r.headers.items():
            assert UNIQUE not in k and UNIQUE not in v


def test_no_logging_of_query(client, caplog, capsys) -> None:
    with caplog.at_level(logging.DEBUG):
        client.post("/search", data={"q": UNIQUE})
        client.post("/search", json={"query": UNIQUE})
        client.get(f"/search?q={UNIQUE}")
        client.get(f"/{UNIQUE}")
    out = capsys.readouterr()
    assert UNIQUE not in caplog.text
    assert UNIQUE not in out.out and UNIQUE not in out.err
