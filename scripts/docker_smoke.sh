#!/usr/bin/env bash
# Builds and starts Well-Well from nothing (no containers, no volumes) and checks it comes up complete.
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT=wellwell-smoke
PORT="${SMOKE_PORT:-8791}"
export WELLWELL_PORT="$PORT"
export GEMINI_API_KEY="${GEMINI_API_KEY:-$(grep '^GEMINI_API_KEY=' .env | cut -d= -f2)}"

echo "== removing any previous smoke stack"
docker compose -p "$PROJECT" down -v --remove-orphans >/dev/null 2>&1 || true

echo "== building and starting from scratch"
start=$(date +%s)
docker compose -p "$PROJECT" up -d --build

echo "== waiting for the app to report ready"
for _ in $(seq 1 300); do
  state=$(curl -s -X POST "http://localhost:$PORT/api/status" | python3 -c "import sys,json; print(json.load(sys.stdin).get('state',''))" 2>/dev/null || true)
  [ "$state" = "ready" ] && break
  sleep 3
done
echo "state=$state after $(( $(date +%s) - start ))s"
[ "$state" = "ready" ] || { docker compose -p "$PROJECT" logs app | tail -40; exit 1; }

echo "== checking data, vectors, search and UI"
curl -s -X POST "http://localhost:$PORT/api/status" | python3 -c "
import sys, json
s = json.load(sys.stdin)['stats']
assert s['wells'] == 200 and s['inspections'] == 39567 and s['repairs'] == 5665, s
print('dataset ok:', s['wells'], 'wells,', s['inspections'], 'inspections,', s['repairs'], 'repairs')"
docker compose -p "$PROJECT" exec -T db psql -U wellwell -At -c "select count(*) from vector_cache.chunk_vectors" \
  | python3 -c "import sys; n=int(sys.stdin.read()); assert n == 14562, n; print('vectors ok:', n)"
docker compose -p "$PROJECT" exec -T app python -c "
from wellwell.db.session import session_scope
from wellwell.analytics.documents import search_documents
with session_scope() as s:
    r = search_documents(s, 'when does a well count as producing oil', kind='manual', k=2)
assert 'hybrid' in r.summary and r.rows[0]['doc_id'] == 'OM-T200', r.summary
print('search ok:', r.summary[:120])"
curl -s -o /dev/null -w "ui: %{http_code}\n" "http://localhost:$PORT/"
curl -s -X POST "http://localhost:$PORT/api/record" -H 'Content-Type: application/json' -d '{"record_id":"INS-0001-0203"}' \
  | python3 -c "import sys,json; assert json.load(sys.stdin)['kind']=='inspection'; print('records ok')"

echo "== all good; tearing down (set KEEP=1 to leave it running)"
[ "${KEEP:-0}" = "1" ] || docker compose -p "$PROJECT" down -v
