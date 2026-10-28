"""Registry write audit is additive; version and audit rows are database-immutable."""
from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from app.persistence.base import Base


class PlatformStrategyAuditRow(Base):
    __tablename__ = "platform_strategy_audit"
    __table_args__ = (
        CheckConstraint(
            "action IN ('create_strategy','update_strategy','retire_strategy',"
            "'lock_strategy','publish_version','create_run')",
            name="ck_strategy_audit_action",
        ),
        CheckConstraint("length(reason) > 0", name="ck_strategy_audit_reason"),
        Index("ix_strategy_audit_resource", "resource_type", "resource_id", "created_at_ms"),
        Index("ix_strategy_audit_actor", "actor_id", "created_at_ms"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(24), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    before_sha256: Mapped[str | None] = mapped_column(String(64))
    after_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
