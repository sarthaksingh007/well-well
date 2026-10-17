from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import text

from wellwell.analytics.compliance import Compliance
from wellwell.analytics.documents import search_documents
from wellwell.analytics.equipment import installed_pumps
from wellwell.analytics.facts import Facts
from wellwell.analytics.result import ToolResult
from wellwell.config import get_settings
from wellwell.db.dialect import is_postgres, plain_row
from wellwell.db.session import get_read_only_engine
from wellwell.harness.sql_guard import ALLOWED_TABLES, guard_sql
from wellwell.memory.feedback import Feedback, PrivateMemory

PERIOD_HELP = ("period: all | this_year | last_year | YYYY | YYYY-MM | summer_YYYY | last_N_days | last_N_weeks | "
               "last_N_months | last_N_years; or start/end as YYYY-MM-DD")



def _schema_hint() -> str:
    from wellwell.db.base import Base

    tables = [t for name, t in sorted(Base.metadata.tables.items()) if name in ALLOWED_TABLES]
    skip = {"dataset", "source_file", "loaded_at"}
    return "; ".join(f"{t.name}({', '.join(c.name for c in t.columns if c.name not in skip)})" for t in tables)

@dataclass(frozen=True)
class ToolSpec:
    name: str
    purpose: str
    args: dict[str, str]
    example: dict
    writes: bool = False
    required: tuple[str, ...] = field(default_factory=tuple)


