"""Additive SQLAlchemy strategy registry; isolated from legacy live SQLite.

No table here grants broker execution permission or changes execution state.
"""
from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base


class PlatformStrategyRow(Base):
    __tablename__ = "platform_strategies"
    __table_args__ = (
        UniqueConstraint("owner_id", "slug", name="uq_platform_strategy_owner_slug"),
        CheckConstraint("state IN ('draft','validated','armed','active','paused','failed','disabled','retired')", name="ck_platform_strategy_state"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(96), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


class PlatformStrategyVersionRow(Base):
    __tablename__ = "platform_strategy_versions"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version", name="uq_platform_version_strategy_version"),
        Index("ix_platform_version_strategy_created", "strategy_id", "created_at_ms"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(64), ForeignKey("platform_strategies.id", ondelete="RESTRICT"), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    parameters_json: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


class PlatformStrategyRunRow(Base):
    __tablename__ = "platform_strategy_runs"
    __table_args__ = (
        CheckConstraint("mode IN ('validation','simulation')", name="ck_platform_run_offline_only"),
        CheckConstraint("state IN ('idle','queued','active','paused','failed','stopped')", name="ck_platform_run_state"),
        Index("ix_platform_run_version_created", "version_id", "created_at_ms"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(64), ForeignKey("platform_strategy_versions.id", ondelete="RESTRICT"), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default="validation")
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="queued")
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_heartbeat_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
