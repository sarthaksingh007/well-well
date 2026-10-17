from enum import StrEnum

from sqlalchemy import JSON, Enum, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON}


def enum_column(enum_cls: type[StrEnum]) -> Enum:
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=True,
        length=40,
        values_callable=lambda members: [member.value for member in members],
        name=f"ck_{enum_cls.__name__.lower()}",
    )


class LoadedFromMixin:
    """Which dataset and file a row was loaded from, so every answer can point back to it."""

    dataset: Mapped[str] = mapped_column(String(40), default="sample")
    source_file: Mapped[str | None] = mapped_column(String(300))