SPECS = [
    ToolSpec("well_status", "One well right now: producing or not (from the latest inspection and the manual rule), "
             "last inspection and inspector, recorded status and whether it is out of date, reasons, notes, "
             "corrections, user claims.", {"well": "WELL-0001"}, {"well": "WELL-0001"}, required=("well",)),
    ToolSpec("wells_not_producing", "Wells not producing at their latest inspection, with the reason for each "
             "(open repairs, notes) or 'no recorded reason'.",
             {"area": "area name (optional)", "well_type": "T200|T350 (optional)", "wells": "list (optional)"},
             {"area": "Northeast Block 8"}),
    ToolSpec("temperature_history", "Wellhead vs air temperature readings for one well, with producing flag per "
             "reading. Default the last 13 weekly readings.",
             {"well": "WELL-0001", "weeks": "int (optional)", "period": PERIOD_HELP},
             {"well": "WELL-0001", "weeks": 13}, required=("well",)),
    ToolSpec("spills", "Real spills (corrected detections removed) for a well or area in a period: date, volume, "
             "class, location, regulator report status. Also lists detections excluded by corrections.",
             {"well": "optional", "area": "optional", "period": PERIOD_HELP, "min_volume_bbl": "optional number",
              "reportable_only": "true to keep only spills needing a regulator report (>= 5 bbl)"},
             {"well": "WELL-0001", "period": "all"}),
    ToolSpec("downtime", "Downtime days per well (7 days per weekly inspection not producing) in a period, with "
             "the cause of each not-producing inspection. Use top_n for rankings.",
             {"wells": "list (optional)", "area": "optional", "well_type": "optional", "period": PERIOD_HELP,
              "top_n": "optional int"}, {"area": "Northeast Block 7", "period": "last_4_months", "top_n": 3}),
    ToolSpec("aggregate", "Counts or sums across one or two dimensions, for bar charts and heatmaps. metric: "
             "spill_count | spill_volume_bbl | downtime_days | repair_cost_usd | repair_count | "
             "corrective_repair_count | fines_usd. rows_by / columns_by: area | well | well_type | month | year | "
             "equipment_kind.",
             {"metric": "spill_count", "rows_by": "area", "columns_by": "month (optional)", "period": PERIOD_HELP,
              "area": "optional", "well_type": "optional"},
             {"metric": "spill_count", "rows_by": "area", "columns_by": "month", "period": "2026"}),
    ToolSpec("repairs", "Maintenance jobs that started in a period, with cost per well and totals.",
             {"wells": "list (optional)", "area": "optional", "period": PERIOD_HELP,
              "maintenance_type": "scheduled|corrective (optional)", "equipment_kind": "optional"},
             {"wells": ["WELL-0121"], "period": "this_year"}),
    ToolSpec("rca_due", "Wells needing a root-cause analysis: corrective repairs on the same equipment kind in the "
             "last 12 months at or above the threshold (manual §10, or a team rule if one was recorded).",
             {"area": "optional", "equipment_kind": "pump_jack|flowline|wellhead|rod_string|downhole_pump|separator",
              "well_type": "optional", "wells": "optional"}, {"area": "Northeast Block 7", "equipment_kind": "pump_jack"}),
    ToolSpec("compliance", "Rule breaches and open items vs the SOPs for a well or area: SR-01 report deadline, "
             "SR-02 containment (from repair reports), SR-03 clean-up verification, RC-04 notice responses, "
             "RC-02 flowline test, RC-01 inspection gaps, pending RCAs, unrecorded shut-ins.",
             {"well": "optional", "area": "optional", "period": PERIOD_HELP, "protocols": "optional list e.g. "
              "['SR-02']", "only_breaches": "optional bool"}, {"well": "WELL-0001", "period": "last_12_months"}),
    ToolSpec("inspection_detail", "One inspection: readings, AI spill estimate, corrections, notes that agree or "
             "disagree, repairs and regulator events it triggered, and whether a spill detection stands.",
             {"inspection_id": "INS-0101-0197", "well": "or well + on_date", "on_date": "YYYY-MM-DD"},
             {"well": "WELL-0101", "on_date": "2026-08-20"}),
    ToolSpec("wells", "List wells with location and producing status at the latest inspection (for maps).",
             {"area": "optional", "well_type": "optional", "status": "producing|not_producing (optional)"},
             {"area": "Northeast Block 8"}),
    ToolSpec("search_documents", "Search manuals, SOPs and repair reports for rules, deadlines and facts that "
             "only appear in text.", {"query": "text", "kind": "manual|sop|maintenance_report (optional)",
                                      "well": "optional"}, {"query": "spill containment limit", "kind": "sop"},
             required=("query",)),
    ToolSpec("query_sql", f"Read-only SQL ({'PostgreSQL' if is_postgres() else 'SQLite'}) for questions no other tool "
             "covers, including anything that lives in note or document text (use ILIKE on notes.text or "
             "documents.text). Columns: " + _schema_hint() + ". Views: inspection_facts (one row per inspection with "
             "corrections applied: inspection_id, asset_id, well_type, area_id, area_name, lat, lon, inspected_at, "
             "inspector_user_id, method, eff_wellhead_temp_c, eff_ambient_temp_c, temp_delta_c, producing_min_delta_c, "
             "producing 0/1, eff_spill, eff_spill_volume_bbl, has_correction, notes) and "
             "latest_inspection (same columns, latest per well). notes.note_type: operator_note (shift notes by "
             "operators), operator_feedback, correction, data_correction. documents.kind: manual, sop (SOPs, MOC memos, "
             "recall notices, regulator letters), maintenance_report (free text incl. 'Parts installed' with pump "
             "model/serial/lot, 'Follow-up', delays and waiting reasons).",
             {"sql": "SELECT ..."}, {"sql": "SELECT asset_id, created_at, text FROM notes WHERE note_type = "
                                            "'operator_note' AND text ILIKE '%gas%' ORDER BY created_at"},
             required=("sql",)),
    ToolSpec("installed_pumps", "The pump in each well today (from 'Parts installed' lines in repair reports: model, "
             "serial, lot, install job, install cost), checked against supplier recall notices, plus shared notes and "
             "claims users made about each well. Use for recalls and 'which pumps are still in the ground'.",
             {"well": "optional", "area": "optional", "recalled_only": "optional bool"}, {"recalled_only": True}),
    ToolSpec("remember_preference", "Save how THIS user wants answers shown, for this user only ('just for me: sort by "
             "area', 'costs in thousands'). Never use it for facts about wells.", {"preference": "text"},
             {"preference": "Sort well lists by area, then well ID; show costs in thousands of dollars."},
             required=("preference",)),
    ToolSpec("recall_my_conversations", "This user's own earlier conversations (never anyone else's). Use for "
             "'the wells we discussed yesterday'.", {"days_ago": "optional int"}, {"days_ago": 1}),
    ToolSpec("record_correction", "Save a user's correction to a raw reading or to the AI's analysis of one "
             "inspection. Keeps the original. field: wellhead_temp_c | ambient_temp_c | spill_detected | "
             "spill_est_volume_bbl. kind: raw_data | ai_analysis.",
             {"inspection_id": "or well + on_date", "well": "WELL-0164", "on_date": "2026-10-15",
              "field": "wellhead_temp_c", "corrected_value": "41.0", "reason": "why, in the user's words",
              "kind": "raw_data"},
             {"well": "WELL-0164", "on_date": "2026-10-15", "field": "wellhead_temp_c", "corrected_value": 41.0,
              "reason": "Handheld thermal gun was faulty; crew re-measured", "kind": "raw_data"},
             writes=True, required=("field", "corrected_value", "reason")),
    ToolSpec("record_note", "Save shared knowledge about a well that every user should see (planned shut-ins, "
             "workovers, context). Use scope well_type with well_type for notes about a whole type.",
             {"well": "WELL-0007", "text": "the user's statement", "valid_from": "YYYY-MM-DD", "valid_to": "optional",
              "scope": "well|well_type", "well_type": "optional"},
             {"well": "WELL-0007", "text": "Shut in from 17 Oct for a 10-day planned workover",
              "valid_from": "2026-10-17", "valid_to": "2026-10-27"}, writes=True, required=("text",)),
    ToolSpec("record_rule", "Save a team rule that replaces a manual threshold for one well type only. rule: "
             "rca_min_corrective | producing_min_delta_c.",
             {"rule": "rca_min_corrective", "value": 2, "well_type": "T350", "equipment_kind": "pump_jack",
              "statement": "the user's words"},
             {"rule": "rca_min_corrective", "value": 2, "well_type": "T350", "equipment_kind": "pump_jack",
              "statement": "RCA after 2 pump jack repairs for T350"}, writes=True,
             required=("rule", "value", "statement")),
    ToolSpec("record_claim", "Save and fact-check a user's statement about whether a well is running or stopped "
             "now. claimed_state: producing | shut_in | down. Compares with the latest inspection and with what "
             "others said.", {"well": "WELL-0175", "claimed_state": "shut_in", "since": "YYYY-MM-DD (optional)",
                              "statement": "the user's words"},
             {"well": "WELL-0175", "claimed_state": "producing", "statement": "WELL-0175 is running fine"},
             writes=True, required=("well", "claimed_state", "statement")),
]
SPEC_BY_NAME = {spec.name: spec for spec in SPECS}


