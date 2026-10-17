import asyncio
import json
import re
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text

from wellwell.config import PROJECT_ROOT, get_settings
from wellwell.db.dialect import group_concat, plain_row
from wellwell.db.session import session_scope
from wellwell.services import copilot, loading

WEB_DIR = PROJECT_ROOT / "web"

app = FastAPI(title="Well-Well API", version="1.0")


@app.on_event("startup")
def _warm_up_search() -> None:
    """Load the embedding model in the background so the first question isn't slowed by it."""
    def warm() -> None:
        try:
            from wellwell.analytics import embeddings

            if embeddings.available():
                embeddings.embed_query("warm up")
                embeddings._matrix()
        except Exception:
            pass

    threading.Thread(target=warm, daemon=True).start()

EXAMPLES = [
    {"question": "Which wells in my area were down the longest this summer, and why?", "visual": "bar chart"},
    {"question": "Is WELL-0001 producing oil right now? Show its status card.", "visual": "status card"},
    {"question": "Which wells in Northeast Block 8 are not producing oil right now, and why?", "visual": "table"},
    {"question": "Show WELL-0001 wellhead vs air temperature for the last 13 weeks.", "visual": "line chart"},
    {"question": "Show on a map every well in Northeast Block 8 with a real spill in the last 90 days.",
     "visual": "map"},
    {"question": "Show a heatmap of 2026 spills by area and month.", "visual": "heatmap"},
]


class UserRequest(BaseModel):
    user_id: str


class ConversationRequest(BaseModel):
    user_id: str
    conversation_id: int


class AskRequest(BaseModel):
    user_id: str
    question: str = Field(max_length=4000)
    conversation_id: int | None = None
    search_mode: Literal["hybrid", "semantic", "keyword"] = "semantic"
    use_playbook: bool = True


class RateRequest(BaseModel):
    user_id: str
    message_id: int
    rating: str
    comment: str | None = None


def _rows(sql: str, **params) -> list[dict]:
    with session_scope() as session:
        return [plain_row(dict(r)) for r in session.execute(text(sql), params).mappings()]


def _with_locations(visual: dict | None) -> dict | None:
    """Maps need coordinates; fill them in from the well register when a tool's rows don't carry them."""
    if not visual or visual.get("kind") != "map":
        return visual
    rows = visual.get("rows") or []
    missing = {r.get("well") or r.get("asset_id") for r in rows if r.get("lat") is None}
    missing.discard(None)
    if missing:
        located = {r["asset_id"]: r for r in _rows("SELECT asset_id, lat, lon FROM wells")}
        for row in rows:
            well = row.get("well") or row.get("asset_id")
            if row.get("lat") is None and well in located:
                row["lat"], row["lon"] = located[well]["lat"], located[well]["lon"]
    return visual


@app.exception_handler(PermissionError)
async def _forbidden(_request: Request, error: PermissionError):
    return JSONResponse({"error": str(error)}, status_code=403)


@app.exception_handler(ValueError)
async def _bad_request(_request: Request, error: ValueError):
    return JSONResponse({"error": str(error)}, status_code=400)


@app.post("/api/bootstrap")
def bootstrap() -> dict:
    if data_status()["state"] in ("initialising", "empty", "failed", "loading"):
        return {"users": [], "fleet": None, "examples": EXAMPLES, "today": get_settings().today,
                "model": get_settings().gemma_model, "status": data_status()}
    users = _rows("""SELECT u.user_id, u.name, u.role, u.team_id, t.name AS team
                       FROM users u LEFT JOIN teams t ON t.team_id = u.team_id ORDER BY u.user_id""")
    fleet = _rows("""SELECT (SELECT COUNT(*) FROM wells) AS wells, (SELECT COUNT(*) FROM areas) AS areas,
                            (SELECT MAX(inspection_date) FROM inspections) AS data_to""")[0]
    from wellwell.analytics import embeddings

    return {"users": users, "fleet": fleet, "examples": EXAMPLES, "today": get_settings().today,
            "model": get_settings().llm_models.split(",")[0],
            "search": {"modes": ["hybrid", "semantic", "keyword"], "default": "semantic",
                       "semantic_available": embeddings.available(),
                       "embedding_model": get_settings().embed_model if embeddings.available() else None}}


@app.post("/api/conversations/list")
def conversations(request: UserRequest) -> list[dict]:
    return copilot.conversation_history(request.user_id)


@app.post("/api/conversations/new")
def new_conversation(request: UserRequest) -> dict:
    return {"conversation_id": copilot.start_conversation(request.user_id)}


@app.post("/api/conversations/messages")
def messages(request: ConversationRequest) -> list[dict]:
    result = copilot.conversation_messages(request.user_id, request.conversation_id)
    for message in result:
        message["visual"] = _with_locations(message.get("visual"))
    return result


