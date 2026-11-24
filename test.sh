#!/usr/bin/env bash
# End-to-end test runner: unit/integration tests, then black-box checks against a live app.
# Env: LIVE=1 force real network, LIVE=0 force offline demo mode, PORT (default 5055).
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
ROOT="$(pwd)"

if [ -t 1 ]; then G=$'\e[32m'; R=$'\e[31m'; Y=$'\e[33m'; B=$'\e[1m'; N=$'\e[0m'; else G=""; R=""; Y=""; B=""; N=""; fi
PASS=0; FAIL=0; WARN=0
pass() { PASS=$((PASS+1)); echo "${G}PASS${N} $1"; }
fail() { FAIL=$((FAIL+1)); echo "${R}FAIL${N} $1${2:+ -- $2}"; }
warn() { WARN=$((WARN+1)); echo "${Y}WARN${N} $1"; }
check() { # check "name" cmd...
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then pass "$name"; else fail "$name"; fi
}

PY="${PYTHON:-python3}"; command -v "$PY" >/dev/null 2>&1 || PY=python
PORT="${PORT:-5055}"
BASE="http://127.0.0.1:$PORT"
APP_PID=""
cleanup() { [ -n "$APP_PID" ] && kill "$APP_PID" >/dev/null 2>&1; wait "$APP_PID" 2>/dev/null; }
trap cleanup EXIT

echo "${B}== 0. Dependencies ==${N}"
if "$PY" -c "import flask, requests, bs4, pytest" 2>/dev/null; then
  pass "python deps importable (flask, requests, bs4, pytest)"
else
  fail "python deps missing" "run: pip install -r custom-engine/requirements-dev.txt"
  echo "${B}Summary:${N} $PASS passed, $FAIL failed, $WARN warnings"; exit 1
fi

# JSON helper: jq when present, python otherwise.  json_eval <jq-expr> <py-expr over d>
HAVE_JQ=0; command -v jq >/dev/null 2>&1 && HAVE_JQ=1
jget() { # jget '<jq filter>' '<python expr using d>'  (reads JSON on stdin)
  if [ "$HAVE_JQ" = 1 ]; then jq -r "$1"; else "$PY" -c "import sys,json;d=json.load(sys.stdin);print($2)"; fi
}

echo "${B}== 1. pytest (unit + integration) ==${N}"
PYTEST_OUT=$(cd custom-engine && "$PY" -m pytest 2>&1); PYTEST_RC=$?
printf '%s\n' "$PYTEST_OUT" | tail -n 25
if [ "$PYTEST_RC" -eq 0 ]; then pass "pytest suite"; else fail "pytest suite" "see output above (cd custom-engine && python -m pytest)"; fi

echo "${B}== 2. Network detection ==${N}"
ONLINE=0
case "${LIVE:-auto}" in
  1) ONLINE=1; echo "LIVE=1: assuming network is available" ;;
  0) ONLINE=0; echo "LIVE=0: forcing offline mode" ;;
  *) if curl -s -o /dev/null --max-time 4 https://html.duckduckgo.com/html/ 2>/dev/null \
        || "$PY" -c "import requests;requests.get('https://html.duckduckgo.com/html/',timeout=4)" 2>/dev/null; then ONLINE=1; fi ;;
esac
if [ "$ONLINE" = 1 ]; then
  echo "online: using real engines"
else
  export PSE_DEMO_MODE=1
  echo "${Y}offline: using demo engines alongside real engines (real engines will fail gracefully)${N}"
fi

echo "${B}== 3. Start app on $BASE ==${N}"
mkdir -p logs data
# demo_down is a fault-injection engine that always raises: it proves T8
# (one engine down -> search still works) against the running app.
PSE_ENGINES="duckduckgo,wikipedia,marginalia,brave,bing,demo_down" \
  PORT="$PORT" HOST=127.0.0.1 "$PY" custom-engine/app.py > logs/app.log 2>&1 &
