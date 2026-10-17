# Well-Well UI design (Google Stitch)

Stitch project: **`15795806469362855286`** ("WellWise — Trust-first well operations copilot"; product renamed to **Well-Well** mid-session, the project title was not renamed)
Design system asset: `assets/2908807678691728921` ("WellWise Industrial Dark")
Open in Stitch: https://stitch.withgoogle.com/projects/15795806469362855286

Scope follows the FlytBase "Asset Intelligence Copilot" brief: **one chat screen with generative UI**. The copilot answers inline with the visual that fits (status card, table, line chart, bar chart, map, heatmap), every answer carries citation chips plus a "How this was computed" line, and memory/corrections are first-class. Mockup data is taken from `data/raw/challenge/sample-data` and `03-evaluation-queries.md` (Q1–Q18).

## Screens

PNG = 1440 px wide render (chat screens are cropped to the viewport; the HTML has the full thread). HTML = Stitch-generated Tailwind reference. Use the HTML as the source of truth for structure and copy.

| # | Screen | Theme | Files (`docs/design/stitch/`) | Stitch screen id |
|---|---|---|---|---|
| 10 | Chat shell, empty state (user picker open, conversations, Memory entry, 6 example questions) | dark | `10-chat-empty-dark.png/.html` | `efe8dbfe6afc436f99c806cebb35a189` |
| 11 | Answers: **well status card** (WELL-0001, Q1) + **table** (NE-8 not producing, Q2 after correction) + "Correction applied" row | dark | `11-chat-status-table-dark.png/.html` | `83f1bbe27aef4775859f95e7b8503384` |
| 12 | Answers: **line chart** (WELL-0001 wellhead vs air, 13 weeks, Q3) + **bar chart** (NE-7 downtime, Q6) | dark | `12-chat-line-bar-dark.png/.html` | `fc56bb676f7048ab814fb26448a23385` |
| 13 | Answers: **map** (NE-8 spills, last 90 days, Q7/Q15) + **heatmap** (2026 spills area x month, Q8) | dark | `13-chat-map-heatmap-dark.png/.html` | `f67c2ee063d141c88d71b90c712586a8` |
| 14 | **Conflict / correction cards**: "Your statement contradicts the data" (Q17), "Two users disagree" (Q18), "Correction applied" (Q12) + rule change for all T350 wells (Q14) | dark | `14-chat-conflict-correction-dark.png/.html` | `785fdef38d724a50872c0feacb92a74f` |
| 15 | **Memory drawer**: Shared well knowledge / My private memory / Corrections log (Q12–Q17) | dark | `15-memory-drawer-dark.png/.html` | `e49e6e4ff62d4d8e8bbdd114378c5b4b` |
| 11-alt | Alternative of 11 (status card more spacious) | dark | `alt/11-chat-status-table-dark-alt.*` | `c207ec08b0944b75ad0400df07c7fd60` |
| 12-alt | Alternative of 12 (full-height, slate bars) | dark | `alt/12-chat-line-bar-dark-alt.*` | `aef39ccd69bb4c368bee0376ac46e414` |

**Light theme:** `10-…-light` through `15-…-light` (`.png` + `.html`), one per dark screen. A Stitch `generate_variants` (COLOR_SCHEME, light tokens below) was submitted for all six dark screens but had not shown up in the project after about 15 minutes. The light files were therefore built locally: the cleaned dark HTML with a token swap (dark hex → light hex from the table below) and a heatmap ramp re-mapped for light, then rendered with headless Chrome at 1440 px. Layout and copy match dark exactly. If the Stitch light variants show up later, they will appear in the project as new screens titled like the dark ones.

**PNG provenance:** `10-chat-empty-dark.png` is the Stitch screenshot. `11…15-*-dark.png` and all `*-light.png` are headless-Chrome renders of the cleaned local HTML. The raw Stitch screenshots for 11–15 dark are in `alt/stitch-raw/`.

Small data slips still in the mockups (fix in the build, not in the design): screen 14 shows a third question from an unnamed "Field Integrity Lead" (it should be a named user, e.g. Laura Simmons). Map labels "NE-08 · SEC 17" and "BOUNDARY … (LEASE …)" are decoration; the build should show only the area name.

### Known differences between PNG and HTML

Stitch added invented chrome on some screens (a top strip "WORKSPACE: NORTHEAST_PROD · SESSION · LATENCY · Telemetry Live", "v2.4-stream", "Grounding: SAP PM + SCADA Historian", "PYDECK ·"/"PLOTLY ·" title prefixes, "COORD: NAD83", "Policy P1 Overwrite", "Export Ledger"). A Stitch cleanup edit was applied in the Stitch canvas but its DOM edits are not returned by the API, so the **PNGs still show that chrome**. The local `.html` files for 11–15 were cleaned by hand (header strip removed, footer note set to "Gemma 4 can be wrong. Every number links to its record.", bar fills set to slate). **Do not build any of that chrome.** Also ignore "Ctrl+Enter to send" (st.chat_input sends on Enter).

### Superseded screens (pre-brief, do not build)

Generated from the first brief (Attention Board / Well 360 / Conflicts inbox / Memory page / Data sources / Citation drawer with Cambay Basin data). Kept in the Stitch project for reference only: `4eb2f40fc8c54be997b8369af0cd9b2a` (Attention Board, saved as `legacy/01-attention-board-dark.*`), `8d525a97d0fc4527966e6305ca4042b7` (Ask), `b0e00657d90f4af08542f00d30f50bd6` (Well 360), `756e98f7e913487b908ada7671ff1a53` (Conflicts), `0a7a8e5f8fd34268aa2e1733aeec01c5` (Memory), `a6c9248da08d490eb8dc19606953f296` (Data sources), `2c742b736903450db06f8d45f3d0617a` (Citation drawer), `b3c77e82b53340c7a80f6d1fee3dff41` (first empty state, "WellWise" name), `4090657ad3f149cb8c74e1590dae67fe` (generated photo asset).

