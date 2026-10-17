import re
from functools import cached_property

from sqlalchemy import text
from sqlalchemy.orm import Session


class Directory:
    """Names for IDs and IDs for names: users, areas, teams, wells."""

    def __init__(self, session: Session):
        self.session = session

    @cached_property
    def users(self) -> dict[str, dict]:
        rows = self.session.execute(text("SELECT user_id, name, role, team_id FROM users")).mappings()
        return {row["user_id"]: dict(row) for row in rows}

    @cached_property
    def areas(self) -> dict[str, dict]:
        rows = self.session.execute(text("SELECT area_id, name, team_id, center_lat, center_lon FROM areas")).mappings()
        return {row["area_id"]: dict(row) for row in rows}

    @cached_property
    def teams(self) -> dict[str, dict]:
        rows = self.session.execute(text("SELECT team_id, name, lead_user_id FROM teams")).mappings()
        return {row["team_id"]: dict(row) for row in rows}

    @cached_property
    def well_ids(self) -> set[str]:
        return set(self.session.scalars(text("SELECT asset_id FROM wells")))

    def user_name(self, user_id: str | None) -> str | None:
        if not user_id:
            return None
        user = self.users.get(user_id)
        return user["name"] if user else user_id

    def area_name(self, area_id: str | None) -> str | None:
        return self.areas.get(area_id, {}).get("name", area_id) if area_id else None

    def resolve_user(self, text_value: str | None) -> str | None:
        if not text_value:
            return None
        if text_value in self.users:
            return text_value
        lowered = text_value.strip().lower()
        for user_id, user in self.users.items():
            if user["name"].lower() == lowered:
                return user_id
        return None

    def resolve_area(self, text_value: str | None) -> str | None:
        """Accepts "Northeast Block 8", "NE-8", "ne 08", "Block 8" (when unambiguous) or the area_id itself."""
        if not text_value:
            return None
        if text_value in self.areas:
            return text_value
        lowered = re.sub(r"\s+", " ", text_value.strip().lower())
        for area_id, area in self.areas.items():
            if lowered in (area["name"].lower(), area_id.lower()):
                return area_id

        def key(value: str) -> tuple[str, int] | None:
            value = value.lower().replace("northeast", "ne").replace("southwest", "sw").replace("northwest", "nw")
            value = value.replace("southeast", "se").replace("block", "")
            match = re.fullmatch(r"\s*([a-z]*)[\s-]*0*(\d+)\s*", value)
            return (match.group(1), int(match.group(2))) if match else None

        wanted = key(lowered)
        if wanted is None:
            return None
        matches = [area_id for area_id, area in self.areas.items()
                   if key(area_id) == wanted or key(area["name"]) == wanted
                   or (not wanted[0] and key(area_id) and key(area_id)[1] == wanted[1])]
        return matches[0] if len(matches) == 1 else None

    def resolve_well(self, text_value: str | None) -> str | None:
        if not text_value:
            return None
        candidate = text_value.strip().upper()
        if candidate in self.well_ids:
            return candidate
        match = re.search(r"(\d{1,4})", candidate)
        if match:
            formatted = f"WELL-{int(match.group(1)):04d}"
            if formatted in self.well_ids:
                return formatted
        return None
