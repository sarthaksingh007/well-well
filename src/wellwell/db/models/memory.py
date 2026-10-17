from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from wellwell.db.base import Base, enum_column
from wellwell.domain.enums import ClaimStatus, CorrectionKind, CorrectionStatus, DocKind, Scope


class Correction(Base):
    """A fix to a raw reading or to the AI's analysis. The original value is always kept."""

    __tablename__ = "corrections"
    __table_args__ = (Index("ix_corrections_target", "target_id", "field", "status"),)

    correction_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[CorrectionKind] = mapped_column(enum_column(CorrectionKind))
    scope: Mapped[Scope] = mapped_column(enum_column(Scope), default=Scope.WELL)
    asset_id: Mapped[str | None] = mapped_column(String(40), index=True)
    well_type: Mapped[str | None] = mapped_column(String(20))
    target_table: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str] = mapped_column(String(60))
    field: Mapped[str] = mapped_column(String(60))
    original_value: Mapped[str | None] = mapped_column(Text)
    corrected_value: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    author_user_id: Mapped[str] = mapped_column(String(40))
    source_note_id: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[CorrectionStatus] = mapped_column(enum_column(CorrectionStatus), default=CorrectionStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RuleOverride(Base):
    """A team rule that replaces a manual threshold for one well type (or the whole fleet)."""

    __tablename__ = "rule_overrides"

    override_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    scope: Mapped[Scope] = mapped_column(enum_column(Scope), default=Scope.WELL_TYPE)
    well_type: Mapped[str | None] = mapped_column(String(20))
    rule: Mapped[str] = mapped_column(String(60))
    equipment_kind: Mapped[str | None] = mapped_column(String(40))
    value: Mapped[float] = mapped_column()
    manual_value: Mapped[float | None] = mapped_column()
    statement: Mapped[str] = mapped_column(Text)
    author_user_id: Mapped[str] = mapped_column(String(40))
    status: Mapped[CorrectionStatus] = mapped_column(enum_column(CorrectionStatus), default=CorrectionStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Claim(Base):
    """Something a user asserted about a well that we could check against the data."""

    __tablename__ = "claims"

    claim_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(40), index=True)
    author_user_id: Mapped[str] = mapped_column(String(40))
    statement: Mapped[str] = mapped_column(Text)
    claimed_state: Mapped[str] = mapped_column(String(40))
    effective_from: Mapped[date | None] = mapped_column(Date)
    status: Mapped[ClaimStatus] = mapped_column(enum_column(ClaimStatus))
    evidence: Mapped[dict | None]
    note_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Document(Base):
    __tablename__ = "documents"

    doc_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    kind: Mapped[DocKind] = mapped_column(enum_column(DocKind))
    title: Mapped[str] = mapped_column(String(300))
    asset_id: Mapped[str | None] = mapped_column(String(40), index=True)
    maintenance_id: Mapped[str | None] = mapped_column(String(40))
    well_type: Mapped[str | None] = mapped_column(String(20))
    path: Mapped[str] = mapped_column(String(400))
    text: Mapped[str] = mapped_column(Text)
    dataset: Mapped[str] = mapped_column(String(40), default="sample")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(ForeignKey("documents.doc_id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int]
    section: Mapped[str | None] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)


class Conversation(Base):
    __tablename__ = "conversations"

    conversation_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str | None] = mapped_column(String(300))
    started_on: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Message(Base):
    __tablename__ = "messages"

    message_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
                                                 index=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    answer: Mapped[dict | None]
    asset_ids: Mapped[list | None]
    sent_on: Mapped[date] = mapped_column(Date)
    model_id: Mapped[str | None] = mapped_column(String(100))
    latency_ms: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ToolRun(Base):
    __tablename__ = "tool_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.message_id", ondelete="CASCADE"), index=True)
    step: Mapped[int]
    tool: Mapped[str] = mapped_column(String(60))
    args: Mapped[dict]
    result: Mapped[dict | None]
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AnswerFeedback(Base):
    """A user's verdict on one copilot answer: helpful or wrong, and what was wrong."""

    __tablename__ = "answer_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.message_id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    rating: Mapped[str] = mapped_column(String(10))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class LoadRun(Base):
    """One run of the dataset loader, updated as it goes so the UI can show progress."""

    __tablename__ = "load_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset: Mapped[str] = mapped_column(String(40))
    path: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="running")
    stage: Mapped[str | None] = mapped_column(String(120))
    wells_total: Mapped[int] = mapped_column(Integer, default=0)
    wells_done: Mapped[int] = mapped_column(Integer, default=0)
    counts: Mapped[dict | None]
    warnings: Mapped[list | None]
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class PlaybookPattern(Base):
    """A question pattern and the tool plan that answered it, with how well that went. The agent's learned memory."""

    __tablename__ = "playbook"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pattern_hash: Mapped[str] = mapped_column(String(64), index=True)
    pattern: Mapped[str] = mapped_column(Text)
    plan: Mapped[list]
    plan_signature: Mapped[str] = mapped_column(String(300))
    visual: Mapped[str | None] = mapped_column(String(20))
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    answers: Mapped[int] = mapped_column(Integer, default=1)
    thumbs_up: Mapped[int] = mapped_column(Integer, default=0)
    thumbs_down: Mapped[int] = mapped_column(Integer, default=0)
    eval_passes: Mapped[int] = mapped_column(Integer, default=0)
    last_message_ids: Mapped[list | None]
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UserPreference(Base):
    """How one user likes answers shown ("sort by area", "costs in $k"). Private to that user."""

    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
