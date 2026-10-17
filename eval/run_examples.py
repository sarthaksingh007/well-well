"""Runs the challenge's example scenarios end to end through the copilot and checks key facts.

Usage: uv run python eval/run_examples.py [--only Q1,Q2] [--fresh]
--fresh reloads the sample dataset first so memory from earlier runs does not leak in.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from wellwell.services.copilot import ask, start_conversation

MICHAEL, TYLER, JESSICA, JAKE, LAURA = "U-201", "U-204", "U-202", "U-102", "U-401"

# Each scenario: list of (key, user, question, new_chat, expected facts that must appear, expected visual).
SCENARIOS: dict[str, list[tuple]] = {
    "Q1": [("Q1", MICHAEL, "Is WELL-0001 producing oil right now? When was it last inspected, and by whom?", True,
            ["INS-0001-0203", "Ryan Brooks", "3.2", "CTX-0001-003", "Jake Morales", "drone"], "status_card")],
    "Q2": [("Q2", MICHAEL, "Which wells in Northeast Block 8 are not producing oil right now, and why? Show them in a "
            "table.", True, ["WELL-0164", "WELL-0170", "WELL-0172", "WELL-0175", "MNT-0170-031", "MNT-0172-028"],
            "table")],
    "Q3": [("Q3", MICHAEL, "Show a line chart of WELL-0001's wellhead temperature against air temperature for the "
            "last 13 weeks, and mark when it was not producing oil.", True, ["8 Oct", "15 Oct"], "line")],
    "Q4": [("Q4", MICHAEL, "How many oil spills has WELL-0001 had in the last 4 years, and how much oil in total? "
            "List them in a table.", True, ["6", "15.1", "INS-0001-0070"], "table")],
    "Q5": [("Q5", MICHAEL, "How many days was WELL-0001 down in 2025?", True, ["21", "MNT-0001-021", "MNT-0001-023"],
            None)],
    "Q6": [("Q6", MICHAEL, "Which 3 wells in Northeast Block 7 had the most downtime in the last 4 months? Show a bar "
            "chart.", True, ["WELL-0121", "49", "WELL-0138", "WELL-0107", "42"], "bar")],
    "Q7": [("Q7", MICHAEL, "Show on a map every well in Northeast Block 8 with a real spill in the last 90 days.", True,
            ["WELL-0151", "WELL-0165", "WELL-0175", "WELL-0186"], "map")],
    "Q8": [("Q8", MICHAEL, "Show a heatmap of 2026 spills by geographic area and month.", True, ["35", "43", "37", "26"],
            "heatmap")],
    "Q9": [("Q9", MICHAEL, "Which rules has WELL-0001 broken in the last 12 months, and what is still open?", True,
            ["SR-01", "SR-02", "SR-03", "RC-04", "REG-0001-002", "5,000"], "table")],
    "Q10": [("Q10", MICHAEL, "In Southwest Block 24, which spills in the last 90 days were not contained within the SOP "
             "time limit?", True, ["WELL-0082", "WELL-0100", "7.1", "8.1"], "table")],
    "Q11": [
        ("Q11a", MICHAEL, "Which 3 wells in Northeast Block 7 had the most downtime in the last 4 months? Show a bar "
         "chart.", True, ["WELL-0121"], "bar"),
        ("Q11b", MICHAEL, "For the three wells we discussed yesterday, what have repairs cost this year?", True,
         ["25,794", "46,942", "5,176"], None),
    ],
    "Q12": [
        ("Q12a", MICHAEL, "The 15 Oct reading for WELL-0164 is wrong. The handheld thermal gun used in that inspection "
         "was faulty; the field crew re-measured the wellhead at 41.0 °C.", True,
         ["INS-0164-0205", "24.0", "21.1", "Pump jack not moving"], None),
        ("Q12b", MICHAEL, "Which wells in Northeast Block 8 are not producing oil right now, and why? Show them in a "
         "table.", False, ["WELL-0170", "WELL-0172", "WELL-0175"], "table"),
    ],
    "Q13": [("Q13", MICHAEL, "The AI estimated the 24 Sep spill at WELL-0151 as 4.5 bbl. The field crew measured 5.4 "
             "bbl.", True, ["INS-0151-0201", "SR-01", "MNT-0151-025", "23 Nov"], None)],
    "Q14": [
        ("Q14a", MICHAEL, "Which wells in Northeast Block 7 need a root-cause analysis on their pump jack?", True,
         ["WELL-0108", "WELL-0121", "WELL-0138"], "table"),
        ("Q14b", MICHAEL, "Note for all T350 wells: our team now wants a pump jack root-cause analysis after 2 repairs, "
         "not 3.", False, ["T350"], None),
        ("Q14c", MICHAEL, "Which wells in Northeast Block 7 need a root-cause analysis on their pump jack?", True,
         ["WELL-0108", "WELL-0120", "WELL-0121", "WELL-0138"], "table"),
    ],
    "Q15": [
        ("Q15a", JESSICA, "Is WELL-0164 producing oil right now?", True, ["41.0", "Michael Turner", "Pump jack not moving"],
         "status_card"),
        ("Q15b", LAURA, "Which spills in Northeast Block 8 in the last 90 days needed a report to the regulator?", True,
         ["WELL-0151", "5.4"], "table"),
    ],
    "Q16": [
        ("Q16a", JAKE, "FYI for everyone: WELL-0007 will be shut in from 17 Oct for a 10-day planned workover.", True,
         ["WELL-0007"], None),
        ("Q16b", MICHAEL, "Is any downtime planned for WELL-0007?", True, ["17 Oct", "Jake Morales"], None),
        ("Q16c", JESSICA, "For the three wells we discussed yesterday, what have repairs cost this year?", True,
         ["earlier conversation"], None),
    ],
    "Q17": [
        ("Q17a", MICHAEL, "WELL-0175 has been shut in for planned work since 14 Oct.", True, ["WELL-0175"], None),
        ("Q17b", TYLER, "WELL-0175 is running fine.", True, ["0.4", "Jessica Reed", "Michael Turner"], None),
        ("Q17c", JAKE, "WELL-0001 is back online since 14 Oct.", True, ["CTX-0001-003", "20 Oct"], None),
    ],
    "Q18": [("Q18", MICHAEL, "Was the 20 Aug spill at WELL-0101 real oil?", True,
             ["INS-0101-0197", "CTX-0101-002", "CTX-0101-003", "MNT-0101-029"], None)],
}


def _normalise(text: str) -> str:
    return text.replace(" ", " ").replace(" ", " ")


def run(only: list[str] | None) -> int:
    results, conversations = [], {}
    keys = only or list(SCENARIOS)
    for key in keys:
        for step_key, user, question, new_chat, expected, visual in SCENARIOS[key]:
            conversation = None if new_chat else conversations.get(user)
            if conversation is None:
                conversation = start_conversation(user)
            started = time.time()
            reply = ask(user, question, conversation)
            conversations[user] = reply.conversation_id
            haystack = _normalise(reply.answer + " " + json.dumps(reply.visual or {}, default=str))
            missing = [fact for fact in expected if fact.lower() not in haystack.lower()]
            got_visual = (reply.visual or {}).get("kind")
            visual_ok = visual is None or got_visual == visual
            results.append({"key": step_key, "ok": not missing and visual_ok, "missing": missing,
                            "visual": got_visual, "expected_visual": visual, "seconds": round(time.time() - started),
                            "tools": [s["tool"] for s in reply.steps], "answer": reply.answer})
            mark = "PASS" if not missing and visual_ok else "FAIL"
            if mark == "PASS" and reply.message_id:
                from wellwell.services import playbook

                playbook.feedback(reply.message_id, "eval_pass")
            print(f"{mark} {step_key:5} {round(time.time() - started):>3}s tools={[s['tool'] for s in reply.steps]} "
                  f"visual={got_visual} missing={missing}")
            if mark == "FAIL":
                print("      " + re.sub(r"\s+", " ", reply.answer)[:700])
    passed = sum(r["ok"] for r in results)
    print(f"\n{passed}/{len(results)} steps passed")
    Path("eval/last_run.json").write_text(json.dumps(results, indent=2))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="")
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()
    if args.fresh:
        from wellwell.db.session import create_schema, create_views, session_scope
        from wellwell.ingestion.loader import DatasetLoader
        from wellwell.cli import DEFAULT_SAMPLE

        create_schema(drop_first=True)
        with session_scope() as session:
            DatasetLoader(session, DEFAULT_SAMPLE, "sample").run()
        create_views()
    sys.exit(run([k.strip() for k in args.only.split(",") if k.strip()] or None))
