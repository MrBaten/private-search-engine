"""Offline tests for search.privacy."""

from __future__ import annotations

import logging

import pytest
from flask import Flask, make_response

from search import privacy


# --- strip_tracking_params -------------------------------------------------


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://a.com/p?utm_source=x&utm_medium=y", "https://a.com/p"),
        ("https://a.com/p?fbclid=abc", "https://a.com/p"),
        ("https://a.com/p?gclid=1&id=7", "https://a.com/p?id=7"),
        ("https://a.com/p?UTM_Source=x&id=7", "https://a.com/p?id=7"),
        ("https://a.com/p?FBCLID=1&Id=7", "https://a.com/p?Id=7"),
        ("https://a.com/p?msclkid=1&mc_eid=2&_hsenc=3", "https://a.com/p"),
        ("https://a.com/p?mtm_campaign=1&pk_kwd=2&hsa_net=3", "https://a.com/p"),
    ],
)
def test_strips_tracking(url: str, expected: str) -> None:
    assert privacy.strip_tracking_params(url) == expected


def test_keeps_non_tracking_in_order_with_blank_values() -> None:
    url = "https://a.com/p?z=1&utm_source=x&a=&flag&m=2%20x"
    assert privacy.strip_tracking_params(url) == "https://a.com/p?z=1&a=&flag&m=2%20x"


def test_fragment_preserved() -> None:
    assert (
        privacy.strip_tracking_params("https://a.com/p?utm_source=x&q=1#sec")
        == "https://a.com/p?q=1#sec"
    )
    assert privacy.strip_tracking_params("https://a.com/p?fbclid=1#sec") == "https://a.com/p#sec"


def test_only_tracking_has_no_trailing_question_mark() -> None:
    out = privacy.strip_tracking_params("https://a.com/?gclid=1&utm_term=2")
    assert out == "https://a.com/"
    assert not out.endswith("?")


def test_untouched_urls_returned_identically() -> None:
    for url in ("https://a.com/p?q=1", "https://a.com/", "https://a.com/p?"):
        assert privacy.strip_tracking_params(url) == url


@pytest.mark.parametrize("bad", ["", "not a url", "http://[bad?utm_source=1", None, 42, b"x?utm_a=1"])
def test_invalid_input_returns_unchanged(bad: object) -> None:
    assert privacy.strip_tracking_params(bad) == bad  # type: ignore[arg-type]


def test_constants_are_lowercase_and_nonempty() -> None:
    assert all(p == p.lower() for p in privacy.TRACKING_PARAMS)
    for name in ("fbclid", "gclid", "msclkid", "mc_cid", "_hsenc", "mkt_tok"):
        assert name in privacy.TRACKING_PARAMS
    assert "utm_" in privacy.TRACKING_PREFIXES


# --- sanitize_query --------------------------------------------------------


def test_sanitize_non_str() -> None:
    assert privacy.sanitize_query(None) == ""
    assert privacy.sanitize_query(123) == ""
    assert privacy.sanitize_query(["a"]) == ""
    assert privacy.sanitize_query(b"abc") == ""


def test_sanitize_control_chars_and_whitespace() -> None:
    assert privacy.sanitize_query("  hello\x00 \x07world\r\n\t  again  ") == "hello world again"
    assert privacy.sanitize_query("a b\x7fc") == "a b c"


def test_sanitize_truncation() -> None:
    assert privacy.sanitize_query("a" * 1000) == "a" * 512
    assert privacy.sanitize_query("abc def", max_len=4) == "abc"
    assert len(privacy.sanitize_query("x " * 1000, max_len=10)) <= 10


def test_sanitize_keeps_unicode() -> None:
    assert privacy.sanitize_query("  বাংলা   ভাষা ") == "বাংলা ভাষা"


# --- apply_security_headers ------------------------------------------------


def test_security_headers_on_real_response() -> None:
    app = Flask(__name__)

    @app.route("/")
    def index():  # type: ignore[no-untyped-def]
        resp = make_response("ok")
        resp.set_cookie("a", "1")
        resp.set_cookie("b", "2")
        return resp

    app.after_request(privacy.apply_security_headers)
    resp = app.test_client().get("/")
    for name, value in privacy.SECURITY_HEADERS.items():
        assert resp.headers[name] == value
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert resp.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert resp.headers["X-Permitted-Cross-Domain-Policies"] == "none"
    assert "Set-Cookie" not in resp.headers
    assert resp.headers.getlist("Set-Cookie") == []
    assert resp.headers["Cache-Control"] == "no-store, max-age=0"
    assert resp.headers["Pragma"] == "no-cache"


def test_apply_returns_same_response() -> None:
    app = Flask(__name__)
    with app.app_context():
        resp = app.response_class("x")
        assert privacy.apply_security_headers(resp) is resp


# --- no_log ----------------------------------------------------------------


def test_no_log_idempotent() -> None:
    privacy.no_log()
    privacy.no_log()
    assert logging.getLogger("werkzeug").level >= logging.ERROR
    assert logging.getLogger("gunicorn.access").disabled is True


def test_no_log_never_prints(capsys: pytest.CaptureFixture[str]) -> None:
    privacy.no_log()
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""
