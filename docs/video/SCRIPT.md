# Well-Well demo video: narration script

Length **3:08**, 1440×900. Follows the brief: first how we built it, then one real question end to end (WELL-0001).

`well-well-demo.mp4` has the Sarvam AI voice (bulbul:v3, aditya). `well-well-demo-no-voice.mp4` is the same picture with no audio: to record in your own voice, read each line at its start time.


## How we built it

**0:00.0–0:09.1** · Hi everyone. This is Well-Well, a copilot for oil-well teams. You ask a question in plain English, and it gives you the answer with proof from the records.

**0:09.1–0:21.3** · So the problem is that the data is spread everywhere: inspections, repairs, reports, manuals and people's notes. Even a simple question takes hours. So we put it all in one place.

**0:21.3–0:35.8** · First, the data. We load the full dataset into PostgreSQL: two hundred wells, around forty thousand inspections, repairs, reports and notes. The same command also loads the test dataset, and it just merges by ID.

**0:35.8–0:49.1** · We didn't hard-code the rules. The loader reads them from the manuals and SOPs: when a well counts as producing, the spill deadlines, the RCA rule. So if the manuals change, the answers change too.

**0:49.1–1:06.2** · Next, the tools. Every number comes from typed SQL tools, like well status, downtime, spills and compliance, so the AI never makes up numbers. For the manuals and reports we use search: BGE-large embeddings stored in Postgres, plus keyword search.

**1:06.2–1:21.0** · The AI model is Gemini Flash-Lite, with Gemma 4 as a backup. Its only jobs are to pick the right tools and write the answer. Then our checks remove any citation it didn't actually read, and flag any number that isn't in the data.

**1:21.0–1:39.2** · Memory has two levels. Shared memory holds corrections, notes and claims that every user sees. Private memory is each user's own chat history. When someone corrects data, we keep the original value with who changed it and why, and if records disagree, it says so openly.

**1:39.2–1:52.8** · We also added a playbook that learns which tool plans worked before, and it has an on and off switch. The backend is Python and FastAPI, we designed the UI in Google Stitch, and everything runs with one docker compose up.


## One use case

**1:52.8–2:05.1** · Now let me show one real question. I'm Michael, the production supervisor, and I'm asking: is WELL-0001 producing oil right now, when was it last inspected, and by whom?

**2:05.1–2:13.7** · You can see the steps live. It calls the well status tool, which reads the latest inspection and compares it with the T200 manual's rule.

**2:13.7–2:26.4** · The answer is no, it's not producing. The wellhead is only 3.2 degrees above the air temperature, and the manual says it needs at least 15. It was last inspected on 15 October by Ryan Brooks.

**2:26.4–2:32.8** · Notice that the system still says the well is active. That's out of date, and the copilot flags it.

**2:32.8–2:39.0** · It picked a status card automatically. For other questions it shows a table, chart, map or heatmap.

**2:39.0–2:48.9** · Every fact has a source. If I click this note, I see the original: the well was shut in for a planned rod replacement. So the stop is planned, not a failure.

**2:48.9–2:57.3** · If I open how this was answered, I can see the full path: my question, the tool it called, and the exact records it used.


## Close

**2:57.3–3:08.0** · So that's Well-Well. It's correct, it shows the evidence, and it's honest about conflicts. It's open source under MIT, and the link is in our write-up. Thank you!
