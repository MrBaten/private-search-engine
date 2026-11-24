"""Offline tests for search.aggregator using fake engine callables."""
from __future__ import annotations

import time

import pytest

from search import aggregator, cache


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


def R(engine: str, url: str, title: str = "t", snippet: str = "") -> dict:
    return {"title": title, "url": url, "snippet": snippet, "engine": engine}


def fake(engine: str, rows: list[dict], calls: list | None = None):
    def fn(query, *, timeout=3.0, safe_search=True, language="en"):
        if calls is not None:
            calls.append(engine)
        return list(rows)
    return fn


# --- normalize_url -------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("http://example.com/a", "https://example.com/a"),
    ("https://www.example.com/a", "https://example.com/a"),
    ("https://example.com/a/", "https://example.com/a"),
    ("https://EXAMPLE.com/a", "https://example.com/a"),
    ("https://example.com/a#frag", "https://example.com/a"),
    ("https://example.com/a?utm_source=x&id=1", "https://example.com/a?id=1"),
    ("https://example.com/a?b=2&a=1", "https://example.com/a?a=1&b=2"),
    ("https://example.com:443/a", "https://example.com/a"),
    ("http://example.com:80/a", "https://example.com/a"),
])
def test_normalize_url_equivalences(a, b):
    assert aggregator.normalize_url(a) == aggregator.normalize_url(b)


def test_normalize_url_root_and_ports():
    assert aggregator.normalize_url("https://example.com/") == "https://example.com"
    assert aggregator.normalize_url("https://example.com") == "https://example.com"
    assert ":8080" in aggregator.normalize_url("https://example.com:8080/x")


def test_normalize_url_distinguishes_different_pages():
    assert aggregator.normalize_url("https://example.com/a") != aggregator.normalize_url("https://example.com/b")
    assert aggregator.normalize_url("https://example.com/a?id=1") != aggregator.normalize_url("https://example.com/a?id=2")


def test_normalize_url_never_raises():
    assert isinstance(aggregator.normalize_url("http://[bad"), str)
    assert isinstance(aggregator.normalize_url(""), str)


# --- dedup / merge -------------------------------------------------------

def test_dedup_same_url_from_two_engines():
    engines = {
        "a": fake("a", [R("a", "http://www.example.com/page/?utm_source=x", "A title", "short")]),
        "b": fake("b", [R("b", "https://example.com/page#top", "B title", "a much longer snippet")]),
    }
    resp = aggregator.search_with_meta("q", engines=engines, use_cache=False)
    assert len(resp["results"]) == 1
    r = resp["results"][0]
    assert r["engines"] == ["a", "b"]
    assert r["snippet"] == "a much longer snippet"
    assert "utm_" not in r["url"]


def test_tracking_params_stripped_from_output_url():
    engines = {"a": fake("a", [R("a", "https://example.com/p?id=7&utm_campaign=z&fbclid=abc")])}
    resp = aggregator.search_with_meta("q", engines=engines, use_cache=False)
    url = resp["results"][0]["url"]
    assert url.startswith("https://example.com/p")
    assert "utm_" not in url and "fbclid" not in url
    assert "id=7" in url


def test_invalid_urls_skipped():
    engines = {"a": fake("a", [R("a", "javascript:alert(1)"), R("a", ""), R("a", "https://example.com/ok")])}
    resp = aggregator.search_with_meta("q", engines=engines, use_cache=False)
    assert [r["url"] for r in resp["results"]] == ["https://example.com/ok"]


# --- scoring -------------------------------------------------------------

def test_multi_engine_result_outranks_single_engine_first():
    engines = {
        "a": fake("a", [R("a", "https://solo.example/1"), R("a", "https://shared.example/x")]),
        "b": fake("b", [R("b", "https://other.example/1"), R("b", "https://shared.example/x")]),
    }
    resp = aggregator.search_with_meta("q", engines=engines, use_cache=False)
    assert resp["results"][0]["url"] == "https://shared.example/x"
    # 10*2 + (10-1)*0.5 + (10-1)*0.5 = 29.0
    assert resp["results"][0]["score"] == 29.0
    # single-engine #1 scores 10 + 5 = 15
    assert {r["url"]: r["score"] for r in resp["results"]}["https://solo.example/1"] == 15.0


def test_score_ties_broken_by_position_then_url():
    engines = {"a": fake("a", [R("a", "https://z.example/"), R("a", "https://a.example/")])}
    resp = aggregator.search_with_meta("q", engines=engines, use_cache=False)
    assert [r["url"] for r in resp["results"]] == ["https://z.example/", "https://a.example/"]


def test_title_and_primary_engine_from_best_position():
    engines = {
        "a": fake("a", [R("a", "https://x.example/1"), R("a", "https://x.example/p", "from a")]),
        "b": fake("b", [R("b", "https://x.example/p", "from b")]),
    }
    resp = aggregator.search_with_meta("q", engines=engines, use_cache=False)
    shared = next(r for r in resp["results"] if r["url"].endswith("/p"))
    assert shared["title"] == "from b"
    assert shared["engine"] == "b"
    assert shared["engines"] == ["a", "b"]


