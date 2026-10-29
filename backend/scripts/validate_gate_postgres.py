"""PostgreSQL 16 migration + append-only offline gate smoke, disposable CI DB ONLY."""
from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
import os
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.persistence.base import make_engine
from app.persistence.session import make_session_factory
from app.persistence import strategy_registry_service as registry
from app.persistence.execution_validation_service import OfflineExecutionGate
from app.domain.execution_validation import GateState, ApprovalMode

def main():
    url = os.environ.get("DATABASE_URL", "")
    if os.getenv("STERLING_GATE_DISPOSABLE_DB") != "yes":
        raise RuntimeError("explicit disposable DB flag required")
    if not url.startswith("postgresql+") or not url.split("?",1)[0].endswith("/sterling_gate_ci"):
        raise RuntimeError("refuse any database except sterling_gate_ci")
    backend = Path(__file__).resolve().parents[1]
    cfg=Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location",str(backend / "alembic"))
    command.upgrade(cfg, "head")
    engine=make_engine(url)
    original=registry._identity
    registry._identity=lambda _: SimpleNamespace(user_id="synthetic-admin",role="admin",view=None,owner_uid=None)
    try:
        gate=OfflineExecutionGate(make_session_factory(engine))
        gate.transition(access_token="synthetic",strategy_id=None,
                        new_state=GateState.VALIDATION_REQUIRED,
                        approval_mode=ApprovalMode.BLOCKED,reason="postgres validation")
        gate.transition(access_token="synthetic",strategy_id=None,
                        new_state=GateState.EMERGENCY_STOP,
                        approval_mode=ApprovalMode.BLOCKED,reason="synthetic global stop")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM platform_gate_events")).scalar_one()==2
        for sql in ("UPDATE platform_gate_events SET reason='tamper'",
                    "DELETE FROM platform_gate_events"):
            try:
                with engine.begin() as conn:
                    conn.execute(text(sql))
            except DBAPIError:
                pass
            else:
                raise AssertionError("append-only gate record mutable: "+sql)
        try:
            gate.transition(access_token="synthetic",strategy_id=None,
                            new_state=GateState.APPROVED,approval_mode=ApprovalMode.AUTO,
                            reason="cannot skip review",explicit_emergency_reset=True)
        except PermissionError:
            pass
        else:
            raise AssertionError("emergency stop incorrectly reversible")
        with engine.begin() as conn:
            conn.execute(text("""
            CREATE FUNCTION sterling_gate_ci_reject_insert() RETURNS TRIGGER AS $$
            BEGIN
                RAISE EXCEPTION 'synthetic gate audit outage';
            END;
            $$ LANGUAGE plpgsql
            """))
            conn.execute(text("""
            CREATE TRIGGER sterling_gate_ci_fail_insert BEFORE INSERT ON platform_gate_events
            FOR EACH ROW EXECUTE FUNCTION sterling_gate_ci_reject_insert()
            """))
        try:
            try:
                gate.transition(access_token="synthetic",strategy_id=None,
                                new_state=GateState.VALIDATION_REQUIRED,
                                approval_mode=ApprovalMode.BLOCKED,
                                reason="incident-reviewed: expected rollback",
                                explicit_emergency_reset=True)
            except DBAPIError:
                pass
            else:
                raise AssertionError("audit insert failure did not block transition")
            with engine.connect() as conn:
                assert conn.execute(text("SELECT count(*) FROM platform_gate_events")).scalar_one()==2
        finally:
            with engine.begin() as conn:
                conn.execute(text("DROP TRIGGER sterling_gate_ci_fail_insert ON platform_gate_events"))
                conn.execute(text("DROP FUNCTION sterling_gate_ci_reject_insert()"))
        print("PASS: PG migration, immutable events, incident reset veto, atomic audit rollback")
    finally:
        registry._identity=original
        engine.dispose()

if __name__=="__main__":
    main()