class ToolBox:
    def __init__(self, session, user_id: str, conversation_id: int | None, search_mode: str = "semantic"):
        self.session = session
        self.facts = Facts(session)
        self.compliance = Compliance(self.facts)
        self.feedback = Feedback(self.facts, user_id)
        self.private = PrivateMemory(session, user_id, conversation_id)
        self._handlers: dict[str, Callable[..., ToolResult]] = {
            "well_status": self.facts.well_status,
            "wells_not_producing": self.facts.wells_not_producing,
            "temperature_history": self.facts.temperature_history,
            "spills": self.facts.spills,
            "downtime": self.facts.downtime,
            "aggregate": self.facts.aggregate,
            "repairs": self.facts.repairs,
            "rca_due": self.facts.rca_due,
            "compliance": self.compliance.check,
            "inspection_detail": self.facts.inspection_detail,
            "wells": self.facts.wells,
            "search_documents": lambda **a: search_documents(session, **{**a, "mode": search_mode}),
            "query_sql": self._query_sql,
            "recall_my_conversations": self.private.recall,
            "record_correction": self.feedback.record_correction,
            "record_note": self.feedback.record_shared_note,
            "record_rule": self.feedback.record_rule,
            "record_claim": self.feedback.record_claim,
            "installed_pumps": lambda **a: installed_pumps(session, **a),
            "remember_preference": self._remember_preference,
        }
        self.user_id = user_id

    def run(self, name: str, args: dict) -> ToolResult:
        if name not in self._handlers:
            raise ValueError(f"Unknown tool '{name}'. Available: {', '.join(self._handlers)}")
        spec = SPEC_BY_NAME[name]
        missing = [r for r in spec.required if args.get(r) in (None, "", [])]
        if missing:
            raise ValueError(f"{name} needs {', '.join(missing)}")
        clean = {k: v for k, v in args.items() if v not in (None, "") and not k.startswith("_")}
        for key in ("wells", "protocols"):
            if isinstance(clean.get(key), str):
                clean[key] = [part.strip() for part in clean[key].split(",") if part.strip()]
        if "period" in clean and clean.get("period") and str(clean["period"]).lower() in ("none", "null"):
            clean.pop("period")
        return self._handlers[name](**clean)

    def _remember_preference(self, preference: str) -> ToolResult:
        from wellwell.db.models import UserPreference

        self.session.add(UserPreference(user_id=self.user_id, text=preference))
        self.session.flush()
        return ToolResult(summary=f"Saved as a private preference for this user only: {preference}. Other users are "
                                  "not affected.", rows=[], visual={"kind": "none"})

    def _query_sql(self, sql: str) -> ToolResult:
        safe = guard_sql(sql, get_settings().sql_row_limit)
        with get_read_only_engine().connect() as connection:
            result = connection.execute(text(safe))
            rows = [plain_row(dict(r)) for r in result.mappings()]
        rows = [{k: v[:3000] + " …" if isinstance(v, str) and len(v) > 3000 else v for k, v in r.items()} for r in rows]
        citation_columns = ("inspection_id", "maintenance_id", "event_id", "note_id", "correction_id")
        citations = [str(r[c]) for r in rows for c in citation_columns if r.get(c)]
        return ToolResult(summary=f"{len(rows)} row(s) from: {safe}", rows=rows, citations=citations[:60],
                          visual={"kind": "table"})
