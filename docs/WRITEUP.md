# Well-Well: Asset Intelligence Copilot

FlytBase AHC SWE Hack Day, Pune · 17 Oct 2026 · Open source (MIT) · Repo: https://github.com/sarthaksingh007/well-well · Run: `docker compose up`, then open http://localhost:8765

**Core idea:** the AI decides *what to look up and how to say it*, and our code decides *what is true*. Every number in an answer is computed in SQL or Python from the records. Every claim cites the inspection, repair, regulator event, note or manual section it came from.

---

## Page 1: System design

```
Web UI (designed in Google Stitch): chat, user picker, status card / table / line / bar / map / heatmap, agent graph, memory drawer
   │  POST /api/ask (steps streamed live)        FastAPI, privacy enforced per user in the service layer
Agent harness ── JSON tool protocol ── Gemini 3.8 Flash → 3.5 Flash → 3.5 Flash-Lite → Gemma 4 26B (fallback chain, backoff, hedged)
   │  guardrails: injection/delete filters · read-only SQL · unknown citations removed · ungrounded numbers flagged
20 typed tools ── status, spills, downtime, aggregate, repairs, RCA, compliance, installed pumps · doc search · SQL · memory
   │
PostgreSQL 16 ── dataset tables · inspection_facts view (corrections applied) · memory · chats · 14,562 BGE-large vectors
Loader ── fleet → manuals/SOPs (rules extracted) → wells, inspections, repairs + reports (facts extracted), regulator events, notes
```

**How the data is stored, indexed and retrieved**
- `wellwell load <folder> --dataset test` upserts rows on the dataset's own IDs. The test release merges next to the sample, and loading twice changes nothing.
- **Rules come from the documents, not from code.** From each manual the loader reads the producing threshold (T200 15 °C, T350 20 °C), the alarm limit and the RCA rule. From each free-text repair report it reads the containment hours, the spill class and the compliance table.
- **One source of truth.** The `inspection_facts` view applies the latest correction to each field. It also works out the margin over air temperature, whether the well is producing, and whether a spill still counts after corrections. Every tool and every user reads from it.
- **Document search.** Manuals, SOPs and reports are split by heading. Each section is embedded with BGE-large (`bge-large-en-v1.5`, run locally) and stored in Postgres, keyed by text hash, so nothing is embedded twice. Results are combined with BM25 keyword search (reciprocal rank fusion).
- For questions that no tool covers, there is a read-only SQL fallback, checked against an allowlist.

**Tools, and how numbers, documents and notes are combined**
The tools are typed and take parameters (any well, area, well type, period or metric), so they don't depend on the exact wording of a question. Sources are combined inside the tools:

- **Downtime** = 7 days for each weekly inspection below the manual's threshold. Each stop is explained by the repair it triggered, or by a note covering that date.
- **SR-02** compares the discovery time with containment hours that appear only in the report text.
- **SR-01, SR-03 and RC-04** compare regulator events with the SOP deadlines.

**Memory, in two levels**
- **Shared:** notes, corrections and claims, stored per well or per well type with the author and the reason. Every user's later answers use them.
- **Learning from feedback:** a correction to raw data or to the AI's analysis keeps the original value, plus who changed it, why and when. A newer correction replaces the older one in answers, but both stay on record. Team rules (for example "T350: RCA after 2 repairs") apply to that well type only.
- **Private:** each user's conversations, the wells mentioned in them, and their "just for me" display preferences (e.g. sort by area, costs in $k). Recall only searches the signed-in user's chats.
- **Self-evolving playbook:** each verified answer saves an anonymised question pattern and the tool plan that answered it. A 👍 or a passed eval raises its score, and a 👎 marks it to avoid. Similar new questions get the saved plan as a hint. It has an on/off switch.

