# Well-Well architecture

An evidence-first copilot for oil-well operations. The language model decides *what to look up and how to say it*; deterministic code decides *what is true*.

## 1. System overview

```
┌──────────────────── Web UI (Stitch design, FastAPI-served) ───────────────┐
│ user picker · my conversations · chat with inline visuals                 │
│ status card · table · line · bar · map · heatmap · citation chips         │
│ "how this was answered" · 👍/👎 · memory drawer (shared / private / log)   │
└───────────────────────────────────┬───────────────────────────────────────┘
                                    │ ask(user, question, conversation)
┌───────────────────────────────────▼───────────────────────────────────────┐
│ CopilotService                                                            │
│  input guardrails → load this user's chat history → run agent → persist   │
├───────────────────────────────────────────────────────────────────────────┤
│ Agent harness (our own, ~250 lines)                                       │
│  JSON action protocol: {"calls":[...]} or {"final":{answer, visual}}      │
│  up to 6 rounds, parallel calls, 120 s budget, parse-error recovery       │
│  output guardrails: unknown citations removed, ungrounded numbers flagged,│
│  requested visual enforced, "must call a tool" for factual questions      │
├───────────────────────────────────────────────────────────────────────────┤
│ Tools (typed, deterministic)                                              │
│  read:  well_status · wells_not_producing · temperature_history · spills  │
│         downtime · aggregate · repairs · rca_due · compliance             │
│         inspection_detail · wells · search_documents · query_sql (RO)     │
│  memory: record_correction · record_note · record_rule · record_claim     │
│          recall_my_conversations                                          │
├───────────────────────────────────────────────────────────────────────────┤
│ Model gateway: Gemini 3.5 Flash-Lite → 3.1 Flash-Lite → Gemma 4 26B       │
│                (LLM_MODELS); Ollama / OpenRouter also supported           │
├───────────────────────────────────────────────────────────────────────────┤
│ PostgreSQL 16 (SQLite fallback) · vector_cache schema (BGE-large vectors) │
│  dataset tables · memory tables · chat tables · views                     │
├───────────────────────────────────────────────────────────────────────────┤
│ Loader: fleet → manuals/SOPs (rules extracted) → per well: metadata,      │
│  inspections, maintenance + reports (facts extracted), regulator events,  │
│  notes → formal corrections → views                                       │
└───────────────────────────────────────────────────────────────────────────┘
```

Code layout: `src/wellwell/{ingestion, db, analytics, memory, harness, services}`, `ui/`, `eval/`, `tests/`.

## 2. Data: storage, indexing, retrieval

**Loading.** `wellwell load <folder> --dataset <name>` reads the dataset exactly as published (JSON, JSONL, Markdown). Rows are upserted on the dataset's own IDs, so the test release loads on top of the sample and re-running changes nothing. The full sample (200 wells, 39,567 inspections, 5,665 repairs, 1,514 documents) loads in about 6 seconds.

**Rules are read from the documents, not hard-coded.**

| Source | What the loader extracts | Stored in |
|---|---|---|
| Well-type manuals | Producing threshold (T200 15 °C, T350 20 °C) with section ref, temperature alarm, RCA count; T350 inherits RCA from T200 | `well_type_rules` |
| Repair reports (free text) | Containment hours, spill class, report date, every row of the compliance-check table (SR-01…WS-07, "Manual §10 Action required") | `maintenance.report_*`, `report_compliance` |
| Formal correction notes | "Not a spill" → a correction record against the named inspection | `corrections` |
| Manuals, SOPs, reports | Split by heading for search | `documents`, `document_chunks` |

**Tables.**
- Dataset: `areas`, `teams`, `users`, `wells`, `equipment`, `inspections`, `maintenance`, `regulatory_events`, `notes`, plus the extracted `well_type_rules` and `report_compliance`.
- Memory: `corrections`, `rule_overrides`, `claims`, `notes` with `origin = copilot`, `answer_feedback`.
- Chat: `conversations`, `messages`, `tool_runs`.

