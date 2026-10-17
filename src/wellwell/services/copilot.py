import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import structlog
from sqlalchemy import select

from sqlalchemy import text

from wellwell.analytics.periods import today
from wellwell.config import get_settings
from wellwell.db.models import AnswerFeedback, Conversation, Message, ToolRun, User
from wellwell.db.session import session_scope
from wellwell.domain.citations import find_wells
from wellwell.harness.agent import Agent, AgentAnswer, AgentConfig
from wellwell.harness.guardrails import check_input
from wellwell.harness.llm import LLMError, build_llm
from wellwell.harness.prompts import build_system
from wellwell.harness.tools import ToolBox
from wellwell.services import playbook

log = structlog.get_logger()


@dataclass
class CopilotReply:
    conversation_id: int
    answer: str
    visual: dict | None
    citations: list[str]
    steps: list[dict]
    trust: dict
    model: str | None
    latency_ms: int
    blocked: bool = False
    message_id: int | None = None


def _visual_payload(answer: AgentAnswer) -> dict | None:
    if not answer.visual:
        return None
    step = next((s for s in answer.steps if s.number == answer.visual["step"]), None)
    if step is None or step.result is None:
        return None
    data = step.result.to_dict()
    return {**answer.visual, "rows": data["rows"], "card": data["card"], "tool": step.tool}


def start_conversation(user_id: str, on_day: date | None = None) -> int:
    with session_scope() as session:
        conversation = Conversation(user_id=user_id, started_on=on_day or today())
        session.add(conversation)
        session.flush()
        return conversation.conversation_id


def ask(user_id: str, question: str, conversation_id: int | None = None, provider: str | None = None,
        on_step: Callable[[str], None] | None = None, search_mode: str = "semantic",
        use_playbook: bool | None = None) -> CopilotReply:
    started = time.perf_counter()
    if conversation_id is None:
        conversation_id = start_conversation(user_id)
    verdict = check_input(question)
    if not verdict.allowed:
        return CopilotReply(conversation_id, verdict.message or "", None, [], [], {"blocked": True}, None, 0, True)

    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None:
            raise ValueError(f"Unknown user {user_id}")
        conversation = session.get(Conversation, conversation_id)
        if conversation is None or conversation.user_id != user_id:
            raise PermissionError("That conversation belongs to someone else")
        history_rows = session.scalars(select(Message).where(Message.conversation_id == conversation_id)
                                       .order_by(Message.message_id.desc()).limit(6)).all()[::-1]
        history = "\n".join(f"{m.role}: {m.content[:1200]}" for m in history_rows)
        user_dict = {"user_id": user.user_id, "name": user.name, "role": user.role.value, "team_id": user.team_id}
        from wellwell.analytics.directory import Directory

        team = Directory(session).teams.get(user.team_id or "", {}).get("name")
        system = build_system(get_settings().today, user_dict, team)
        preferences = session.execute(text("SELECT text FROM user_preferences WHERE user_id = :u ORDER BY id"),
                                      {"u": user_id}).scalars().all()
        if preferences:
            system += ("\nThis user's private preferences (apply them to every answer for this user, and say so):\n- "
                       + "\n- ".join(preferences))
        toolbox = ToolBox(session, user_id, conversation_id, search_mode)
        learned = playbook.hints(question) if playbook.enabled(use_playbook) else []
        if learned and on_step:
            on_step(f"playbook {json.dumps({'similar_patterns': len(learned)})}")
        try:
            agent = Agent(build_llm(provider), toolbox, system, AgentConfig(on_step=on_step))
            result = agent.run(question, history, playbook.as_prompt(learned))
        except LLMError as error:
            session.rollback()
            return CopilotReply(conversation_id, f"The language model is unavailable right now ({error}). Your question "
                                "was not lost; please try again or switch the model provider.", None, [], [],
                                {"model_error": str(error)}, None, int((time.perf_counter() - started) * 1000))

        if not conversation.title:
            conversation.title = question[:120]
        session.add(Message(conversation_id=conversation_id, user_id=user_id, role="user", content=question,
                            asset_ids=find_wells(question), sent_on=today()))
        visual = _visual_payload(result)
        answer_wells = find_wells(result.answer)
        if visual:
            for row in visual.get("rows") or []:
                well = row.get("well") or row.get("asset_id")
                if well and well not in answer_wells:
                    answer_wells.append(well)
        assistant = Message(conversation_id=conversation_id, user_id=user_id, role="assistant", content=result.answer,
                            answer={"citations": result.citations, "trust": result.trust,
                                    "visual": {k: v for k, v in (visual or {}).items() if k != "rows"} or None},
                            asset_ids=answer_wells, sent_on=today(), model_id=result.model,
                            latency_ms=int((time.perf_counter() - started) * 1000))
        session.add(assistant)
        session.flush()
        for step in result.steps:
            session.add(ToolRun(message_id=assistant.message_id, step=step.number, tool=step.tool, args=step.args,
                                result=step.result.to_dict() if step.result else None, error=step.error,
                                latency_ms=step.latency_ms))
        steps = [{"number": s.number, "tool": s.tool, "args": s.args, "error": s.error, "latency_ms": s.latency_ms,
                  "summary": s.result.summary if s.result else None,
                  "citations": s.result.citations[:12] if s.result else []} for s in result.steps]
        result.trust["playbook"] = [{"id": h["id"], "pattern": h["pattern"], "plan": h["plan"], "avoid": h["avoid"],
                                     "similarity": h["similarity"]} for h in learned]
        assistant.answer = {**(assistant.answer or {}), "trust": result.trust}
        if not result.trust.get("degraded"):
            playbook.record(question, result.steps, (visual or {}).get("kind"), assistant.message_id)
        return CopilotReply(conversation_id, result.answer, visual, result.citations, steps, result.trust,
                            result.model, int((time.perf_counter() - started) * 1000),
                            message_id=assistant.message_id)


