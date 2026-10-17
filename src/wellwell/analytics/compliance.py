from datetime import date, datetime, timedelta

from wellwell.analytics.facts import Facts, _date, _dt, spill_class
from wellwell.analytics.periods import resolve_period, today
from wellwell.analytics.result import ToolResult, cite
from wellwell.domain.enums import SpillClass

CONTAINMENT_LIMIT_HOURS = 4
VERIFICATION_DAYS = 60
NOV_RESPONSE_DAYS = 30
MAX_INSPECTION_GAP_DAYS = 14


def _check(protocol: str, status: str, well: str, detail: str, citations: list[str], *, due: date | None = None,
           event_date: date | None = None, fine_usd: float | None = None, open_item: bool = False) -> dict:
    return {"protocol": protocol, "status": status, "well": well, "date": event_date, "due": due,
            "detail": detail, "fine_usd": fine_usd, "still_open": open_item, "citations": citations}


class Compliance:
    """Compares the records with the SOP deadlines. Every breach carries the records that prove it."""

    def __init__(self, facts: Facts):
        self.facts = facts

    def check(self, well: str | None = None, area: str | None = None, period: str | None = "last_12_months",
              start: str | None = None, end: str | None = None, protocols: list[str] | None = None,
              only_breaches: bool = False) -> ToolResult:
        window = resolve_period(period, start, end)
        where, params = self.facts._scope_filter(area, None, [well] if well else None)
        params.update(s=window.start.isoformat(), e=window.end.isoformat())
        now = datetime.combine(today(), datetime.min.time())
        checks: list[dict] = []

        spills = self.facts._rows(
            f"""SELECT f.* FROM inspection_facts f WHERE f.eff_spill = 1
                 AND f.inspection_date BETWEEN :s AND :e {where} ORDER BY f.inspected_at""", **params)
        for spill in spills:
            checks += self._spill_checks(spill, now)

        wells_in_scope = [r["asset_id"] for r in self.facts._rows(
            f"SELECT f.asset_id FROM wells f WHERE 1=1 {where}", **params)]
        for asset_id in wells_in_scope:
            checks += self._nov_checks(asset_id, window, now)
            checks += self._flowline_test_checks(asset_id, window)
            checks += self._inspection_gap_checks(asset_id, window)
            checks += self._pending_actions(asset_id)

        if protocols:
            wanted = {p.upper() for p in protocols}
            checks = [c for c in checks if c["protocol"].upper() in wanted]
        if only_breaches:
            checks = [c for c in checks if c["status"] == "breach"]

        breaches = [c for c in checks if c["status"] == "breach"]
        fines = sum(c["fine_usd"] or 0 for c in checks if c["fine_usd"])
        open_fines = sum(c["fine_usd"] or 0 for c in checks if c["fine_usd"] and c["still_open"])
        scope = well or (self.facts.directory.area_name(self.facts.directory.resolve_area(area)) if area else "fleet")
        def section(title: str, items: list[dict]) -> str:
            if not items:
                return ""
            lines = "; ".join(f"{c['protocol']} {c['well']}: {c['detail']}"
                              + (f" [cites {', '.join(c['citations'])}]" if c["citations"] else "") for c in items)
            return f" {title} ({len(items)}): {lines}."

        pending = [c for c in checks if c["status"] in ("pending", "review", "unknown")]
        met = [c for c in checks if c["status"] == "met"]
        summary = (f"{scope}, {window.label}: {len(breaches)} breach(es)"
                   + (f", USD {fines:,.0f} in fines, USD {open_fines:,.0f} of it still open" if fines else ", no fines")
                   + "." + section("BREACHES", breaches)
                   + section("STILL PENDING (open actions, not breaches yet)", pending)
                   + section("MET", met))
        return ToolResult(
            summary=summary, rows=checks, citations=cite(*[x for c in checks for x in c["citations"]]),
            card={"breaches": len(breaches), "fines_usd": fines, "open_fines_usd": open_fines,
                  "period": window.as_dict()},
            visual={"kind": "table"},
            rules_applied=["SOP-SR SR-01: written notice within 24 h (2 h if major) for spills of 5 bbl or more",
                           "SOP-SR SR-02: contained within 4 h of discovery (time is in the repair report)",
                           "SOP-SR SR-03: agency verification within 60 days for reportable/major spills",
                           "SOP-RC RC-04: answer notices of violation within 30 days",
                           "SOP-RC RC-02: flowline integrity test once per calendar year",
                           "SOP-RC RC-01: inspect at least every 14 days",
                           "T200 manual §10 / §4: RCA after repeated corrective repairs; record shut-ins first"],
        )

    def _spill_checks(self, spill: dict, now: datetime) -> list[dict]:
        checks = []
        asset_id, inspection_id = spill["asset_id"], spill["inspection_id"]
        discovered = _dt(spill["inspected_at"])
        volume = spill["eff_spill_volume_bbl"]
        klass = spill_class(volume)
        corrections = self.facts._correction_lines(inspection_id)
        volume_fix = next((c for c in corrections if c["field"] == "spill_est_volume_bbl"), None)
        label = f"spill {discovered:%d %b %Y} ({volume} bbl, {klass.value if klass else 'unknown'})"
        if volume_fix:
            label += f", volume corrected from {volume_fix['original_value']} by {volume_fix['corrected_by']}"
        base_citations = [inspection_id, *([volume_fix["correction_id"]] if volume_fix else [])]

        repair = self.facts._one(
            """SELECT maintenance_id, report_doc_id, report_containment_hours, report_spill_class
                 FROM maintenance WHERE triggered_by_inspection_id = :i ORDER BY started_at LIMIT 1""",
            i=inspection_id)

        if klass in (SpillClass.REPORTABLE, SpillClass.MAJOR):
            sr01 = self.facts._sr01_status(spill, klass)
            status = "breach" if sr01["breach"] else "met"
            checks.append(_check("SR-01", status, asset_id, f"{label}: written notice {sr01['status']}",
                                 [*base_citations, *sr01["citations"]], due=_date(sr01["deadline"]),
                                 event_date=discovered.date()))
            novs = self.facts._rows(
                """SELECT * FROM regulatory_events WHERE related_inspection_id = :i
                    AND event_type = 'notice_of_violation'""", i=inspection_id)
            for nov in novs:
                checks[-1]["citations"].append(nov["event_id"])
                checks[-1]["detail"] += (f"; led to notice of violation {nov['event_id']} with a USD "
                                         f"{nov['fine_usd']:,.0f} fine ({nov['status']})")
                checks[-1]["fine_usd"] = (checks[-1]["fine_usd"] or 0) + (nov["fine_usd"] or 0)
                checks[-1]["still_open"] = nov["status"] == "open"

            due = discovered.date() + timedelta(days=VERIFICATION_DAYS)
            visits = self.facts._rows(
                """SELECT * FROM regulatory_events WHERE related_inspection_id = :i
                    AND event_type = 'site_inspection' ORDER BY date""", i=inspection_id)
            verified = [v for v in visits if v["status"] == "closed"]
            if verified:
                checks.append(_check("SR-03", "met", asset_id, f"{label}: clean-up verified on {verified[-1]['date']}",
                                     [*base_citations, verified[-1]["event_id"]], due=due))
            elif today() > due:
                checks.append(_check("SR-03", "breach", asset_id,
                                     f"{label}: agency verification was due by {due:%d %b %Y} and is still open",
                                     [*base_citations, *[v["event_id"] for v in visits]], due=due, open_item=True))
            else:
                checks.append(_check("SR-03", "pending", asset_id,
                                     f"{label}: agency verification of the clean-up due by {due:%d %b %Y}",
                                     [*base_citations, *[v["event_id"] for v in visits]], due=due, open_item=True))

        if repair and repair["report_containment_hours"] is not None:
            hours = repair["report_containment_hours"]
            late = hours > CONTAINMENT_LIMIT_HOURS
            checks.append(_check("SR-02", "breach" if late else "met", asset_id,
                                 f"{label}: contained after about {hours} h (limit {CONTAINMENT_LIMIT_HOURS} h), "
                                 f"from report {repair['maintenance_id']}",
                                 [*base_citations, repair["maintenance_id"], repair["report_doc_id"]],
                                 event_date=discovered.date()))
        elif klass is not None:
            checks.append(_check("SR-02", "unknown", asset_id, f"{label}: no repair report gives the containment time",
                                 base_citations, event_date=discovered.date()))

        if repair and repair["report_spill_class"] and klass and repair["report_spill_class"] != klass.value:
            checks.append(_check("SR-01", "review", asset_id,
                                 f"Report {repair['maintenance_id']} classed the spill as '{repair['report_spill_class']}', "
                                 f"but at {volume} bbl it is '{klass.value}'; the report's reporting assessment no longer holds",
                                 [*base_citations, repair["maintenance_id"]], event_date=discovered.date()))
        return checks

    def _nov_checks(self, asset_id: str, window, now: datetime) -> list[dict]:
        checks = []
        novs = self.facts._rows(
            """SELECT * FROM regulatory_events WHERE asset_id = :a AND event_type = 'notice_of_violation'
                AND date BETWEEN :s AND :e ORDER BY date""",
            a=asset_id, s=window.start.isoformat(), e=window.end.isoformat())
        for nov in novs:
            due = _date(nov["date"]) + timedelta(days=NOV_RESPONSE_DAYS)
            response = self.facts._one(
                """SELECT * FROM regulatory_events WHERE asset_id = :a AND event_type = 'nov_response_filed'
                    AND date >= :d AND (related_inspection_id = :i OR related_inspection_id IS NULL)
                    ORDER BY date LIMIT 1""", a=asset_id, d=nov["date"], i=nov["related_inspection_id"])
            if response and _date(response["date"]) <= due:
                checks.append(_check("RC-04", "met", asset_id, f"Notice {nov['event_id']} answered on {response['date']}",
                                     [nov["event_id"], response["event_id"]], due=due, event_date=_date(nov["date"])))
            elif today() > due:
                late = f"answered late on {response['date']}" if response else "no response is recorded"
                checks.append(_check("RC-04", "breach", asset_id,
                                     f"Notice {nov['event_id']} needed a response by {due:%d %b %Y}; {late}",
                                     [nov["event_id"], *([response["event_id"]] if response else [])], due=due,
                                     event_date=_date(nov["date"]), open_item=response is None))
            else:
                checks.append(_check("RC-04", "pending", asset_id,
                                     f"Notice {nov['event_id']} needs a response by {due:%d %b %Y}",
                                     [nov["event_id"]], due=due, event_date=_date(nov["date"]), open_item=True))
        return checks

    def _flowline_test_checks(self, asset_id: str, window) -> list[dict]:
        checks = []
        tests = self.facts._rows(
            """SELECT maintenance_id, DATE(started_at) AS day FROM maintenance WHERE asset_id = :a
                AND LOWER(issue) LIKE '%flowline integrity test%' ORDER BY started_at""", a=asset_id)
        installed = self.facts._one("SELECT installed_on FROM wells WHERE asset_id = :a", a=asset_id)
        installed_year = _date(installed["installed_on"]).year if installed and installed["installed_on"] else 0
        first_data_year = 2023
        for year in range(max(window.start.year, installed_year, first_data_year), window.end.year + 1):
            done = [t for t in tests if str(t["day"]).startswith(str(year))]
            if done:
                checks.append(_check("RC-02", "met", asset_id, f"Flowline integrity test done on {done[0]['day']} ({year})",
                                     [done[0]["maintenance_id"]], event_date=_date(done[0]["day"])))
            elif year < today().year:
                checks.append(_check("RC-02", "breach", asset_id, f"No flowline integrity test recorded in {year}", []))
            else:
                checks.append(_check("RC-02", "pending", asset_id, f"Flowline integrity test for {year} not yet recorded",
                                     [], due=date(year, 12, 31), open_item=True))
        return checks

    def _inspection_gap_checks(self, asset_id: str, window) -> list[dict]:
        days = [r["inspected_at"] for r in self.facts._rows(
            """SELECT inspected_at FROM inspections WHERE asset_id = :a AND inspection_date BETWEEN :s AND :e
                ORDER BY inspected_at""", a=asset_id, s=window.start.isoformat(), e=window.end.isoformat())]
        checks = []
        for previous, current in zip(days, days[1:], strict=False):
            gap = (_dt(current) - _dt(previous)).days
            if gap > MAX_INSPECTION_GAP_DAYS:
                checks.append(_check("RC-01", "breach", asset_id,
                                     f"{gap} days between inspections ({_dt(previous):%d %b} to {_dt(current):%d %b %Y})",
                                     [], event_date=_dt(current).date()))
        return checks

    def _pending_actions(self, asset_id: str) -> list[dict]:
        actions = []
        rca = self.facts.rca_due(wells=[asset_id])
        for row in rca.rows:
            ids = row["repairs"].split(", ")
            flagged = self.facts._rows(
                f"""SELECT maintenance_id, detail FROM report_compliance WHERE asset_id = :a
                     AND LOWER(result) LIKE '%action%' AND maintenance_id IN ({', '.join(f"'{i}'" for i in ids)})""",
                a=asset_id)
            detail = (f"Root-cause analysis due on the {row['equipment']}: {row['corrective_repairs_12m']} corrective "
                      f"repairs in the last 12 months ({row['repairs']}), threshold {row['threshold']} per {row['rule']}")
            if flagged:
                detail += "; report " + ", ".join(f["maintenance_id"] for f in flagged) + " marks it 'Action required'"
            actions.append(_check("RCA", "pending", asset_id, detail, ids, open_item=True))

        notes = self.facts._rows(
            """SELECT * FROM notes WHERE asset_id = :a AND note_type != 'correction'
                AND (valid_to IS NULL OR valid_to >= :t) AND valid_from <= :t
                AND (LOWER(text) LIKE '%shut in%' OR LOWER(text) LIKE '%shut-in%' OR LOWER(text) LIKE '%workover%')""",
            a=asset_id, t=today().isoformat())
        for note in notes:
            start = _date(note["valid_from"])
            recorded = self.facts._one(
                "SELECT maintenance_id FROM maintenance WHERE asset_id = :a AND DATE(started_at) >= :s LIMIT 1",
                a=asset_id, s=(start - timedelta(days=1)).isoformat())
            if not recorded and start <= today():
                actions.append(_check(
                    "T200 §4", "pending", asset_id,
                    f"Planned shut-in from {start:%d %b} ({note['note_id']}) is not in the maintenance system; the manual "
                    "requires it to be recorded before work starts", [note["note_id"], "OM-T200"], open_item=True))
        return actions
