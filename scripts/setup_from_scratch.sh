#!/usr/bin/env bash
# Well-Well from scratch on a new Mac or Linux machine: clone, install, database, test data, app.
#   bash setup_from_scratch.sh                 (asks for the Gemini key)
#   GEMINI_API_KEY=... bash setup_from_scratch.sh
# Needs: Docker Desktop running, git. Installs uv if missing. No Hugging Face token needed.
set -euo pipefail

REPO="https://github.com/sarthaksingh007/well-well"
DIR="${WELLWELL_DIR:-$HOME/well-well}"
MODELS="${LLM_MODELS:-gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemma-4-26b-a4b-it}"

say() { printf "\n\033[1;36m▶ %s\033[0m\n" "$1"; }

command -v git >/dev/null || { echo "Install git first."; exit 1; }
command -v docker >/dev/null || { echo "Install Docker Desktop first: https://www.docker.com/products/docker-desktop"; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is installed but not running. Start Docker Desktop and run this again."; exit 1; }
if ! command -v uv >/dev/null; then
  say "Installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

say "Code ($DIR)"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only -q; else git clone -q "$REPO" "$DIR"; fi
cd "$DIR"
git log --oneline -1

say "Settings (.env)"
[ -f .env ] || cp .env.example .env
KEY="${GEMINI_API_KEY:-${GOOGLE_API_KEY:-}}"
if [ -z "$KEY" ] && ! grep -q "^GEMINI_API_KEY=." .env; then
  read -r -p "Gemini API key (from https://aistudio.google.com/apikey): " KEY
fi
set_env() { grep -q "^$1=" .env && sed -i.bak "s|^$1=.*|$1=$2|" .env || echo "$1=$2" >> .env; rm -f .env.bak; }
[ -n "$KEY" ] && set_env GEMINI_API_KEY "$KEY"
set_env LLM_MODELS "$MODELS"
set_env TODAY 2027-01-15
grep -E "^(LLM_MODELS|TODAY)=" .env

say "Python packages (first time takes a few minutes)"
uv sync --extra embeddings

say "PostgreSQL"
if ! docker ps --format '{{.Names}}' | grep -qx wellwell-pg; then
  docker start wellwell-pg >/dev/null 2>&1 || docker run -d --name wellwell-pg -p 5433:5432 \
    -e POSTGRES_USER=wellwell -e POSTGRES_PASSWORD=wellwell -e POSTGRES_DB=wellwell postgres:16 >/dev/null
fi
until docker exec wellwell-pg pg_isready -U wellwell >/dev/null 2>&1; do sleep 1; done
echo "ready on 127.0.0.1:5433"

say "Test dataset from GitHub (600 wells; search vectors come from seed/, nothing is re-embedded)"
pkill -f "wellwell serve" 2>/dev/null || true
uv run wellwell load-github

say "App"
mkdir -p logs
nohup uv run wellwell serve > logs/serve.log 2>&1 &
until curl -s -X POST http://127.0.0.1:8765/api/health >/dev/null 2>&1; do sleep 1; done
command -v open >/dev/null && open http://localhost:8765 || true

cat <<EOF

✅ Well-Well is running at http://localhost:8765
   Pick a user (top left) → New chat → ask a question from:
   $DIR/data/raw/challenge/test-data/final-questions.md

   Run all final questions + PDF:  cd $DIR && uv run python eval/run_final.py
   Logs:                           $DIR/logs/serve.log
   Stop:                           pkill -f "wellwell serve"
   Start again later:              cd $DIR && nohup uv run wellwell serve > logs/serve.log 2>&1 &
EOF
