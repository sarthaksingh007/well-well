"""Builds docs/Well-Well-LLD.pdf. Schema tables are generated from the SQLAlchemy models so they match the code."""

import html
import subprocess
from pathlib import Path

from wellwell.db.base import Base
from wellwell.db import models  # noqa: F401
from wellwell.harness.tools import SPECS

ROOT = Path(__file__).resolve().parents[1]
OUT_HTML = ROOT / "docs" / "lld" / "well-well-lld.html"
OUT_PDF = ROOT / "docs" / "Well-Well-LLD.pdf"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

GROUPS = {
    "Dataset (as published)": ["areas", "teams", "users", "wells", "equipment", "inspections", "maintenance",
                               "regulatory_events", "notes"],
    "Extracted at load time": ["well_type_rules", "report_compliance", "documents", "document_chunks"],
    "Memory and feedback": ["corrections", "rule_overrides", "claims", "answer_feedback"],
    "Conversations (private per user)": ["conversations", "messages", "tool_runs"],
}


def schema_html() -> str:
    parts = []
    for group, tables in GROUPS.items():
        parts.append(f"<h3>{group}</h3><div class=schema>")
        for name in tables:
            table = Base.metadata.tables[name]
            rows = []
            for column in table.columns:
                flags = []
                if column.primary_key:
                    flags.append("PK")
                for fk in column.foreign_keys:
                    flags.append(f"FK → {fk.target_fullname}")
                if column.index:
                    flags.append("idx")
                rows.append(f"<tr><td class=mono>{column.name}</td><td class=mono>{html.escape(str(column.type))}</td>"
                            f"<td>{', '.join(flags)}</td></tr>")
            doc = (table.info.get("doc") or "")
            parts.append(f"<div class=tbl><div class=tname>{name}</div>{doc}<table><tr><th>Column</th><th>Type</th>"
                         f"<th>Keys</th></tr>{''.join(rows)}</table></div>")
        parts.append("</div>")
    return "\n".join(parts)


def tools_html() -> str:
    rows = []
    for spec in SPECS:
        args = ", ".join(f"<span class=mono>{k}</span>" for k in spec.args)
        rows.append(f"<tr><td class=mono>{spec.name}</td><td>{'write' if spec.writes else 'read'}</td>"
                    f"<td>{html.escape(spec.purpose)}</td><td>{args}</td></tr>")
    return ("<table><tr><th>Tool</th><th>Mode</th><th>Purpose</th><th>Arguments</th></tr>" + "".join(rows)
            + "</table>")


CSS = """
@page { size: A4; margin: 16mm 14mm 16mm 14mm; }
body { font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif; font-size: 9.6pt; color: #16202b; line-height: 1.42; }
h1 { font-size: 22pt; margin: 0 0 2mm; letter-spacing: -0.3px; }
h2 { font-size: 13.5pt; margin: 7mm 0 2mm; padding-bottom: 1mm; border-bottom: 1.5px solid #2f6fd6; page-break-after: avoid; }
h3 { font-size: 11pt; margin: 4mm 0 1.5mm; color: #2a3a4c; page-break-after: avoid; }
p, li { margin: 1mm 0; }
.sub { color: #5a6b7d; font-size: 10pt; margin-bottom: 5mm; }
.mono, code, pre { font-family: 'SF Mono', Menlo, Consolas, monospace; font-size: 8.4pt; }
pre { background: #f3f6f9; border: 1px solid #dde4ea; border-radius: 4px; padding: 2.5mm; white-space: pre-wrap;
      page-break-inside: avoid; line-height: 1.3; font-size: 6.9pt; }
table { border-collapse: collapse; width: 100%; margin: 1.5mm 0 3mm; page-break-inside: auto; }
th, td { border: 1px solid #d9e0e7; padding: 1.2mm 1.8mm; text-align: left; vertical-align: top; }
th { background: #eef3f8; font-weight: 600; }
tr { page-break-inside: avoid; }
.schema { column-count: 2; column-gap: 5mm; }
.tbl { break-inside: avoid; page-break-inside: avoid; margin-bottom: 2mm; }
.schema table { font-size: 7.6pt; margin: 0.8mm 0 1.5mm; }
.schema th, .schema td { padding: 0.6mm 1.2mm; }
.schema .mono { font-size: 7.2pt; }
.schema h3 { column-span: all; }
.tname { font-family: 'SF Mono', Menlo, monospace; font-weight: 700; font-size: 9.4pt; color: #2f6fd6; margin-top: 2mm; }
.cols { display: flex; gap: 5mm; } .cols > div { flex: 1; }
.note { background: #f6f9fd; border-left: 3px solid #2f6fd6; padding: 2mm 3mm; margin: 2mm 0; }
.pb { page-break-before: always; }
"""