## Design tokens

Risk/status colours are used **only** for status (not producing, breach, unconfirmed, producing). The accent is used **only** for interactive elements (buttons, links, selected items, citation chip text). Charts use neutral slate for data, red only for "not producing"/spill marks.

### Colour — dark (demo theme)

| Token | Hex | Use |
|---|---|---|
| `bg` | `#0E1217` | app background |
| `surface` | `#151B22` | sidebar, cards, answer containers |
| `surface-2` | `#1C242E` | inputs, chips, table header, user bubble |
| `border` | `#27313D` | 1 px hairlines |
| `text` | `#E6EAF0` | primary text |
| `text-muted` | `#8A96A6` | labels, captions, "How this was computed" |
| `text-faint` | `#5B6675` | disabled, axis ticks |
| `accent` | `#4C9EEB` (hover `#3A8BD6`) | buttons, links, selected conversation, citation chip text |
| `danger` | `#E5484D` | not producing, spill, breach, open violation |
| `warning` | `#F2A93B` | out of date, unconfirmed, conflict, report overdue |
| `success` | `#3DB57A` | producing, confirmed, correction applied |
| `slate` | `#64748B` | neutral data series, superseded; context bar `#3A4452` |
| badge tint | status hex at 12 % alpha (`#E5484D1F` etc.) | badge backgrounds |

### Colour — light

| Token | Hex |
|---|---|
| `bg` | `#F6F7F9` |
| `surface` | `#FFFFFF` |
| `surface-2` | `#EEF1F4` |
| `border` | `#D9DEE5` |
| `text` | `#121820` |
| `text-muted` | `#5B6675` |
| `text-faint` | `#8A96A6` |
| `accent` | `#1F6FD1` (hover `#195CB0`) |
| `danger` | `#C9353A` |
| `warning` | `#B7791F` |
| `success` | `#1E8A55` |
| `slate` | `#64748B`; context bar `#C3CAD3` |
| badge tint | status hex at 10 % alpha |

Charts: wellhead line = `text`, air line = `slate` dashed, threshold band = `slate` at 10 %, not-producing points/shading = `danger`; bars = `slate`; heatmap = single-hue `surface-2` → `danger`; map = dark (or light) grey basemap, spill pins `danger`, "report overdue" ring `warning`, other wells `slate`.

### Type

| Role | Font | Size / line height / weight |
|---|---|---|
| UI | IBM Plex Sans | body 14/20 400; secondary 13/20 |
| Page / empty-state heading | IBM Plex Sans | 24/32 600 |
| Card / section title | JetBrains Mono, uppercase, tracking 0.06em | 12/16 600 |
| Caption / label | IBM Plex Sans or JetBrains Mono uppercase | 11–12/16 500, `text-muted` |
| Data: well IDs, record IDs, numbers, dates, temperatures | JetBrains Mono | 12–14 400/500 (large ID in status card 18/24 600) |

Google Fonts: `IBM+Plex+Sans:wght@400;500;600;700` and `JetBrains+Mono:wght@400;500;600`.

### Spacing, radii, layout

- Spacing scale 4 / 8 / 12 / 16 / 24 / 32 px. Card padding 16 px (12 px in dense tables). Gap between chat turns 24–32 px.
- Radii: cards and buttons 6 px; chips and badges 4 px.
- No shadows, no gradients (except the heatmap scale); separation by 1 px borders and surface steps.
- Sidebar 264 px; chat column max 880–920 px, centred; desktop 1440 px wide.

## Component inventory (Streamlit mapping)

| Component | Content | Streamlit |
|---|---|---|
| User picker | avatar initials, name, role · team | `st.sidebar.selectbox` with `format_func` |
| Conversation list | this user's chats only, title + relative date | sidebar buttons / `st.radio` styled |
| Memory entry | "Shared 42 · Private 6 · Corrections 9" | sidebar button opening a dialog or page |
| Citation chip | `[INS-0001-0203]`, `[T200 manual §9.3]`, `[Spill Response SOP SR-01]` | inline HTML `<span class="chip">` |
| "How this was computed" | mono muted line under every visual | `st.caption` with custom CSS |
| Well status card | ID + name + status badge; type, area, team, in charge, installed, location; recorded vs inferred status with Δ readout; last inspection; downtime; open regulator items; last repair; active shared notes; equipment | `st.container(border=True)` + `st.columns(2)` + HTML badges |
| Table answer | Well, Type, Above air, Recorded status, Reason, Citations | `st.dataframe` (or HTML table for chip columns) |
| Line / bar / heatmap | as above | Plotly (`plotly_dark` base + token colours) |
| Map | pins coloured by status, tooltip with date/volume/reportability | pydeck `ScatterplotLayer` |
| Contradiction card | "You said" vs "The data shows", unconfirmed tag, actions | bordered container with amber left border + `st.button` row |
| Two users disagree | two notes side by side, AI detection line, choice buttons | same |
| Correction applied | record + field, ~~original~~ → corrected, by whom, why, scope chip, effect, confirm/undo | green left border container |
| Memory drawer | tabs Shared well knowledge / My private memory / Corrections log | `st.dialog` or page with `st.tabs` |
| Follow-up chips | 2–3 suggested questions | `st.button` (pills) |
