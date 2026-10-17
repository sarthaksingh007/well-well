# Well-Well

An evidence-first copilot for oil-well operations. Ask about your wells in plain English and get an answer you can check: every number comes from a deterministic tool, every claim cites the inspection, repair, regulator event, note or manual section it came from, and the answer arrives with the visual that fits it.

Built for the FlytBase *Asset Intelligence Copilot* challenge (Hacktoberfest 2026 Hack Day, Pune). Runs on **Gemini 3.8 Flash** with a fallback chain down to **Gemma 4** (a model whose daily quota runs out is skipped), **BGE-large** embeddings for document search, and **PostgreSQL 16**. Models are configurable with `LLM_MODELS` (an all-Gemma setup works too).

## Run it: exact steps (test dataset + all final questions)

Needs: [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`), Docker Desktop running, git,
and a free Gemini key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey). No Hugging Face token.

```bash
git clone https://github.com/sarthaksingh007/well-well && cd well-well
GEMINI_API_KEY=<your key> ./scripts/run_all.sh
```

That one command:
1. creates `.env` from `.env.example` with your key (`TODAY=2027-01-15`, the test dataset's "today"),
2. installs the Python packages (`uv sync --extra embeddings`),
3. starts PostgreSQL 16 in Docker (`wellwell-pg` on port 5433),
4. pulls the test dataset from GitHub and loads it (`wellwell load-github`, 600 wells; the 42,793 search vectors are
   imported from `seed/`, nothing is re-embedded),
5. starts the app at **http://localhost:8765** (log in `logs/serve.log`),
6. runs the 11 final questions in order as the named users (`eval/run_final.py`) and writes every full answer to
   `docs/results/final/*.json` plus **`docs/results/final/final-answers.pdf`**.

`./scripts/run_all.sh --skip-final` only starts the app. The same steps by hand:

```bash
cp .env.example .env                     # set GEMINI_API_KEY
uv sync --extra embeddings
docker run -d --name wellwell-pg -p 5433:5432 -e POSTGRES_USER=wellwell -e POSTGRES_PASSWORD=wellwell -e POSTGRES_DB=wellwell postgres:16
until docker exec wellwell-pg pg_isready -U wellwell; do sleep 1; done
uv run wellwell load-github              # test data from GitHub
uv run wellwell serve                    # terminal 1 → http://localhost:8765
uv run python eval/run_final.py          # terminal 2 → docs/results/final/ + PDF
```

## Quick start (sample dataset)

**Any machine with Docker** (Postgres + the app, data loaded on first start):

```bash
export GEMINI_API_KEY=...        # free key from aistudio.google.com/apikey
docker compose up --build
# open http://localhost:8765
```

The first start clones the challenge dataset into `data/raw/challenge` and loads it (about 30 s).

**Local development:**

```bash
uv sync
cp .env.example .env            # add GEMINI_API_KEY; DATABASE_URL picks Postgres or SQLite
uv run wellwell init            # create schema and load the sample if the database is empty
uv run wellwell serve           # http://localhost:8765
```

Load the test release (600 wells, today = 15 Jan 2027) straight from GitHub. It pulls the latest dataset repo and
reloads the database with `test-data/dataset`:

```bash
uv run wellwell load-github           # then set TODAY=2027-01-15 in .env and restart `wellwell serve`
```

With Docker: `DATA_FOLDER=test-data/dataset TODAY=2027-01-15 docker compose up`, or on a running stack
`docker compose exec app wellwell load-github`.

Or load any local folder: `uv run wellwell load path/to/dataset --dataset test --reset`.

The final questions run end to end with `uv run python eval/run_final.py`; answers land in `docs/results/final/`.

Ask from the terminal:

```bash
uv run wellwell ask "Is WELL-0001 producing oil right now?" --user U-201
```

## How it works

```
Web UI from the Stitch design (user picker, live field overview, inline visuals, agent graph, memory drawer, mobile)
        │
CopilotService ── guardrails (input, SQL, citations, numbers, requested visual)
        │
Agent harness (JSON tool protocol; Gemini 3.5 Flash-Lite → 3.1 Flash-Lite → Gemma 4, with backoff and hedged requests)
        │  calls
Deterministic tools ── well_status · wells_not_producing · temperature_history · spills · downtime
                       aggregate · repairs · rca_due · compliance · inspection_detail · wells
                       search_documents (BGE-large + BM25) · query_sql (read-only)
                       record_correction · record_note · record_rule · record_claim · recall_my_conversations
        │
PostgreSQL 16 (SQLite also supported): the dataset as loaded + corrections, notes, rules, claims, conversations, playbook
        ├ vector_cache schema: BGE-large vectors keyed by text hash (survive resets; seeded from seed/ on fresh installs)
        └ view inspection_facts: corrected readings, temperature margin, producing flag, effective spills
```

- **Rules come from the documents, not the code.** The loader reads the producing threshold, temperature alarm and RCA rule out of each well-type manual, and containment times and compliance tables out of the free-text repair reports.
- **The model never does arithmetic.** Downtime (7 days per not-producing weekly inspection), spill classes, deadlines and RCA counts are computed in SQL and Python; Gemma 4 chooses tools, reads results and writes the answer.
- **Corrections never overwrite.** They sit beside the original with author, reason and scope, and the `inspection_facts` view applies them for every user.
- **Two memory levels.** Shared knowledge (notes, corrections, team rules, claims) is visible to everyone; conversation history is private to each user.
- **Hybrid document search.** BGE-large vectors stored in Postgres, fused with BM25; modes Semantic (default), Smart and Exact words.
- **Self-evolving playbook.** Verified answers teach anonymised question patterns → tool plans; 👍 and eval passes raise trust, 👎 marks a plan to avoid. Advisory only, with an off switch.
- **Conflicts are surfaced, not resolved silently.** Claims are checked against the latest inspection; disagreeing notes and corrections are shown side by side.

## Tests

```bash
uv run pytest -q                       # 95 tests: unit, edge cases, API, loader, embeddings
uv run python eval/run_examples.py     # the 18 example scenarios end to end (27/27 steps pass)
./scripts/docker_smoke.sh              # build and verify the Docker stack from scratch
```

## License

MIT. The challenge dataset belongs to its authors and is not included in this repository.
