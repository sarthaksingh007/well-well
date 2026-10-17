"""Run the final questions in order, as the named users, and save every full answer.

    uv run python eval/run_final.py [--only Q3]
"""
import argparse
import json
import sys
import time
from pathlib import Path

from wellwell.services.copilot import ask, start_conversation

MICHAEL, ROBERT, LAURA, CHRIS, OLIVIA, NATHAN = "U-201", "U-301", "U-401", "U-402", "U-501", "U-601"

# (id, user, question, new_session)
TURNS = [
    ("Q1", MICHAEL, "All our wells feed one reservoir. Map the reservoir pressure across the field from the pressure data.", True),
    ("Q2", ROBERT, "Which well produced the most oil in 2026?", True),
    ("Q3", LAURA, "Since 1 Nov 2026, every time an operator note reports gas at a well, did we get someone out to that "
     "well in time under our operating procedures? List every report and whether the response met the deadline.", True),
    ("Q4", CHRIS, "Across all areas, which temporary flowline bypasses are in place right now, and is each one allowed? "
     "Also list any bypass, past or present, that stayed in after its approval ran out or was never approved at all.", True),
    ("Q5", MICHAEL, "The State Oil & Gas Commission wrote to us in November 2026 about spills. Which of our spills does "
     "the letter cover, and on which ones are we not compliant? Show me a table.", True),
    ("Q6", NATHAN, "Across the whole field, not just my area, for which wells do the latest inspection and the operator "
     "notes disagree about whether the well is producing oil? For each one, what is its real status right now, and why?", True),
    ("Q7.1", ROBERT, "Over the last month the operators have written notes about wells that sound or behave wrong. Which "
     "of those wells haven't had a repair since? Rank the 10 most likely to need a repair in the next 90 days, other than "
     "a spill clean-up, and quote the notes as evidence.", True),
    ("Q7.2", ROBERT, "Which three would you send a crew to first this week, and why?", False),
    ("Q8.1", NATHAN, "WELL-0333's recalled pump was swapped out yesterday. The work order isn't in the system yet. Please "
     "count it as replaced from now on.", True),
    ("Q8.2", NATHAN, "Also, just for me: whenever you list wells, sort them by area and then by well ID, and show costs in "
     "thousands of dollars.", False),
    ("Q8.3", ROBERT, "Which recalled pumps are still in the ground right now?", True),
    ("Q8.4", OLIVIA, "Nathan told me the recalled pump on WELL-0577 was swapped as well. Take it off the recall list.", True),
    ("Q8.5", NATHAN, "Show me the recalled pumps that are still in the ground in my area, and what it cost to install each "
     "one.", True),
    ("Q9.1", ROBERT, "How many recalled pumps are still in the ground, and in which areas?", True),
    ("Q9.2", ROBERT, "Different topic. Who leads Northwest Production, and how many wells does that team run?", False),
    ("Q9.3", ROBERT, "What does our operations SOP say about cold weather? Name one well that missed the check in December.", False),
    ("Q9.4", ROBERT, "An operator reported a gas smell at WELL-0332 on 3 Dec. What was the H2S reading?", False),
    ("Q9.5", ROBERT, "Back to the pumps from my first question: which of those wells are not producing oil right now, and "
     "when was each one last repaired?", False),
    ("Q9.6", ROBERT, "Of all the wells we've talked about today, which one would you deal with first, and why?", False),
    ("Q10", MICHAEL, "When a well stays shut in longer than the repair itself, what are we usually waiting on: parts, crews, "
     "contractors or weather? Break it down by area for jobs started in 2026, with the typical number of extra days, and "
     "tell me if any area stands out.", True),
    ("Q11", OLIVIA, "Which region of the field consistently runs hotter?", True),
]


def run_group(turns, out: Path) -> None:
    conversation = None
    for turn_id, user, question, new_session in turns:
        if new_session or conversation is None:
            conversation = start_conversation(user)
        started = time.time()
        reply = ask(user, question, conversation, search_mode="hybrid")
        conversation = reply.conversation_id
        record = {"id": turn_id, "user": user, "question": question, "answer": reply.answer,
                  "visual": reply.visual, "citations": reply.citations,
                  "tools": [s.get("tool") for s in reply.steps], "model": reply.model,
                  "seconds": round(time.time() - started, 1), "trust": reply.trust}
        (out / f"{turn_id}.json").write_text(json.dumps(record, indent=1, default=str))
        kind = (reply.visual or {}).get("kind")
        rows = len((reply.visual or {}).get("rows") or [])
        print(f"{turn_id} {record['seconds']}s tools={record['tools']} visual={kind} rows={rows}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", help="question ids or prefixes, e.g. Q3 Q8")
    parser.add_argument("--out", default="docs/results/final")
    parser.add_argument("--workers", type=int, default=1, help="questions answered at the same time (turns of one question stay in order)")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list] = {}
    for turn in TURNS:
        if args.only and not any(turn[0] == o or turn[0].startswith(o + ".") for o in args.only):
            continue
        groups.setdefault(turn[0].split(".")[0], []).append(turn)
    if args.workers > 1:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(args.workers) as pool:
            list(pool.map(lambda g: run_group(g, out), groups.values()))
    else:
        for group in groups.values():
            run_group(group, out)
    from final_report import build, to_pdf

    page = build(out)
    pdf = to_pdf(page)
    print(f"Report: {pdf or page}", flush=True)


if __name__ == "__main__":
    main()