BODY = """
<h1>Well-Well — Low-Level Design</h1>
<div class=sub>Asset Intelligence Copilot · FlytBase challenge, Hacktoberfest 2026 Hack Day Pune · Gemma 4 · version 1.0, 17 Oct 2026</div>

<div class=note><b>Design principle.</b> The language model decides <i>what to look up and how to say it</i>; deterministic code
decides <i>what is true</i>. Every number shown to a user is computed by SQL/Python from the records and every claim carries the
IDs of the records and document sections it came from.</div>

<h2>1. Component view</h2>
<pre>
 Streamlit UI (ui/app.py)
   user picker · conversation list · chat · inline visuals · citation chips · trust panel · 👍/👎 · memory drawer
        │  services.copilot.ask(user_id, question, conversation_id, provider, on_step)
        ▼
 services/copilot.py ── CopilotService
   check_input()  →  load history (own conversation only)  →  build_system()  →  Agent.run()  →  persist
        │
        ▼
 harness/agent.py ── Agent                                  harness/llm.py ── LLM gateway
   rounds ≤ 6 · budget 120 s · parallel calls ≤ 4    ◀────▶  GemmaOnGemini (26B A4B → 31B fallback)
   extract_json() · _finish() · _choose_visual()             OpenAICompatible (Ollama / OpenRouter)
        │ ToolBox.run(name, args)
        ▼
 harness/tools.py ── ToolBox (18 tools, typed specs)       harness/guardrails.py · harness/sql_guard.py
        │
        ├─ analytics/facts.py        Facts       (status, spills, downtime, aggregate, repairs, RCA, details)
        ├─ analytics/compliance.py   Compliance  (SR-01/02/03, RC-01/02/04, pending actions)
        ├─ analytics/documents.py    BM25 search over manual / SOP / report sections
        ├─ analytics/periods.py      period strings → closed date ranges
        └─ memory/feedback.py        Feedback (corrections, notes, rules, claims) · PrivateMemory (recall)
        │
        ▼
 db/  SQLAlchemy models + views.sql  ──  SQLite (data/wellwell.db), Postgres-compatible models
        ▲
 ingestion/loader.py ── DatasetLoader  (+ ingestion/documents.py extractors, ingestion/normalize.py)
</pre>

<h3>Module responsibilities</h3>
<table>
<tr><th>Module</th><th>Key types / functions</th><th>Responsibility</th></tr>
<tr><td class=mono>config.py</td><td class=mono>Settings, get_settings()</td><td>.env driven settings: provider, Gemma model ids, DB URL, budgets, "today".</td></tr>
<tr><td class=mono>db/base.py</td><td class=mono>Base, enum_column(), LoadedFromMixin</td><td>Declarative base; enums stored as TEXT + CHECK; every dataset row records dataset + source file.</td></tr>
<tr><td class=mono>db/models/*</td><td class=mono>fleet.py, records.py, memory.py</td><td>Tables in §3.</td></tr>
<tr><td class=mono>db/session.py</td><td class=mono>get_engine(), get_read_only_engine(), session_scope(), create_schema(), create_views()</td><td>Read-write engine (WAL) and a separate read-only engine (<code>mode=ro</code> + <code>PRAGMA query_only</code>) for agent SQL.</td></tr>
<tr><td class=mono>db/views.sql</td><td class=mono>inspection_facts, latest_inspection</td><td>Corrections applied, temperature margin, producing flag, effective spills (§4).</td></tr>
<tr><td class=mono>ingestion/loader.py</td><td class=mono>DatasetLoader.run() → LoadReport</td><td>Idempotent upsert of a dataset folder; documents; corrections from notes.</td></tr>
<tr><td class=mono>ingestion/documents.py</td><td class=mono>extract_manual_rules(), extract_report_facts(), split_sections()</td><td>Rules from manuals; containment, class, compliance table from reports.</td></tr>
<tr><td class=mono>analytics/facts.py</td><td class=mono>Facts, spill_class()</td><td>All read tools except compliance; explanations of stops; causes of downtime.</td></tr>
<tr><td class=mono>analytics/compliance.py</td><td class=mono>Compliance.check()</td><td>SOP deadline checks with citations.</td></tr>
<tr><td class=mono>analytics/result.py</td><td class=mono>ToolResult, cite()</td><td>Uniform tool output (§6).</td></tr>
<tr><td class=mono>memory/feedback.py</td><td class=mono>Feedback, PrivateMemory</td><td>Writes shared memory under the signed-in user; private recall.</td></tr>
<tr><td class=mono>harness/*</td><td class=mono>Agent, ToolBox, SPECS, build_system(), guard_sql(), check_input()</td><td>Agent loop, tool registry, prompts, guardrails.</td></tr>
<tr><td class=mono>services/copilot.py</td><td class=mono>ask(), start_conversation(), conversation_history(), conversation_messages(), rate_answer()</td><td>Entry point for UI and CLI; persistence; privacy checks.</td></tr>
<tr><td class=mono>cli.py</td><td class=mono>wellwell load | ask</td><td>Load datasets, ask from the terminal.</td></tr>
</table>

<h2 class=pb>2. Key flows</h2>
<h3>2.1 Ask a question</h3>
<pre>
User ─▶ UI ─▶ ask(user, q, conv)
               ├─ check_input(q)                       blocked? → polite refusal, nothing stored
               ├─ load User, Conversation (must belong to user) and last 6 messages
               ├─ system = build_system(today, user, team) + tool catalogue
               └─ Agent.run(q, history)
                    loop (≤ 6 rounds, ≤ 120 s):
                      prompt = history + question + "Step n tool(args) → ToolResult.for_model()" …
                      reply  = LLM.complete(system, prompt)     (Gemma 4; thought parts dropped)
                      action = extract_json(reply)              (fences, prose, trailing commas tolerated)
                      ├─ {"calls":[…]} → ToolBox.run() each (errors become observations)
                      └─ {"final":{answer, visual, citations}}
                           no tool yet & factual?  → "call a tool first", continue
                           _finish(): drop unknown citations · flag ungrounded numbers
                                      enforce requested visual · pick tool rows for the visual
               ├─ persist Message(user), Message(assistant + citations + trust + visual meta), ToolRun × n
               └─ CopilotReply{answer, visual{kind, rows|card}, citations, steps, trust, model, message_id}
</pre>
<h3>2.2 Correction (e.g. "the 15 Oct reading for WELL-0164 was 41.0 °C")</h3>
<pre>
Agent ─▶ record_correction(well, on_date, field=wellhead_temp_c, value=41.0, reason, kind=raw_data)
          ├─ inspection_detail() resolves INS-0164-0205; snapshot "before"
          ├─ supersede older active corrections for (inspection, field); note if another user disagreed
          ├─ INSERT corrections(original_value=24.0, corrected_value=41.0, author=signed-in user, reason)
          ├─ inspection_facts now yields 41.0 − 19.9 = 21.1 °C ≥ 15 → producing   ("after" snapshot)
          ├─ inspector note "Pump jack not moving." matches NOT_RUNNING_HINTS → disagreement returned
          └─ for spill fields: re-run Compliance._spill_checks() → SR-01/SR-02/SR-03 effects
Every later answer, for every user, reads inspection_facts → sees the correction and who made it.
</pre>
<h3>2.3 Claim (e.g. Tyler: "WELL-0175 is running fine")</h3>
<pre>
record_claim(well, claimed_state=producing, statement)
  ├─ well_status() → latest inspection INS-0175-… by Jessica Reed, 0.4 °C above air → not producing
  ├─ status = contradicted_by_data (kept as unconfirmed) · verdict text with reading + inspector
  ├─ other statements: earlier claims/notes with the opposite state (e.g. Michael Turner's shut-in note)
  └─ INSERT notes(origin=copilot, text + [unconfirmed]) and claims(...)
</pre>
<h3>2.4 Load a dataset</h3>
<pre>
wellwell load &lt;folder&gt; --dataset sample|test [--reset]
  create_schema() → fleet/areas.json, users.json → teams, areas, users
  fleet/well-types/*.md → documents + extract_manual_rules() → well_type_rules
  fleet/sops/*.md → documents
  assets/WELL-*/ metadata.json → wells, equipment
                 inspections.jsonl → inspections            (INSERT OR REPLACE, 2,000-row batches)
                 maintenance.jsonl + report_file → maintenance + documents + report_compliance
                 regulatory-events.jsonl → regulatory_events
                 context.jsonl → notes
  formal correction notes → corrections ("not a spill" → spill_detected=false; "x bbl" → volume)
  create_views() · LoadReport{counts, warnings}
</pre>

<h2 class=pb>3. Data model</h2>
<p>Primary keys are the dataset's own IDs (INS-0001-0203, MNT-0001-032, REG-0001-002, CTX-0001-003), so citations need no translation and a second dataset merges by key. Copilot-created records use COR-nnnn, FB-nnnn, CLM-nnnn, RULE-nnnn; documents use OM-T200, SOP-SR, RPT-MNT-…</p>
__SCHEMA__

<h2 class=pb>4. Derived views</h2>
<h3>inspection_facts</h3>
<pre>
for each inspection i:
  c_wellhead, c_ambient, c_spill, c_volume = latest ACTIVE correction value per field (or NULL)
  eff_wellhead = COALESCE(c_wellhead, i.wellhead_temp_c)        eff_ambient = COALESCE(c_ambient, i.ambient_temp_c)
  threshold    = latest ACTIVE rule_overrides(producing_min_delta_c) for the well type or fleet,
                 else well_type_rules.producing_min_delta_c (15 °C T200, 20 °C T350 — read from the manuals)
  temp_delta_c = ROUND(eff_wellhead − eff_ambient, 1)
  producing    = (eff_wellhead − eff_ambient) ≥ threshold
  eff_spill    = c_spill if present else i.spill_detected
  eff_volume   = c_volume or i.spill_est_volume_bbl, only when eff_spill
  has_correction, area_name, lat, lon, well_type carried along
latest_inspection = inspection_facts rows at each well's MAX(inspected_at)
</pre>

<h2>5. Algorithms</h2>
<table>
<tr><th>Fact</th><th>Definition used in code</th></tr>
<tr><td>Producing now</td><td><code>latest_inspection.producing</code>. Recorded status is shown as out of date when it disagrees.</td></tr>
<tr><td>Downtime</td><td>7 × count of <code>inspection_facts</code> rows with producing = 0 and inspection_date in [start, end]. Missed inspections add nothing. <code>maintenance.downtime_hours</code> is never used.</td></tr>
<tr><td>Cause of a stop</td><td>Repair triggered by that inspection, else a repair whose [started, completed] covers it or starts within 3 days, else a note valid on that date; otherwise "no recorded cause".</td></tr>
<tr><td>Reason not producing</td><td>Open repairs at the latest inspection + notes covering the date (valid_from ≤ d+7 and valid_to ≥ d−7, or open-ended and created within 45 days) + copilot notes and claims.</td></tr>
<tr><td>Periods</td><td>Rolling windows end yesterday (last full day of data) and count back: last_4_months = 16 Jun–16 Oct 2026; last_90_days = 18 Jul–16 Oct. Calendar years and months are closed ranges. A repair belongs to a period by its start date.</td></tr>
<tr><td>Spill class</td><td>&lt;1 minor · 1–&lt;5 moderate · 5–&lt;25 reportable · ≥25 major (SOP-SR §3), on the effective volume.</td></tr>
<tr><td>SR-01</td><td>Reportable/major: spill_report_filed for the inspection with hours_to_report ≤ 24 (≤ 2 major); none and deadline passed → breach ("overdue"). Linked notice of violation adds its fine and status.</td></tr>
<tr><td>SR-02</td><td><code>maintenance.report_containment_hours</code> of the repair triggered by the spill inspection &gt; 4 → breach.</td></tr>
<tr><td>SR-03</td><td>Reportable/major: closed site_inspection for the inspection → met; else due = discovery + 60 days; past due → breach, otherwise pending.</td></tr>
<tr><td>RC-04</td><td>Notice of violation needs nov_response_filed within 30 days; none and past due → breach.</td></tr>
<tr><td>RC-02 / RC-01</td><td>"Annual flowline integrity test" repair in each calendar year; gaps &gt; 14 days between inspections.</td></tr>
<tr><td>RCA due</td><td>Corrective repairs per (well, equipment kind) with start in (today−365, today] ≥ threshold; threshold from the manual or the newest active team rule for that well type and equipment.</td></tr>
<tr><td>Report class mismatch</td><td>Repair report's spill class differs from the class of the effective volume → "review" item (the report's reporting assessment no longer holds).</td></tr>
</table>

<h2 class=pb>6. Tool contracts</h2>
<p>Every tool returns <code>ToolResult{summary, rows[], card{}, citations[], visual{kind,…}, rules_applied[], warnings[]}</code>.
The model sees <code>for_model()</code> (rows capped at 40, no visual); the UI draws from <code>rows</code>/<code>card</code> of the step the answer points to.</p>
__TOOLS__

<h3>Agent action protocol</h3>
<pre>
{"thought": "short plan", "calls": [{"tool": "downtime", "args": {"area": "Northeast Block 7", "period": "last_4_months", "top_n": 3}}]}
{"thought": "…", "final": {"answer": "markdown with [INS-…] citations",
                            "visual": {"step": 1, "kind": "bar", "title": "Downtime, last 4 months"},
                            "citations": ["INS-0121-0201", "MNT-0121-030"]}}
</pre>
<h3>Visual selection</h3>
<table><tr><th>Answer is about</th><th>Kind</th><th>Rendered from</th></tr>
<tr><td>one well's state</td><td>status_card</td><td>card of well_status / inspection_detail / record_correction</td></tr>
<tr><td>a list</td><td>table</td><td>rows</td></tr>
<tr><td>a value over time</td><td>line</td><td>rows(date, wellhead_temp_c, air_temp_c, producing)</td></tr>
<tr><td>comparing wells / areas</td><td>bar</td><td>rows + hint{x, y}</td></tr>
<tr><td>where things are</td><td>map</td><td>rows with lat/lon (looked up by well if missing)</td></tr>
<tr><td>two dimensions</td><td>heatmap</td><td>aggregate(rows_by, columns_by) matrix + total</td></tr></table>
<p>A visual named in the question (regex in <code>requested_visual()</code>) overrides the model's choice.</p>

<h2>7. Memory design</h2>
<table>
<tr><th>Level</th><th>Write path</th><th>Read path</th><th>Scope rule</th></tr>
<tr><td>Shared notes</td><td>record_note → notes(origin=copilot)</td><td>well_status._current_notes, reasons, downtime causes</td><td>well / well_type / fleet</td></tr>
<tr><td>Corrections</td><td>record_correction → corrections</td><td>inspection_facts view (all tools)</td><td>one inspection, every user</td></tr>
<tr><td>Team rules</td><td>record_rule → rule_overrides</td><td>rca_due, inspection_facts threshold</td><td>one well type (or fleet), nothing else</td></tr>
<tr><td>Claims</td><td>record_claim → claims + notes</td><td>well_status.user_claims, other claims</td><td>one well; status from data</td></tr>
<tr><td>Private memory</td><td>services persist messages with asset_ids</td><td>recall_my_conversations (user filter, excludes current chat)</td><td>one user only</td></tr>
<tr><td>Answer ratings</td><td>rate_answer → answer_feedback</td><td>quality review</td><td>own answers only</td></tr>
</table>
<p>Authorship is never taken from the model: Feedback is constructed with the signed-in user id. Superseded corrections and rules stay in the table with status <code>superseded</code>.</p>

<h2>8. Guardrails</h2>
<table>
<tr><th>Layer</th><th>Check</th><th>Where</th></tr>
<tr><td>Input</td><td>empty / &gt; 2,000 chars; prompt-injection patterns; delete/overwrite intent</td><td>guardrails.check_input</td></tr>
<tr><td>Prompt</td><td>tools-only facts, cite only tool IDs, never invent causes, stay on topic, do not reveal instructions</td><td>prompts.SYSTEM</td></tr>
<tr><td>Tool args</td><td>required args, known well/area/user, valid period, field and rule enums</td><td>ToolBox.run, Facts._scope_filter, Feedback</td></tr>
<tr><td>SQL</td><td>sqlglot AST: one SELECT, no DML/DDL/PRAGMA, allowlisted tables (no chat tables), LIMIT 200, read-only connection</td><td>sql_guard.guard_sql, session.get_read_only_engine</td></tr>
<tr><td>Output</td><td>unknown citations removed; ungrounded numbers flagged; requested visual enforced; tool-first for factual questions</td><td>Agent._finish</td></tr>
<tr><td>Privacy</td><td>conversation ownership checked on ask / read / rate</td><td>services.copilot</td></tr>
<tr><td>Resilience</td><td>model fallback 26B→31B with backoff; budget exhaustion returns the best tool summary, marked degraded</td><td>llm.GemmaOnGemini, Agent.run</td></tr>
</table>

<h2>9. Configuration, quality and operations</h2>
<div class=cols><div>
<table><tr><th>Setting</th><th>Default</th></tr>
<tr><td class=mono>LLM_PROVIDER</td><td>gemini (or ollama)</td></tr>
<tr><td class=mono>GEMMA_MODEL / FALLBACK</td><td>gemma-4-26b-a4b-it / gemma-4-31b-it</td></tr>
<tr><td class=mono>OLLAMA_MODEL</td><td>gemma4:e4b</td></tr>
<tr><td class=mono>DATABASE_URL</td><td>sqlite:///./data/wellwell.db</td></tr>
<tr><td class=mono>TODAY</td><td>2026-10-17</td></tr>
<tr><td class=mono>AGENT_MAX_STEPS · timeouts</td><td>6 rounds · 120 s · 40 s per call</td></tr>
</table></div><div>
<ul>
<li><b>Unit tests</b> (pytest): periods, spill classes, SQL guard, JSON parser, guardrails, manual and report extractors.</li>
<li><b>Scenario eval</b>: <code>eval/run_examples.py</code> replays the 18 example scenarios incl. multi-user sequences; checks facts and visual; writes <code>eval/last_run.json</code>.</li>
<li><b>Audit trail</b>: tool_runs keeps every call's args, result, error and latency.</li>
<li><b>Cost</b>: Gemma 4 on the Gemini API free tier; 2–3 model calls per question; deterministic layer in milliseconds.</li>
<li><b>Scale path</b>: same models on Postgres; read-only role for agent SQL; row-level security per team.</li>
</ul></div></div>
"""


def main() -> None:
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    body = BODY.replace("__SCHEMA__", schema_html()).replace("__TOOLS__", tools_html())
    OUT_HTML.write_text(f"<!doctype html><html><head><meta charset=utf-8><title>Well-Well LLD</title>"
                        f"<style>{CSS}</style></head><body>{body}</body></html>", encoding="utf-8")
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={OUT_PDF}", OUT_HTML.as_uri()], check=True, capture_output=True)
    print(OUT_PDF)


if __name__ == "__main__":
    main()
