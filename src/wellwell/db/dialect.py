"""The few SQL snippets that differ between Postgres (production) and SQLite (zero-setup fallback)."""

from decimal import Decimal
from functools import lru_cache

from wellwell.config import get_settings


@lru_cache
def is_postgres() -> bool:
    return get_settings().database_url.startswith("postgresql")


def name() -> str:
    return "postgres" if is_postgres() else "sqlite"


def month(expr: str) -> str:
    return f"to_char({expr}, 'YYYY-MM')" if is_postgres() else f"strftime('%Y-%m', {expr})"


def year(expr: str) -> str:
    return f"to_char({expr}, 'YYYY')" if is_postgres() else f"strftime('%Y', {expr})"


def group_concat(expr: str, separator: str = ", ") -> str:
    if is_postgres():
        return f"string_agg(CAST({expr} AS TEXT), '{separator}')"
    return f"GROUP_CONCAT({expr}, '{separator}')"


def round_(expr: str, digits: int) -> str:
    return f"ROUND(CAST({expr} AS NUMERIC), {digits})"


def plain(value):
    """Postgres hands back Decimal for rounded numbers; the rest of the code works in floats."""
    return float(value) if isinstance(value, Decimal) else value


def plain_row(row: dict) -> dict:
    return {key: plain(value) for key, value in row.items()}
