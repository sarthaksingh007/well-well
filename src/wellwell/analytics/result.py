from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any


def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


@dataclass
class ToolResult:
    """What every tool returns: facts for the model, rows for the UI, and the records it used."""

    summary: str
    rows: list[dict] = field(default_factory=list)
    card: dict | None = None
    citations: list[str] = field(default_factory=list)
    visual: dict | None = None
    rules_applied: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return _plain(asdict(self))

    def for_model(self, max_rows: int = 40) -> dict:
        payload = self.to_dict()
        if len(payload["rows"]) > max_rows:
            payload["rows_truncated"] = f"showing {max_rows} of {len(payload['rows'])} rows"
            payload["rows"] = payload["rows"][:max_rows]
        payload.pop("visual", None)
        return payload


def cite(*ids: str | None) -> list[str]:
    seen: dict[str, None] = {}
    for item in ids:
        if item:
            seen[item] = None
    return list(seen)