def rate_answer(user_id: str, message_id: int, rating: str, comment: str | None = None) -> None:
    """Thumbs up/down on an answer. Only the person who asked can rate it."""
    from wellwell.db.models import AnswerFeedback

    if rating not in ("up", "down"):
        raise ValueError("rating must be 'up' or 'down'")
    with session_scope() as session:
        message = session.get(Message, message_id)
        if message is None or message.user_id != user_id or message.role != "assistant":
            raise PermissionError("You can only rate your own answers")
        session.add(AnswerFeedback(message_id=message_id, user_id=user_id, rating=rating,
                                   comment=(comment or "")[:2000] or None))
    playbook.feedback(message_id, rating)


def conversation_history(user_id: str) -> list[dict]:
    """A user's own conversations, newest first. Never includes anyone else's."""
    with session_scope() as session:
        conversations = session.scalars(select(Conversation).where(Conversation.user_id == user_id)
                                        .order_by(Conversation.conversation_id.desc())).all()
        return [{"conversation_id": c.conversation_id, "title": c.title or "New chat", "started_on": c.started_on}
                for c in conversations]


def conversation_messages(user_id: str, conversation_id: int) -> list[dict]:
    with session_scope() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None or conversation.user_id != user_id:
            raise PermissionError("That conversation belongs to someone else")
        messages = session.scalars(select(Message).where(Message.conversation_id == conversation_id)
                                   .order_by(Message.message_id)).all()
        result = []
        for m in messages:
            visual = None
            meta = (m.answer or {}).get("visual")
            if meta and meta.get("step"):
                run = session.scalars(select(ToolRun).where(ToolRun.message_id == m.message_id,
                                                            ToolRun.step == meta["step"])).first()
                if run and run.result:
                    visual = {**meta, "rows": run.result.get("rows"), "card": run.result.get("card")}
            rating = session.execute(select(AnswerFeedback.rating, AnswerFeedback.comment).where(
                AnswerFeedback.message_id == m.message_id, AnswerFeedback.user_id == user_id)
                .order_by(AnswerFeedback.id.desc())).first() if m.role == "assistant" else None
            result.append({"message_id": m.message_id, "role": m.role, "content": m.content,
                           "rating": rating[0] if rating else None, "rating_comment": rating[1] if rating else None,
                           "citations": (m.answer or {}).get("citations", []), "trust": (m.answer or {}).get("trust"),
                           "visual": visual, "created_at": m.created_at})
        return result