@app.post("/api/ask")
async def ask(request: AskRequest) -> StreamingResponse:
    """Streams newline-delimited JSON: {"type": "step"} events while the agent works, then one "answer"."""
    current = data_status()
    if current["state"] not in ("ready", "updating"):
        async def not_ready():
            yield json.dumps({"type": "answer", "conversation_id": request.conversation_id, "message_id": None,
                              "answer": f"I can't answer yet. {current['message']}", "visual": None,
                              "citations": [], "steps": [], "trust": {"not_ready": True}, "model": None,
                              "latency_ms": 0, "blocked": True}) + "\n"
        return StreamingResponse(not_ready(), media_type="application/x-ndjson")
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_step(text_value: str) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, {"type": "step", "text": text_value})

    def work() -> None:
        try:
            reply = copilot.ask(request.user_id, request.question, request.conversation_id, on_step=on_step,
                                search_mode=request.search_mode, use_playbook=request.use_playbook)
            payload = asdict(reply)
            payload["visual"] = _with_locations(payload.get("visual"))
            event = {"type": "answer", **payload}
        except PermissionError as error:
            event = {"type": "error", "message": str(error)}
        except Exception as error:
            event = {"type": "error", "message": f"Something went wrong while answering: {str(error)[:300]}"}
        loop.call_soon_threadsafe(queue.put_nowait, event)
        loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=work, daemon=True).start()

    async def stream():
        while True:
            event = await queue.get()
            if event is None:
                break
            yield json.dumps(event, default=str) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.post("/api/rate")
def rate(request: RateRequest) -> dict:
    copilot.rate_answer(request.user_id, request.message_id, request.rating, request.comment)
    return {"ok": True}


@app.post("/api/memory")
def memory(request: UserRequest) -> dict:
    names = {u["user_id"]: u["name"] for u in _rows("SELECT user_id, name FROM users")}

    def named(rows: list[dict], *keys: str) -> list[dict]:
        for row in rows:
            for key in keys:
                row[key.replace("_user_id", "")] = names.get(row.get(key), row.get(key))
        return rows

    shared = named(_rows("""SELECT note_id, asset_id, scope, well_type, author_user_id, text, valid_from, valid_to,
                                   created_at FROM notes WHERE origin = 'copilot' ORDER BY created_at DESC"""),
                   "author_user_id")
    corrections = named(_rows("""SELECT correction_id, kind, scope, asset_id, target_id, field, original_value,
                                        corrected_value, reason, author_user_id, status, created_at
                                   FROM corrections WHERE source_note_id IS NULL ORDER BY created_at DESC"""),
                        "author_user_id")
    rules = named(_rows("""SELECT override_id, scope, well_type, rule, equipment_kind, value, manual_value, statement,
                                  author_user_id, status, created_at FROM rule_overrides ORDER BY created_at DESC"""),
                  "author_user_id")
    claims = named(_rows("""SELECT claim_id, asset_id, author_user_id, statement, claimed_state, status, created_at
                              FROM claims ORDER BY created_at DESC"""), "author_user_id")
    private = _rows(f"""SELECT c.conversation_id, c.title, c.started_on,
                              (SELECT {group_concat("m.asset_ids", "")} FROM messages m
                                WHERE m.conversation_id = c.conversation_id) AS wells
                         FROM conversations c WHERE c.user_id = :u ORDER BY c.conversation_id DESC""",
                    u=request.user_id)
    for row in private:
        wells: list[str] = []
        for chunk in (row.pop("wells") or "").replace("][", ",").strip("[]").split(","):
            well = chunk.strip().strip('"')
            if well and well not in wells:
                wells.append(well)
        row["wells"] = wells
    return {"shared_notes": shared, "corrections": corrections, "rules": rules, "claims": claims,
            "private_conversations": private,
            "dataset_corrections": _rows("SELECT COUNT(*) AS n FROM corrections WHERE source_note_id IS NOT NULL")[0]["n"]}


class RecordRequest(BaseModel):
    record_id: str = Field(max_length=120)


_RECORD_SOURCES = [
    ("INS-", "inspection", "SELECT * FROM inspection_facts WHERE inspection_id = :i"),
    ("MNT-", "repair", "SELECT * FROM maintenance WHERE maintenance_id = :i"),
    ("REG-", "regulator event", "SELECT * FROM regulatory_events WHERE event_id = :i"),
    ("CTX-", "human note", "SELECT * FROM notes WHERE note_id = :i"),
    ("FB-", "note shared in the copilot", "SELECT * FROM notes WHERE note_id = :i"),
    ("COR-", "correction", "SELECT * FROM corrections WHERE correction_id = :i"),
    ("CLM-", "user claim", "SELECT * FROM claims WHERE claim_id = :i"),
    ("RULE-", "team rule", "SELECT * FROM rule_overrides WHERE override_id = :i"),
]