**Conflicts and new data**
- A second-hand claim ("Nathan told me…") is checked against what that person actually told the copilot; it is never acted on as fact.
- A user's claim is checked against the latest inspection. If the data contradicts it, the claim is kept as *unconfirmed*, both sides are shown, and the user is asked to confirm.
- Opinion notes never override data; only formal corrections do. An out-of-date recorded status is flagged.
- New data goes through the same loader (`wellwell load-github` pulls the test release), the rules are re-read, and only new text is embedded. The test release's new fields (T500, plugged wells, structured data corrections, recall notices, MOC memos, operator notes) loaded without schema rewrites; recalled pumps come from the "Parts installed" lines in report text. Anything the parser can't read is listed as a load warning, not guessed.

**Models and cost**
- **Answers:** Gemini 3.8 Flash, then 3.5 Flash, 3.5 Flash-Lite and Gemma 4 26B, all on the free tier of the Gemini API. A model whose daily quota runs out is skipped for the rest of the run.
- **Embeddings:** BGE-large, running locally.
- **Speed:** 1 to 3 model calls per question; the median answer takes about 4 s.
- **Cost:** USD 0.

**What we would do next:** row-level security per team, proactive alerts (new stops, SR deadlines), checking AI spill estimates against the inspection images, and a review queue for claims and corrections.

---

## Page 2: Results

### Sample dataset: the 18 example scenarios
**18/18 scenarios and 27/27 steps correct**, run end to end through the API on a fresh database. "Correct" means the facts, IDs, dates, names, cited records and the right visual. The median answer took 4 s. We also have 95 automated tests (unit, edge cases, API, loader, embeddings, guardrails), and the Docker from-scratch smoke test passes.

Examples: WELL-0001 not producing (INS-0001-0203, 3.2 °C < 15, planned shut-in CTX-0001-003); NE-7 top-3 downtime 0121 49 d, 0138 42 d, 0107 42 d (bar); 2026 spill heatmap by area × month; WELL-0164 correction 24.0 → 41.0 °C seen by every user; Tyler's claim on WELL-0175 contradicted (0.4 °C) and kept as unconfirmed.

### Final questions: our copilot's answers (test dataset, 600 wells, today = 15 Jan 2027)
Each turn asked exactly as written, in order, as the named user; model Gemini 3.5 Flash-Lite. Full answers with tables
and citations: `Well-Well-Final-Answers.pdf`. ✅ correct · ⚠️ partly correct · ❌ wrong.

**Q1 (Michael, reservoir pressure map)** ✅ "The database does not contain reservoir or wellhead pressure measurements… As the
closest honest alternative, the map shows wellhead temperature above air and producing status for all 600 wells (554
producing, 46 not)."

**Q2 (Robert, best producer 2026)** ✅ "Oil volumes are not recorded, so this can't be answered." Labelled stand-in: wells
producing at all 52 weekly inspections in 2026 (e.g. WELL-0008, 0016, 0017), and the most downtime (WELL-0496, 112 days).

**Q3 (Laura, gas reports since 1 Nov)** ⚠️ Rule from [SOP-OPS OPS-1]: a maintenance job or manual inspection within 24 h
(12 h from 1 Jan 2027; drones don't count). Table of 16 gas notes: 13 met, 3 missed, e.g. WELL-0332 (CTX-0332-004, 3 Dec,
no response). The reference has 26 reports, so 10 notes worded differently were missed.

**Q4 (Chris, temporary bypasses)** ⚠️ In place now: WELL-0248 allowed (MOC-2026-031, extended by MOC-2026-044 to 28 Feb 2027);
WELL-0249 still in and flagged; WELL-0405 in since 20 Dec with no MOC (not approved). Allowed: 0013, 0062, 0118, 0476.
Missed: 0249 is *expired* (MOC-2026-036 ran out 15 Dec), and the past violations at 0158, 0087, 0561.

**Q5 (Michael, regulator letter)** ⚠️ Letter of 20 Nov 2026 covers NE-7/NE-8 spills ≥ 5 bbl since 1 Jun 2026: 6 spills; the test is
"soil samples sent to the lab" in the clean-up report. Non-compliant named: WELL-0123 ✓, WELL-0215 ✗ (reference: 0123, 0182, 0262).

