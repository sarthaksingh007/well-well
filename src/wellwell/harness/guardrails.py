import re
from dataclasses import dataclass

from wellwell.domain.enums import VisualKind

MAX_QUESTION_CHARS = 2000

_INJECTION = re.compile(
    r"ignore (all|any|the|your) (previous|prior|above) (instructions|rules)|disregard (the|your) (rules|instructions)"
    r"|reveal (the|your) (system )?prompt|you are now|act as (an?|the) (admin|developer|system)|jailbreak",
    re.IGNORECASE,
)
_DESTRUCTIVE = re.compile(r"\b(delete|drop|truncate|wipe|erase|remove)\b.{0,40}\b(data|records|table|database|"
                          r"inspections|history|notes)\b", re.IGNORECASE)

_VISUAL_WORDS = [
    (VisualKind.HEATMAP, re.compile(r"\bheat ?map\b", re.IGNORECASE)),
    (VisualKind.MAP, re.compile(r"\b(on a map|map of|show (it |them )?on (a |the )?map|as a map|\bmap\b)", re.IGNORECASE)),
    (VisualKind.LINE, re.compile(r"\bline (chart|graph)\b|\btrend line\b", re.IGNORECASE)),
    (VisualKind.BAR, re.compile(r"\bbar (chart|graph)\b|\bbars\b", re.IGNORECASE)),
    (VisualKind.TABLE, re.compile(r"\b(in |as )?a table\b|\btabular\b|\btable\b", re.IGNORECASE)),
    (VisualKind.STATUS_CARD, re.compile(r"\b(status )?card\b", re.IGNORECASE)),
]
_NUMBER = re.compile(r"(?<![\w\-§.])(\d+(?:[.,]\d+)*)(?![\w-])")


@dataclass(frozen=True)
class InputVerdict:
    allowed: bool
    message: str | None = None


def check_input(question: str) -> InputVerdict:
    if not question.strip():
        return InputVerdict(False, "Please ask a question about the wells.")
    if len(question) > MAX_QUESTION_CHARS:
        return InputVerdict(False, f"That message is too long. Please keep it under {MAX_QUESTION_CHARS} characters.")
    if _INJECTION.search(question):
        return InputVerdict(False, "I can only help with questions about the oil field data, and I can't change how I "
                                   "work. Ask me about wells, inspections, repairs, spills or compliance.")
    if _DESTRUCTIVE.search(question):
        return InputVerdict(False, "I never delete or overwrite records. If something in the data is wrong, tell me "
                                   "the correct value and why, and I'll record a correction that keeps the original.")
    return InputVerdict(True)


def requested_visual(question: str) -> VisualKind | None:
    for kind, pattern in _VISUAL_WORDS:
        if pattern.search(question):
            return kind
    return None


def ungrounded_numbers(answer: str, evidence_text: str) -> list[str]:
    """Numbers in the answer that appear nowhere in the tool results or the question."""
    evidence = {n.replace(",", "") for n in _NUMBER.findall(evidence_text)}
    evidence |= {n.rstrip("0").rstrip(".") for n in evidence if "." in n}
    missing = []
    for raw in _NUMBER.findall(answer):
        number = raw.replace(",", "")
        trimmed = number.rstrip("0").rstrip(".") if "." in number else number
        if len(number) <= 1 or number in evidence or trimmed in evidence:
            continue
        if re.fullmatch(r"(19|20)\d{2}", number) or number in {"24", "7", "14", "30", "60", "90", "12"}:
            continue
        missing.append(raw)
    return missing