**The key view: `inspection_facts`.** For every inspection it applies the latest active correction per field, keeping the original beside it. It also computes `temp_delta_c`, the threshold for the well's type (or a team rule), `producing`, `eff_spill` and `eff_spill_volume_bbl`. `latest_inspection` is "right now". Every number in the tools is derived from this one view, so a correction changes every later answer for every user.

**Retrieval.**
- Structured questions go to SQL inside typed tools.
- Text questions go to hybrid search over sections of the manuals, SOPs and reports: BGE-large (`bge-large-en-v1.5`) vectors stored in Postgres, keyed by text hash so nothing is embedded twice, fused with BM25 by reciprocal rank. Modes: Semantic (default), Smart (hybrid), Exact words. A 28 MB seed of all 14,562 vectors ships in `seed/` for fresh installs.
- Questions no tool covers go to `query_sql`. It is read-only (a separate read-only connection plus `query_only`), and an AST check allows exactly one SELECT over allowlisted tables, with a forced LIMIT.

## 3. Tools and how numbers, documents and notes combine

| Tool | Answers | Rules it applies |
|---|---|---|
| `well_status` | Is it producing now, last inspection, why, recorded status stale? | Manual threshold; open repairs and notes covering the date; claims; corrections |
| `wells_not_producing` | Which wells are down now and why | Same, per well; "no recorded reason" instead of guessing |
| `temperature_history` | Wellhead vs air over time | Producing flag per reading |
| `spills` | Real spills, volumes, classes, map points | Corrected detections excluded (SOP-SR §7); class bands; SR-01 report status |
| `downtime` | Days down per well, rankings, causes | 7 days per not-producing weekly inspection; cause from the triggering or overlapping repair, else a note |
| `aggregate` | Heatmaps and bar charts (area/well/type × month/year) | Same definitions as above |
| `repairs` | Jobs and cost per well | A repair belongs to a period by its start date |
| `rca_due` | Root-cause analyses needed | Manual §10 count in 12 months, or a team rule at its scope |
| `compliance` | Breaches and open items | SR-01 24 h / 2 h, SR-02 4 h from report text, SR-03 60 days, RC-04 30 days, RC-02 yearly test, RC-01 14-day gap, pending RCA, shut-ins not in the maintenance system |
| `inspection_detail` | One inspection with all linked records | Formal correction vs opinion notes; whether a detection stands |

Periods are parsed by code (`last_4_months`, `2025`, `summer_2026`…). Rolling windows end on the last full day of data, matching the challenge's definition (e.g. 16 Jun–16 Oct).

Every tool returns the same `ToolResult`: a summary for the model, rows or a card for the UI, the record IDs it used, the rules it applied, and a suggested visual.

## 4. Agent loop

1. Input guardrails block prompt injection, delete/overwrite requests and oversized input.
2. The system prompt sets today's date, the current user (name, role, team), the tool catalogue with examples, the answer and visual rules, and the memory rules.
3. Each round the model replies with one JSON object: tool calls (several can run in parallel) or a final answer. Tool errors go back to the model as observations, not crashes.
4. Final answer checks:
   - cited IDs must have appeared in tool results this turn, or they are removed
   - numbers not found in any tool output are flagged in the trust panel
   - a visual the user named overrides the model's choice
   - the visual is drawn from the tool rows, never from model-written data
5. Everything is persisted: the question, the answer, citations, trust info, and each tool run with its full result.

