# PLAN.md — private-search-engine

Status: planning phase complete. This file is the single source of truth for
architecture, file ownership and interface contracts. Subagents MUST NOT edit
files they do not own. If a contract seems wrong, implement to the contract
and report the concern in your final message — do not change other files.

---

## 1. Decision: SearXNG vs custom aggregator

**Build both.**

| Component | Purpose | Why |
|---|---|---|
| `searxng/` (official `searxng/searxng` image) | Production-grade meta-search (70+ engines, maintained scrapers, built-in limiter) | Battle-tested, maintained by a community that fixes broken scrapers |
| `custom-engine/` (Python 3.11 + Flask) | Lightweight, readable aggregator for portfolio/demo | Shows meta-search, ranking, dedup, privacy engineering end-to-end in ~1k LOC |

Nginx fronts both: `/` → custom engine, `/searxng/` → SearXNG.

## 2. Architecture

```
Browser ──POST /search──► Nginx :80 ──► custom-engine :5000 (gunicorn, Flask)
                            │                 │
                            │                 ├─ privacy.py   (headers, URL cleaning, no logging)
                            │                 ├─ cache.py     (in-memory TTL+LRU, hashed keys)
                            │                 └─ aggregator.py ──ThreadPool(3s)──► engines/
                            │                                        ├ duckduckgo.py (HTML scrape)
                            │                                        ├ wikipedia.py  (MediaWiki API)
                            │                                        ├ brave.py      (HTML scrape)
                            │                                        ├ bing.py       (HTML scrape)
                            │                                        └ demo.py       (offline fixtures, opt-in)
                            └──/searxng/──► searxng :8080 ──► valkey (limiter state, in-memory)
```

Request flow: form POST (query never in URL/history) → `app.py` sanitises →
`aggregator.search_with_meta()` → cache hit? return : fan out to engines in
parallel, each capped at 3 s → normalise + strip tracking params → dedup →
score → cache → render `results.html` (or JSON).

## 3. File structure & ownership

| Owner | Files (exclusive) |
|---|---|
| **Main agent (planner)** | `PLAN.md`, `custom-engine/search/__init__.py`, `custom-engine/search/types.py`, `custom-engine/search/engines/__init__.py`, `custom-engine/search/engines/base.py`, `custom-engine/search/engines/demo.py` |
| **S1 Aggregator** | `custom-engine/search/aggregator.py`, `custom-engine/tests/test_aggregator.py` |
| **S2 DuckDuckGo** | `custom-engine/search/engines/duckduckgo.py`, `custom-engine/tests/test_engine_duckduckgo.py` |
| **S3 Wikipedia** | `custom-engine/search/engines/wikipedia.py`, `custom-engine/tests/test_engine_wikipedia.py` |
| **S4 Brave** | `custom-engine/search/engines/brave.py`, `custom-engine/tests/test_engine_brave.py` |
| **S5 Bing** | `custom-engine/search/engines/bing.py`, `custom-engine/tests/test_engine_bing.py` |
| **S6 Privacy** | `custom-engine/search/privacy.py`, `custom-engine/tests/test_privacy.py` |
| **S7 Cache** | `custom-engine/search/cache.py`, `custom-engine/tests/test_cache.py` |
| **S8 App** | `custom-engine/app.py`, `custom-engine/tests/test_app.py` |
| **S9 Frontend** | `custom-engine/templates/index.html`, `custom-engine/templates/results.html`, `custom-engine/templates/base.html`, `custom-engine/static/style.css`, `custom-engine/static/script.js`, `custom-engine/static/favicon.svg` |
| **S10 SearXNG** | `searxng/docker-compose.yml`, `searxng/settings.yml`, `searxng/limiter.toml`, `searxng/.env`, `searxng/.env.example` |
| **S11 Infra** | `nginx/nginx.conf`, `docker-compose.yml` (root), `custom-engine/Dockerfile`, `custom-engine/.dockerignore`, `custom-engine/requirements.txt`, `run.sh` |
| **S12 Tests** | `test.sh`, `custom-engine/pytest.ini`, `custom-engine/requirements-dev.txt`, `custom-engine/tests/__init__.py`, `custom-engine/tests/conftest.py`, `custom-engine/tests/test_integration.py` |
| **S13 Docs** | `README.md`, `.gitignore`, `LICENSE` |