APP_PID=$!
UP=0
for _ in $(seq 1 30); do
  if curl -s -f --max-time 1 "$BASE/healthz" >/dev/null 2>&1; then UP=1; break; fi
  kill -0 "$APP_PID" 2>/dev/null || break
  sleep 0.5
done
if [ "$UP" = 1 ]; then pass "app healthy on port $PORT"; else
  fail "app did not start" "see logs/app.log"; tail -n 15 logs/app.log
  echo "${B}Summary:${N} $PASS passed, $FAIL failed, $WARN warnings"; exit 1
fi

echo "${B}== 4. Requirement checks ==${N}"
post_json() { curl -s --max-time 15 -H 'Content-Type: application/json' -d "{\"query\":\"$1\"}" "$BASE/search"; }

# T1
CODE=$(curl -s -o /tmp/pse_t1.json -w '%{http_code}' --max-time 15 -H 'Content-Type: application/json' -d '{"query":"python"}' "$BASE/search")
COUNT=$(jget '.count' 'd["count"]' < /tmp/pse_t1.json 2>/dev/null || echo 0)
if [ "$CODE" = 200 ] && [ "${COUNT:-0}" -ge 5 ] 2>/dev/null; then pass "T1 search returns 200 with $COUNT results (>=5)"; else fail "T1 search" "HTTP $CODE count=$COUNT"; fi

# T2
post_json "test123unique" >/dev/null
curl -s --max-time 15 -o /dev/null -d "q=test123unique&safe=1&lang=en&cat=all" "$BASE/search"
sleep 0.3
if grep -rq "test123unique" logs/ data/ 2>/dev/null; then fail "T2 privacy: query found on disk" "$(grep -rl test123unique logs/ data/ | head -3 | tr '\n' ' ')"; else pass "T2 privacy: query not in logs/ or data/"; fi

# T3: dedup on normalised URL
post_json "python" > /tmp/pse_t3.json
if [ "$HAVE_JQ" = 1 ]; then
  jq -r '.results[].url' /tmp/pse_t3.json > /tmp/pse_urls.txt
else
  "$PY" -c "import json;[print(r['url']) for r in json.load(open('/tmp/pse_t3.json'))['results']]" > /tmp/pse_urls.txt
fi
NORM=$(sed -E 's#^[a-zA-Z]+://##; s#^www\.##; s#[?\#].*$##; s#/+$##' /tmp/pse_urls.txt | tr 'A-Z' 'a-z')
TOTAL=$(printf '%s\n' "$NORM" | grep -c .)
UNIQ=$(printf '%s\n' "$NORM" | sort -u | grep -c .)
if [ "$TOTAL" -gt 0 ] && [ "$TOTAL" = "$UNIQ" ]; then pass "T3 dedup: $TOTAL results, all unique"; else fail "T3 dedup" "$TOTAL results but $UNIQ unique"; fi

# T4
if [ -s /tmp/pse_urls.txt ] && ! grep -qiE 'utm_|fbclid|gclid' /tmp/pse_urls.txt; then pass "T4 tracking params stripped"; else fail "T4 tracking params" "$(grep -iE 'utm_|fbclid|gclid' /tmp/pse_urls.txt | head -2 | tr '\n' ' ')"; fi

# T5 (first call above warmed the cache for "python")
RESP=$(curl -s --max-time 15 -w '\n%{time_total}' -H 'Content-Type: application/json' -d '{"query":"python"}' "$BASE/search")
TT=$(printf '%s' "$RESP" | tail -n1); BODY=$(printf '%s' "$RESP" | sed '$d')
CACHED=$(printf '%s' "$BODY" | jget '.cached' 'str(d["cached"]).lower()')
if [ "$CACHED" = true ] && "$PY" -c "import sys;sys.exit(0 if float('$TT')<0.100 else 1)"; then pass "T5 cache: cached=true in ${TT}s (<0.100)"; else fail "T5 cache" "cached=$CACHED time=${TT}s"; fi

