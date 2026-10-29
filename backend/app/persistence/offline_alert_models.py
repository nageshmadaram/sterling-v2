"""Phase 9 offline append-only alert and notification attempt ledger. No broker authority."""
from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.persistence.base import Base

class PlatformOfflineAlertEventRow(Base):
    __tablename__ = "platform_offline_alert_events"
    __table_args__ = (
        UniqueConstraint("strategy_id","sequence",name="uq_offline_alert_sequence"),
        UniqueConstraint("alert_id","alert_sequence",name="uq_offline_alert_revision"),
        CheckConstraint("sequence > 0 AND alert_sequence > 0",name="ck_offline_alert_sequences"),
        CheckConstraint("execution_authorized = false",name="ck_offline_alert_no_execution"),
        CheckConstraint("length(previous_sha256)=64 AND length(content_sha256)=64",name="ck_offline_alert_hashes"),
        CheckConstraint("event_kind IN ('created','acknowledged','resolved','expired','delivery_pending','delivery_sent','delivery_failed','delivery_acknowledged','delivery_expired','escalated')",name="ck_offline_alert_kind"),
        Index("ix_offline_alert_owner_time","strategy_id","occurred_at_ms"),
        Index("ix_offline_alert_id","alert_id"),
    )
    id: Mapped[str] = mapped_column(String(36),primary_key=True)
    alert_id: Mapped[str] = mapped_column(String(64),nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(64),ForeignKey("platform_strategies.id",ondelete="RESTRICT"),nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger,nullable=False)
    alert_sequence: Mapped[int] = mapped_column(BigInteger,nullable=False)
    event_kind: Mapped[str] = mapped_column(String(32),nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128),nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32),nullable=False)
    reason: Mapped[str] = mapped_column(String(512),nullable=False)
    payload_json: Mapped[str] = mapped_column(Text,nullable=False)
    occurred_at_ms: Mapped[int] = mapped_column(BigInteger,nullable=False)
    previous_sha256: Mapped[str] = mapped_column(String(64),nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64),nullable=False)
    execution_authorized: Mapped[bool] = mapped_column(Boolean,nullable=False,server_default="false")
