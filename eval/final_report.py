"""Turn the saved final answers into one HTML page and a PDF, for the write-up and the submission.

    uv run python eval/final_report.py [docs/results/final]
"""
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import markdown

USERS = {"U-201": "Michael Turner", "U-301": "Robert Hayes", "U-401": "Laura Simmons", "U-402": "Chris Dalton",
         "U-501": "Olivia Grant", "U-601": "Nathan Pierce"}
CSS = """@page{size:A4;margin:12mm}body{font:9pt/1.4 -apple-system,Helvetica,Arial,sans-serif;color:#111}
h1{font-size:16pt;margin:0 0 4px}h2{font-size:11.5pt;margin:16px 0 4px;border-bottom:1.5px solid #0b6;padding-bottom:2px}
.q{background:#f3f6f4;border-left:3px solid #0b6;padding:5px 8px;margin:4px 0 6px}.meta{color:#555;font-size:8pt}
table{border-collapse:collapse;width:100%;font-size:7.4pt;margin:5px 0}th,td{border:1px solid #ccd;padding:2px 4px;
text-align:left;vertical-align:top}th{background:#eef3f0}h2,.q{break-after:avoid}tr{break-inside:avoid}p{margin:3px 0}ul{margin:2px 0}"""


def _order(path: Path) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", path.stem))


def _rows_table(rows: list[dict], limit: int = 40) -> str:
    if not rows:
        return ""
    columns = [c for c in rows[0] if c not in ("lat", "lon", "text", "what_people_said")][:9]
    head = "".join(f"<th>{html.escape(c)}</th>" for c in columns)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(r.get(c, '')))[:120]}</td>" for c in columns) + "</tr>"
                   for r in rows[:limit])
    more = f"<p class=meta>…and {len(rows) - limit} more rows</p>" if len(rows) > limit else ""
    return f"<table><tr>{head}</tr>{body}</table>{more}"


def build(folder: Path) -> Path:
    turns = [json.loads(p.read_text()) for p in sorted(folder.glob("Q*.json"), key=_order)]
    parts = ["<h1>Well-Well: answers to the final questions</h1>",
             "<p class=meta>Test dataset (600 wells, today = 15 Jan 2027). Each turn asked exactly as written, in order, "
             "as the named user. Answers, citations and the visual's data are the copilot's own output.</p>"]
    for t in turns:
        visual = t.get("visual") or {}
        kind = visual.get("kind") or "none"
        meta = (f"{USERS.get(t['user'], t['user'])} · model {t.get('model')} · {t.get('seconds')} s · tools: "
                f"{', '.join(x for x in t.get('tools', []) if x) or 'none'} · visual: {kind}")
        parts.append(f"<div class=turn><h2>{html.escape(t['id'])}</h2><div class=q>{html.escape(t['question'])}</div>"
                     f"<p class=meta>{html.escape(meta)}</p>{markdown.markdown(t['answer'], extensions=['tables'])}"
                     f"{_rows_table(visual.get('rows') or [])}</div>")
    page = folder / "final-answers.html"
    page.write_text(f"<!doctype html><meta charset=utf-8><title>Well-Well final answers</title><style>{CSS}</style>"
                    + "".join(parts))
    return page


def to_pdf(page: Path) -> Path | None:
    candidates = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", shutil.which("google-chrome"),
                  shutil.which("chromium"), shutil.which("chromium-browser"), shutil.which("chrome")]
    chrome = next((c for c in candidates if c and Path(c).exists()), None)
    if not chrome:
        return None
    pdf = page.with_suffix(".pdf")
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--user-data-dir={page.parent / '.chrome'}", f"--print-to-pdf={pdf}", page.resolve().as_uri()],
                   capture_output=True, timeout=120)
    shutil.rmtree(page.parent / ".chrome", ignore_errors=True)
    return pdf if pdf.exists() else None


def main(folder: str = "docs/results/final") -> None:
    page = build(Path(folder))
    pdf = to_pdf(page)
    print(f"Report: {pdf or page}" + ("" if pdf else " (no Chrome found, so HTML only; open it and print to PDF)"))


if __name__ == "__main__":
    main(*sys.argv[1:])