def test_search_returns_results_list():
    engines = {"a": fake("a", [R("a", "https://example.com/")])}
    out = aggregator.search("q", engines=engines, use_cache=False)
    assert isinstance(out, list) and out[0]["url"] == "https://example.com/"


# --- failures and timeouts ----------------------------------------------

def test_engine_exception_goes_to_failed_others_returned():
    def boom(query, **kw):
        raise RuntimeError("secret query text")

    engines = {"bad": boom, "good": fake("good", [R("good", "https://example.com/1")])}
    resp = aggregator.search_with_meta("q", engines=engines, use_cache=False)
    assert resp["engines_failed"] == ["bad"]
    assert resp["engines_ok"] == ["good"]
    assert len(resp["results"]) == 1


def test_empty_engine_result_is_ok_not_failed():
    engines = {"a": fake("a", [])}
    resp = aggregator.search_with_meta("q", engines=engines, use_cache=False)
    assert resp["engines_ok"] == ["a"] and resp["engines_failed"] == []
    assert resp["results"] == []


def test_hung_engine_does_not_block():
    def slow(query, **kw):
        time.sleep(5)
        return [R("slow", "https://slow.example/")]

    engines = {"slow": slow, "fast": fake("fast", [R("fast", "https://example.com/1")])}
    t0 = time.monotonic()
    resp = aggregator.search_with_meta("q", engines=engines, timeout=0.5, use_cache=False)
    assert time.monotonic() - t0 < 1.5
    assert resp["engines_failed"] == ["slow"]
    assert resp["engines_ok"] == ["fast"]
    assert len(resp["results"]) == 1


def test_engines_run_in_parallel():
    def nap(name):
        def fn(query, **kw):
            time.sleep(0.4)
            return [R(name, f"https://{name}.example/")]
        return fn

    engines = {n: nap(n) for n in ("a", "b", "c")}
    t0 = time.monotonic()
    resp = aggregator.search_with_meta("q", engines=engines, timeout=2.0, use_cache=False)
    assert time.monotonic() - t0 < 1.0
    assert len(resp["results"]) == 3


# --- empty query ---------------------------------------------------------

@pytest.mark.parametrize("q", ["", "   ", "\n\t"])
def test_empty_query_makes_no_engine_calls(q):
    calls: list = []
    engines = {"a": fake("a", [R("a", "https://example.com/")], calls)}
    resp = aggregator.search_with_meta(q, engines=engines)
    assert calls == []
    assert resp["results"] == [] and resp["engines_ok"] == [] and resp["engines_failed"] == []
    assert resp["cached"] is False


# --- cache ---------------------------------------------------------------

def test_cache_hit_on_second_call():
    calls: list = []
    engines = {"a": fake("a", [R("a", "https://example.com/1")], calls)}
    first = aggregator.search_with_meta("hello world", engines=engines)
    second = aggregator.search_with_meta("hello world", engines=engines)
    assert first["cached"] is False
    assert second["cached"] is True
    assert calls == ["a"]
    assert second["results"] == first["results"]
    assert isinstance(second["elapsed_ms"], float)


def test_cache_result_not_mutable_by_caller():
    engines = {"a": fake("a", [R("a", "https://example.com/1")])}
    aggregator.search_with_meta("q", engines=engines)
    hit = aggregator.search_with_meta("q", engines=engines)
    hit["results"].clear()
    again = aggregator.search_with_meta("q", engines=engines)
    assert len(again["results"]) == 1


def test_use_cache_false_bypasses_cache():
    calls: list = []
    engines = {"a": fake("a", [R("a", "https://example.com/1")], calls)}
    aggregator.search_with_meta("q", engines=engines, use_cache=False)
    aggregator.search_with_meta("q", engines=engines, use_cache=False)
    assert calls == ["a", "a"]


def test_all_failed_response_not_cached():
    calls: list = []

    def boom(query, **kw):
        calls.append("boom")
        raise RuntimeError("x")

    engines = {"bad": boom}
    r1 = aggregator.search_with_meta("q", engines=engines)
    r2 = aggregator.search_with_meta("q", engines=engines)
    assert r1["engines_failed"] == ["bad"] and r1["results"] == []
    assert r2["cached"] is False
    assert calls == ["boom", "boom"]


def test_empty_results_not_cached():
    calls: list = []
    engines = {"a": fake("a", [], calls)}
    aggregator.search_with_meta("q", engines=engines)
    aggregator.search_with_meta("q", engines=engines)
    assert calls == ["a", "a"]


def test_default_engines_used_when_none(monkeypatch):
    import search.engines as eng

    calls: list = []
    monkeypatch.setattr(eng, "get_enabled_engines",
                        lambda: {"a": fake("a", [R("a", "https://example.com/")], calls)})
    resp = aggregator.search_with_meta("q", use_cache=False)
    assert calls == ["a"] and resp["engines_ok"] == ["a"]
