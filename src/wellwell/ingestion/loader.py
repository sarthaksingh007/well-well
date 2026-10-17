import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from wellwell.db.models import (
    Area,
    Correction,
    Document,
    DocumentChunk,
    Equipment,
    Inspection,
    Maintenance,
    Note,
    RegulatoryEvent,
    ReportComplianceItem,
    Team,
    User,
    Well,
    WellTypeRule,
)
from wellwell.domain.enums import CorrectionKind, DocKind, NoteOrigin, NoteType, Scope
from wellwell.ingestion.documents import extract_manual_rules, extract_report_facts, split_sections, title_of
from wellwell.ingestion.normalize import parse_date, parse_datetime

log = structlog.get_logger()

_NOT_A_SPILL = re.compile(r"\bnot (?:a |any )?(?:crude )?(?:oil )?spill\b|\bnot crude\b|\bno crude\b", re.IGNORECASE)
_VOLUME = re.compile(r"(\d+(?:\.\d+)?)\s*bbl", re.IGNORECASE)


@dataclass
class LoadReport:
    dataset: str
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def add(self, table: str, count: int = 1) -> None:
        self.counts[table] = self.counts.get(table, 0) + count


def _jsonl(path: Path) -> Iterator[dict]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _upsert(session: Session, model, rows: list[dict]) -> None:
    """Insert or update on the dataset's own primary key, on Postgres or SQLite."""
    if not rows:
        return
    table = model.__table__
    build = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    keys = [column.name for column in table.primary_key.columns]
    for start in range(0, len(rows), 1000):
        statement = build(table).values(rows[start:start + 1000])
        changes = {name: statement.excluded[name] for name in rows[0] if name not in keys}
        session.execute(statement.on_conflict_do_update(index_elements=keys, set_=changes) if changes
                        else statement.on_conflict_do_nothing(index_elements=keys))