**Q6 (Nathan, which status is right)** ⚠️ Compared latest inspection, recorded status and operator notes; e.g. WELL-0010 not
producing (INS-0010-0219, 14 Jan, below the T350 rule) although notes say "unit still running". Did not produce the full list of 12.

**Q7 (Robert, what operators hear)** ⚠️ Top 10 from last-month notes with no repair since: 1 WELL-0323 (stuffing box leak,
CTX-0323-002), 2 WELL-0422 (separator high-pressure alarm), 3 WELL-0329 (drive tripping), then 0370, 0579, 0020, 0581,
0043, 0302, 0097, each quoting its note. Crew first: 0323, 0422, 0329. Missed the recalled-pump and temperature-trend signals.

**Q8 (who told you that)** ✅ mostly.
1. Nathan's WELL-0333 swap was saved as a shared note in his name (FB-0001, no work order).
2. His sort / $k preference was saved privately.
3. Recalled pumps for Robert: the tool returned the 9 from 'Parts installed' vs recall lots LOT-2409-A/C, LOT-2410-B, but the
   written answer also listed 0149/0215/0304, which are not in the tool data ❌.
4. Olivia's second-hand WELL-0577 claim was recorded as unconfirmed, and the pump stays on the list.
5. Nathan's NW-12 list was sorted by area and ID, in $k: 0305 $24.5k, 0426 $24.0k, 0428 $27.1k, plus 0333 shown with his note.

**Q9 (Robert, one session)** ✅ 9.1–9.4, ⚠️ 9.5.
1. 9 recalled pumps: NW-12 ×4, SW-23 ×2, NE-08 ×2, SE-32 ×1.
2. Nathan Pierce leads Northwest Production, which runs 150 wells.
3. OPS-5: at or below −5 °C on a producing well, a heat-trace check is due within 7 days; WELL-0310 missed it (INS-0310-0213,
   17 Dec, −6.0 °C).
4. No H2S measurement exists for WELL-0332.
5. Lost the pump context and listed all 46 non-producing wells.
6. Deal with WELL-0310 first.

**Q10 (Michael, what we wait on)** ❌ Said waiting reasons are not tracked; they are in the repair-report text (parts, crews,
contractors, weather), so the breakdown was missed. It gave 2026 job counts and average duration by area instead.

**Q11 (Olivia, hotter region)** ⚠️ Southeast Block 31 runs hottest (31.4 °C above air vs 26.3–29.2 °C elsewhere), as a bar chart;
it did not say this is mostly because SE-31 has the most T500 wells.

### What worked
- Deterministic tools made answers exact and repeatable. The model never does arithmetic.
- Reading thresholds and deadlines from the manuals and SOPs, instead of hard-coding them.
- A single corrected view meant every correction reached every answer and every user.
- Prefetching obvious lookups and switching to a faster model cut typical answers from 40–130 s down to 2–10 s.

### What didn't, and why
- The test release added rules that live only in new documents (operations SOP rev 2, MOC memos, a recall notice, a
  regulator letter) and in free-text operator notes. Our typed tools covered recalls (new `installed_pumps` tool), but
  gas response, bypass approvals and note-vs-inspection status were answered through SQL over note text, which is
  less complete than a dedicated tool. Next step: small parsers that turn operator notes into events (gas, bypass in/out,
  shut-in/restart) the same way we parse repair reports.
- Free-tier quotas: the strongest model ran out mid-run; the chain now skips an exhausted model instead of retrying for hours.
- The free Gemma endpoint often returned 500/504 errors. We fixed this with backoff, hedged requests and a model fallback chain.
- A Postgres NULL-ordering difference picked the wrong downtime cause. Our eval caught it, and explicit ordering fixed it.
- Private recall first fell back to an unrelated chat. It now needs a real match, and otherwise says there is no such conversation.