The protocol is plain JSON in text, so the same loop runs on any model in the chain (Gemini Flash-Lite, Gemma 4) and on Ollama or OpenRouter. Obvious lookups (a named well's state) are prefetched, so many answers need one model call. A learned playbook adds advisory hints from similar verified answers.

## 5. Memory

| Level | What | Scope | Where |
|---|---|---|---|
| Shared knowledge | Notes ("WELL-0007 shut in from 17 Oct for 10 days") | Well, well type or fleet | `notes` (origin copilot) |
| Learning from feedback: raw data | "The 15 Oct reading was faulty, real value 41.0 °C" | One inspection | `corrections` (kind raw_data) |
| Learning from feedback: AI analysis | "The spill was 5.4 bbl, not 4.5" | One inspection | `corrections` (kind ai_analysis) |
| Team rules | "T350: RCA after 2 pump-jack repairs" | One well type only | `rule_overrides` |
| Claims | "WELL-0175 is running fine" | One well, checked against data | `claims` + `notes` |
| Private memory | This user's conversations and the wells in them | One user | `conversations`, `messages` |

- **Nothing overwrites a record.** Each correction keeps `original_value`, `corrected_value`, the author, the reason and when it was made. A newer correction supersedes the older one, and both stay on record.
- **Shared items are written by the tools under the signed-in user's ID.** The model cannot choose the author.
- **Private memory is only reachable through `recall_my_conversations`,** which filters by the current user and excludes the current chat. Reading someone else's chat raises a permission error at the service layer.

## 6. Conflicts and honesty

| Situation | What the copilot does |
|---|---|
| A user's claim contradicts the latest inspection | Says so with the reading and inspector, stores the claim as unconfirmed, keeps both statements, asks to confirm |
| A user's claim matches the data | Confirms it and cites the inspection |
| Two users disagree (claims or notes) | Lists both, with authors and dates |
| A correction disagrees with the inspector's own note | Applies the correction, quotes the note, asks the author to confirm |
| Opinion notes disagree about a detection | No formal correction, so the detection stands; both notes are shown |
| Recorded status disagrees with the inspections | Inspections win; the status is flagged as out of date |
| Nothing explains a stop | Says "no recorded reason" and suggests a field check; never invents a cause |

## 7. New data and unseen questions

- **New data:** the test release loads with the same command and merges by ID. The thresholds and RCA rule are re-read from whatever manuals ship with it. Report parsing uses tolerant patterns, and anything it can't parse is listed as a load warning rather than guessed.
- **Unseen questions:** tools take parameters (any well, area, type, period, metric or dimension), not question templates. `aggregate` covers new cross-tabs, `search_documents` covers rules found only in text, and `query_sql` covers the rest under the read-only guard.
- **Regression check:** `eval/run_examples.py` replays the 18 example scenarios, including the multi-user sequences, and checks the key facts and the visual for each.

## 8. Models and cost

| Use | Model | Cost |
|---|---|---|
| Reasoning, tool choice, answers | Gemini 3.5 Flash-Lite (≈1.2 s per call), then 3.1 Flash-Lite, then Gemma 4 26B, on the Gemini API | Free tier |
| Offline / self-hosted | Gemma 4 through Ollama (`gemma4:e4b` … `26b`) | Local compute only |
| Embeddings | BGE-large (`BAAI/bge-large-en-v1.5`), local on Apple GPU/CPU, vectors in Postgres | Local compute only |

A typical question takes 2–3 model calls of about 3–9k input tokens each and 10–40 s end to end. The deterministic layer answers in milliseconds.

## 9. Guardrails summary

- **Input:** prompt-injection and destructive-intent filters, a 2,000-character limit, and staying on topic.
- **Tools:** typed arguments, known wells and areas only, read-only SQL with an AST allowlist and a row limit, writes only through the memory tools, and the author is always the signed-in user.
- **Output:** citation whitelist, number-grounding check, enforced visual, and graceful degradation when the model is down or the budget runs out.
- **Privacy:** private history is filtered by user at the service layer, not only in the prompt.

## 10. What we would do next

- **Embeddings:** pgvector index once the corpus grows beyond in-memory scoring.
- **Corrections:** a review queue for corrections and claims, plus a confidence score per source.
- **Database:** move to Postgres with row-level security for multi-tenant deployments, and incremental loads with change data capture.
- **Proactive alerts:** wells newly not producing, SR deadlines about to pass, RCAs due.
- **Images:** use the inspection images (Gemma 4 is multimodal) to second-check AI spill estimates.
- **Testing:** a larger regression suite generated from the test dataset.
