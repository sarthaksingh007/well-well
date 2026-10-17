from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from wellwell.db.base import Base, LoadedFromMixin, enum_column
from wellwell.domain.enums import InspectionMethod, MaintenanceType, NoteOrigin, NoteType, Scope


class Inspection(LoadedFromMixin, Base):
    __tablename__ = "inspections"
    __table_args__ = (Index("ix_inspections_asset_time", "asset_id", "inspected_at"),)

    inspection_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("wells.asset_id"))
    inspected_at: Mapped[datetime] = mapped_column(DateTime)
    inspection_date: Mapped[date] = mapped_column(Date, index=True)
    inspector_user_id: Mapped[str | None] = mapped_column(String(40))
    method: Mapped[InspectionMethod | None] = mapped_column(enum_column(InspectionMethod))
    ambient_temp_c: Mapped[float | None] = mapped_column(Float)
    wellhead_temp_c: Mapped[float | None] = mapped_column(Float)
    spill_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    spill_area_pct: Mapped[float | None] = mapped_column(Float)
    spill_est_volume_bbl: Mapped[float | None] = mapped_column(Float)
    evidence_images: Mapped[list | None]
    notes: Mapped[str | None] = mapped_column(Text)


class Maintenance(LoadedFromMixin, Base):
    __tablename__ = "maintenance"
    __table_args__ = (Index("ix_maintenance_asset_start", "asset_id", "started_at"),)

    maintenance_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("wells.asset_id"))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    maintenance_type: Mapped[MaintenanceType] = mapped_column(enum_column(MaintenanceType))
    equipment_kind: Mapped[str | None] = mapped_column(String(40))
    issue: Mapped[str | None] = mapped_column(Text)
    action_taken: Mapped[str | None] = mapped_column(Text)
    technician_user_id: Mapped[str | None] = mapped_column(String(40))
    contractor: Mapped[str | None] = mapped_column(String(200))
    downtime_hours: Mapped[float | None] = mapped_column(Float)
    cost_labor_usd: Mapped[float | None] = mapped_column(Float)
    cost_parts_usd: Mapped[float | None] = mapped_column(Float)
    cost_total_usd: Mapped[float | None] = mapped_column(Float)
    triggered_by_inspection_id: Mapped[str | None] = mapped_column(String(40), index=True)
    report_file: Mapped[str | None] = mapped_column(String(300))
    report_doc_id: Mapped[str | None] = mapped_column(String(80))
    report_containment_hours: Mapped[float | None] = mapped_column(Float)
    report_spill_class: Mapped[str | None] = mapped_column(String(40))
    report_written_on: Mapped[date | None] = mapped_column(Date)


class ReportComplianceItem(Base):
    """One row of the 'Compliance check' table at the end of a repair report."""

    __tablename__ = "report_compliance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    maintenance_id: Mapped[str] = mapped_column(String(40), index=True)
    asset_id: Mapped[str] = mapped_column(String(40), index=True)
    protocol_id: Mapped[str] = mapped_column(String(20))
    result: Mapped[str] = mapped_column(String(40))
    met: Mapped[bool | None] = mapped_column(Boolean)
    detail: Mapped[str | None] = mapped_column(Text)


class RegulatoryEvent(LoadedFromMixin, Base):
    __tablename__ = "regulatory_events"

    event_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("wells.asset_id"), index=True)
    date: Mapped[date] = mapped_column(Date)
    agency: Mapped[str | None] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(40))
    protocol_id: Mapped[str | None] = mapped_column(String(20))
    related_inspection_id: Mapped[str | None] = mapped_column(String(40), index=True)
    hours_to_report: Mapped[float | None] = mapped_column(Float)
    detail: Mapped[str | None] = mapped_column(Text)
    fine_usd: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str | None] = mapped_column(String(20))


class Note(LoadedFromMixin, Base):
    """Human context, from the dataset's context.jsonl and from what people tell the copilot."""

    __tablename__ = "notes"
    __table_args__ = (Index("ix_notes_scope", "scope", "asset_id", "well_type"),)

    note_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    origin: Mapped[NoteOrigin] = mapped_column(enum_column(NoteOrigin), default=NoteOrigin.DATASET)
    scope: Mapped[Scope] = mapped_column(enum_column(Scope), default=Scope.WELL)
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("wells.asset_id"))
    well_type: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    author_user_id: Mapped[str | None] = mapped_column(String(40))
    note_type: Mapped[NoteType] = mapped_column(enum_column(NoteType))
    inspection_id: Mapped[str | None] = mapped_column(String(40), index=True)
    text: Mapped[str] = mapped_column(Text)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
