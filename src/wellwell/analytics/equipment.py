import re
from sqlalchemy import text

from wellwell.analytics.result import ToolResult

_PART = re.compile(r"\*\*Parts installed:\*\*\s*(?P<model>[^,\n]+?)\s+pump,\s*serial\s+(?P<serial>[\w-]+),\s*lot\s+(?P<lot>[\w-]+)", re.I)
_RECALL_SECTION = re.compile(r"##\s*What is recalled(?P<body>.*?)(?:\n##\s|\Z)", re.S | re.I)
_LOT_LINE = re.compile(r"^\s*[-*]\s*(LOT-[\w-]+)", re.M)


def recall_notices(session) -> list[dict]:
    """Every recall notice in the documents, with the lots it names in its 'What is recalled' list."""
    notices = []
    for doc_id, title, body in session.execute(text(
            "SELECT doc_id, title, text FROM documents WHERE kind = 'sop' AND text ILIKE '%recall%'")):
        section = _RECALL_SECTION.search(body)
        lots = _LOT_LINE.findall(section.group("body")) if section else []
        if lots:
            deadline = re.search(r"by (\d{1,2} \w{3} \d{4})", body)
            notices.append({"doc_id": doc_id, "title": title, "lots": lots,
                            "deadline": deadline.group(1) if deadline else None})
    return notices


def installed_pumps(session, well: str | None = None, area: str | None = None, recalled_only: bool = False) -> ToolResult:
    """The pump in each well today (latest 'Parts installed' line), checked against recall notices."""
    rows = session.execute(text("""
        SELECT m.asset_id, w.area_id, a.name AS area_name, w.team_id, m.maintenance_id, m.started_at, m.completed_at,
               m.cost_total_usd, d.doc_id, d.text
        FROM maintenance m JOIN documents d ON d.doc_id = m.report_doc_id
        JOIN wells w ON w.asset_id = m.asset_id LEFT JOIN areas a ON a.area_id = w.area_id
        WHERE d.text ILIKE '%Parts installed%' ORDER BY m.asset_id, m.started_at""")).mappings().all()
    current: dict[str, dict] = {}
    history: dict[str, list] = {}
    for r in rows:
        match = _PART.search(r["text"])
        if not match:
            continue
        item = {"asset_id": r["asset_id"], "area_id": r["area_id"], "area_name": r["area_name"], "team_id": r["team_id"],
                "model": match["model"].strip(), "serial": match["serial"], "lot": match["lot"],
                "installed_by_job": r["maintenance_id"], "installed_on": r["started_at"], "report": r["doc_id"],
                "install_cost_usd": r["cost_total_usd"]}
        history.setdefault(r["asset_id"], []).append(item)
        current[r["asset_id"]] = item
    notices = recall_notices(session)
    recalled_lots = {lot: n for n in notices for lot in n["lots"]}
    notes = session.execute(text("""
        SELECT n.note_id, n.asset_id, n.created_at, n.text, u.name AS author FROM notes n
        LEFT JOIN users u ON u.user_id = n.author_user_id WHERE n.origin = 'copilot'""")).mappings().all()
    claims = session.execute(text("""
        SELECT c.claim_id, c.asset_id, c.statement, c.status, c.created_at, u.name AS author FROM claims c
        LEFT JOIN users u ON u.user_id = c.author_user_id""")).mappings().all()
    out = []
    for asset_id, item in sorted(current.items()):
        notice = recalled_lots.get(item["lot"])
        item = dict(item, recalled=bool(notice), recall_notice=notice["doc_id"] if notice else None,
                    earlier_pumps=len(history[asset_id]) - 1,
                    recalled_pump_replaced=any(recalled_lots.get(h["lot"]) for h in history[asset_id][:-1]) and not notice)
        said = [f'{n["author"]} ({n["created_at"]:%d %b %Y}, {n["note_id"]}): {n["text"]}' for n in notes if n["asset_id"] == asset_id]
        said += [f'{c["author"]} claimed ({c["created_at"]:%d %b %Y}, {c["claim_id"]}, {c["status"]}): {c["statement"]}'
                 for c in claims if c["asset_id"] == asset_id]
        item["what_people_said"] = " | ".join(said) or None
        out.append(item)
    if well:
        out = [r for r in out if r["asset_id"] == well]
    if area:
        out = [r for r in out if area.lower() in (r["area_id"] or "").lower() or area.lower() in (r["area_name"] or "").lower()]
    if recalled_only:
        out = [r for r in out if r["recalled"]]
    citations = [r["installed_by_job"] for r in out] + [n["doc_id"] for n in notices]
    lots = "; ".join(f'{n["doc_id"]} ({n["title"]}): lots {", ".join(n["lots"])}, replace by {n["deadline"]}' for n in notices)
    summary = (f"{len(out)} well(s). Current pump = the latest 'Parts installed' line in that well's repair reports "
               f"Recall notices: {lots or 'none found'}. "
               "This is what the records show; 'what_people_said' lists shared notes and claims from users that the "
               "records do not yet confirm (e.g. a swap with no work order) — report them with who said it, and do "
               "not silently drop or keep a pump on that basis.")
    return ToolResult(summary=summary, rows=out, citations=citations[:80], visual={"kind": "table"},
                      rules_applied=[f'{n["doc_id"]}: recalled lots {", ".join(n["lots"])}' for n in notices])
