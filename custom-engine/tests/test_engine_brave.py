"""Offline tests for the Brave engine module."""
from __future__ import annotations

from typing import Any

import pytest

from search.engines import base, brave


class FakeResp:
    """Minimal stand-in for requests.Response."""

    def __init__(self, text: str) -> None:
        self.text = text


def _patch(monkeypatch: pytest.MonkeyPatch, text: Any, calls: list | None = None) -> None:
    def fake(url: str, **kwargs: Any) -> Any:
        if calls is not None:
            calls.append((url, kwargs))
        return None if text is None else FakeResp(text)

    monkeypatch.setattr("search.engines.base.fetch", fake)


PRIMARY = """
<html><body><div id="results">
<div class="snippet" data-type="web"><a href="https://one.example/a"><div class="title">One</div></a>
 <div class="snippet-description">First snippet</div></div>
<div class="snippet ad sponsored" data-type="web"><a href="https://ad.example/"><div class="title">Buy now</div></a></div>
<div class="snippet" data-type="ad"><a href="https://ad2.example/"><div class="title">Ad2</div></a></div>
<div class="snippet" data-type="web"><a href="https://search.brave.com/x"></a>
 <a href="https://two.example/b"><span class="snippet-title">Two</span></a>
 <div class="generic-snippet"><div class="content">Second snippet</div></div></div>
<div data-type="web"><a href="https://three.example/c">Three</a><div class="description">Third</div></div>
</div></body></html>
"""

FALLBACK = """
<html><body><main id="results">
<div class="card"><a href="https://fb1.example/p"><h3>Fallback One</h3></a>
 <p class="description">Desc one</p></div>
<div class="card"><a href="https://brave.com/about"><h3>Brave internal</h3></a></div>
<div class="card"><a href="https://fb2.example/q"><h3>Fallback Two</h3></a></div>
<a href="https://notitle.example/">   </a>
</main></body></html>
"""


def test_primary_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, PRIMARY)
    res = brave.search("anything")
    assert [r["url"] for r in res] == [
        "https://one.example/a", "https://two.example/b", "https://three.example/c"]
    assert [r["title"] for r in res] == ["One", "Two", "Three"]
    assert [r["snippet"] for r in res] == ["First snippet", "Second snippet", "Third"]
    assert all(r["engine"] == "brave" == brave.NAME for r in res)


def test_fallback_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, FALLBACK)
    res = brave.search("q")
    assert [r["url"] for r in res] == ["https://fb1.example/p", "https://fb2.example/q"]
    assert res[0]["title"] == "Fallback One"
    assert res[0]["snippet"] == "Desc one"
    assert res[1]["snippet"] == ""


def test_internal_brave_links_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    html = ('<div id="results"><div data-type="web"><a href="https://search.brave.com/s">T</a>'
            '</div><div data-type="web"><a href="https://brave.com/">B</a></div></div>')
    _patch(monkeypatch, html)
    assert brave.search("q") == []


@pytest.mark.parametrize("page", [
    "<html><body><h1>Please solve the CAPTCHA</h1></body></html>",
    "<html><body>Rate limit exceeded. Too many requests.</body></html>",
])
def test_captcha_page(monkeypatch: pytest.MonkeyPatch, page: str) -> None:
    _patch(monkeypatch, page)
    assert brave.search("q") == []


def test_fetch_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, None)
    assert brave.search("q") == []


@pytest.mark.parametrize("garbage", ["", "\x00\x01<<<>>>", "<div><a href=", "not html at all"])
def test_garbage(monkeypatch: pytest.MonkeyPatch, garbage: str) -> None:
    _patch(monkeypatch, garbage)
    assert brave.search("q") == []


@pytest.mark.parametrize("q", ["", "   ", "\n"])
def test_empty_query_no_fetch(monkeypatch: pytest.MonkeyPatch, q: str) -> None:
    calls: list = []
    _patch(monkeypatch, PRIMARY, calls)
    assert brave.search(q) == []
    assert calls == []


def test_fetch_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("secret-query")

    monkeypatch.setattr("search.engines.base.fetch", boom)
    assert brave.search("secret-query") == []


def test_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    items = "".join(
        f'<div data-type="web"><a href="https://e{i}.example/">T{i}</a></div>' for i in range(25))
    _patch(monkeypatch, f'<div id="results">{items}</div>')
    res = brave.search("q")
    assert len(res) == base.MAX_RESULTS_PER_ENGINE == 10
    assert res[0]["url"] == "https://e0.example/"


def test_safesearch_param(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    _patch(monkeypatch, PRIMARY, calls)
    brave.search("hello", safe_search=True)
    brave.search("hello", safe_search=False, language="zz9")
    p1, p2 = calls[0][1]["params"], calls[1][1]["params"]
    assert calls[0][0] == "https://search.brave.com/search"
    assert p1 == {"q": "hello", "source": "web", "safesearch": "strict"}
    assert p2["safesearch"] == "off"
    assert calls[1][1]["headers"]["Accept-Language"].startswith("en")
