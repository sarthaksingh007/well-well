"""The agent's learned playbook: which tool plans answered which kinds of question, and how that went.

Patterns are anonymised (well IDs, dates and numbers masked), so one user's questions never surface
in another user's hints. Hints are advisory: tools still compute every fact.
"""

import hashlib
import re
from datetime import datetime

import numpy as np
import structlog
from sqlalchemy import select

from wellwell.config import get_settings
from wellwell.db.models import PlaybookPattern
from wellwell.db.session import session_scope

log = structlog.get_logger()

_MASKS = [
    (re.compile(r"\bWELL-\d{4}\b", re.I), "WELL-####"),
    (re.compile(r"\b(INS|MNT|REG|CTX|COR|FB|CLM|RULE)-[\w-]+\b"), r"\1-…"),
    (re.compile(r"\b\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I), "<date>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "<date>"),
    (re.compile(r"\b\d+(\.\d+)?\b"), "#"),
]
MIN_SIMILARITY = 0.82


def enabled(request_flag: bool | None = None) -> bool:
    return get_settings().playbook_enabled and request_flag is not False


def anonymise(question: str) -> str:
    text = " ".join(question.split())
    for pattern, replacement in _MASKS:
        text = pattern.sub(replacement, text)
    return text


def _plan(steps) -> list[dict]:
    return [{"tool": s.tool, "args": sorted((s.args or {}).keys())} for s in steps if s.result and not s.error]


def _embed(text: str) -> bytes | None:
    from wellwell.analytics import embeddings

    if not embeddings.available():
        return None
    return embeddings.embed_query(text).astype(np.float32).tobytes()


def record(question: str, steps, visual_kind: str | None, message_id: int | None) -> None:
    """Called after every answer that used tools without errors."""
    if not get_settings().playbook_enabled:
        return
    plan = _plan(steps)
    if not plan or any(s.tool.startswith("record_") or s.tool == "recall_my_conversations" for s in steps):
        return
    pattern = anonymise(question)
    signature = " → ".join(f"{p['tool']}({', '.join(p['args'])})" for p in plan)
    digest = hashlib.sha256(f"{pattern}|{signature}".encode()).hexdigest()
    try:
        with session_scope() as session:
            row = session.scalar(select(PlaybookPattern).where(PlaybookPattern.pattern_hash == digest))
            if row is None:
                row = PlaybookPattern(pattern_hash=digest, pattern=pattern, plan=plan, plan_signature=signature[:300],
                                      visual=visual_kind, embedding=_embed(pattern), answers=0, uses=0,
                                      last_message_ids=[])
                session.add(row)
            row.answers = (row.answers or 0) + 1
            row.last_message_ids = ([message_id] + list(row.last_message_ids or []))[:20] if message_id else row.last_message_ids
            row.updated_at = datetime.now()
    except Exception as error:
        log.warning("playbook.record_failed", error=str(error)[:200])


def feedback(message_id: int, outcome: str) -> None:
    """outcome: up | down | eval_pass. Credits the pattern that produced this answer."""
    column = {"up": "thumbs_up", "down": "thumbs_down", "eval_pass": "eval_passes"}.get(outcome)
    if not column or not get_settings().playbook_enabled:
        return
    with session_scope() as session:
        for row in session.scalars(select(PlaybookPattern)):
            if message_id in (row.last_message_ids or []):
                setattr(row, column, (getattr(row, column) or 0) + 1)
                row.updated_at = datetime.now()
                return


def score(row: PlaybookPattern) -> float:
    return (row.thumbs_up or 0) * 2 + (row.eval_passes or 0) * 2 + (row.answers or 0) * 0.25 - (row.thumbs_down or 0) * 3


def hints(question: str, limit: int = 2) -> list[dict]:
    """Similar past patterns that worked (and any that didn't), for the prompt."""
    try:
        query = _embed(anonymise(question))
        if query is None:
            return []
        q = np.frombuffer(query, dtype=np.float32)
        with session_scope() as session:
            rows = [r for r in session.scalars(select(PlaybookPattern)) if r.embedding]
            ranked = []
            for row in rows:
                similarity = float(np.frombuffer(row.embedding, dtype=np.float32) @ q)
                if similarity >= MIN_SIMILARITY:
                    ranked.append((similarity, row))
            ranked.sort(key=lambda item: (-item[0], -score(item[1])))
            chosen = []
            for similarity, row in ranked:
                if score(row) > 0 or (row.thumbs_down or 0) > 0:
                    chosen.append({"id": row.id, "pattern": row.pattern, "plan": row.plan_signature,
                                   "similarity": round(similarity, 3), "score": score(row),
                                   "avoid": (row.thumbs_down or 0) > (row.thumbs_up or 0) + (row.eval_passes or 0)})
                    row.uses = (row.uses or 0) + 1
                if len(chosen) >= limit:
                    break
            return chosen
    except Exception as error:
        log.warning("playbook.hints_failed", error=str(error)[:200])
        return []


def as_prompt(found: list[dict]) -> str:
    if not found:
        return ""
    lines = ["Playbook (advisory, learned from earlier answers; the tools still decide every fact):"]
    for hint in found:
        verdict = "AVOID this plan, users rated it wrong" if hint["avoid"] else "worked well"
        lines.append(f"- Similar question \"{hint['pattern']}\" → {hint['plan']} ({verdict}, similarity {hint['similarity']})")
    return "\n".join(lines)


def listing() -> list[dict]:
    with session_scope() as session:
        rows = session.scalars(select(PlaybookPattern)).all()
        return sorted(({"id": r.id, "pattern": r.pattern, "plan": r.plan_signature, "visual": r.visual,
                        "answers": r.answers, "uses": r.uses, "thumbs_up": r.thumbs_up, "thumbs_down": r.thumbs_down,
                        "eval_passes": r.eval_passes, "score": score(r), "updated_at": r.updated_at} for r in rows),
                      key=lambda r: -r["score"])
