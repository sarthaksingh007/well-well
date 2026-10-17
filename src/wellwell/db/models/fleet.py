from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from wellwell.db.base import Base, LoadedFromMixin, enum_column
from wellwell.domain.enums import RecordedStatus, Role


class Team(LoadedFromMixin, Base):
    __tablename__ = "teams"

    team_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    lead_user_id: Mapped[str | None] = mapped_column(String(40))


class User(LoadedFromMixin, Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[Role] = mapped_column(enum_column(Role))
    team_id: Mapped[str | None] = mapped_column(String(40))


class Area(LoadedFromMixin, Base):
    __tablename__ = "areas"

    area_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.team_id"))
    center_lat: Mapped[float | None] = mapped_column(Float)
    center_lon: Mapped[float | None] = mapped_column(Float)


class Well(LoadedFromMixin, Base):
    __tablename__ = "wells"

    asset_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    asset_category: Mapped[str] = mapped_column(String(40), default="oil_well")
    well_type: Mapped[str] = mapped_column(String(20), index=True)
    area_id: Mapped[str | None] = mapped_column(ForeignKey("areas.area_id"), index=True)
    team_id: Mapped[str | None] = mapped_column(String(40))
    in_charge_user_id: Mapped[str | None] = mapped_column(String(40))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    installed_on: Mapped[date | None] = mapped_column(Date)
    recorded_status: Mapped[RecordedStatus | None] = mapped_column(enum_column(RecordedStatus))
    last_inspection_id: Mapped[str | None] = mapped_column(String(40))
    last_inspected_at: Mapped[datetime | None] = mapped_column(DateTime)

    equipment: Mapped[list["Equipment"]] = relationship(back_populates="well")


class Equipment(LoadedFromMixin, Base):
    __tablename__ = "equipment"

    equipment_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("wells.asset_id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(100))

    well: Mapped[Well] = relationship(back_populates="equipment")


class WellTypeRule(Base):
    """Numbers pulled out of each well-type manual, with the section they came from."""

    __tablename__ = "well_type_rules"

    well_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    manual_doc_id: Mapped[str] = mapped_column(String(40))
    producing_min_delta_c: Mapped[float] = mapped_column(Float)
    producing_rule_ref: Mapped[str] = mapped_column(String(60))
    temp_alarm_max_c: Mapped[float | None] = mapped_column(Float)
    rca_min_corrective: Mapped[int] = mapped_column(Integer, default=3)
    rca_window_days: Mapped[int] = mapped_column(Integer, default=365)
    rca_rule_ref: Mapped[str] = mapped_column(String(60))
    notes: Mapped[str | None] = mapped_column(Text)
