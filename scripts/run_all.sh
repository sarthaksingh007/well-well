#!/usr/bin/env bash
# One command from a fresh unzip to a running copilot with the final questions answered:
#   GEMINI_API_KEY=... ./scripts/run_all.sh            (add --skip-final to only start the app)
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf "\n\033[1;36m▶ %s\033[0m\n" "$1"; }
need() { command -v "$1" >/dev/null || { echo "Missing $1. $2"; exit 1; }; }

need uv "Install it: curl -LsSf https://astral.sh/uv/install.sh | sh"
need docker "Install Docker Desktop and start it."
need git "Install git."
docker info >/dev/null 2>&1 || { echo "Docker is installed but not running. Start Docker Desktop."; exit 1; }

say "Settings (.env)"
[ -f .env ] || cp .env.example .env
if [ -n "${GEMINI_API_KEY:-}" ]; then
  grep -q "^GEMINI_API_KEY=" .env && sed -i.bak "s|^GEMINI_API_KEY=.*|GEMINI_API_KEY=${GEMINI_API_KEY}|" .env || echo "GEMINI_API_KEY=${GEMINI_API_KEY}" >> .env
  rm -f .env.bak
fi
grep -q "^GEMINI_API_KEY=." .env || { echo "Set GEMINI_API_KEY: GEMINI_API_KEY=... ./scripts/run_all.sh"; exit 1; }
grep -q "^TODAY=" .env || echo "TODAY=2027-01-15" >> .env

say "Python packages"
uv sync --extra embeddings

say "PostgreSQL"
if ! docker ps --format '{{.Names}}' | grep -qx wellwell-pg; then
  docker start wellwell-pg >/dev/null 2>&1 || docker run -d --name wellwell-pg -p 5433:5432 \
    -e POSTGRES_USER=wellwell -e POSTGRES_PASSWORD=wellwell -e POSTGRES_DB=wellwell postgres:16 >/dev/null
fi
until docker exec wellwell-pg pg_isready -U wellwell >/dev/null 2>&1; do sleep 1; done
echo "ready on 127.0.0.1:5433"

say "Test dataset from GitHub (600 wells, today = 15 Jan 2027)"
uv run wellwell load-github

say "Web app"
pkill -f "wellwell serve" 2>/dev/null || true
mkdir -p logs
nohup uv run wellwell serve > logs/serve.log 2>&1 &
until curl -s -X POST http://127.0.0.1:8765/api/health >/dev/null 2>&1; do sleep 1; done
echo "open http://localhost:8765  (log: logs/serve.log)"

if [ "${1:-}" != "--skip-final" ]; then
  say "Final questions (in order, as the named users)"
  uv run python eval/run_final.py
  echo "Answers saved in docs/results/final/ (PDF: docs/results/final/final-answers.pdf)"
fi

say "Done. The app keeps running at http://localhost:8765 (stop it with: pkill -f 'wellwell serve')"