@app.post("/api/record")
def record(request: RecordRequest) -> dict:
    """The evidence behind a citation chip: the raw record, or the document section it points to."""
    raw = request.record_id.strip().strip("[]")
    for prefix, kind, sql in _RECORD_SOURCES:
        if raw.startswith(prefix):
            rows = _rows(sql, i=raw)
            if not rows:
                break
            names = {u["user_id"]: u["name"] for u in _rows("SELECT user_id, name FROM users")}
            fields = {k: names.get(v, v) if k.endswith("user_id") else v for k, v in rows[0].items()
                      if k not in ("dataset", "evidence_images") and v not in (None, "")}
            return {"id": raw, "kind": kind, "fields": fields}
    doc_id, _, section = raw.partition(" › ")
    for candidate in (doc_id, f"RPT-{doc_id}" if doc_id.startswith("MNT-") else None,
                      "OM-T200" if doc_id.lower().startswith("t200") else None,
                      "OM-T350" if doc_id.lower().startswith("t350") else None,
                      "SOP-SR" if doc_id.startswith("SOP-SR") else None,
                      "SOP-RC" if doc_id.startswith("SOP-RC") else None,
                      "SOP-WS" if doc_id.startswith("SOP-WS") else None):
        if not candidate:
            continue
        doc = _rows("SELECT doc_id, title, kind, path, text FROM documents WHERE doc_id = :d", d=candidate)
        if doc:
            text_value = doc[0]["text"]
            needle = re.search(r"§\s*([\d.]+)|\b((?:SR|RC|WS)-\d{2})\b", raw)
            excerpt = text_value[:1800]
            if needle:
                key = needle.group(1) or needle.group(2)
                at = next((i for i in (text_value.find(f"**{key}**"), text_value.find(f"### {key}"),
                                       text_value.find(f"## {key}"), text_value.find(key)) if i >= 0), -1)
                if at >= 0:
                    excerpt = text_value[max(at - 200, 0): at + 1400]
            return {"id": raw, "kind": doc[0]["kind"], "title": doc[0]["title"], "path": doc[0]["path"],
                    "excerpt": excerpt}
    raise HTTPException(404, f"No record called {raw}")


@app.post("/api/playbook")
def playbook_list() -> dict:
    from wellwell.services import playbook

    return {"enabled": get_settings().playbook_enabled, "patterns": playbook.listing()}


@app.post("/api/overview")
def overview() -> dict:
    """Field at a glance for the empty state: real numbers, each one a question you can click."""
    from wellwell.analytics.periods import today

    now = today()
    rows = _rows("""
        SELECT a.name AS area, COUNT(*) AS wells, SUM(CASE WHEN f.producing = 1 THEN 1 ELSE 0 END) AS producing
          FROM latest_inspection f JOIN areas a ON a.area_id = f.area_id GROUP BY a.name ORDER BY a.name""")
    spills = _rows("""SELECT COUNT(*) AS n, COALESCE(SUM(eff_spill_volume_bbl), 0) AS bbl FROM inspection_facts
                       WHERE eff_spill = 1 AND inspection_date > :d""", d=(now - __import__("datetime").timedelta(days=30)))
    violations = _rows("""SELECT COUNT(*) AS n, COALESCE(SUM(fine_usd), 0) AS fines FROM regulatory_events
                           WHERE event_type = 'notice_of_violation' AND status = 'open'""")
    wells = sum(r["wells"] for r in rows)
    producing = sum(r["producing"] for r in rows)
    return {
        "as_of": now.isoformat(),
        "tiles": [
            {"label": "Producing now", "value": producing, "of": wells, "tone": "success",
             "question": "How many wells are producing oil right now, by area? Show a bar chart."},
            {"label": "Not producing", "value": wells - producing, "tone": "danger",
             "question": "Which wells are not producing oil right now, and why? Show them in a table."},
            {"label": "Spills, last 30 days", "value": spills[0]["n"], "detail": f"{spills[0]['bbl']:.1f} bbl",
             "tone": "warning", "question": "Show on a map every real spill in the last 30 days."},
            {"label": "Open violations", "value": violations[0]["n"],
             "detail": f"USD {violations[0]['fines']:,.0f} in fines", "tone": "danger",
             "question": "Which notices of violation are still open, and what is overdue?"},
        ],
        "areas": rows,
    }


@app.post("/api/status")
def data_status() -> dict:
    try:
        return loading.status()
    except Exception as error:
        return {"state": "initialising", "message": f"Database not reachable yet: {str(error)[:200]}",
                "stats": None, "run": None}


@app.post("/api/health")
def health() -> dict:
    return {"ok": True, "wells": _rows("SELECT COUNT(*) AS n FROM wells")[0]["n"]}


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    page = Path(WEB_DIR / "index.html")
    if not page.exists():
        raise HTTPException(404, "web/index.html is not built yet")
    return FileResponse(page)
