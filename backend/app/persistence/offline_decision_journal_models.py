"""Phase 6 independent append-only journal for non-authoritative queue evidence."""
from __future__ import annotations
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.persistence.base import Base

class PlatformDecisionJournalRow(Base):
    __tablename__ = "platform_decision_journal"
    __table_args__ = (
        UniqueConstraint("stream_id","sequence",name="uq_decision_journal_sequence"),
        UniqueConstraint("orchestration_event_id",name="uq_decision_journal_orchestration_event"),
        CheckConstraint("sequence > 0",name="ck_decision_journal_positive_seq"),
        CheckConstraint("length(previous_sha256)=64 AND length(content_sha256)=64",name="ck_decision_journal_hash"),
        CheckConstraint("execution_authorized = false",name="ck_decision_journal_offline"),
        Index("ix_decision_journal_run", "run_id","occurred_at_ms"),
        Index("ix_decision_journal_item", "item_id","sequence"),
    )
    id: Mapped[str] = mapped_column(String(36),primary_key=True)
    stream_id: Mapped[str] = mapped_column(String(128),nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger,nullable=False)
    orchestration_event_id: Mapped[str] = mapped_column(
        String(36),ForeignKey("platform_orchestration_events.id",ondelete="RESTRICT"),nullable=False)
    strategy_id: Mapped[str] = mapped_column(
        String(64),ForeignKey("platform_strategies.id",ondelete="RESTRICT"),nullable=False)
    version_id: Mapped[str] = mapped_column(
        String(64),ForeignKey("platform_strategy_versions.id",ondelete="RESTRICT"),nullable=False)
    run_id: Mapped[str] = mapped_column(
        String(64),ForeignKey("platform_strategy_runs.id",ondelete="RESTRICT"),nullable=False)
    item_id: Mapped[str | None] = mapped_column(String(64))
    signal_id: Mapped[str] = mapped_column(String(128),nullable=False)
    decision_type: Mapped[str] = mapped_column(String(40),nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128),nullable=False)
    actor_role: Mapped[str] = mapped_column(String(24),nullable=False)
    reason: Mapped[str] = mapped_column(String(512),nullable=False)
    risk_state: Mapped[str] = mapped_column(String(64),nullable=False)
    approval_mode: Mapped[str] = mapped_column(String(32),nullable=False)
    approval_status: Mapped[str] = mapped_column(String(32),nullable=False)
    queue_state: Mapped[str] = mapped_column(String(32),nullable=False)
    run_state: Mapped[str] = mapped_column(String(32),nullable=False)
    data_quality_json: Mapped[str] = mapped_column(Text,nullable=False)
    risk_event_id: Mapped[str | None] = mapped_column(String(36))
    approval_event_id: Mapped[str | None] = mapped_column(String(36))
    risk_policy_sha256: Mapped[str] = mapped_column(String(64),nullable=False)
    version_sha256: Mapped[str] = mapped_column(String(64),nullable=False)
    lineage_sha256: Mapped[str] = mapped_column(String(64),nullable=False)
    payload_json: Mapped[str] = mapped_column(Text,nullable=False)
    execution_authorized: Mapped[bool] = mapped_column(nullable=False,default=False,server_default="false")
    previous_sha256: Mapped[str] = mapped_column(String(64),nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64),nullable=False)
    occurred_at_ms: Mapped[int] = mapped_column(BigInteger,nullable=False)
