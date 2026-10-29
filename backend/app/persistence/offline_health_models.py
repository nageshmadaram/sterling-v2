"""Phase 10 append-only offline health evidence, never broker live telemetry."""
from sqlalchemy import BigInteger,Boolean,CheckConstraint,ForeignKey,String,Text,UniqueConstraint,Index
from sqlalchemy.orm import Mapped,mapped_column
from app.persistence.base import Base
class PlatformOfflineHealthEventRow(Base):
    __tablename__="platform_offline_health_events"
    __table_args__=(
        UniqueConstraint("strategy_id","sequence",name="uq_offline_health_sequence"),
        CheckConstraint("sequence>0",name="ck_offline_health_sequence"),
        CheckConstraint("length(previous_sha256)=64 AND length(content_sha256)=64",name="ck_offline_health_hash"),
        CheckConstraint("execution_authorized=false",name="ck_offline_health_no_authority"),
        Index("ix_offline_health_time","strategy_id","occurred_at_ms"),
    )
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    strategy_id:Mapped[str]=mapped_column(String(64),ForeignKey("platform_strategies.id",ondelete="RESTRICT"),nullable=False)
    sequence:Mapped[int]=mapped_column(BigInteger,nullable=False)
    actor_id:Mapped[str]=mapped_column(String(128),nullable=False)
    actor_role:Mapped[str]=mapped_column(String(24),nullable=False)
    report_json:Mapped[str]=mapped_column(Text,nullable=False)
    occurred_at_ms:Mapped[int]=mapped_column(BigInteger,nullable=False)
    previous_sha256:Mapped[str]=mapped_column(String(64),nullable=False)
    content_sha256:Mapped[str]=mapped_column(String(64),nullable=False)
    execution_authorized:Mapped[bool]=mapped_column(Boolean,nullable=False,server_default="false")
