"""Immutable hypothetical performance evidence, bound to real Phase 6 offline approvals.

No broker fills are inferred from decision logs. Prices/costs are strictly
operator-supplied synthetic assumptions. This service is NOT exposed to HTTP.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.domain.offline_performance import HypotheticalFillPair, HypotheticalTrade, hypothetic_pnl
from app.persistence.offline_performance_models import PlatformHypotheticalTradeRow
from app.persistence.offline_decision_journal_service import read_for_operator
from app.persistence.offline_scheduler_service import _read as read_queue
from app.persistence.execution_validation_service import _lock
from app.persistence.session import session_scope
from app.persistence import strategy_registry_service as registry
from app.services.offline_performance_metrics import summarize, compare, threshold_alerts

ZERO = "0" * 64


def canonical(doc):
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(doc):
    return hashlib.sha256(canonical(doc).encode()).hexdigest()


def _read(db: Session, strategy_id: str):
    rows=list(db.scalars(select(PlatformHypotheticalTradeRow).where(
        PlatformHypotheticalTradeRow.stream_id=="strategy:"+strategy_id
    ).order_by(PlatformHypotheticalTradeRow.sequence)))
    prev=ZERO
    for idx,row in enumerate(rows,1):
        if row.sequence!=idx or row.previous_sha256!=prev or row.execution_authorized is not False:
            raise PermissionError("hypothetical analytics audit sequence or authority invalid")
        content={"id":row.id,"stream":row.stream_id,"sequence":row.sequence,
                 "strategy_id":row.strategy_id,"version_id":row.version_id,
                 "entry_run_id":row.entry_run_id,"exit_run_id":row.exit_run_id,
                 "entry_decision_id":row.entry_decision_id,
                 "exit_decision_id":row.exit_decision_id,
                 "entry_at_ms":row.entry_at_ms,"exit_at_ms":row.exit_at_ms,
                 "actor_id":row.actor_id,"actor_role":row.actor_role,
                 "reason":row.reason,"evidence_kind":row.evidence_kind,
                 "execution_authorized":row.execution_authorized,
                 "payload":json.loads(row.payload_json),
                 "source_sha256":row.source_sha256,"at_ms":row.occurred_at_ms,
                 "previous":prev}
        if digest(content)!=row.content_sha256:
            raise PermissionError("hypothetical analytics evidence tampered")
        prev=row.content_sha256
    return rows


def _completed_decision(data):
    return (data["decision_type"]=="run_transition"
            and data["run_state"]=="completed"
            and data["approval_status"]=="allow"
            and bool(data["risk_event_id"]) and bool(data["approval_event_id"])
            and data["data_quality"].get("status")=="good"
            and data["execution_authorized"] is False
            and data["item_id"] is not None)


def _queue_symbol(rows, item_id):
    for row in rows:
        if row.item_id==item_id and row.kind=="queue_transition":
            data=json.loads(row.payload_json)
            ident=data.get("identity") or {}
            if isinstance(ident,dict) and ident.get("qualified_symbol"):
                return ident["qualified_symbol"]
    raise PermissionError("no immutable queue symbol for analytical trade")


def _source_refs(db, token, strategy_id, fixture):
    journal=read_for_operator(db,access_token=token,strategy_id=strategy_id,limit=None)
    lookup={x["journal_event_id"]:x for x in journal}
    entry=lookup.get(fixture.entry_decision_id)
    exit=lookup.get(fixture.exit_decision_id)
    if not entry or not exit or not _completed_decision(entry) or not _completed_decision(exit):
        raise PermissionError("entry and exit require separate completed offline approvals")
    if (entry["strategy_id"]!=strategy_id or exit["strategy_id"]!=strategy_id
            or entry["version_id"]!=exit["version_id"]
            or entry["sequence"]>=exit["sequence"]
            or entry["item_id"]==exit["item_id"]
            or entry["risk_policy_sha256"]!=exit["risk_policy_sha256"]
            or entry["version_sha256"]!=exit["version_sha256"]):
        raise PermissionError("entry/exit lineage or decision sequence mismatch")
    queue=read_queue(db,strategy_id)
    symbol_entry=_queue_symbol(queue,entry["item_id"])
    symbol_exit=_queue_symbol(queue,exit["item_id"])
    if symbol_entry!=symbol_exit or ":" not in symbol_entry:
        raise PermissionError("entry/exit symbol mismatch")
    # Do not infer hypothetical entry/exit prices from journaled approvals.
    return entry,exit,symbol_entry


class OfflinePerformanceService:
    """No methods for broker placement; no public mutation API."""
    def __init__(self,session_factory:sessionmaker):
        self._sessions=session_factory

    def record_hypothetical(self,*,access_token:str,strategy_id:str,
                            fixture:HypotheticalFillPair) -> str:
        actor=registry._identity(access_token)
        with session_scope(self._sessions) as db:
            _lock(db)
            registry._strategy(db,actor,strategy_id)
            entry,exit,qualified=_source_refs(db,access_token,strategy_id,fixture)
            rows=_read(db,strategy_id)
            if any(r.exit_decision_id==fixture.exit_decision_id for r in rows):
                raise PermissionError("offline hypothetical exit already recorded")
            gross,net=hypothetic_pnl(fixture)
            exchange,symbol=qualified.split(":",1)
            trade_id=str(uuid4())
            trade=HypotheticalTrade(
                id=trade_id,strategy_id=strategy_id,
                version_id=entry["version_id"],
                entry_run_id=entry["run_id"],exit_run_id=exit["run_id"],
                signal_id=entry["signal_id"],symbol=symbol,exchange=exchange,
                entry_decision_id=entry["journal_event_id"],
                exit_decision_id=exit["journal_event_id"],
                entry_at_utc=fixture.entry_at_utc,exit_at_utc=fixture.exit_at_utc,
                entry_reason=fixture.entry_reason,exit_reason=fixture.exit_reason,
                side=fixture.side,units=fixture.units,entry_price=fixture.entry_price,
                exit_price=fixture.exit_price,fees_inr=fixture.fees_inr,
                slippage_inr=fixture.slippage_inr,gross_pnl_inr=gross,net_pnl_inr=net,
                market_regime=fixture.market_regime,
                approval_mode=entry["approval_mode"],
                risk_snapshot_ref=entry["risk_event_id"],
                risk_amount_inr=fixture.risk_amount_inr)
            source_hash=digest({
                "entry_audit":entry["audit_ref"],"exit_audit":exit["audit_ref"],
                "entry_lineage":entry["lineage_sha256"],
                "exit_lineage":exit["lineage_sha256"],
                "version_sha256":entry["version_sha256"],
                "risk_policy_sha256":entry["risk_policy_sha256"],
                "fixture":fixture.model_dump(mode="json"),
            })
            number=len(rows)+1
            previous=rows[-1].content_sha256 if rows else ZERO
            at=int(time.time()*1000)
            payload={"trade":trade.model_dump(mode="json"),
                     "fixture":fixture.model_dump(mode="json"),
                     "entry_audit_ref":entry["audit_ref"],
                     "exit_audit_ref":exit["audit_ref"],
                     "risk_policy_sha256":entry["risk_policy_sha256"],
                     "version_sha256":entry["version_sha256"],
                     "offline_quality":"synthetic_unattested",
                     "not_broker_fill":True}
            content={
                "id":trade_id,"stream":"strategy:"+strategy_id,"sequence":number,
                "strategy_id":strategy_id,"version_id":trade.version_id,
                "entry_run_id":trade.entry_run_id,"exit_run_id":trade.exit_run_id,
                "entry_decision_id":fixture.entry_decision_id,
                "exit_decision_id":fixture.exit_decision_id,
                "entry_at_ms":int(fixture.entry_at_utc.timestamp()*1000),
                "exit_at_ms":int(fixture.exit_at_utc.timestamp()*1000),
                "actor_id":actor.user_id,"actor_role":actor.role,
                "reason":fixture.operator_reason,"evidence_kind":"offline_hypothetical",
                "execution_authorized":False,"payload":payload,
                "source_sha256":source_hash,"at_ms":at,"previous":previous,
            }
            db.add(PlatformHypotheticalTradeRow(
                id=trade_id,stream_id=content["stream"],sequence=number,
                strategy_id=strategy_id,version_id=trade.version_id,
                entry_run_id=trade.entry_run_id,exit_run_id=trade.exit_run_id,
                entry_decision_id=fixture.entry_decision_id,
                exit_decision_id=fixture.exit_decision_id,
                entry_at_ms=content["entry_at_ms"],exit_at_ms=content["exit_at_ms"],
                actor_id=actor.user_id,actor_role=actor.role,
                reason=fixture.operator_reason,evidence_kind="offline_hypothetical",
                execution_authorized=False,payload_json=canonical(payload),
                source_sha256=source_hash,previous_sha256=previous,
                content_sha256=digest(content),occurred_at_ms=at,
            ))
            db.flush()
            return trade_id

    def trades(self,*,access_token:str,strategy_id:str,
               version_id:str|None=None,symbol:str|None=None,
               regime:str|None=None,approval_mode:str|None=None,
               date_from:str|None=None,date_to:str|None=None):
        actor=registry._identity(access_token)
        with session_scope(self._sessions) as db:
            registry._strategy(db,actor,strategy_id)
            rows=_read(db,strategy_id)
            all_journal=read_for_operator(db,access_token=access_token,
                                          strategy_id=strategy_id,limit=None)
            journal={x["journal_event_id"]:x for x in all_journal}
            result=[]
            for row in rows:
                payload=json.loads(row.payload_json)
                trade=HypotheticalTrade.model_validate(payload["trade"])
                fixture=HypotheticalFillPair.model_validate(payload["fixture"])
                entry,exit=journal.get(row.entry_decision_id),journal.get(row.exit_decision_id)
                if not entry or not exit or not _completed_decision(entry) or not _completed_decision(exit):
                    raise PermissionError("orphan synthetic trade / invalid source approvals")
                recomputed=hypothetic_pnl(fixture)
                source_sha=digest({
                    "entry_audit":entry["audit_ref"],"exit_audit":exit["audit_ref"],
                    "entry_lineage":entry["lineage_sha256"],
                    "exit_lineage":exit["lineage_sha256"],
                    "version_sha256":entry["version_sha256"],
                    "risk_policy_sha256":entry["risk_policy_sha256"],
                    "fixture":fixture.model_dump(mode="json"),
                })
                if source_sha!=row.source_sha256:
                    raise PermissionError("source evidence fingerprint changed")
                if (trade.gross_pnl_inr!=recomputed[0] or trade.net_pnl_inr!=recomputed[1]
                    or trade.execution_authorized is not False
                    or trade.id!=row.id or trade.version_id!=row.version_id
                    or trade.entry_decision_id!=row.entry_decision_id
                    or trade.exit_decision_id!=row.exit_decision_id
                    or trade.entry_run_id!=entry["run_id"] or trade.exit_run_id!=exit["run_id"]
                    or payload["entry_audit_ref"]!=entry["audit_ref"]
                    or payload["exit_audit_ref"]!=exit["audit_ref"]
                    or payload["version_sha256"]!=entry["version_sha256"]
                    or payload["risk_policy_sha256"]!=entry["risk_policy_sha256"]):
                    raise PermissionError("synthetic evidence P&L or lineage invalid")
                if (version_id and trade.version_id!=version_id
                    or symbol and f"{trade.exchange}:{trade.symbol}"!=symbol
                    or regime and trade.market_regime!=regime
                    or approval_mode and trade.approval_mode!=approval_mode
                    or date_from and trade.exit_at_utc.date().isoformat()<date_from
                    or date_to and trade.exit_at_utc.date().isoformat()>date_to):
                    continue
                result.append(trade)
            return tuple(sorted(result,key=lambda t:(t.exit_at_utc,t.id)))

    def report(self,*,access_token:str,strategy_id:str,
               starting_equity_inr:Decimal|None=None,rolling_window:int=5,**filters):
        trades=self.trades(access_token=access_token,strategy_id=strategy_id,**filters)
        report=summarize(trades,starting_equity_inr=starting_equity_inr,
                         rolling_window=rolling_window)
        report["strategy_id"]=strategy_id
        report["kpi_alerts"]=threshold_alerts(report)
        report["record_count"]=len(trades)
        return report

    def comparison(self,*,access_token:str,strategy_id:str,group_by:str,
                   starting_equity_inr:Decimal|None=None,rolling_window:int=5,**filters):
        trades=self.trades(access_token=access_token,strategy_id=strategy_id,**filters)
        return compare(trades,group_by=group_by,starting_equity_inr=starting_equity_inr,
                       rolling_window=rolling_window)
