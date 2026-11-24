"""End-to-end tests through the real Flask app, mapped to the 8 client requirements."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from app import create_app
from search import aggregator

ENGINE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = ENGINE_ROOT.parent
NEEDLE = "test123unique"


@pytest.fixture
def pse_app(pse_fake_engines):
    """Real Flask app wired to the fake engines with caching enabled."""
    return create_app({"TESTING": True, "ENGINES": pse_fake_engines, "USE_CACHE": True})


@pytest.fixture
def pse_client(pse_app):
    return pse_app.test_client()


def _post(client, query):
    return client.post("/search", json={"query": query})


# (1) ------------------------------------------------------------------
def test_req1_json_search_returns_at_least_five_results(pse_client):
    resp = _post(pse_client, "python")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] >= 5
    assert len(data["results"]) == data["count"]
    assert sorted(data["engines_ok"]) == ["fake_a", "fake_b"]


# (2) ------------------------------------------------------------------
def _scan_for_needle(roots, since):
    hits = []
    skip_dirs = {str(ENGINE_ROOT / "tests"), ".git"}
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath in skip_dirs or any(s in Path(dirpath).parts for s in (".git",)):
                dirnames[:] = []
                continue
            for name in filenames:
                p = Path(dirpath) / name
                try:
                    if p.stat().st_mtime < since:
                        continue
                    if NEEDLE.encode() in p.read_bytes():
                        hits.append(str(p))
                except OSError:
                    continue
    return hits


def test_req2_query_never_logged_printed_or_written(pse_client, caplog, capfd):
    since = time.time() - 1
    caplog.set_level(logging.DEBUG)
    logging.getLogger().setLevel(logging.DEBUG)
    resp = _post(pse_client, NEEDLE)
    assert resp.status_code == 200
    form = pse_client.post("/search", data={"q": NEEDLE, "safe": "1", "lang": "en", "cat": "all"})
    assert form.status_code == 200
    pse_client.get("/search?q=" + NEEDLE)

    assert NEEDLE not in caplog.text
    assert all(NEEDLE not in r.getMessage() for r in caplog.records)
    out, err = capfd.readouterr()
    assert NEEDLE not in out
    assert NEEDLE not in err

    hits = _scan_for_needle([ENGINE_ROOT, tempfile.gettempdir()], since)
    assert hits == []


# (3) ------------------------------------------------------------------
def test_req3_duplicate_url_appears_once_with_both_engines(pse_client):
    data = _post(pse_client, "python").get_json()
    docs = [r for r in data["results"] if "example.org/docs/python" in r["url"]]
    assert len(docs) == 1
    assert docs[0]["engines"] == ["fake_a", "fake_b"]
    home = [r for r in data["results"] if r["url"].startswith(("https://www.example.com/python", "http://example.com/python"))]
    assert len(home) == 1
    assert home[0]["engines"] == ["fake_a", "fake_b"]
    assert data["count"] == 6


# (4) ------------------------------------------------------------------
def test_req4_tracking_parameters_removed(pse_client):
    data = _post(pse_client, "python").get_json()
    assert data["results"]
    for r in data["results"]:
        for bad in ("utm_", "fbclid", "gclid"):
            assert bad not in r["url"]
    # non-tracking params survive
    assert any("page=2" in r["url"] for r in data["results"])


# (5) ------------------------------------------------------------------
def test_req5_second_identical_query_is_cached_and_fast(pse_app):
    calls = {"n": 0}

    def counting(query, *, timeout=3.0, safe_search=True, language="en"):
        calls["n"] += 1
        return [{"title": f"R{i}", "url": f"https://example.com/{i}", "snippet": "s", "engine": "counting"}
                for i in range(6)]

    app = create_app({"TESTING": True, "ENGINES": {"counting": counting}, "USE_CACHE": True})
    client = app.test_client()
    first = _post(client, "cache me").get_json()
    assert first["cached"] is False
    t0 = time.perf_counter()
    second = _post(client, "cache me").get_json()
    elapsed = time.perf_counter() - t0
    assert second["cached"] is True
    assert elapsed < 0.100
    assert calls["n"] == 1
    assert second["results"] == first["results"]


# (6) ------------------------------------------------------------------
def test_req6_frontend_is_clean(pse_client):
    resp = pse_client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "PrivateSearch" in html
    low = html.lower()
    assert "google-analytics" not in low
    assert "fonts.googleapis" not in low
    for tag in re.findall(r"<(?:script|link|img|iframe)\b[^>]*>", html, flags=re.I):
        for m in re.finditer(r"""\b(?:src|href)\s*=\s*["']?\s*(https?:)?//""", tag, flags=re.I):
            pytest.fail(f"external resource reference: {tag}")
    assert "<style" not in low
    assert resp.headers.get("Referrer-Policy") == "no-referrer"
    assert "Set-Cookie" not in resp.headers


