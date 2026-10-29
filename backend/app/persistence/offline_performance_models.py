"""Immutable synthetic analytical trade evidence, never an executed trade."""
from __future__ import annotations
from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.persistence.base import Base


class PlatformHypotheticalTradeRow(Base):
    __tablename__ = "platform_hypothetical_trade_events"
    __table_args__ = (
        UniqueConstraint("stream_id", "sequence", name="uq_hyp_trade_sequence"),
        UniqueConstraint("entry_decision_id", "exit_decision_id", name="uq_hyp_trade_pair"),
        UniqueConstraint("exit_decision_id", name="uq_hyp_unique_exit_decision"),
        CheckConstraint("sequence > 0", name="ck_hyp_trade_sequence"),
        CheckConstraint("execution_authorized = false", name="ck_hyp_offline_only"),
        CheckConstraint("evidence_kind = 'offline_hypothetical'", name="ck_hyp_evidence_kind"),
        CheckConstraint("length(previous_sha256)=64 AND length(content_sha256)=64", name="ck_hyp_hashes"),
        Index("ix_hyp_trade_strategy_time", "strategy_id", "exit_at_ms"),
        Index("ix_hyp_trade_version_time", "version_id", "exit_at_ms"),
    )
    id: Mapped[str] = mapped_column(String(36),primary_key=True)
    stream_id: Mapped[str] = mapped_column(String(128),nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger,nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(64),ForeignKey("platform_strategies.id",ondelete="RESTRICT"),nullable=False)
    version_id: Mapped[str] = mapped_column(String(64),ForeignKey("platform_strategy_versions.id",ondelete="RESTRICT"),nullable=False)
    entry_run_id: Mapped[str] = mapped_column(String(64),ForeignKey("platform_strategy_runs.id",ondelete="RESTRICT"),nullable=False)
    exit_run_id: Mapped[str] = mapped_column(String(64),ForeignKey("platform_strategy_runs.id",ondelete="RESTRICT"),nullable=False)
    entry_decision_id: Mapped[str] = mapped_column(String(36),ForeignKey("platform_decision_journal.id",ondelete="RESTRICT"),nullable=False)
    exit_decision_id: Mapped[str] = mapped_column(String(36),ForeignKey("platform_decision_journal.id",ondelete="RESTRICT"),nullable=False)
    entry_at_ms: Mapped[int] = mapped_column(BigInteger,nullable=False)
    exit_at_ms: Mapped[int] = mapped_column(BigInteger,nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128),nullable=False)
    actor_role: Mapped[str] = mapped_column(String(24),nullable=False)
    reason: Mapped[str] = mapped_column(String(512),nullable=False)
    evidence_kind: Mapped[str] = mapped_column(String(32),nullable=False)
    execution_authorized: Mapped[bool] = mapped_column(Boolean,nullable=False,server_default="false")
    payload_json: Mapped[str] = mapped_column(Text,nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64),nullable=False)
    previous_sha256: Mapped[str] = mapped_column(String(64),nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64),nullable=False)
    occurred_at_ms: Mapped[int] = mapped_column(BigInteger,nullable=False)
