"""Offline tests for the Bing engine."""
from __future__ import annotations

import base64

import pytest

from search.engines import base, bing


class FakeResp:
    """Minimal response stand-in."""

    def __init__(self, text: str) -> None:
        self.text = text


def _wrap(url: str) -> str:
    enc = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    return f"https://www.bing.com/ck/a?!&&p=abc&u=a1{enc}&ntb=1"


def _item(href: str, title: str, snip: str) -> str:
    return (f'<li class="b_algo"><h2><a href="{href}">{title}</a></h2>'
            f'<div class="b_caption"><p>{snip}</p></div></li>')


def _page(items: str) -> str:
    return f'<html><body><ol id="b_results">{items}</ol></body></html>'


def _patch(monkeypatch, html):
    calls = []

    def fake(url, **kw):
        calls.append((url, kw))
        return None if html is None else FakeResp(html)

    monkeypatch.setattr("search.engines.base.fetch", fake)
    return calls


def test_parse_order_unwrap_and_ads(monkeypatch):
    html = _page(
        _item("https://one.example/a", "One", "first")
        + '<li class="b_ad"><h2><a href="https://ad.example/">Ad</a></h2></li>'
        + '<li class="b_algo b_ad"><h2><a href="https://ad2.example/">Ad2</a></h2></li>'
        + _item(_wrap("https://two.example/page?x=1"), "Two", "second")
        + _item("https://three.example/", "Three", "third")
    )
    _patch(monkeypatch, html)
    res = bing.search("hello")
    assert [r["url"] for r in res] == [
        "https://one.example/a", "https://two.example/page?x=1", "https://three.example/"]
    assert [r["title"] for r in res] == ["One", "Two", "Three"]
    assert res[0]["snippet"] == "first"
    assert all(r["engine"] == "bing" for r in res)


def test_malformed_base64_skipped(monkeypatch):
    bad = "https://www.bing.com/ck/a?!&&u=a1!!!notbase64***"
    notbing = base64.urlsafe_b64encode(b"ftp://x.example/").decode()
    html = _page(
        _item(bad, "Bad", "x")
        + _item(f"https://www.bing.com/ck/a?u=a1{notbing}", "Ftp", "x")
        + _item("https://ok.example/", "Ok", "y")
    )
    _patch(monkeypatch, html)
    res = bing.search("q")
    assert [r["url"] for r in res] == ["https://ok.example/"]


def test_captcha_page(monkeypatch):
    _patch(monkeypatch, "<html><body>Please verify you are human. CAPTCHA</body></html>")
    assert bing.search("q") == []


def test_fetch_none(monkeypatch):
    _patch(monkeypatch, None)
    assert bing.search("q") == []


def test_garbage(monkeypatch):
    _patch(monkeypatch, "\x00<<<>>>not html at all")
    assert bing.search("q") == []


def test_empty_query_no_fetch(monkeypatch):
    calls = _patch(monkeypatch, _page(_item("https://a.example/", "A", "a")))
    assert bing.search("") == []
    assert bing.search("   ") == []
    assert calls == []


def test_fetch_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr("search.engines.base.fetch", boom)
    assert bing.search("q") == []


def test_cap(monkeypatch):
    items = "".join(_item(f"https://s{i}.example/", f"T{i}", "s") for i in range(25))
    _patch(monkeypatch, _page(items))
    res = bing.search("q")
    assert len(res) == base.MAX_RESULTS_PER_ENGINE == 10
    assert res[0]["url"] == "https://s0.example/"


@pytest.mark.parametrize("safe,expected", [(True, "strict"), (False, "off")])
def test_adlt_mapping(monkeypatch, safe, expected):
    calls = _patch(monkeypatch, _page(""))
    bing.search("q", safe_search=safe, language="de")
    url, kw = calls[0]
    assert url == "https://www.bing.com/search"
    assert kw["params"]["adlt"] == expected
    assert kw["params"]["q"] == "q"
    assert kw["params"]["setlang"] == "de"


def test_invalid_language_falls_back(monkeypatch):
    calls = _patch(monkeypatch, _page(""))
    bing.search("q", language="english")
    assert calls[0][1]["params"]["setlang"] == "en"
