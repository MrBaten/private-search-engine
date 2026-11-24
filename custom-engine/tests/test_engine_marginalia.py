"""Tests for the Marginalia engine (official JSON API). Offline: fetch is mocked."""
from __future__ import annotations

from search.engines import base, marginalia

# Shape documented at https://about.marginalia-search.com/article/api/
PAYLOAD = {
    "query": "climate change effects",
    "license": "CC-BY-NC-SA 4.0",
    "results": [
        {"url": "https://example.org/climate", "title": "Climate effects", "description": "Overview  of\neffects."},
        {"url": "ftp://bad.example/", "title": "Bad scheme", "description": ""},
        {"url": "https://example.net/b", "title": "", "description": None},
        "not-a-dict",
    ],
}


class FakeResp:
    def __init__(self, payload, raise_json=False):
        self._payload = payload
        self._raise = raise_json
        self.status_code = 200

    def json(self):
        if self._raise:
            raise ValueError("bad json")
        return self._payload


def _patch(monkeypatch, resp):
    calls = []

    def fake(url, **kw):
        calls.append((url, kw))
        return resp

    monkeypatch.setattr("search.engines.base.fetch", fake)
    return calls


def test_parses_documented_shape(monkeypatch):
    calls = _patch(monkeypatch, FakeResp(PAYLOAD))
    res = marginalia.search("climate change effects")
    assert [r["url"] for r in res] == ["https://example.org/climate", "https://example.net/b"]
    assert res[0]["snippet"] == "Overview of effects."
    assert res[1]["title"] == "https://example.net/b"  # empty title falls back to URL
    assert all(r["engine"] == "marginalia" for r in res)
    url, kw = calls[0]
    assert url == "https://api2.marginalia-search.com/search"
    assert kw["params"]["query"] == "climate change effects"
    assert kw["params"]["nsfw"] == 1


def test_public_key_by_default_env_key_when_set(monkeypatch):
    calls = _patch(monkeypatch, FakeResp(PAYLOAD))
    marginalia.search("x")
    assert calls[-1][1]["headers"]["API-Key"] == "public"
    monkeypatch.setenv("MARGINALIA_API_KEY", "my-key")
    marginalia.search("x")
    assert calls[-1][1]["headers"]["API-Key"] == "my-key"


def test_safe_search_off(monkeypatch):
    calls = _patch(monkeypatch, FakeResp(PAYLOAD))
    marginalia.search("x", safe_search=False)
    assert calls[0][1]["params"]["nsfw"] == 0


def test_rate_limited_returns_empty(monkeypatch):
    _patch(monkeypatch, None)  # base.fetch returns None on HTTP 429
    assert marginalia.search("x") == []


def test_bad_json_marks_unavailable(monkeypatch):
    _patch(monkeypatch, FakeResp(None, raise_json=True))
    base.reset_unavailable()
    assert marginalia.search("x") == []
    assert base.unavailable_reason() == "invalid JSON response"


def test_odd_payloads(monkeypatch):
    for payload in (None, [], {"results": "nope"}, {}):
        _patch(monkeypatch, FakeResp(payload))
        assert marginalia.search("x") == []


def test_empty_query_no_request(monkeypatch):
    calls = _patch(monkeypatch, FakeResp(PAYLOAD))
    assert marginalia.search("  ") == []
    assert calls == []


def test_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr("search.engines.base.fetch", boom)
    assert marginalia.search("x") == []


def test_cap(monkeypatch):
    many = {"results": [{"url": f"https://s{i}.example/", "title": f"T{i}", "description": ""} for i in range(30)]}
    _patch(monkeypatch, FakeResp(many))
    assert len(marginalia.search("x")) == base.MAX_RESULTS_PER_ENGINE
