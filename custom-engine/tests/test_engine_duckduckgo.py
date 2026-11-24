"""Offline tests for the DuckDuckGo engine (HTTP is monkeypatched)."""
from __future__ import annotations

from urllib.parse import quote

import pytest

from search.engines import base, duckduckgo


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


def _result_block(i: int, target: str, extra_class: str = "") -> str:
    return f"""
    <div class="result results_links results_links_deep web-result {extra_class}">
      <div class="links_main links_deep result__body">
        <h2 class="result__title">
          <a rel="nofollow" class="result__a"
             href="//duckduckgo.com/l/?uddg={quote(target, safe='')}&amp;rut=abc{i}">Title {i}</a>
        </h2>
        <a class="result__snippet" href="x">Snippet   number
           {i}</a>
      </div>
    </div>"""


AD_BLOCK = """
<div class="result results_links result--ad">
  <h2 class="result__title"><a class="result__a"
    href="//duckduckgo.com/y.js?ad_provider=bing&amp;u3=https%3A%2F%2Fads.example">Buy now</a></h2>
  <a class="result__snippet">Sponsored</a>
</div>"""

FIXTURE = (
    "<html><body><div id='links' class='results'>"
    + _result_block(1, "https://example.com/a?x=1&y=2")
    + AD_BLOCK
    + _result_block(2, "https://example.org/b")
    + _result_block(3, "http://example.net/c#frag")
    + "</div></body></html>"
)


@pytest.fixture
def capture(monkeypatch):
    """Patch base.fetch and record the calls."""
    calls: list[dict] = []

    def install(response):
        def fake(url, **kwargs):
            calls.append({"url": url, **kwargs})
            if isinstance(response, Exception):
                raise response
            return response

        monkeypatch.setattr("search.engines.base.fetch", fake)
        return calls

    return install


def test_parses_three_organic_results_and_skips_ad(capture):
    capture(FakeResponse(FIXTURE))
    results = duckduckgo.search("hello world")
    assert [r["title"] for r in results] == ["Title 1", "Title 2", "Title 3"]
    assert [r["url"] for r in results] == [
        "https://example.com/a?x=1&y=2",
        "https://example.org/b",
        "http://example.net/c#frag",
    ]
    assert all(r["engine"] == "duckduckgo" for r in results)
    assert results[0]["snippet"] == "Snippet number 1"
    assert all("duckduckgo.com" not in r["url"] for r in results)


def test_name_constant():
    assert duckduckgo.NAME == "duckduckgo"


def test_fetch_none_returns_empty(capture):
    capture(None)
    assert duckduckgo.search("q") == []


def test_garbage_html_returns_empty(capture):
    capture(FakeResponse("<<<not html at all \x00 ]]>"))
    assert duckduckgo.search("q") == []


def test_captcha_page_returns_empty(capture):
    capture(FakeResponse(
        "<html><body><form class='anomaly-modal'>Unfortunately, bots use "
        "DuckDuckGo too. Please complete the following challenge. "
        "Select all squares containing a duck.</form></body></html>"
    ))
    assert duckduckgo.search("q") == []


@pytest.mark.parametrize("q", ["", "   ", "\n\t"])
def test_empty_query_does_not_fetch(capture, q):
    calls = capture(FakeResponse(FIXTURE))
    assert duckduckgo.search(q) == []
    assert calls == []


def test_fetch_raising_returns_empty(capture):
    capture(RuntimeError("boom"))
    assert duckduckgo.search("q") == []


def test_results_capped_at_max(capture):
    blocks = "".join(_result_block(i, f"https://example.com/{i}") for i in range(25))
    capture(FakeResponse(f"<html><body>{blocks}</body></html>"))
    results = duckduckgo.search("q")
    assert len(results) == base.MAX_RESULTS_PER_ENGINE == 10
    assert results[0]["url"] == "https://example.com/0"


def test_safe_search_param_passed(capture):
    calls = capture(FakeResponse(FIXTURE))
    duckduckgo.search("cats", safe_search=True)
    duckduckgo.search("cats", safe_search=False)
    assert calls[0]["data"]["kp"] == "1"
    assert calls[1]["data"]["kp"] == "-2"
    assert calls[0]["data"]["q"] == "cats"
    assert calls[0]["method"] == "POST"
    # Query must not appear in the URL.
    assert "cats" not in calls[0]["url"]


def test_language_validation(capture):
    calls = capture(FakeResponse(FIXTURE))
    duckduckgo.search("q", language="de")
    duckduckgo.search("q", language="BAD!")
    duckduckgo.search("q", language="bn")
    assert calls[0]["data"]["kl"] == "de-de"
    assert calls[1]["data"]["kl"] == "us-en"
    assert "kl" not in calls[2]["data"]


def test_unwrap_url_helpers():
    assert duckduckgo._unwrap_url("https://example.com/x") == "https://example.com/x"
    assert duckduckgo._unwrap_url("/relative") is None
    assert duckduckgo._unwrap_url("//duckduckgo.com/y.js?u3=1") is None
    assert duckduckgo._unwrap_url("javascript:alert(1)") is None
