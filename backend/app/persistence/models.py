"""
ORM models mirroring existing raw-sqlite tables (Phase 5a).

Table names + columns match app/services/db.py so a future dual-write (Phase 5b)
reads/writes the same shapes. Two representative tables are mirrored here; the
remaining tables follow the same pattern.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base


class PositionRow(Base):
    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    underlying: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    data: Mapped[str] = mapped_column(Text, nullable=False)
    entry_ts: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_ts: Mapped[int] = mapped_column(Integer, nullable=False)
    # Columns added via ALTER TABLE in db.py:
    greeks_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notional: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    slippage_bps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class EquitySnapshotRow(Base):
    __tablename__ = "equity_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_value: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    drawdown: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    circuit_breaker_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class MirroredRecord(Base):
    """Generic (store, key) → JSON mirror for the low-volume trading-state stores.

    A uniform parity/audit mirror so every store can dual-write with a one-line
    hook (see app/services/orm_mirror.py). First-class entities (positions,
    equity_snapshots) keep their own typed tables; any store here can be promoted
    to a typed model when it becomes authoritative.
    """
    __tablename__ = "mirrored_records"

    store: Mapped[str] = mapped_column(String, primary_key=True)
    key: Mapped[str] = mapped_column(String, primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    updated_ts: Mapped[int] = mapped_column(Integer, nullable=False)

# Stage-1 additive strategy registry; Alembic imports this metadata module.
from app.persistence.strategy_registry_models import (  # noqa: F401
    PlatformStrategyRow, PlatformStrategyVersionRow, PlatformStrategyRunRow,
)

from app.persistence.strategy_registry_audit import PlatformStrategyAuditRow  # noqa: F401


# Phase 4 independent, append-only offline authorization evidence.
from app.persistence.execution_validation_models import PlatformGateEventRow  # noqa: F401

# Phase 5: audit-only execution scheduler and backlog (append-only, offline).
from app.persistence.offline_scheduler_models import PlatformOrchestrationEventRow  # noqa: F401

# Phase 6 offline journal: shared Phase 5 SQL transaction, no broker authority.
from app.persistence.offline_decision_journal_models import PlatformDecisionJournalRow  # noqa: F401

# Phase 7 opt-in hypothetical evidence only, never a broker trade record.
from app.persistence.offline_performance_models import PlatformHypotheticalTradeRow  # noqa: F401

# Phase 9: immutable informational alerts, not trading authority.
from app.persistence.offline_alert_models import PlatformOfflineAlertEventRow  # noqa: F401

from app.persistence.offline_health_models import PlatformOfflineHealthEventRow  # noqa: F401
