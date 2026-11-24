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


# ---------------------------------------------------------------------------
# Layer 0: embedded results data (current Brave layout, Nov 2026).
# O_PYTHON and O_W3 are verbatim result objects captured from a live Brave
# results page; the other two are constructed edge cases in the same format.
# ---------------------------------------------------------------------------
O_PYTHON = (
    '{title:"Welcome to Python.org",url:"/go/v1/AcmKWfGeqFK1awUqOAH7CvjByKWyfQZ0Ox8U6_GRgBVFmzT6MzQ0V8k_sZ4BIBzfvjuJlvDfcjZpw31aVkCy2O_RSi5tR54UPjJDkAJo9AH0A83_3UnPRJpiORNrE_-KSz6TMFmM5lVAcTi3st4PBMOivVTuJM4OR2rCfn2kOyn3nyQDdPbx-U0A",'
    'full_title:void 0,description:"\\u003Cstrong>Python is a programming language that lets you work quickly and integrate systems more effectively\\u003C/strong>.",'
    'page_age:void 0,profile:{name:"Python",long_name:"python.org"},language:"en",type:"search_result",subtype:"generic",is_live:false,deep_results:void 0,'
    'meta_url:{scheme:"https",netloc:"python.org",hostname:"www.python.org",favicon:e,path:""},'
    'thumbnail:{src:f,original:"https://www.python.org/static/opengraph-icon-200x200.png",logo:true,duplicated:true,is_tripadvisor:false,bo_serp_visible:false},'
    'age:void 0,descriptionLength:52}'
)
O_W3 = (
    '{title:"Introduction to Python",url:"/go/v1/AcmKWfGeqFK1awUqOAH7CvjByKWyfQZ0Ox8U6_GRgtDpRNdDRQXzHcG-f9DSGeq_79zMrdqucqA3hrfezJqD",'
    'full_title:void 0,description:"Python is a popular programming language. It was \\u003Cstrong>created by Guido van Rossum, and released in 1991\\u003C/strong>.",'
    'page_age:void 0,profile:{name:"W3Schools",long_name:"w3schools.com"},language:"en",type:"search_result",subtype:"generic",is_live:false,deep_results:void 0,'
    'meta_url:{scheme:"https",netloc:"w3schools.com",hostname:"www.w3schools.com",favicon:g,path:"› python  › python_intro.asp"},'
    'thumbnail:{src:"https://imgs.search.brave.com/x/rs:fit:200:200:1:0/g:ce/aHR0c",original:"https://www.w3schools.com/images/og/python/python_intro.png",logo:false},'
    'age:void 0,descriptionLength:99}'
)
O_HUMAN = (
    '{title:"Python Introduction | Python Education",url:"/go/v1/AAAA",description:"Intro {with braces} and \\"quotes\\".",'
    'type:"search_result",meta_url:{scheme:"https",netloc:"developers.google.com",hostname:"developers.google.com",'
    'favicon:"https://imgs.search.brave.com/fav",path:"› google for education  › python introduction"},age:void 0}'
)
O_DIRECT = (
    '{title:"Direct link result",url:"https://example.org/direct?x=1",description:"Plain text.",'
    'type:"search_result",meta_url:{scheme:"https",netloc:"example.org",hostname:"example.org",favicon:h,path:"› direct"},age:void 0}'
)


def _embedded_page(*objs: str) -> str:
    data = (
        'web:{type:"search",choice:void 0,results:[' + ",".join(objs) + ']},mixed:{}'
    )
    return (
        "<html><body><main><div data-type=\"web\" class=\"snippet\">"
        "<a href=\"/go/v1#AbC\"><div class=\"title\">Welcome to Python.org</div></a></div></main>"
        "<script>__sveltekit_data = {data:[{x:1},{" + data + "}]};</script></body></html>"
    )


def test_embedded_layer_rebuilds_urls_from_live_format(monkeypatch):
    html = _embedded_page(O_PYTHON, O_W3, O_HUMAN, O_DIRECT)
    monkeypatch.setattr("search.engines.base.fetch", lambda *a, **k: FakeResp(html))
    res = brave.search("python programming")
    assert [r["url"] for r in res] == [
        "https://www.python.org/",
        "https://www.w3schools.com/python/python_intro.asp",
        "https://example.org/direct?x=1",
    ]
    assert res[0]["title"] == "Welcome to Python.org"
    assert res[0]["snippet"].startswith("Python is a programming language")
    assert "<strong>" not in res[1]["snippet"] and "Guido van Rossum" in res[1]["snippet"]
    assert all(r["engine"] == "brave" for r in res)


def test_embedded_layer_drops_human_readable_breadcrumbs():
    assert brave._parse_embedded(_embedded_page(O_HUMAN)) == []


def test_embedded_layer_absent_falls_back_to_markup_layers():
    assert brave._parse_embedded("<html><div data-type='web'></div></html>") == []


# ---------------------------------------------------------------------------
# Official Brave Search API mode (BRAVE_API_KEY set).
# ---------------------------------------------------------------------------
API_PAYLOAD = {
    "query": {"original": "climate change effects"},
    "web": {"results": [
        {"title": "The <strong>Effects</strong> of Climate Change", "url": "https://science.nasa.gov/climate-change/effects/",
         "description": "Changes to <strong>Earth's climate</strong> drive effects."},
        {"title": "Internal", "url": "https://search.brave.com/x", "description": ""},
        {"title": "No URL"},
    ]},
}


class FakeJsonResp:
    status_code = 200

    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def test_api_mode_used_when_key_set(monkeypatch):
    calls = []

    def fake(url, **kw):
        calls.append((url, kw))
        return FakeJsonResp(API_PAYLOAD)

    monkeypatch.setattr("search.engines.base.fetch", fake)
    monkeypatch.setenv("BRAVE_API_KEY", "test-key")
    res = brave.search("climate change effects", safe_search=False, language="de")
    url, kw = calls[0]
    assert url == "https://api.search.brave.com/res/v1/web/search"
    assert kw["headers"]["X-Subscription-Token"] == "test-key"
    assert kw["params"]["safesearch"] == "off"
    assert kw["params"]["search_lang"] == "de"
    assert [r["url"] for r in res] == ["https://science.nasa.gov/climate-change/effects/"]
    assert res[0]["title"] == "The Effects of Climate Change"
    assert "<strong>" not in res[0]["snippet"]


def test_api_mode_not_used_without_key(monkeypatch):
    calls = []

    def fake(url, **kw):
        calls.append(url)
        return FakeResp("<html></html>")

    monkeypatch.setattr("search.engines.base.fetch", fake)
    brave.search("x")
    assert calls == ["https://search.brave.com/search"]
