import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import structlog

from wellwell.analytics.result import ToolResult
from wellwell.domain.citations import find_citations
from wellwell.domain.enums import VisualKind
from wellwell.harness.guardrails import requested_visual, ungrounded_numbers
from wellwell.harness.llm import LLM, LLMError
from wellwell.harness.tools import SPEC_BY_NAME, ToolBox

log = structlog.get_logger()


@dataclass
class Step:
    number: int
    tool: str
    args: dict
    result: ToolResult | None = None
    error: str | None = None
    latency_ms: int = 0


@dataclass
class AgentAnswer:
    answer: str
    visual: dict | None
    citations: list[str]
    steps: list[Step]
    trust: dict
    model: str | None = None
    wrote_memory: bool = False


@dataclass
class AgentConfig:
    max_rounds: int = 10
    max_seconds: int = 150
    observation_chars: int = 6000
    on_step: Callable[[str], None] | None = field(default=None)


def extract_json(text: str) -> dict:
    """Pulls the first JSON object out of a model reply, tolerating code fences and stray prose."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    depth, in_string, escaped = 0, False, False
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = cleaned[start:index + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return json.loads(re.sub(r",\s*([}\]])", r"\1", candidate))
    raise ValueError("unbalanced JSON object in reply")


class Agent:
    def __init__(self, llm: LLM, toolbox: ToolBox, system: str, config: AgentConfig | None = None):
        self.llm = llm
        self.toolbox = toolbox
        self.system = system
        self.config = config or AgentConfig()

    def _notify(self, message: str) -> None:
        if self.config.on_step:
            self.config.on_step(message)

    def _observation(self, step: Step) -> str:
        if step.error:
            return f"Step {step.number} {step.tool}({json.dumps(step.args)}) ERROR: {step.error}"
        body = json.dumps(step.result.for_model(max_rows=25), default=str)
        if len(body) > self.config.observation_chars:
            body = body[: self.config.observation_chars] + "... (truncated)"
        return f"Step {step.number} {step.tool}({json.dumps(step.args)}) ->\n{body}"

    def _prefetch(self, question: str) -> list[Step]:
        """Answers about a named well's state almost always start with well_status, so run it before the first call."""
        if not _STATE_WORDS.search(question):
            return []
        wells = list(dict.fromkeys(re.findall(r"\bWELL-\d{4}\b", question.upper())))[:3]
        steps = []
        for well in wells:
            step = Step(len(steps) + 1, "well_status", {"well": well})
            self._notify(f"well_status {json.dumps(step.args)}")
            tick = time.perf_counter()
            try:
                step.result = self.toolbox.run("well_status", step.args)
            except Exception as error:
                step.error = str(error)[:300]
            step.latency_ms = int((time.perf_counter() - tick) * 1000)
            steps.append(step)
        return steps

    def run(self, question: str, history: str, guidance: str = "") -> AgentAnswer:
        started = time.monotonic()
        steps: list[Step] = self._prefetch(question)
        self.timings: list[dict] = []
        feedback_note = ""
        model = None
        wanted_visual = requested_visual(question)
        for _round in range(self.config.max_rounds):
            if time.monotonic() - started > self.config.max_seconds:
                break
            scratchpad = "\n\n".join(self._observation(s) for s in steps) or "(no tool results yet)"
            prompt = (f"Conversation so far:\n{history or '(new conversation)'}\n\nUser question:\n{question}\n\n"
                      + (f"{guidance}\n\n" if guidance else "")
                      + f"Tool results:\n{scratchpad}\n{feedback_note}\nReply with one JSON object.")
            try:
                tick = time.perf_counter()
                completion = self.llm.complete(self.system, prompt)
                self.timings.append({"what": "model", "ms": int((time.perf_counter() - tick) * 1000),
                                     "prompt_chars": len(self.system) + len(prompt)})
            except LLMError:
                if not any(s.result for s in steps):
                    raise
                log.warning("agent.model_lost_after_tools", steps=len(steps))
                break
            model = completion.model
            try:
                action = extract_json(completion.text)
            except ValueError as error:
                feedback_note = f"\nYour last reply was not valid JSON ({error}). Reply with ONE JSON object only."
                continue
            feedback_note = ""
            if "final" in action:
                if not steps and _looks_factual(question):
                    feedback_note = "\nYou must call at least one tool before answering a question about the data."
                    continue
                return self._finish(action["final"], steps, question, wanted_visual, model)
            calls = action.get("calls") or ([{"tool": action["tool"], "args": action.get("args", {})}]
                                            if action.get("tool") else [])
            if not calls:
                feedback_note = "\nReply with either \"calls\" or \"final\"."
                continue
            for call in calls[:4]:
                step = Step(len(steps) + 1, str(call.get("tool")), call.get("args") or {})
                self._notify(f"{step.tool} {json.dumps(step.args)}")
                tick = time.perf_counter()
                try:
                    step.result = self.toolbox.run(step.tool, step.args)
                except Exception as error:
                    step.error = str(error)[:500]
                    log.info("tool.error", tool=step.tool, error=step.error)
                step.latency_ms = int((time.perf_counter() - tick) * 1000)
                steps.append(step)

        fallback = next((s for s in reversed(steps) if s.result), None)
        if fallback:
            facts = " ".join(s.result.summary for s in steps if s.result)
            cited = " ".join(f"[{c}]" for c in dict.fromkeys(c for s in steps if s.result for c in s.result.citations[:8]))
            text = ("The language model stopped responding, so here is what the data tools found, unedited: "
                    f"{facts} {cited}")
        else:
            text = "I couldn't answer that right now. Please try again."
        return self._finish({"answer": text, "visual": {"step": fallback.number if fallback else None}},
                            steps, question, wanted_visual, model, degraded=True)

    def _finish(self, final: dict, steps: list[Step], question: str, wanted: VisualKind | None, model: str | None,
                degraded: bool = False) -> AgentAnswer:
        answer = str(final.get("answer") or "").strip()
        known: set[str] = set()
        evidence_parts = [question]
        for step in steps:
            if step.result:
                known.update(step.result.citations)
                evidence_parts.append(json.dumps(step.result.to_dict(), default=str))
        evidence = "\n".join(evidence_parts)
        for record_id in re.findall(r"\b[A-Z]{2,4}-\d{4}-\d{2,4}\b|\b(?:COR|FB|CLM|RULE)-\d{4}\b", evidence):
            known.add(record_id)

        cited = find_citations(answer) + [c for c in final.get("citations") or [] if isinstance(c, str)]
        record_like = re.compile(r"^(INS|MNT|REG|CTX|COR|FB|CLM|RULE|RPT)-")
        unknown = sorted({c for c in cited if record_like.match(c) and c not in known})
        for bad in unknown:
            answer = answer.replace(f"[{bad}]", "").replace(bad, "")
        citations = [c for c in dict.fromkeys(cited) if c not in unknown]

        visual = self._choose_visual(final.get("visual") or {}, steps, wanted)
        numbers = ungrounded_numbers(answer, evidence)
        wrote = any(s.result and SPEC_BY_NAME.get(s.tool) and SPEC_BY_NAME[s.tool].writes for s in steps)
        trust = {
            "tool_calls": len(steps),
            "records_cited": len(citations),
            "unknown_citations_removed": unknown,
            "ungrounded_numbers": numbers,
            "rules_applied": sorted({r for s in steps if s.result for r in s.result.rules_applied}),
            "warnings": sorted({w for s in steps if s.result for w in s.result.warnings}),
            "degraded": degraded,
            "model_calls": getattr(self, "timings", []),
            "tool_ms": sum(s.latency_ms for s in steps),
        }
        return AgentAnswer(answer, visual, citations, steps, trust, model, wrote)

    def _choose_visual(self, chosen: dict, steps: list[Step], wanted: VisualKind | None) -> dict | None:
        by_number = {s.number: s for s in steps if s.result}
        if not by_number:
            return None
        step = by_number.get(chosen.get("step")) if isinstance(chosen.get("step"), int) else None
        kind = wanted.value if wanted else (chosen.get("kind") or None)
        if step is None:
            preferred = [s for s in by_number.values() if s.result.rows or s.result.card]
            if kind:
                preferred = [s for s in preferred if (s.result.visual or {}).get("kind") == kind] or preferred
            step = preferred[-1] if preferred else None
        if step is None:
            return None
        kind = kind or (step.result.visual or {}).get("kind") or "none"
        if kind == "none":
            return None
        return {"kind": kind, "step": step.number, "title": chosen.get("title") or step.tool.replace("_", " ").title(),
                "hint": step.result.visual or {}}


_STATE_WORDS = re.compile(r"\b(producing|production|running|online|offline|down|shut|stopped|status|inspected|"
                          r"inspection|working|planned|downtime)\b", re.IGNORECASE)


def _looks_factual(question: str) -> bool:
    return bool(re.search(r"well|spill|inspect|repair|down|produc|area|block|rule|cost|report|note", question, re.I))