def test_req6_get_search_redirects_without_searching(pse_client):
    resp = pse_client.get("/search?q=leak")
    assert resp.status_code == 303
    assert "leak" not in resp.headers.get("Location", "")


# (7) ------------------------------------------------------------------
@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not installed")
@pytest.mark.parametrize("subdir", ["", "searxng"])
def test_req7_docker_compose_config_valid(subdir):
    cwd = REPO_ROOT / subdir if subdir else REPO_ROOT
    if not (cwd / "docker-compose.yml").exists():
        pytest.fail(f"missing {cwd / 'docker-compose.yml'}")
    proc = subprocess.run(["docker", "compose", "config", "-q"], cwd=cwd,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


# (8) ------------------------------------------------------------------
def test_req8_engine_failure_raising_engine_still_returns_results(pse_fake_engines, pse_failing_engine):
    engines = dict(pse_fake_engines)
    engines["broken"] = pse_failing_engine()
    client = create_app({"TESTING": True, "ENGINES": engines, "USE_CACHE": False}).test_client()
    data = _post(client, "python").get_json()
    assert data["count"] >= 5
    assert data["engines_failed"] == ["broken"]
    assert "broken" not in data["engines_ok"]


def test_req8_engine_failure_timeout_engine_listed_failed(pse_fake_engines, pse_slow_engine):
    engines = dict(pse_fake_engines)
    engines["slow"] = pse_slow_engine(3.0)
    t0 = time.perf_counter()
    resp = aggregator.search_with_meta("python", engines=engines, timeout=0.5, use_cache=False)
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0
    assert "slow" in resp["engines_failed"]
    assert len(resp["results"]) >= 5
    assert not any(r["url"].startswith("https://slow.example.com") for r in resp["results"])


# demo mode ----------------------------------------------------------------
def test_demo_mode_end_to_end(monkeypatch):
    monkeypatch.setenv("PSE_DEMO_MODE", "1")
    monkeypatch.setenv("PSE_ENGINES", "demo_a,demo_b")
    client = create_app({"TESTING": True, "USE_CACHE": False}).test_client()
    data = _post(client, "python").get_json()
    assert data["count"] >= 5
    assert sorted(data["engines_ok"]) == ["demo_a", "demo_b"]
    urls = [r["url"] for r in data["results"]]
    assert len(urls) == len(set(urls))
    for u in urls:
        assert not re.search(r"utm_|fbclid|gclid", u)
    assert all(r["title"].startswith("[Demo]") for r in data["results"])
    shared = [r for r in data["results"] if len(r["engines"]) == 2]
    assert shared, "overlapping demo URLs should merge"


def test_engine_failure_fault_injection_engine_end_to_end(monkeypatch):
    """PSE_ENGINES=...,demo_down: the always-raising engine is reported as failed
    while the demo engines still return deduplicated results."""
    from search import aggregator
    from search.engines import get_enabled_engines

    monkeypatch.setenv("PSE_ENGINES", "demo_a,demo_b,demo_down")
    engines = get_enabled_engines()
    assert set(engines) == {"demo_a", "demo_b", "demo_down"}
    resp = aggregator.search_with_meta("fault injection", engines=engines, use_cache=False)
    assert resp["engines_failed"] == ["demo_down"]
    assert len(resp["results"]) >= 5


def test_fault_engine_not_enabled_by_demo_mode_alone(monkeypatch):
    """demo_down must never be switched on implicitly."""
    from search.engines import get_enabled_engines

    monkeypatch.setenv("PSE_DEMO_MODE", "1")
    assert "demo_down" not in get_enabled_engines()
