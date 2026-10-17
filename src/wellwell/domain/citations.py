import re

CITATION_PATTERN = re.compile(
    r"\b(?:RPT-MNT-\d{4}-[\w-]+|(?:INS|MNT|REG|CTX)-\d{4}-\d{2,4}|(?:COR-CTX-\d{4}-\d{3})|(?:COR|FB|CLM|RULE)-\d{4}"
    r"|OM-T\d{3}|SOP-(?:SR|RC|WS))\b"
)
WELL_PATTERN = re.compile(r"\bWELL-\d{4}\b")


def find_citations(text: str) -> list[str]:
    return list(dict.fromkeys(match.group(0) for match in CITATION_PATTERN.finditer(text)))


def find_wells(text: str) -> list[str]:
    return list(dict.fromkeys(WELL_PATTERN.findall(text)))


def record_kind(citation: str) -> str:
    prefixes = {
        "INS": "inspection", "MNT": "repair", "REG": "regulator event", "CTX": "human note", "COR": "correction",
        "FB": "copilot note", "CLM": "user claim", "RULE": "team rule", "RPT": "repair report", "OM": "manual",
        "SOP": "procedure",
    }
    return prefixes.get(citation.split("-")[0], "record")
