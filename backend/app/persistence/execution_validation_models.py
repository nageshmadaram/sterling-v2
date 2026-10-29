"""Separate append-only, non-authoritative strategy gate event ledger."""
from __future__ import annotations
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.persistence.base import Base

class PlatformGateEventRow(Base):
    __tablename__ = "platform_gate_events"
    __table_args__ = (
        UniqueConstraint("stream_id", "sequence", name="uq_gate_stream_sequence"),
        CheckConstraint("sequence > 0", name="ck_gate_positive_sequence"),
        CheckConstraint("kind IN ('state_transition','operator_approval','signal_received','gate_evaluated','risk_decision','approval_decision')", name="ck_gate_event_kind"),
        CheckConstraint("length(previous_sha256) = 64 AND length(content_sha256) = 64", name="ck_gate_hash_lengths"),
        Index("ix_gate_strategy_time", "strategy_id", "occurred_at_ms"),
        Index("ix_gate_run_time", "run_id", "occurred_at_ms"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    stream_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    strategy_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("platform_strategies.id", ondelete="RESTRICT"))
    version_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("platform_strategy_versions.id", ondelete="RESTRICT"))
    run_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("platform_strategy_runs.id", ondelete="RESTRICT"))
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    previous_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
