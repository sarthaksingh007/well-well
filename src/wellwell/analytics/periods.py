import re
from dataclasses import dataclass
from datetime import date, timedelta

from wellwell.config import get_settings


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    label: str

    def as_dict(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "label": self.label}


def today() -> date:
    return date.fromisoformat(get_settings().today)


def _minus_months(day: date, months: int) -> date:
    month_index = day.year * 12 + day.month - 1 - months
    year, month = divmod(month_index, 12)
    month += 1
    for candidate in range(day.day, 27, -1):
        try:
            return date(year, month, candidate)
        except ValueError:
            continue
    return date(year, month, min(day.day, 28))


def resolve_period(period: str | None = None, start: str | None = None, end: str | None = None) -> Period:
    """Turns "last_4_months", "2025", "2026-07" or explicit dates into a closed date range.

    Rolling windows end on the last full day of data (yesterday) and are counted back from there,
    which is how the challenge defines "last 4 months" (16 Jun to 16 Oct when today is 17 Oct).
    """
    now = today()
    if start or end:
        start_day = date.fromisoformat(start) if start else date(1900, 1, 1)
        end_day = date.fromisoformat(end) if end else now
        return Period(start_day, end_day, f"{start_day:%d %b %Y} to {end_day:%d %b %Y}")

    text = (period or "all").strip().lower().replace(" ", "_")
    yesterday = now - timedelta(days=1)

    if text in {"all", "all_time", "ever", "last_4_years"}:
        return Period(date(1900, 1, 1), now, "all records")
    if text in {"this_year", "ytd", "year_to_date"}:
        return Period(date(now.year, 1, 1), now, f"{now.year} to date")
    if text == "last_year":
        return Period(date(now.year - 1, 1, 1), date(now.year - 1, 12, 31), str(now.year - 1))
    if re.fullmatch(r"\d{4}", text):
        year = int(text)
        return Period(date(year, 1, 1), date(year, 12, 31), text)
    if match := re.fullmatch(r"(\d{4})-(\d{2})", text):
        year, month = int(match.group(1)), int(match.group(2))
        next_month = date(year + (month == 12), month % 12 + 1, 1)
        return Period(date(year, month, 1), next_month - timedelta(days=1), f"{date(year, month, 1):%b %Y}")
    if match := re.fullmatch(r"summer_?(\d{4})?", text):
        year = int(match.group(1) or now.year)
        return Period(date(year, 6, 1), date(year, 8, 31), f"summer {year} (Jun to Aug)")
    if match := re.fullmatch(r"last_(\d+)_(day|week|month|year)s?", text):
        amount, unit = int(match.group(1)), match.group(2)
        if unit == "day":
            start_day = yesterday - timedelta(days=amount)
        elif unit == "week":
            start_day = yesterday - timedelta(weeks=amount)
        elif unit == "month":
            start_day = _minus_months(yesterday, amount)
        else:
            start_day = _minus_months(yesterday, amount * 12)
        return Period(start_day, yesterday, f"last {amount} {unit}{'s' if amount > 1 else ''} "
                                            f"({start_day:%d %b} to {yesterday:%d %b %Y})")
    raise ValueError(
        f"Unknown period '{period}'. Use all, this_year, last_year, YYYY, YYYY-MM, summer_YYYY, "
        "last_N_days, last_N_weeks, last_N_months, last_N_years, or explicit start/end dates."
    )
