"""Append-only offline schedule/backlog event records; NO order/execution authority."""
from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.persistence.base import Base


class PlatformOrchestrationEventRow(Base):
    __tablename__ = "platform_orchestration_events"
    __table_args__ = (
        UniqueConstraint("stream_id", "sequence", name="uq_orchestration_stream_sequence"),
        CheckConstraint("sequence > 0", name="ck_orchestration_positive_sequence"),
        CheckConstraint(
            "kind IN ('schedule_created','schedule_paused','schedule_cancelled',"
            "'queue_transition','run_transition','gate_binding','requeue_denied',"
            "'operator_alert')", name="ck_orchestration_event_kind",
        ),
        CheckConstraint("length(previous_sha256)=64 AND length(content_sha256)=64",
                        name="ck_orchestration_hash_lengths"),
        Index("ix_orchestration_run_time", "run_id", "occurred_at_ms"),
        Index("ix_orchestration_schedule_time", "schedule_id", "occurred_at_ms"),
        Index("ix_orchestration_item_time", "item_id", "occurred_at_ms"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    stream_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    strategy_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("platform_strategies.id", ondelete="RESTRICT"), nullable=False)
    version_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("platform_strategy_versions.id", ondelete="RESTRICT"), nullable=False)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("platform_strategy_runs.id", ondelete="RESTRICT"), nullable=False)
    schedule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    item_id: Mapped[str | None] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(24), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    previous_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at_ms: Mapped[int] = mapped_column(BigInteger(), nullable=False)
