import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from wellwell.analytics.directory import Directory
from wellwell.db import dialect
from wellwell.db.dialect import plain_row
from wellwell.analytics.periods import resolve_period, today
from wellwell.analytics.result import ToolResult, cite
from wellwell.domain.enums import SpillClass

SPILL_SOP = "SOP-SR"
NOT_RUNNING_HINTS = re.compile(r"not moving|not running|stopped|idle|shut|down|no motion", re.IGNORECASE)


def spill_class(volume: float | None) -> SpillClass | None:
    if volume is None:
        return None
    if volume >= 25:
        return SpillClass.MAJOR
    if volume >= 5:
        return SpillClass.REPORTABLE
    if volume >= 1:
        return SpillClass.MODERATE
    return SpillClass.MINOR


def _date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value)[:10])


def _dt(value) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value)[:19])


class Facts:
    """Deterministic answers to the questions operators ask. The model never does this arithmetic."""

    def __init__(self, session: Session):
        self.session = session
        self.directory = Directory(session)

    def _rows(self, sql: str, **params) -> list[dict]:
        return [plain_row(dict(row)) for row in self.session.execute(text(sql), params).mappings()]

    def _one(self, sql: str, **params) -> dict | None:
        rows = self._rows(sql, **params)
        return rows[0] if rows else None

    def _rule(self, well_type: str) -> dict:
        return self._one("SELECT * FROM well_type_rules WHERE well_type = :t", t=well_type) or {}

    def _scope_filter(self, area: str | None = None, well_type: str | None = None,
                      wells: list[str] | None = None, alias: str = "f") -> tuple[str, dict]:
        clauses, params = [], {}
        if area:
            area_id = self.directory.resolve_area(area)
            if area_id is None:
                raise ValueError(f"Unknown area '{area}'. Known areas: "
                                 + ", ".join(a["name"] for a in self.directory.areas.values()))
            clauses.append(f"{alias}.area_id = :area_id")
            params["area_id"] = area_id
        if well_type:
            clauses.append(f"{alias}.well_type = :well_type")
            params["well_type"] = well_type.upper()
        if wells:
            resolved = [self.directory.resolve_well(w) for w in wells]
            missing = [w for w, r in zip(wells, resolved, strict=False) if r is None]
            if missing:
                raise ValueError(f"Unknown well(s): {', '.join(missing)}")
            keys = [f"w{i}" for i in range(len(resolved))]
            clauses.append(f"{alias}.asset_id IN ({', '.join(':' + k for k in keys)})")
            params.update(dict(zip(keys, resolved, strict=False)))
        return (" AND " + " AND ".join(clauses)) if clauses else "", params

    def _well(self, well: str) -> dict:
        asset_id = self.directory.resolve_well(well)
        if asset_id is None:
            raise ValueError(f"Unknown well '{well}'. Wells look like WELL-0001.")
        return self._one("SELECT * FROM wells WHERE asset_id = :a", a=asset_id)

    def _corrections_for(self, inspection_id: str) -> list[dict]:
        return self._rows(
            "SELECT * FROM corrections WHERE target_id = :i AND status = 'active' ORDER BY created_at", i=inspection_id)

    def _correction_lines(self, inspection_id: str) -> list[dict]:
        lines = []
        for correction in self._corrections_for(inspection_id):
            lines.append({
                "correction_id": correction["correction_id"],
                "field": correction["field"],
                "original_value": correction["original_value"],
                "corrected_value": correction["corrected_value"],
                "corrected_by": self.directory.user_name(correction["author_user_id"]),
                "reason": correction["reason"],
                "source_note_id": correction["source_note_id"],
                "kind": correction["kind"],
            })
        return lines

    def _notes_about_inspection(self, inspection_id: str) -> list[dict]:
        rows = self._rows("SELECT * FROM notes WHERE inspection_id = :i OR text LIKE :pattern ORDER BY created_at",
                          i=inspection_id, pattern=f"%{inspection_id}%")
        return [{"note_id": r["note_id"], "type": r["note_type"], "author": self.directory.user_name(r["author_user_id"]),
                 "created_at": r["created_at"], "text": r["text"], "is_formal_correction": r["note_type"] == "correction"}
                for r in rows]

    def _context_notes(self, asset_id: str, on_day: date, well_type: str | None = None) -> list[dict]:
        """Notes that explain the state of a well on a given day (shut-ins, workovers, delays)."""
        rows = self._rows(
            """SELECT * FROM notes
                WHERE note_type != 'correction'
                  AND (asset_id = :a OR (scope = 'well_type' AND well_type = :t) OR scope = 'fleet')
                ORDER BY created_at""",
            a=asset_id, t=well_type,
        )
        relevant = []
        for row in rows:
            valid_from = _date(row["valid_from"]) or _date(row["created_at"])
            valid_to = _date(row["valid_to"])
            created = _date(row["created_at"])
            covers = valid_from <= on_day + timedelta(days=7) and (
                (valid_to is not None and valid_to >= on_day - timedelta(days=7))
                or (valid_to is None and created >= on_day - timedelta(days=45))
            )
            if covers:
                relevant.append({
                    "note_id": row["note_id"], "author": self.directory.user_name(row["author_user_id"]),
                    "type": row["note_type"], "origin": row["origin"], "text": row["text"],
                    "valid_from": valid_from, "valid_to": valid_to, "created_at": row["created_at"],
                })
        return relevant

    def _open_repairs(self, asset_id: str, on_moment: datetime) -> list[dict]:
        rows = self._rows(
            """SELECT * FROM maintenance WHERE asset_id = :a AND started_at <= :m
                 AND (completed_at IS NULL OR completed_at >= :m) ORDER BY started_at""",
            a=asset_id, m=on_moment.isoformat(sep=" "),
        )
        now = datetime.combine(today(), datetime.min.time())
        return [{
            "maintenance_id": r["maintenance_id"], "issue": r["issue"], "equipment_kind": r["equipment_kind"],
            "type": r["maintenance_type"], "started": _date(r["started_at"]),
            "planned_or_actual_end": _date(r["completed_at"]),
            "still_open": r["completed_at"] is None or _dt(r["completed_at"]) > now,
        } for r in rows]

    def _claims(self, asset_id: str) -> list[dict]:
        rows = self._rows("SELECT * FROM claims WHERE asset_id = :a ORDER BY created_at", a=asset_id)
        return [{"claim_id": r["claim_id"], "author": self.directory.user_name(r["author_user_id"]),
                 "statement": r["statement"], "claimed_state": r["claimed_state"], "status": r["status"],
                 "created_at": r["created_at"], "note_id": r["note_id"]} for r in rows]

    def _explain_not_producing(self, latest: dict, well: dict) -> tuple[list[str], list[str]]:
        reasons, citations = [], []
        inspected_at = _dt(latest["inspected_at"])
        for repair in self._open_repairs(well["asset_id"], inspected_at):
            state = "still open" if repair["still_open"] else f"finished {repair['planned_or_actual_end']:%d %b}"
            reasons.append(f"Repair {repair['maintenance_id']}: {repair['issue']} ({repair['equipment_kind']}), "
                           f"started {repair['started']:%d %b %Y}, {state}")
            citations.append(repair["maintenance_id"])
        for note in self._context_notes(well["asset_id"], inspected_at.date(), well["well_type"]):
            reasons.append(f"Note {note['note_id']} by {note['author']}: {note['text']}")
            citations.append(note["note_id"])
        return reasons, citations

    def well_status(self, well: str) -> ToolResult:
        row = self._well(well)
        asset_id = row["asset_id"]
        latest = self._one("SELECT * FROM latest_inspection WHERE asset_id = :a", a=asset_id)
        if latest is None:
            return ToolResult(summary=f"{asset_id} has no inspections on record.", citations=[asset_id])
        rule = self._rule(row["well_type"])
        producing = bool(latest["producing"])
        recorded = row["recorded_status"]
        stale = (recorded == "active" and not producing) or (recorded == "down" and producing)
        reasons, reason_citations = ([], []) if producing else self._explain_not_producing(latest, row)
        corrections = self._correction_lines(latest["inspection_id"])
        inspection_notes = self._notes_about_inspection(latest["inspection_id"])
        claims = self._claims(asset_id)
        disagreements = []
        if producing and latest["notes"] and NOT_RUNNING_HINTS.search(latest["notes"]):
            disagreements.append(f"The inspector's own note on {latest['inspection_id']} says \"{latest['notes']}\", "
                                 "which suggests the well was not running.")
        threshold = latest["producing_min_delta_c"]
        override = threshold != rule.get("producing_min_delta_c")
        card = {
            "asset_id": asset_id, "name": row["name"], "well_type": row["well_type"],
            "area": self.directory.area_name(row["area_id"]), "team": self.directory.teams.get(row["team_id"], {}).get("name"),
            "in_charge": self.directory.user_name(row["in_charge_user_id"]),
            "location": {"lat": row["lat"], "lon": row["lon"]},
            "producing_now": producing,
            "latest_inspection": {
                "inspection_id": latest["inspection_id"], "inspected_at": latest["inspected_at"],
                "method": latest["method"], "inspector": self.directory.user_name(latest["inspector_user_id"]),
                "wellhead_temp_c": latest["eff_wellhead_temp_c"], "ambient_temp_c": latest["eff_ambient_temp_c"],
                "original_wellhead_temp_c": latest["wellhead_temp_c"],
                "temp_above_air_c": latest["temp_delta_c"], "inspector_note": latest["notes"],
            },
            "rule": {"min_above_air_c": threshold, "source": rule.get("producing_rule_ref"),
                     "overridden_by_team_rule": override},
            "recorded_status": recorded, "recorded_status_out_of_date": stale,
            "reasons_not_producing": reasons, "corrections_applied": corrections,
            "notes_on_latest_inspection": inspection_notes, "user_claims": claims, "disagreements": disagreements,
            "current_and_planned_notes": self._current_notes(asset_id, row["well_type"]),
            **self._card_extras(asset_id),
        }
        verdict = "is producing oil" if producing else "is NOT producing oil"
        summary = (f"{asset_id} {verdict} as of its latest inspection {latest['inspection_id']} "
                   f"({_date(latest['inspection_date']):%d %b %Y}, {latest['method']}, "
                   f"{card['latest_inspection']['inspector']}): wellhead {latest['eff_wellhead_temp_c']} °C vs air "
                   f"{latest['eff_ambient_temp_c']} °C = {latest['temp_delta_c']} °C above air; the {row['well_type']} "
                   f"rule needs at least {threshold} °C ({rule.get('producing_rule_ref')}). "
                   f"Recorded status '{recorded}'{' is out of date' if stale else ' agrees'}.")
        if reasons:
            summary += " Why: " + " | ".join(reasons) + "."
            if any("not yet in the maintenance system" in r.lower() for r in reasons):
                summary += " The shut-in is not yet recorded in the maintenance system."
        if not producing and not reasons:
            summary += " No repair or note explains the stop; suggest a field check."
        if latest["notes"]:
            summary += f" Inspector's note: \"{latest['notes']}\"."
        for note in card["current_and_planned_notes"]:
            if note["note_id"] in reason_citations:
                continue
            summary += (f" Note {note['note_id']} by {note['author']} ({note['valid_from']} to "
                        f"{note['valid_to'] or 'open'}): {note['text']}")
            reason_citations.append(note["note_id"])
        for claim in claims:
            summary += f" Claim {claim['claim_id']} by {claim['author']}: \"{claim['statement']}\" ({claim['status']})."
        if corrections:
            summary += " Corrections applied: " + "; ".join(
                f"{c['correction_id']} {c['field']} {c['original_value']} -> {c['corrected_value']} by "
                f"{c['corrected_by']} ({c['reason']})" for c in corrections)
        if disagreements:
            summary += " Disagreement: " + " ".join(disagreements)
        citations = cite(latest["inspection_id"], *reason_citations, rule.get("manual_doc_id"),
                         *[c["correction_id"] for c in corrections], *[n["note_id"] for n in inspection_notes],
                         *[c["claim_id"] for c in claims])
        return ToolResult(summary=summary, card=card, citations=citations,
                          visual={"kind": "status_card"},
                          rules_applied=[f"{rule.get('producing_rule_ref')}: producing when wellhead is at least "
                                         f"{threshold} °C above air"])

    def _card_extras(self, asset_id: str) -> dict:
        year_start = date(today().year, 1, 1).isoformat()
        stopped = self._one("SELECT COUNT(*) AS n FROM inspection_facts WHERE asset_id = :a AND producing = 0 "
                            "AND inspection_date >= :s", a=asset_id, s=year_start)
        last_repair = self._one("SELECT maintenance_id, issue, maintenance_type, started_at, completed_at, cost_total_usd "
                                "FROM maintenance WHERE asset_id = :a ORDER BY started_at DESC LIMIT 1", a=asset_id)
        open_events = self._rows("SELECT event_id, event_type, protocol_id, date, fine_usd, detail FROM regulatory_events "
                                 "WHERE asset_id = :a AND status = 'open' ORDER BY date", a=asset_id)
        return {
            "downtime_days_this_year": 7 * (stopped["n"] if stopped else 0),
            "last_repair": last_repair,
            "open_regulatory_events": open_events,
        }

    def _current_notes(self, asset_id: str, well_type: str) -> list[dict]:
        """Notes valid today or starting later: planned shut-ins, workovers, things people told us."""
        rows = self._rows(
            """SELECT * FROM notes WHERE note_type != 'correction'
                 AND (asset_id = :a OR (scope = 'well_type' AND well_type = :t) OR scope = 'fleet')
                 AND (valid_to IS NULL OR valid_to >= :d)
               ORDER BY created_at""", a=asset_id, t=well_type, d=today().isoformat())
        current = []
        for row in rows:
            created = _date(row["created_at"])
            if row["valid_to"] is None and row["origin"] == "dataset" and created < today() - timedelta(days=45):
                continue
            current.append({"note_id": row["note_id"], "author": self.directory.user_name(row["author_user_id"]),
                            "text": row["text"], "valid_from": _date(row["valid_from"]),
                            "valid_to": _date(row["valid_to"]), "origin": row["origin"]})
        return current

    def wells_not_producing(self, area: str | None = None, well_type: str | None = None,
                            wells: list[str] | None = None) -> ToolResult:
        where, params = self._scope_filter(area, well_type, wells)
        latest_rows = self._rows(f"SELECT * FROM latest_inspection f WHERE f.producing = 0 {where} ORDER BY f.asset_id",
                                 **params)
        rows, citations = [], []
        for latest in latest_rows:
            well = self._well(latest["asset_id"])
            reasons, reason_citations = self._explain_not_producing(latest, well)
            corrections = self._correction_lines(latest["inspection_id"])
            stale = well["recorded_status"] == "active"
            rows.append({
                "well": latest["asset_id"], "type": latest["well_type"],
                "last_inspected": _date(latest["inspection_date"]), "inspection_id": latest["inspection_id"],
                "wellhead_above_air_c": latest["temp_delta_c"],
                "recorded_status": well["recorded_status"] + (" (out of date)" if stale else ""),
                "reason": "; ".join(reasons) if reasons else "No recorded reason - suggest a field check",
                "inspector_note": latest["notes"] or "",
                "corrected": bool(corrections),
            })
            citations += [latest["inspection_id"], *reason_citations]
        # wells whose correction flipped them back to producing are worth calling out
        flipped = self._rows(
            f"""SELECT f.asset_id, f.inspection_id, f.temp_delta_c FROM latest_inspection f
                 WHERE f.producing = 1 AND f.has_correction = 1 {where}""", **params)
        notes = [f"{r['asset_id']} is producing after a correction to {r['inspection_id']} "
                 f"({r['temp_delta_c']} °C above air), so it is not listed." for r in flipped]
        for r in flipped:
            citations += [r["inspection_id"], *[c["correction_id"] for c in self._correction_lines(r["inspection_id"])]]
        scope = self.directory.area_name(self.directory.resolve_area(area)) if area else "the fleet"
        summary = f"{len(rows)} well(s) in {scope} are not producing oil at their latest inspection."
        if notes:
            summary += " " + " ".join(notes)
        rules = {self._rule(r["type"]).get("producing_rule_ref") for r in rows}
        return ToolResult(summary=summary, rows=rows, citations=cite(*citations, "OM-T200", "OM-T350"),
                          visual={"kind": "table"}, warnings=notes,
                          rules_applied=[f"{ref}" for ref in rules if ref])

    def temperature_history(self, well: str, weeks: int | None = 13, period: str | None = None,
                            start: str | None = None, end: str | None = None) -> ToolResult:
        row = self._well(well)
        if period or start or end:
            window = resolve_period(period, start, end)
            data = self._rows(
                """SELECT * FROM inspection_facts WHERE asset_id = :a AND inspection_date BETWEEN :s AND :e
                    ORDER BY inspected_at""", a=row["asset_id"], s=window.start.isoformat(), e=window.end.isoformat())
        else:
            data = self._rows("SELECT * FROM inspection_facts WHERE asset_id = :a ORDER BY inspected_at DESC LIMIT :n",
                              a=row["asset_id"], n=weeks or 13)[::-1]
        rows = [{
            "date": _date(r["inspection_date"]), "inspection_id": r["inspection_id"],
            "wellhead_temp_c": r["eff_wellhead_temp_c"], "air_temp_c": r["eff_ambient_temp_c"],
            "above_air_c": r["temp_delta_c"], "producing": bool(r["producing"]),
            "threshold_c": r["producing_min_delta_c"], "corrected": bool(r["has_correction"]),
        } for r in data]
        not_producing = [r for r in rows if not r["producing"]]
        summary = (f"{len(rows)} readings for {row['asset_id']} from {rows[0]['date']:%d %b %Y} to "
                   f"{rows[-1]['date']:%d %b %Y}. Not producing on: "
                   + (", ".join(f"{r['date']:%d %b %Y}" for r in not_producing) or "none")) if rows else "No readings."
        rule = self._rule(row["well_type"])
        return ToolResult(summary=summary, rows=rows, citations=cite(*[r["inspection_id"] for r in not_producing],
                                                                      rule.get("manual_doc_id")),
                          visual={"kind": "line", "x": "date", "y": ["wellhead_temp_c", "air_temp_c"],
                                  "mark": "producing"},
                          rules_applied=[f"{rule.get('producing_rule_ref')}: at least {rule.get('producing_min_delta_c')} "
                                         "°C above air"])

    def _spill_rows(self, where: str, params: dict) -> list[dict]:
        return self._rows(
            f"""SELECT f.* FROM inspection_facts f WHERE f.spill_detected = 1 {where} ORDER BY f.inspected_at""",
            **params)

    def spills(self, well: str | None = None, area: str | None = None, period: str | None = "all",
               start: str | None = None, end: str | None = None, min_volume_bbl: float | None = None,
               reportable_only: bool = False) -> ToolResult:
        window = resolve_period(period, start, end)
        where, params = self._scope_filter(area, None, [well] if well else None)
        where += " AND f.inspection_date BETWEEN :s AND :e"
        params.update(s=window.start.isoformat(), e=window.end.isoformat())
        detections = self._spill_rows(where, params)
        rows, excluded, citations = [], [], []
        for d in detections:
            corrections = self._correction_lines(d["inspection_id"])
            notes = self._notes_about_inspection(d["inspection_id"])
            if not d["eff_spill"]:
                excluded.append({"inspection_id": d["inspection_id"], "date": _date(d["inspection_date"]),
                                 "well": d["asset_id"], "reason": "; ".join(c["reason"] or "" for c in corrections),
                                 "corrected_by": ", ".join(c["corrected_by"] or "" for c in corrections),
                                 "correction": ", ".join(c["correction_id"] for c in corrections)})
                citations += [d["inspection_id"], *[c["source_note_id"] or c["correction_id"] for c in corrections]]
                continue
            volume = d["eff_spill_volume_bbl"]
            klass = spill_class(volume)
            if min_volume_bbl is not None and (volume or 0) < min_volume_bbl:
                continue
            if reportable_only and klass not in (SpillClass.REPORTABLE, SpillClass.MAJOR):
                continue
            volume_fix = next((c for c in corrections if c["field"] == "spill_est_volume_bbl"), None)
            disagreement = [n for n in notes if not n["is_formal_correction"]]
            reporting = self._sr01_status(d, klass) if klass in (SpillClass.REPORTABLE, SpillClass.MAJOR) else None
            rows.append({
                "well": d["asset_id"], "date": _date(d["inspection_date"]), "inspection_id": d["inspection_id"],
                "area": d["area_name"], "volume_bbl": volume, "class": klass.value if klass else None,
                "area_pct": d["spill_area_pct"], "lat": d["lat"], "lon": d["lon"],
                "ai_estimate_bbl": d["spill_est_volume_bbl"] if volume_fix else None,
                "volume_corrected_by": volume_fix["corrected_by"] if volume_fix else None,
                "regulator_report": reporting["status"] if reporting else "not required",
                "notes_disagreeing": "; ".join(f"{n['note_id']} ({n['author']}): {n['text']}" for n in disagreement),
            })
            citations += [d["inspection_id"], *(reporting["citations"] if reporting else []),
                          *[n["note_id"] for n in disagreement], *([volume_fix["correction_id"]] if volume_fix else [])]
        total = round(sum(r["volume_bbl"] or 0 for r in rows), 2)
        scope = well or (self.directory.area_name(self.directory.resolve_area(area)) if area else "the fleet")
        summary = (f"{len(rows)} real spill(s) for {scope} in {window.label}, {total} bbl in total. "
                   f"{len(detections)} detection(s) in the period; {len(excluded)} were corrected as not a spill and "
                   "do not count.")
        if reportable_only:
            summary = f"{len(rows)} spill(s) of 5 bbl or more needing a regulator report for {scope} in {window.label}."
        return ToolResult(summary=summary, rows=rows, citations=cite(*citations, SPILL_SOP),
                          card={"excluded_detections": excluded, "period": window.as_dict(), "total_bbl": total},
                          visual={"kind": "table"},
                          rules_applied=["SOP-SR §7: corrected detections do not count as spills",
                                         "SOP-SR §3: minor <1, moderate 1-<5, reportable 5-<25, major >=25 bbl"])

    def _sr01_status(self, spill: dict, klass: SpillClass) -> dict:
        limit_hours = 2 if klass == SpillClass.MAJOR else 24
        discovered = _dt(spill["inspected_at"])
        deadline = discovered + timedelta(hours=limit_hours)
        reports = self._rows(
            """SELECT * FROM regulatory_events WHERE related_inspection_id = :i AND event_type = 'spill_report_filed'""",
            i=spill["inspection_id"])
        if reports:
            report = reports[0]
            hours = report["hours_to_report"]
            late = hours is not None and hours > limit_hours
            return {"status": f"filed after {hours} h ({'late' if late else 'on time'}, limit {limit_hours} h)",
                    "breach": late, "citations": [report["event_id"]], "deadline": deadline, "hours": hours}
        overdue = datetime.combine(today(), datetime.min.time()) > deadline
        return {"status": f"no written notice on record ({'overdue' if overdue else 'due'} "
                          f"{deadline:%d %b %Y %H:%M} UTC)",
                "breach": overdue, "citations": [], "deadline": deadline, "hours": None}

    def downtime(self, wells: list[str] | None = None, area: str | None = None, well_type: str | None = None,
                 period: str | None = "this_year", start: str | None = None, end: str | None = None,
                 top_n: int | None = None) -> ToolResult:
        window = resolve_period(period, start, end)
        where, params = self._scope_filter(area, well_type, wells)
        params.update(s=window.start.isoformat(), e=window.end.isoformat())
        stopped = self._rows(
            f"""SELECT f.* FROM inspection_facts f
                 WHERE f.producing = 0 AND f.inspection_date BETWEEN :s AND :e {where}
                 ORDER BY f.asset_id, f.inspected_at""", **params)
        in_scope = self._rows(f"SELECT f.asset_id, f.well_type FROM wells f WHERE 1=1 {where}", **params)
        per_well: dict[str, list[dict]] = defaultdict(list)
        for row in stopped:
            per_well[row["asset_id"]].append(row)
        ranking = []
        for well in in_scope:
            readings = per_well.get(well["asset_id"], [])
            ranking.append({"well": well["asset_id"], "type": well["well_type"], "downtime_days": 7 * len(readings),
                            "not_producing_inspections": len(readings)})
        ranking.sort(key=lambda r: (-r["downtime_days"], r["well"]))
        if top_n:
            cutoff = ranking[top_n - 1]["downtime_days"] if len(ranking) >= top_n else 0
            shown = [r for r in ranking if r["downtime_days"] >= cutoff and r["downtime_days"] > 0][:max(top_n, 1)]
            next_up = next((r for r in ranking[len(shown):] if r["downtime_days"] > 0), None)
        else:
            shown = [r for r in ranking if r["downtime_days"] > 0] if not wells else ranking
            next_up = None
        citations = []
        for entry in shown:
            evidence = []
            for reading in per_well.get(entry["well"], []):
                cause = self._cause_for(reading)
                evidence.append({"inspection_id": reading["inspection_id"], "date": _date(reading["inspection_date"]),
                                 "wellhead_c": reading["eff_wellhead_temp_c"], "air_c": reading["eff_ambient_temp_c"],
                                 "above_air_c": reading["temp_delta_c"], "cause": cause["text"]})
                citations += [reading["inspection_id"], *cause["citations"]]
            entry["evidence"] = evidence
        summary = f"Downtime in {window.label}: " + ", ".join(
            f"{r['well']} ({r['type']}) {r['downtime_days']} days ({r['not_producing_inspections']} × 7)"
            for r in shown[:15])
        if len(shown) <= 3:
            for entry in shown:
                for e in entry.get("evidence", [])[:12]:
                    summary += (f" | {entry['well']} {e['date']:%d %b %Y} {e['inspection_id']}: wellhead {e['wellhead_c']} °C"
                                f" vs air {e['air_c']} °C = {e['above_air_c']} °C above air (not producing); cause: {e['cause']}")
        if next_up:
            summary += f". Next: {next_up['well']} with {next_up['downtime_days']} days."
        if not shown:
            summary = f"No downtime in {window.label} for the selected wells."
        visual = {"kind": "bar", "x": "well", "y": ["downtime_days"]} if len(shown) > 1 else {"kind": "none"}
        return ToolResult(summary=summary, rows=shown, citations=cite(*citations),
                          card={"period": window.as_dict(), "next": next_up}, visual=visual,
                          rules_applied=["Dataset Guide: each weekly inspection that finds the well not producing "
                                         "counts as 7 days of downtime; missed inspections add none",
                                         "Well-type manual production temperature rule"])

    def _cause_for(self, reading: dict) -> dict:
        moment = _dt(reading["inspected_at"])
        repairs = self._rows(
            """SELECT maintenance_id, issue, maintenance_type, started_at, completed_at, triggered_by_inspection_id
                 FROM maintenance
                WHERE asset_id = :a AND (triggered_by_inspection_id = :i
                   OR (started_at <= :m AND (completed_at IS NULL OR completed_at >= :m))
                   OR (started_at > :m AND started_at <= :m3))
                ORDER BY CASE WHEN triggered_by_inspection_id = :i THEN 0 ELSE 1 END,
                         CASE WHEN maintenance_type = 'corrective' THEN 0 ELSE 1 END, started_at""",
            a=reading["asset_id"], i=reading["inspection_id"], m=moment.isoformat(sep=" "),
            m3=(moment + timedelta(days=3)).isoformat(sep=" "))
        if repairs:
            first = repairs[0]
            return {"text": f"{first['issue']} ({first['maintenance_id']})", "citations": [first["maintenance_id"]]}
        notes = self._context_notes(reading["asset_id"], moment.date(), reading.get("well_type"))
        if notes:
            return {"text": f"{notes[0]['text']} ({notes[0]['note_id']})", "citations": [notes[0]["note_id"]]}
        return {"text": "no recorded cause", "citations": []}

    def aggregate(self, metric: str, rows_by: str = "area", columns_by: str | None = None,
                  period: str | None = "this_year", start: str | None = None, end: str | None = None,
                  area: str | None = None, well_type: str | None = None) -> ToolResult:
        """Counts or sums across two dimensions: area / well / well_type / month / year / equipment_kind."""
        window = resolve_period(period, start, end)
        where, params = self._scope_filter(area, well_type, None)
        params.update(s=window.start.isoformat(), e=window.end.isoformat())
        dims = {
            "area": "f.area_name", "well": "f.asset_id", "well_type": "f.well_type",
            "month": dialect.month("f.day"), "year": dialect.year("f.day"),
            "equipment_kind": "f.equipment_kind",
        }
        if rows_by not in dims or (columns_by and columns_by not in dims):
            raise ValueError(f"rows_by/columns_by must be one of {sorted(dims)}")
        if metric in ("spill_count", "spill_volume_bbl"):
            base = ("SELECT asset_id, well_type, area_id, area_name, inspection_date AS day, eff_spill_volume_bbl AS v, "
                    "NULL AS equipment_kind FROM inspection_facts WHERE eff_spill = 1")
            value = "COUNT(*)" if metric == "spill_count" else dialect.round_("SUM(f.v)", 1)
        elif metric == "downtime_days":
            base = ("SELECT asset_id, well_type, area_id, area_name, inspection_date AS day, 7 AS v, "
                    "NULL AS equipment_kind FROM inspection_facts WHERE producing = 0")
            value = "SUM(f.v)"
        elif metric in ("repair_cost_usd", "repair_count", "corrective_repair_count"):
            corrective = " AND m.maintenance_type = 'corrective'" if metric == "corrective_repair_count" else ""
            base = ("SELECT m.asset_id, w.well_type, w.area_id, a.name AS area_name, DATE(m.started_at) AS day, "
                    "m.cost_total_usd AS v, m.equipment_kind FROM maintenance m JOIN wells w ON w.asset_id = m.asset_id "
                    f"LEFT JOIN areas a ON a.area_id = w.area_id WHERE 1=1{corrective}")
            value = dialect.round_("SUM(f.v)", 0) if metric == "repair_cost_usd" else "COUNT(*)"
        elif metric == "fines_usd":
            base = ("SELECT r.asset_id, w.well_type, w.area_id, a.name AS area_name, r.date AS day, r.fine_usd AS v, "
                    "NULL AS equipment_kind FROM regulatory_events r JOIN wells w ON w.asset_id = r.asset_id "
                    "LEFT JOIN areas a ON a.area_id = w.area_id WHERE r.fine_usd > 0")
            value = "SUM(f.v)"
        else:
            raise ValueError("metric must be spill_count, spill_volume_bbl, downtime_days, repair_cost_usd, "
                             "repair_count, corrective_repair_count or fines_usd")
        group = [dims[rows_by]] + ([dims[columns_by]] if columns_by else [])
        sql = (f"SELECT {', '.join(f'{g} AS d{i}' for i, g in enumerate(group))}, {value} AS value "
               f"FROM ({base}) f WHERE f.day BETWEEN :s AND :e {where} GROUP BY {', '.join(group)} "
               f"ORDER BY {', '.join(group)}")
        data = self._rows(sql, **params)
        if columns_by:
            matrix: dict[str, dict] = defaultdict(dict)
            columns: list[str] = []
            for r in data:
                matrix[r["d0"]][r["d1"]] = r["value"]
                if r["d1"] not in columns:
                    columns.append(r["d1"])
            columns.sort()
            rows = [{rows_by: key, **{c: values.get(c, 0) for c in columns},
                     "total": round(sum(v or 0 for v in values.values()), 1)} for key, values in matrix.items()]
            visual = {"kind": "heatmap", "x": columns_by, "y": rows_by, "columns": columns}
        else:
            rows = [{rows_by: r["d0"], metric: r["value"]} for r in data]
            visual = {"kind": "bar", "x": rows_by, "y": [metric]}
        summary = f"{metric} by {rows_by}{' and ' + columns_by if columns_by else ''} for {window.label}: " + "; ".join(
            f"{r[rows_by]}: {r.get('total', r.get(metric))}" for r in rows[:12])
        rules = ["SOP-SR §7: corrected detections excluded"] if metric.startswith("spill") else []
        if metric == "downtime_days":
            rules.append("Dataset Guide: 7 days per not-producing weekly inspection")
        return ToolResult(summary=summary, rows=rows, visual=visual, card={"period": window.as_dict()},
                          citations=cite(SPILL_SOP if metric.startswith("spill") else None), rules_applied=rules)

    def repairs(self, wells: list[str] | None = None, area: str | None = None, period: str | None = "this_year",
                start: str | None = None, end: str | None = None, maintenance_type: str | None = None,
                equipment_kind: str | None = None) -> ToolResult:
        window = resolve_period(period, start, end)
        where, params = self._scope_filter(area, None, wells, alias="w")
        params.update(s=window.start.isoformat(), e=window.end.isoformat())
        extra = ""
        if maintenance_type:
            extra += " AND m.maintenance_type = :mt"
            params["mt"] = maintenance_type
        if equipment_kind:
            extra += " AND m.equipment_kind = :ek"
            params["ek"] = equipment_kind
        data = self._rows(
            f"""SELECT m.*, w.well_type FROM maintenance m JOIN wells w ON w.asset_id = m.asset_id
                 WHERE DATE(m.started_at) BETWEEN :s AND :e {where} {extra} ORDER BY m.asset_id, m.started_at""",
            **params)
        rows = [{"well": r["asset_id"], "maintenance_id": r["maintenance_id"], "started": _date(r["started_at"]),
                 "type": r["maintenance_type"], "equipment": r["equipment_kind"], "issue": r["issue"],
                 "technician": self.directory.user_name(r["technician_user_id"]), "contractor": r["contractor"],
                 "downtime_hours": r["downtime_hours"], "cost_usd": r["cost_total_usd"],
                 "report": r["report_doc_id"]} for r in data]
        totals: dict[str, float] = defaultdict(float)
        for r in rows:
            totals[r["well"]] += r["cost_usd"] or 0
        if wells:
            for w in wells:
                totals.setdefault(self.directory.resolve_well(w), 0.0)
        summary = (f"{len(rows)} repair(s) started in {window.label}. Cost per well: "
                   + ", ".join(f"{k}: USD {v:,.0f}" for k, v in totals.items()) + f". Total USD {sum(totals.values()):,.0f}.")
        return ToolResult(summary=summary, rows=rows, citations=cite(*[r["maintenance_id"] for r in rows]),
                          card={"cost_by_well": dict(totals), "period": window.as_dict()},
                          visual={"kind": "table"},
                          rules_applied=["A repair belongs to a period by its start date"])

    def rca_due(self, area: str | None = None, equipment_kind: str | None = None, well_type: str | None = None,
                wells: list[str] | None = None) -> ToolResult:
        where, params = self._scope_filter(area, well_type, wells, alias="w")
        window_start = today() - timedelta(days=365)
        params.update(s=window_start.isoformat(), e=today().isoformat())
        extra = ""
        if equipment_kind:
            extra = " AND m.equipment_kind = :ek"
            params["ek"] = equipment_kind
        counts = self._rows(
            f"""SELECT m.asset_id, w.well_type, m.equipment_kind, COUNT(*) AS n,
                       {dialect.group_concat("m.maintenance_id")} AS ids
                  FROM maintenance m JOIN wells w ON w.asset_id = m.asset_id
                 WHERE m.maintenance_type = 'corrective' AND DATE(m.started_at) > :s AND DATE(m.started_at) <= :e
                       {where} {extra}
                 GROUP BY m.asset_id, w.well_type, m.equipment_kind ORDER BY m.asset_id""", **params)
        overrides = self._rows("SELECT * FROM rule_overrides WHERE rule = 'rca_min_corrective' AND status = 'active' "
                               "ORDER BY created_at")
        rows, citations, applied = [], [], set()
        for c in counts:
            rule = self._rule(c["well_type"])
            threshold, source = rule.get("rca_min_corrective", 3), rule.get("rca_rule_ref")
            for o in overrides:
                if (o["well_type"] == c["well_type"] or o["scope"] == "fleet") and \
                        (o["equipment_kind"] in (None, c["equipment_kind"])):
                    threshold = int(o["value"])
                    source = f"team rule {o['override_id']} by {self.directory.user_name(o['author_user_id'])}"
                    applied.add(f"{o['override_id']}: {o['statement']} ({self.directory.user_name(o['author_user_id'])})")
            if c["n"] >= threshold:
                rows.append({"well": c["asset_id"], "type": c["well_type"], "equipment": c["equipment_kind"],
                             "corrective_repairs_12m": c["n"], "threshold": threshold, "rule": source,
                             "repairs": c["ids"]})
                citations += c["ids"].split(", ")
        summary = (f"{len(rows)} well/equipment pair(s) need a root-cause analysis (corrective repairs in the 12 months "
                   f"to {today():%d %b %Y}): " + ", ".join(f"{r['well']} {r['equipment']} ({r['corrective_repairs_12m']}, "
                                                           f"threshold {r['threshold']} per {r['rule']})" for r in rows))
        return ToolResult(summary=summary, rows=rows, citations=cite(*citations, "OM-T200", "OM-T350"),
                          visual={"kind": "table"},
                          rules_applied=["T200 manual §10: RCA after 3+ corrective events on the same equipment kind "
                                         "in 12 months (T350 §6 refers to it)", *sorted(applied)])

    def inspection_detail(self, inspection_id: str | None = None, well: str | None = None,
                          on_date: str | None = None) -> ToolResult:
        if inspection_id is None:
            if not (well and on_date):
                raise ValueError("Give inspection_id, or well and on_date")
            asset_id = self.directory.resolve_well(well)
            found = self._one("SELECT inspection_id FROM inspections WHERE asset_id = :a AND inspection_date = :d",
                              a=asset_id, d=on_date)
            if found is None:
                raise ValueError(f"No inspection for {asset_id} on {on_date}")
            inspection_id = found["inspection_id"]
        f = self._one("SELECT * FROM inspection_facts WHERE inspection_id = :i", i=inspection_id)
        if f is None:
            raise ValueError(f"Unknown inspection {inspection_id}")
        notes = self._notes_about_inspection(inspection_id)
        corrections = self._correction_lines(inspection_id)
        repairs = self._rows("SELECT maintenance_id, issue, started_at, completed_at, report_doc_id, "
                             "report_containment_hours, report_spill_class FROM maintenance "
                             "WHERE triggered_by_inspection_id = :i", i=inspection_id)
        events = self._rows("SELECT * FROM regulatory_events WHERE related_inspection_id = :i ORDER BY date",
                            i=inspection_id)
        formal = [n for n in notes if n["is_formal_correction"]]
        opinions = [n for n in notes if not n["is_formal_correction"]]
        verdict = None
        if f["spill_detected"]:
            if not f["eff_spill"]:
                verdict = "The detection was formally corrected and does not count as a spill."
            elif opinions:
                verdict = ("People disagree in notes, but none is a formal correction"
                           + (" and a spill repair was done" if repairs else "") + ", so the detection stands.")
            else:
                verdict = "The detection stands."
        card = {
            "inspection_id": inspection_id, "well": f["asset_id"], "inspected_at": f["inspected_at"],
            "inspector": self.directory.user_name(f["inspector_user_id"]), "method": f["method"],
            "wellhead_temp_c": f["eff_wellhead_temp_c"], "air_temp_c": f["eff_ambient_temp_c"],
            "above_air_c": f["temp_delta_c"], "producing": bool(f["producing"]),
            "spill_detected_by_ai": bool(f["spill_detected"]), "spill_counts": bool(f["eff_spill"]),
            "ai_volume_bbl": f["spill_est_volume_bbl"], "effective_volume_bbl": f["eff_spill_volume_bbl"],
            "spill_area_pct": f["spill_area_pct"], "inspector_note": f["notes"],
            "corrections": corrections, "notes": notes, "formal_corrections": len(formal),
            "repairs": repairs, "regulatory_events": events, "verdict": verdict,
        }
        summary = (f"{inspection_id} ({f['asset_id']}, {_date(f['inspection_date']):%d %b %Y}, "
                   f"{card['inspector']}): wellhead {f['eff_wellhead_temp_c']} °C, air {f['eff_ambient_temp_c']} °C, "
                   f"{'producing' if f['producing'] else 'not producing'}.")
        if f["spill_detected"]:
            summary += (f" AI flagged a spill of {f['spill_est_volume_bbl']} bbl"
                        f" (inspector note: \"{f['notes']}\"). {verdict}")
        if opinions:
            summary += " Notes: " + "; ".join(f"{n['note_id']} {n['author']}: {n['text']}" for n in opinions)
        return ToolResult(summary=summary, card=card, visual={"kind": "status_card"},
                          citations=cite(inspection_id, *[n["note_id"] for n in notes],
                                         *[c["correction_id"] for c in corrections],
                                         *[r["maintenance_id"] for r in repairs], *[e["event_id"] for e in events]))

    def wells(self, area: str | None = None, well_type: str | None = None, wells: list[str] | None = None,
              status: str | None = None) -> ToolResult:
        where, params = self._scope_filter(area, well_type, wells)
        data = self._rows(
            f"""SELECT f.asset_id, f.well_type, f.area_name, f.lat, f.lon, f.inspection_id, f.inspection_date,
                       f.temp_delta_c, f.producing, w.recorded_status
                  FROM latest_inspection f JOIN wells w ON w.asset_id = f.asset_id WHERE 1=1 {where}
                 ORDER BY f.asset_id""", **params)
        rows = [{"well": r["asset_id"], "type": r["well_type"], "area": r["area_name"], "lat": r["lat"], "lon": r["lon"],
                 "producing_now": bool(r["producing"]), "above_air_c": r["temp_delta_c"],
                 "last_inspection": r["inspection_id"], "last_inspected": _date(r["inspection_date"]),
                 "recorded_status": r["recorded_status"]} for r in data]
        if status in ("producing", "not_producing"):
            rows = [r for r in rows if r["producing_now"] == (status == "producing")]
        summary = f"{len(rows)} well(s); {sum(r['producing_now'] for r in rows)} producing at the latest inspection."
        return ToolResult(summary=summary, rows=rows, visual={"kind": "map"},
                          citations=cite(*[r["last_inspection"] for r in rows[:50]]))