Final tree:
```
private-search-engine/
  custom-engine/
    app.py  Dockerfile  .dockerignore  requirements.txt  requirements-dev.txt  pytest.ini
    search/ __init__.py types.py aggregator.py privacy.py cache.py
            engines/ __init__.py base.py duckduckgo.py wikipedia.py brave.py bing.py demo.py
    templates/ base.html index.html results.html
    static/ style.css script.js favicon.svg
    tests/ __init__.py conftest.py test_*.py
  searxng/ docker-compose.yml settings.yml limiter.toml .env .env.example
  nginx/nginx.conf
  docker-compose.yml  README.md  run.sh  test.sh  PLAN.md  .gitignore  LICENSE
```

## 4. Global rules (every subagent)

- Python **3.11 compatible** (no 3.12-only syntax). Type hints + docstrings on every public function. `from __future__ import annotations` allowed.
- Import style inside `custom-engine/`: top-level package is `search` (cwd/pythonpath = `custom-engine/`). e.g. `from search import privacy`, `from search.types import Result`.
- **Never** `print()` or log a user query, and never write it to disk. Use `logging.getLogger(__name__)` only for non-query diagnostics (e.g. `"bing: HTTP 429, skipping"`). Never include the query or a URL that contains it in a log message or exception message.
- No API keys, no cookies, no analytics, no external CDN/fonts.
- No `TODO`, `FIXME`, `XXX` anywhere.
- Deps available: `flask`, `requests`, `beautifulsoup4`, `pytest`, stdlib. Nothing else in runtime code (gunicorn only in Docker CMD).
- Tests must run **offline** (no real network). Mock HTTP by monkeypatching `search.engines.base.fetch` (see §5.2).
- Run your own tests before reporting: `cd custom-engine && python -m pytest tests/<your_test_file> -q` (with `PYTHONPATH=.` if pytest.ini isn't there yet).
- The workspace cannot reach duckduckgo/bing/brave/wikipedia — do not try to verify live; say so in your report.

## 5. Interface contracts

### 5.1 `search/types.py` (written by main agent — read it)
```python
class Result(TypedDict):          # what an engine returns
    title: str; url: str; snippet: str; engine: str
class RankedResult(TypedDict):    # what the aggregator returns
    title: str; url: str; snippet: str
    engine: str                   # primary engine (best position / first seen)
    engines: list[str]            # all engines that returned it, sorted by name
    score: float
class SearchResponse(TypedDict):
    results: list[RankedResult]; engines_ok: list[str]; engines_failed: list[str]
    cached: bool; elapsed_ms: float
EngineFn = Callable[..., list[Result]]
```

### 5.2 `search/engines/base.py` (written by main agent — read it)
- `USER_AGENTS: tuple[str, ...]`, `random_user_agent() -> str`
- `default_headers(user_agent: str | None = None) -> dict[str, str]` — browser-like headers incl. `DNT: 1`, no cookies.
- `fetch(url, *, params=None, data=None, method="GET", headers=None, timeout=3.0) -> requests.Response | None`
  Returns `None` on any exception, timeout, HTTP 429, HTTP 403, or any non-2xx. Never raises. Uses a fresh `requests.Session` per call with cookies disabled. Logs only `host + status`, never the URL query.
- `clean_text(s: str | None) -> str` — collapse whitespace, strip.
- `MAX_RESULTS_PER_ENGINE = 10`

**Engines MUST call it as `base.fetch(...)`** (`from search.engines import base`) so tests can `monkeypatch.setattr("search.engines.base.fetch", fake)`.

### 5.3 Engine modules (`duckduckgo.py`, `wikipedia.py`, `brave.py`, `bing.py`)
```python
NAME: str = "duckduckgo"   # | "wikipedia" | "brave" | "bing"
def search(query: str, *, timeout: float = 3.0, safe_search: bool = True,
           language: str = "en") -> list[Result]:
```
- Return at most `base.MAX_RESULTS_PER_ENGINE` results, in engine rank order, each with `engine=NAME`.
- Only absolute `http(s)` URLs. Unwrap engine redirect URLs (DDG `/l/?uddg=`, Bing `/ck/a?...&u=a1<base64>`) to the real destination. Skip ads/sponsored results.
- **Never raise.** Blocked / 429 / captcha / parse error / empty query → return `[]`.
- Do not strip tracking params here (aggregator does it).
- `language`: 2-letter code; validate with `^[a-z]{2}$`, fall back to `"en"`. Only use engine parameters you are confident exist; otherwise omit them and say so in a code comment.

### 5.4 `search/engines/__init__.py` (main agent)
- `ALL_ENGINES: dict[str, EngineFn]` — the 4 real engines.
- `get_enabled_engines() -> dict[str, EngineFn]` — env `PSE_ENGINES` (comma list, default all 4 real) + if `PSE_DEMO_MODE` in `{"1","true","yes"}` also `demo_a`, `demo_b`.
- `demo.py`: two offline fixture engines `demo_a` / `demo_b` with overlapping `example.com/org` URLs (some carrying `utm_*`/`fbclid`) so dedup + tracking stripping are exercised end-to-end. Titles are prefixed `[Demo]` — never presented as real results.
- Integration addition: `demo_down` (fault injection, always raises) is enabled only when named in `PSE_ENGINES`; `test.sh` uses it so T8 proves engine failure against the running app.

### 5.5 `search/privacy.py` (S6)
```python
TRACKING_PARAMS: frozenset[str]          # exact names, lowercase
TRACKING_PREFIXES: tuple[str, ...]       # e.g. ("utm_", "pk_", "mtm_", "hsa_")
def strip_tracking_params(url: str) -> str   # keep order of remaining params; keep fragment; never raise (return input on parse error)
def sanitize_query(raw: object, max_len: int = 512) -> str   # str(), strip control chars, collapse spaces, truncate; "" if not str/None
SECURITY_HEADERS: dict[str, str]
def apply_security_headers(response) -> response   # Flask Response; sets SECURITY_HEADERS, removes Set-Cookie, sets Cache-Control: no-store
def no_log() -> None   # idempotent: silence werkzeug request log (level ERROR), disable Flask/werkzeug access lines; never prints
```
SECURITY_HEADERS must include at least:
`Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'`,
`Permissions-Policy: interest-cohort=(), browsing-topics=(), geolocation=(), camera=(), microphone=()`,
`Cross-Origin-Opener-Policy: same-origin`.
Every privacy decision gets a comment explaining *why*.

### 5.6 `search/cache.py` (S7)
```python
class TTLLRUCache:
    def __init__(self, maxsize: int = 100, ttl: float = 600.0, clock: Callable[[], float] = time.monotonic): ...
    def get(self, key: str) -> Any | None      # None on miss/expired (expired entry removed); hit refreshes LRU position (not TTL)
    def set(self, key: str, value: Any) -> None  # evicts LRU when > maxsize
    def clear(self) -> None
    def __len__(self) -> int; def __contains__(self, key) -> bool
default_cache: TTLLRUCache            # maxsize=100, ttl=600
def make_key(query: str, safe_search: bool, language: str) -> str  # sha256 hex of normalised (casefold, collapsed-space) query + flags; raw query never stored as key
def get(key: str) -> Any | None; def set(key: str, value: Any) -> None; def clear() -> None   # proxies to default_cache
```
Thread-safe (`threading.Lock`), `collections.OrderedDict`. Memory only — never touch disk.

### 5.7 `search/aggregator.py` (S1)
```python
DEFAULT_TIMEOUT = 3.0
def normalize_url(url: str) -> str   # dedup key: strip tracking params (privacy.strip_tracking_params), lowercase scheme+host,
                                     # drop leading "www.", http→https, drop fragment, drop trailing "/" from path (keep "/" root as ""),
                                     # sort remaining query params, drop default ports
def search_with_meta(query: str, *, engines: Mapping[str, EngineFn] | None = None, timeout: float = DEFAULT_TIMEOUT,
                     safe_search: bool = True, language: str = "en", use_cache: bool = True) -> SearchResponse
def search(query: str, **kwargs) -> list[RankedResult]   # = search_with_meta(...)["results"]
```
- `engines=None` → `search.engines.get_enabled_engines()`.
- Empty/whitespace query → empty response, no engine calls.
- Parallel: `ThreadPoolExecutor(max_workers=len(engines))`; collect with `concurrent.futures.wait(timeout=timeout)`; engines not done in time → `engines_failed`; then `executor.shutdown(wait=False, cancel_futures=True)` so a hung engine never blocks the response. Engine exceptions → `engines_failed` (defence in depth; engines shouldn't raise). An engine that returns `[]` counts as `engines_failed` too? **No** → it is `engines_ok` with zero results. Only exception/timeout → failed.
- Each returned URL: `privacy.strip_tracking_params()` before output (output `url` = cleaned original URL, not the normalised key).
- Dedup by `normalize_url`; merge: keep the longest non-empty snippet, title from the best-positioned occurrence.
- **Score** = `10 * n_engines + sum(max(0, 10 - position) * 0.5 for each occurrence)` where position is 0-based rank within that engine. Round to 2 dp. Sort by score desc, then best position asc, then url.
- Cache: key `cache.make_key(query, safe_search, language)`; store only when ≥1 engine succeeded and results non-empty. Cached response returns `cached=True` and fresh `elapsed_ms`.
- `engines_ok` / `engines_failed` sorted lists of names.

### 5.8 `app.py` (S8)
```python
def create_app(config: Mapping[str, Any] | None = None) -> Flask
app = create_app()        # module-level for gunicorn: "app:app"
if __name__ == "__main__": host=env HOST (default 127.0.0.1), port=env PORT (default 5000), debug=False
```
Config keys honoured: `TESTING`, `ENGINES` (Mapping[str, EngineFn] override → passed to aggregator), `USE_CACHE` (default True).
Routes:
- `GET /` → `index.html` (context: `safe_search=True, language="en"`).
- `POST /search`
  - JSON body `{"query": str, "safe_search"?: bool, "language"?: str}` (Content-Type application/json) → JSON
    `{"results": [RankedResult...], "count": int, "engines_ok": [...], "engines_failed": [...], "cached": bool, "elapsed_ms": float}` status 200. Missing/empty query → 400 `{"error": "query is required"}`. (Do not echo the query in JSON.)
  - Form body fields `q`, `safe` (`"1"`/`"0"`, default "1"), `lang` (default "en"), `cat` (`all|images|videos|news|wikipedia`, default all) → render `results.html`. Empty q → 303 redirect to `/`.
  - `cat=wikipedia` → filter to results whose `engines` contains "wikipedia". Other cats: backend returns All (UI notes "showing all results").
- `GET /search` → 303 redirect to `/` (queries must never be in URLs). Also `GET /search?q=...` → 303 to `/` and DOES NOT search.
- `GET /healthz` → `{"status": "ok"}`.
- `GET /robots.txt` → `User-agent: *\nDisallow: /search\n`.
- 404/405/500 handlers render minimal JSON or text, never including request data.
- `after_request` → `privacy.apply_security_headers`. Call `privacy.no_log()` in `create_app`.
- No `session`, no `SECRET_KEY` needed, `MAX_CONTENT_LENGTH = 16 KiB`.
- Context for `results.html`: `query` (str), `results` (list[RankedResult] each with extra key `domain` = hostname without "www."), `count`, `engines_ok`, `engines_failed`, `cached`, `elapsed_ms`, `category`, `safe_search` (bool), `language` (str), `categories` (list of (id, label)), `languages` (list of (code, label)).
- `languages` list (module constant `LANGUAGES`): en English, bn Bangla, de Deutsch, es Español, fr Français, hi Hindi, ja 日本語, pt Português. `CATEGORIES`: all All, images Images, videos Videos, news News, wikipedia Wikipedia.

### 5.9 Frontend (S9)
- `base.html` (layout, `{% block content %}`), `index.html` + `results.html` extend it.
- Must contain text `PrivateSearch` (logo). No external URLs at all (no Google Fonts, CDN, analytics). System font stack.
- **CSP is `script-src 'self'; style-src 'self'`** → NO inline `<script>`, NO `<style>`, NO `style="..."` attributes, NO `onclick=`. Pass state via `data-*` attributes.
- Search form: `method="post" action="/search"`, input `name="q"`, `autocomplete="off" spellcheck="false" autocapitalize="off" autocorrect="off"`, hidden inputs `safe`, `lang`, `cat`. `<meta name="referrer" content="no-referrer">`.
- Result links: `rel="noopener noreferrer nofollow"`, `referrerpolicy="no-referrer"`, show title, domain only (`r.domain`), snippet, engine badges (`r.engines`), score optional (small/muted).
- Filter tabs All/Images/Videos/News/Wikipedia: buttons that re-submit the same POST form with `cat` set (JS) — degrade gracefully without JS (each tab is a `<button type="submit" name="cat" value="...">` inside the form).
- Settings panel: Safe Search ON/OFF + Language `<select>` (from `languages`). Theme toggle (dark default). Preferences persisted only in `localStorage` (never sent to server except as hidden form fields; no cookies). JS wraps localStorage in try/catch.
- Status line: `count` results, `elapsed_ms`, "cached" badge, failed engines listed ("Bing unavailable").
- Mobile responsive (≤ 600px), accessible (labels, focus states, `aria-*`), `/` keyboard shortcut focuses search.
- Static URLs via `url_for('static', filename=...)`.

### 5.10 SearXNG (S10), Infra (S11), Tests (S12), Docs (S13) — see each agent brief; key cross-contract facts:
- Service names: `custom-engine` (port 5000, internal), `searxng` (8080), `valkey`, `nginx` (host port 80).
- Custom engine container: `gunicorn --workers 1 --threads 8 --bind 0.0.0.0:5000 app:app` (1 worker so the in-memory cache is shared; no access log).
- SearXNG env: `SEARXNG_BASE_URL`, `SEARXNG_SECRET` in `searxng/.env` (gitignored; `.env.example` committed). `run.sh` generates a random secret if `.env` is missing or still has the placeholder `change-me`.
- Nginx: `access_log off;` (privacy), security headers, `/searxng/` → searxng.
- `test.sh` runs from repo root, needs no Docker daemon (uses `docker compose config` only if `docker` CLI exists, else validates YAML with Python and warns), auto-detects network: offline → starts app with `PSE_DEMO_MODE=1`.

## 6. Integration update (2026-11-24, after a live test on a home connection)

- DuckDuckGo (HTML) answered with a bot challenge, Brave (HTML) with a
  captcha / HTTP 429, Bing (HTML) with unrelated "poisoned" results. Getting
  past bot detection (TLS impersonation, captcha solving, tracking-cookie
  sessions) is explicitly out of scope.
- `base.mark_unavailable(reason)` / `unavailable_reason()`: a thread-local
  signal set by `fetch()` (HTTP errors, 403/429) and by engines (challenge
  pages). `aggregator._call_engine` resets it per call and reports an engine
  that returned `[]` with a reason as **failed**, so the UI shows it as
  unavailable instead of "no matches".
- Bing relevance guard: if fewer than 30 % of results contain a query term
  (Unicode-aware tokenizer, works for Bangla), the page is dropped.
- New `marginalia.py` (official API, `MARGINALIA_API_KEY`, falls back to the
  shared `public` key) and Brave official-API mode (`BRAVE_API_KEY`).
