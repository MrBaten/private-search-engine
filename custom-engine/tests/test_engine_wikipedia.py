"""Offline tests for the Wikipedia engine."""
from __future__ import annotations

from typing import Any

import pytest

from search.engines import wikipedia


class FakeResp:
    """Minimal response stand-in."""

    def __init__(self, payload: Any = None, exc: Exception | None = None) -> None:
        self._payload = payload
        self._exc = exc

    def json(self) -> Any:
        if self._exc:
            raise self._exc
        return self._payload


def _install(monkeypatch: pytest.MonkeyPatch, resp: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake(url: str, **kw: Any) -> Any:
        calls.append({"url": url, **kw})
        return resp

    monkeypatch.setattr("search.engines.base.fetch", fake)
    return calls


FIXTURE = {
    "batchcomplete": True,
    "query": {
        "search": [
            {
                "title": "Python (programming language)",
                "snippet": 'The <span class="searchmatch">Python</span> language &amp; its &quot;docs&quot;',
            },
            {
                "title": "C++ & Rust: Q/A",
                "snippet": "Plain <b>bold</b>   text\nhere",
            },
            {"title": "Café Münster", "snippet": ""},
        ]
    },
}


def test_parse_three_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeResp(FIXTURE))
    res = wikipedia.search("python")
    assert [r["title"] for r in res] == [
        "Python (programming language)", "C++ & Rust: Q/A", "Café Münster"]
    assert res[0]["url"] == "https://en.wikipedia.org/wiki/Python_(programming_language)"
    assert res[1]["url"] == "https://en.wikipedia.org/wiki/C%2B%2B_%26_Rust:_Q/A"
    assert res[2]["url"] == "https://en.wikipedia.org/wiki/Caf%C3%A9_M%C3%BCnster"
    assert res[0]["snippet"] == 'The Python language & its "docs"'
    assert res[1]["snippet"] == "Plain bold text here"
    assert res[2]["snippet"] == ""
    assert all(r["engine"] == "wikipedia" == wikipedia.NAME for r in res)


def test_legacy_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeResp({"query": {"search": {"0": {"title": "A b", "snippet": "x"}}}}))
    res = wikipedia.search("a")
    assert res[0]["url"] == "https://en.wikipedia.org/wiki/A_b"


def test_language_de(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, FakeResp(FIXTURE))
    res = wikipedia.search("python", language="de")
    assert calls[0]["url"].startswith("https://de.wikipedia.org/")
    assert res[0]["url"].startswith("https://de.wikipedia.org/wiki/")


@pytest.mark.parametrize("bad", ["xx1", "EN", "", "english", "../"])
def test_invalid_language_falls_back(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    calls = _install(monkeypatch, FakeResp(FIXTURE))
    wikipedia.search("python", language=bad)
    assert calls[0]["url"].startswith("https://en.wikipedia.org/")


def test_fetch_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, None)
    assert wikipedia.search("python") == []


def test_json_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeResp(exc=ValueError("bad json")))
    assert wikipedia.search("python") == []


def test_api_error_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeResp({"error": {"code": "x", "info": "y"}}))
    assert wikipedia.search("python") == []


@pytest.mark.parametrize("payload", [None, [], {}, {"query": None}, {"query": {"search": "x"}}])
def test_malformed_payloads(monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    _install(monkeypatch, FakeResp(payload))
    assert wikipedia.search("python") == []


@pytest.mark.parametrize("q", ["", "   "])
def test_empty_query_no_fetch(monkeypatch: pytest.MonkeyPatch, q: str) -> None:
    calls = _install(monkeypatch, FakeResp(FIXTURE))
    assert wikipedia.search(q) == []
    assert calls == []


def test_capped_at_ten(monkeypatch: pytest.MonkeyPatch) -> None:
    hits = [{"title": f"T{i}", "snippet": "s"} for i in range(25)]
    _install(monkeypatch, FakeResp({"query": {"search": hits}}))
    res = wikipedia.search("t")
    assert len(res) == 10
    assert res[0]["title"] == "T0"


def test_descriptive_user_agent_and_params(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, FakeResp(FIXTURE))
    wikipedia.search("hello world")
    headers = calls[0]["headers"]
    assert headers["User-Agent"].startswith("PrivateSearch/1.0")
    assert headers["Accept"] == "application/json"
    p = calls[0]["params"]
    assert p["action"] == "query" and p["list"] == "search"
    assert p["srsearch"] == "hello world" and p["srlimit"] == 10