# T6
HDR=$(curl -s -D - -o /tmp/pse_index.html --max-time 10 "$BASE/")
CODE=$(printf '%s' "$HDR" | head -n1 | awk '{print $2}')
T6=1
[ "$CODE" = 200 ] || T6=0
grep -q "PrivateSearch" /tmp/pse_index.html || T6=0
grep -qi "google-analytics" /tmp/pse_index.html && T6=0
grep -qi "fonts.googleapis" /tmp/pse_index.html && T6=0
if [ "$T6" = 1 ]; then pass "T6 frontend: 200, PrivateSearch present, no analytics/google fonts"; else fail "T6 frontend" "HTTP $CODE or forbidden/missing content"; fi
RC=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE/search?q=leak")
if [ "$RC" = 303 ]; then pass "T6 GET /search?q=... -> 303"; else fail "T6 GET /search redirect" "got $RC"; fi
if printf '%s' "$HDR" | grep -qi '^Referrer-Policy: no-referrer'; then pass "T6 Referrer-Policy: no-referrer"; else fail "T6 Referrer-Policy header"; fi
if printf '%s' "$HDR" | grep -qi '^Set-Cookie:'; then fail "T6 no Set-Cookie" "cookie header present"; else pass "T6 no Set-Cookie"; fi

# T7
if command -v docker >/dev/null 2>&1; then
  for d in . searxng; do
    if (cd "$d" && docker compose config -q >/tmp/pse_docker.err 2>&1); then pass "T7 docker compose config ($d)"; else fail "T7 docker compose config ($d)" "$(head -c 200 /tmp/pse_docker.err)"; fi
  done
else
  if "$PY" - <<'PYEOF' 2>/dev/null
import yaml
for p in ("docker-compose.yml", "searxng/docker-compose.yml"):
    yaml.safe_load(open(p))
PYEOF
  then warn "T7 docker CLI missing: YAML syntax valid, compose config not verified"; else warn "T7 docker CLI missing and YAML could not be validated"; fi
fi

# T8: demo_down always raises; a fresh (uncached) query must still return results
# and report the failure.
post_json "engine failure check" > /tmp/pse_t8.json
FAILED=$(jget '.engines_failed | join(",")' '",".join(d["engines_failed"])' < /tmp/pse_t8.json)
NRES=$(jget '.count' 'd["count"]' < /tmp/pse_t8.json)
if [ "${NRES:-0}" -ge 1 ] 2>/dev/null && printf '%s' "$FAILED" | grep -q demo_down; then
  pass "T8 engine failure: failed=[$FAILED], $NRES results still returned"
else
  fail "T8 engine failure" "engines_failed=[$FAILED] results=$NRES"
fi

echo "${B}== 5. Hygiene ==${N}"
SRC_FILTER=(-I --exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=logs --exclude-dir=data --exclude-dir=.pytest_cache --exclude=test.sh --exclude=PLAN.md --exclude=LICENSE --exclude='*.pyc' --exclude='*.md')
if H=$(grep -rnE "TODO|FIXME" "${SRC_FILTER[@]}" . 2>/dev/null) && [ -n "$H" ]; then fail "no TODO/FIXME in sources" "$(printf '%s' "$H" | head -3 | tr '\n' ' ')"; else pass "no TODO/FIXME in sources"; fi
if H=$(grep -rn "PSE_STUB" "${SRC_FILTER[@]}" . 2>/dev/null) && [ -n "$H" ]; then fail "no PSE_STUB placeholders left" "$(printf '%s' "$H" | cut -d: -f1 | sort -u | head -5 | tr '\n' ' ')"; else pass "no PSE_STUB placeholders left"; fi

echo
echo "${B}== Summary ==${N}"
echo "${G}$PASS passed${N}, ${R}$FAIL failed${N}, ${Y}$WARN warnings${N}"
[ "$FAIL" -eq 0 ] || exit 1
exit 0