class DatasetLoader:
    """Loads a dataset folder (sample-data/ or the test release) into the database.

    Loading is an upsert keyed on the dataset's own IDs, so the test dataset can be loaded on top of
    the sample one and re-running a load changes nothing.
    """

    def __init__(self, session: Session, root: Path, dataset: str, progress=None):
        self.session = session
        self.root = root
        self.dataset = dataset
        self.report = LoadReport(dataset)
        self.progress = progress or (lambda **_: None)
        self._data_corrections: dict[str, dict] = {}

    def run(self) -> LoadReport:
        if not (self.root / "fleet").exists() or not (self.root / "assets").exists():
            raise FileNotFoundError(f"{self.root} doesn't look like a dataset folder (needs fleet/ and assets/)")
        well_dirs = [d for d in sorted((self.root / "assets").iterdir())
                     if d.is_dir() and (d / "metadata.json").exists()]
        self.progress(stage="Reading areas, teams and users", wells_total=len(well_dirs), wells_done=0)
        self._load_fleet()
        self.progress(stage="Reading manuals and procedures")
        self._load_manuals_and_sops()
        for index, well_dir in enumerate(well_dirs, start=1):
            self._load_well(well_dir)
            if index % 10 == 0 or index == len(well_dirs):
                self.progress(stage=f"Loading wells ({well_dir.name})", wells_done=index)
        self.progress(stage="Applying formal corrections")
        self._corrections_from_notes()
        self.session.flush()
        log.info("dataset.loaded", dataset=self.dataset, counts=self.report.counts)
        return self.report

    def _rel(self, path: Path) -> str:
        return str(path.relative_to(self.root))

    def _load_fleet(self) -> None:
        areas_file = self.root / "fleet" / "areas.json"
        users_file = self.root / "fleet" / "users.json"
        areas = json.loads(areas_file.read_text())
        _upsert(self.session, Team, [
            {"team_id": t["team_id"], "name": t["name"], "lead_user_id": t.get("lead_user_id"),
             "dataset": self.dataset, "source_file": self._rel(areas_file)}
            for t in areas.get("teams", [])
        ])
        _upsert(self.session, Area, [
            {"area_id": a["area_id"], "name": a["name"], "team_id": a.get("team_id"),
             "center_lat": (a.get("center") or {}).get("lat"), "center_lon": (a.get("center") or {}).get("lon"),
             "dataset": self.dataset, "source_file": self._rel(areas_file)}
            for a in areas.get("areas", [])
        ])
        users = json.loads(users_file.read_text()).get("users", [])
        _upsert(self.session, User, [
            {"user_id": u["user_id"], "name": u["name"], "role": u["role"], "team_id": u.get("team_id"),
             "dataset": self.dataset, "source_file": self._rel(users_file)}
            for u in users
        ])
        self.report.add("areas", len(areas.get("areas", [])))
        self.report.add("users", len(users))

    def _load_manuals_and_sops(self) -> None:
        fleet = self.root / "fleet"
        rules = {}
        for path in sorted((fleet / "well-types").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            well_type = path.stem
            doc_id = f"OM-{well_type}"
            self._store_document(doc_id, DocKind.MANUAL, title_of(text, well_type), path, text, well_type=well_type)
            rules[well_type] = extract_manual_rules(text, well_type)
        for path in sorted((fleet / "sops").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            doc_match = re.search(r"Document ID:\s*([A-Z0-9-]+)", text)
            doc_id = doc_match.group(1) if doc_match else path.stem
            self._store_document(doc_id, DocKind.SOP, title_of(text, path.stem), path, text)

        for well_type, rule in rules.items():
            inherited = rules.get(rule.inherits_rca_from) if rule.inherits_rca_from else None
            rca_count = rule.rca_min_corrective or (inherited.rca_min_corrective if inherited else None) or 3
            if rule.producing_min_delta_c is None:
                self.report.warnings.append(f"No producing threshold found in the {well_type} manual")
                continue
            _upsert(self.session, WellTypeRule, [{
                "well_type": well_type,
                "manual_doc_id": f"OM-{well_type}",
                "producing_min_delta_c": rule.producing_min_delta_c,
                "producing_rule_ref": rule.producing_rule_ref,
                "temp_alarm_max_c": rule.temp_alarm_max_c,
                "rca_min_corrective": rca_count,
                "rca_window_days": 365,
                "rca_rule_ref": rule.rca_rule_ref,
                "notes": None,
            }])
            self.report.add("well_type_rules")

    def _store_document(self, doc_id: str, kind: DocKind, title: str, path: Path, text: str, **extra) -> None:
        existing = self.session.get(Document, doc_id)
        if existing is not None:
            self.session.query(DocumentChunk).filter(DocumentChunk.doc_id == doc_id).delete()
            self.session.delete(existing)
            self.session.flush()
        self.session.add(Document(doc_id=doc_id, kind=kind, title=title, path=self._rel(path), text=text,
                                  dataset=self.dataset, **extra))
        for index, section in enumerate(split_sections(text)):
            self.session.add(DocumentChunk(doc_id=doc_id, chunk_index=index, section=section.heading,
                                           text=section.text))
        self.report.add("documents")

    def _load_well(self, well_dir: Path) -> None:
        meta_file = well_dir / "metadata.json"
        meta = json.loads(meta_file.read_text())
        asset_id = meta["asset_id"]
        last = meta.get("last_inspection") or {}
        _upsert(self.session, Well, [{
            "asset_id": asset_id,
            "name": meta.get("name", asset_id),
            "asset_category": meta.get("asset_category", "oil_well"),
            "well_type": meta["well_type"],
            "area_id": meta.get("area_id"),
            "team_id": meta.get("team_id"),
            "in_charge_user_id": meta.get("in_charge_user_id"),
            "lat": (meta.get("location") or {}).get("lat"),
            "lon": (meta.get("location") or {}).get("lon"),
            "installed_on": parse_date(meta.get("installed_on")),
            "recorded_status": meta.get("recorded_status"),
            "last_inspection_id": last.get("inspection_id"),
            "last_inspected_at": parse_datetime(last.get("inspected_at")),
            "dataset": self.dataset,
            "source_file": self._rel(meta_file),
        }])
        _upsert(self.session, Equipment, [
            {"equipment_id": e["equipment_id"], "asset_id": asset_id, "kind": e["kind"], "model": e.get("model"),
             "dataset": self.dataset, "source_file": self._rel(meta_file)}
            for e in meta.get("equipment", [])
        ])
        self.report.add("wells")

        inspections_file = well_dir / "inspections.jsonl"
        inspection_rows = []
        for row in _jsonl(inspections_file):
            inspected_at = parse_datetime(row["inspected_at"])
            inspection_rows.append({
                "inspection_id": row["inspection_id"], "asset_id": asset_id, "inspected_at": inspected_at,
                "inspection_date": inspected_at.date(), "inspector_user_id": row.get("inspector_user_id"),
                "method": row.get("method"), "ambient_temp_c": row.get("ambient_temp_c"),
                "wellhead_temp_c": row.get("wellhead_temp_c"), "spill_detected": bool(row.get("spill_detected")),
                "spill_area_pct": row.get("spill_area_pct"), "spill_est_volume_bbl": row.get("spill_est_volume_bbl"),
                "evidence_images": row.get("evidence_images"), "notes": row.get("notes") or None,
                "dataset": self.dataset, "source_file": self._rel(inspections_file),
            })
        _upsert(self.session, Inspection, inspection_rows)
        self.report.add("inspections", len(inspection_rows))

        self._load_maintenance(well_dir, asset_id, meta)

        events_file = well_dir / "regulatory-events.jsonl"
        event_rows = [{
            "event_id": row["event_id"], "asset_id": asset_id, "date": parse_date(row.get("date")),
            "agency": row.get("agency"), "event_type": row.get("event_type"), "protocol_id": row.get("protocol_id"),
            "related_inspection_id": row.get("related_inspection_id"), "hours_to_report": row.get("hours_to_report"),
            "detail": row.get("detail"), "fine_usd": row.get("fine_usd"), "status": row.get("status"),
            "dataset": self.dataset, "source_file": self._rel(events_file),
        } for row in _jsonl(events_file)]
        _upsert(self.session, RegulatoryEvent, event_rows)
        self.report.add("regulatory_events", len(event_rows))

        notes_file = well_dir / "context.jsonl"
        note_rows = [{
            "note_id": row["note_id"], "origin": NoteOrigin.DATASET.value, "scope": Scope.WELL.value,
            "asset_id": asset_id, "well_type": meta["well_type"], "created_at": parse_datetime(row["created_at"]),
            "author_user_id": row.get("author_user_id"), "note_type": row.get("note_type", NoteType.OPERATOR_NOTE),
            "inspection_id": row.get("inspection_id"), "text": row.get("text", ""),
            "valid_from": parse_date(row.get("valid_from")), "valid_to": parse_date(row.get("valid_to")),
            "dataset": self.dataset, "source_file": self._rel(notes_file),
        } for row in _jsonl(notes_file)]
        for row in _jsonl(notes_file):
            if row.get("note_type") == NoteType.DATA_CORRECTION and row.get("field"):
                self._data_corrections[row["note_id"]] = row
        _upsert(self.session, Note, note_rows)
        self.report.add("notes", len(note_rows))

    def _load_maintenance(self, well_dir: Path, asset_id: str, meta: dict) -> None:
        maintenance_file = well_dir / "maintenance.jsonl"
        rows = []
        compliance_items: list[ReportComplianceItem] = []
        for row in _jsonl(maintenance_file):
            cost = row.get("cost_usd") or {}
            record = {
                "maintenance_id": row["maintenance_id"], "asset_id": asset_id,
                "started_at": parse_datetime(row.get("started_at")),
                "completed_at": parse_datetime(row.get("completed_at")),
                "maintenance_type": row.get("maintenance_type"), "equipment_kind": row.get("equipment_kind"),
                "issue": row.get("issue"), "action_taken": row.get("action_taken"),
                "technician_user_id": row.get("technician_user_id"), "contractor": row.get("contractor"),
                "downtime_hours": row.get("downtime_hours"), "cost_labor_usd": cost.get("labor"),
                "cost_parts_usd": cost.get("parts"), "cost_total_usd": cost.get("total"),
                "triggered_by_inspection_id": row.get("triggered_by_inspection_id"),
                "report_file": row.get("report_file"), "report_doc_id": None, "report_containment_hours": None,
                "report_spill_class": None, "report_written_on": None,
                "dataset": self.dataset, "source_file": self._rel(maintenance_file),
            }
            report_path = well_dir / row["report_file"] if row.get("report_file") else None
            if report_path and report_path.exists():
                text = report_path.read_text(encoding="utf-8")
                facts = extract_report_facts(text)
                doc_id = f"RPT-{row['maintenance_id']}"
                self._store_document(doc_id, DocKind.MAINTENANCE_REPORT, title_of(text, doc_id), report_path, text,
                                     asset_id=asset_id, maintenance_id=row["maintenance_id"],
                                     well_type=meta["well_type"])
                record.update(report_doc_id=doc_id, report_containment_hours=facts.containment_hours,
                              report_spill_class=facts.spill_class, report_written_on=facts.written_on)
                self.session.query(ReportComplianceItem).filter(
                    ReportComplianceItem.maintenance_id == row["maintenance_id"]).delete()
                for protocol, result, met, detail in facts.compliance:
                    compliance_items.append(ReportComplianceItem(
                        maintenance_id=row["maintenance_id"], asset_id=asset_id, protocol_id=protocol,
                        result=result, met=met, detail=detail))
            elif row.get("report_file"):
                self.report.warnings.append(f"{row['maintenance_id']}: report file {row['report_file']} not found")
            rows.append(record)
        _upsert(self.session, Maintenance, rows)
        self.session.add_all(compliance_items)
        self.report.add("maintenance", len(rows))

    def _corrections_from_notes(self) -> None:
        """Formal correction notes override the detection on the inspection they name."""
        notes = self.session.scalars(
            select(Note).where(Note.note_type == NoteType.CORRECTION, Note.origin == NoteOrigin.DATASET,
                               Note.inspection_id.is_not(None), Note.dataset == self.dataset)
        )
        for row in self._data_corrections.values():
            self.session.merge(Correction(
                correction_id=f"COR-{row['note_id']}", kind=CorrectionKind.RAW_DATA, scope=Scope.WELL,
                asset_id=row["asset_id"], target_table="inspections", target_id=row["inspection_id"], field=row["field"],
                original_value=str(row.get("original_value")), corrected_value=str(row["corrected_value"]),
                reason=row.get("text"), author_user_id=row.get("author_user_id") or "unknown",
                source_note_id=row["note_id"], created_at=parse_datetime(row["created_at"]),
            ))
            self.report.add("corrections")
        for note in notes:
            inspection = self.session.get(Inspection, note.inspection_id)
            if inspection is None:
                self.report.warnings.append(f"{note.note_id} corrects unknown inspection {note.inspection_id}")
                continue
            if _NOT_A_SPILL.search(note.text):
                field, original, corrected = "spill_detected", str(inspection.spill_detected).lower(), "false"
            elif (volume := _VOLUME.search(note.text)) and inspection.spill_detected:
                field, original, corrected = (
                    "spill_est_volume_bbl", str(inspection.spill_est_volume_bbl), volume.group(1))
            else:
                self.report.warnings.append(f"{note.note_id}: correction text not understood, kept as a note")
                continue
            self.session.merge(Correction(
                correction_id=f"COR-{note.note_id}", kind=CorrectionKind.AI_ANALYSIS, scope=Scope.WELL,
                asset_id=note.asset_id, target_table="inspections", target_id=note.inspection_id, field=field,
                original_value=original, corrected_value=corrected, reason=note.text,
                author_user_id=note.author_user_id or "unknown", source_note_id=note.note_id,
                created_at=note.created_at,
            ))
            self.report.add("corrections")
