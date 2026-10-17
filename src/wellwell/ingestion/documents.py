import re
from dataclasses import dataclass
from datetime import date

from wellwell.ingestion.normalize import parse_date

_HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*$", re.MULTILINE)
_WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


@dataclass(frozen=True)
class Section:
    heading: str
    text: str


def split_sections(markdown: str) -> list[Section]:
    matches = list(_HEADING.finditer(markdown))
    if not matches:
        return [Section("Document", markdown.strip())]
    sections = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        body = markdown[match.end():end].strip()
        if body:
            sections.append(Section(match.group(2).strip(), body))
    return sections


def title_of(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


@dataclass(frozen=True)
class ManualRules:
    producing_min_delta_c: float | None
    producing_rule_ref: str
    temp_alarm_max_c: float | None
    rca_min_corrective: int | None
    rca_rule_ref: str
    inherits_rca_from: str | None


def extract_manual_rules(markdown: str, well_type: str) -> ManualRules:
    """Reads the thresholds out of a well-type manual instead of hard-coding them."""
    producing = re.search(r"at least \**(\d+(?:\.\d+)?)\s*°?\s*C above\**\s*ambient", markdown, re.IGNORECASE)
    alarm = re.search(r"at or below\s*\**(\d+(?:\.\d+)?)\s*°?\s*C", markdown, re.IGNORECASE)
    rca = re.search(r"\b(\w+) or more\s*\**corrective\**", markdown, re.IGNORECASE)
    rca_count = None
    if rca:
        word = rca.group(1).lower()
        rca_count = int(word) if word.isdigit() else _WORD_NUMBERS.get(word)
    if rca_count is None:
        after_three = re.search(r"RCA after (\w+) corrective", markdown, re.IGNORECASE)
        if after_three:
            word = after_three.group(1).lower()
            rca_count = int(word) if word.isdigit() else _WORD_NUMBERS.get(word)
    inherits = re.search(r"Same as (T\d+) section (\d+)", markdown)

    producing_section = _section_number_containing(markdown, producing.start() if producing else -1)
    rca_section = _section_number_containing(markdown, rca.start() if rca else -1)
    if inherits and not rca_section:
        rca_section = f"{inherits.group(1)} manual §{inherits.group(2)}"
    return ManualRules(
        producing_min_delta_c=float(producing.group(1)) if producing else None,
        producing_rule_ref=f"{well_type} manual §{producing_section}" if producing_section else f"{well_type} manual",
        temp_alarm_max_c=float(alarm.group(1)) if alarm else None,
        rca_min_corrective=rca_count,
        rca_rule_ref=(rca_section if rca_section and "manual" in rca_section
                      else f"{well_type} manual §{rca_section}" if rca_section else f"{well_type} manual"),
        inherits_rca_from=inherits.group(1) if inherits else None,
    )


def _section_number_containing(markdown: str, position: int) -> str | None:
    if position < 0:
        return None
    number = None
    for match in re.finditer(r"^#{2,4}\s+(\d+(?:\.\d+)*)\.?\s", markdown, re.MULTILINE):
        if match.start() > position:
            break
        number = match.group(1)
    return number


@dataclass(frozen=True)
class ReportFacts:
    containment_hours: float | None
    spill_class: str | None
    written_on: date | None
    compliance: list[tuple[str, str, bool | None, str]]


_CONTAINED = re.compile(
    r"contained\**\s*(?:about|approximately|roughly|after about|after)?\s*(\d+(?:\.\d+)?)\s*(?:hours|h)\b",
    re.IGNORECASE,
)
_CLASS = re.compile(r"\b(minor|moderate|reportable|major)\b(?:\s+spill|\s+release|\s+class|\")", re.IGNORECASE)
_COMPLIANCE_ROW = re.compile(r"^\|\s*([^|\n]{2,40}?)\s*\|\s*([^|\n]+?)\s*\|\s*([^|\n]*?)\s*\|?\s*$", re.MULTILINE)
_COMPLIANCE_SECTION = re.compile(r"^#{2,3}\s*Compliance check\s*$(.*?)(?=^#{1,3}\s|\Z)", re.MULTILINE | re.DOTALL | re.IGNORECASE)
_WRITTEN = re.compile(r"Report written:\**\s*([^\n*]+)")


def extract_report_facts(markdown: str) -> ReportFacts:
    contained = _CONTAINED.search(markdown)
    spill_class = _CLASS.search(markdown)
    written = _WRITTEN.search(markdown)
    compliance = []
    section = _COMPLIANCE_SECTION.search(markdown)
    for protocol, result, detail in _COMPLIANCE_ROW.findall(section.group(1) if section else ""):
        if protocol.lower() == "protocol" or set(protocol) <= set("-: "):
            continue
        clean_result = result.replace("*", "").strip()
        lowered = clean_result.lower()
        met = False if "not met" in lowered else True if lowered.startswith("met") else None
        compliance.append((protocol, clean_result, met, detail.strip()))
    return ReportFacts(
        containment_hours=float(contained.group(1)) if contained else None,
        spill_class=spill_class.group(1).lower() if spill_class else None,
        written_on=parse_date(written.group(1).strip()) if written else None,
        compliance=compliance,
    )
