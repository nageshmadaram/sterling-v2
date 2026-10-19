"""Hermetic EMA CNC GTT stop-mutation drill; never sends real Kite orders.

The exact FakeKiteGTTWire class below is a bounded in-memory order/GTT
simulator. It contains no HTTP client and cannot delegate to KiteClient.
run_fake_stop() refuses any other wire type, marks the durable command
OUTCOME_UNKNOWN before the first simulated mutation, and never retries a
failed or timed-out request. A mock broker ACK still needs independent
broker-fact/ledger verification via live_protection_outbox before protection
can be claimed. Do not import this as a production order adapter.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from . import live_protection_outbox as outbox
from .live_stop_draft import draft_stop_command


class FakeStopRejected(ValueError):
    """The caller is not using the exclusively offline fake broker wire."""


@dataclass(frozen=True)
class FakeStopResult:
    command_id: str
    status: str
    trigger_id: str = ""
    reason: str = ""
    real_broker_write: bool = False
    retry_authorized: bool = False


class FakeKiteGTTWire:
    """In-memory GTT book and fault-injection transport: no network access.

    'TIMEOUT_AFTER_APPLY' simulates lost response after a broker-side GTT
    mutation; 'MISSING_ACK_AFTER_APPLY' returns an invalid acknowledgement;
    'REJECT_BEFORE_APPLY' simulates a request rejected without a GTT mutation.
    Each scenario consumes exactly one attempted request.
    """

    def __init__(self, account_id: str, *, next_trigger_id: int = 901):
        if (not isinstance(account_id,str) or not account_id.strip()
                or not isinstance(next_trigger_id,int) or
                isinstance(next_trigger_id,bool) or next_trigger_id<=0):
            raise FakeStopRejected("EMA_FAKE_WIRE_IDENTITY_INVALID")
        self.account_id=account_id
        self.next_trigger_id=next_trigger_id
        self.attempts: list[tuple[str,int|None]] = []
        self._gtts: dict[int,dict[str,Any]]={}
        self._next_fault=""

    def fault_next(self, kind: str) -> None:
        if kind not in {"TIMEOUT_AFTER_APPLY",
                        "MISSING_ACK_AFTER_APPLY","REJECT_BEFORE_APPLY"}:
            raise FakeStopRejected("EMA_FAKE_WIRE_FAULT_UNSUPPORTED")
        self._next_fault=kind

    def _fault(self, response: dict) -> dict:
        fault=self._next_fault
        self._next_fault=""
        if fault=="TIMEOUT_AFTER_APPLY":
            raise TimeoutError("EMA_FAKE_GTT_RESPONSE_LOST_AFTER_APPLY")
        if fault=="MISSING_ACK_AFTER_APPLY":
            return {"status":"ok"}
        return response

    @staticmethod
    def _snapshot(trigger_id: int, request: dict) -> dict:
        return {
            "id":trigger_id,"status":"active",
            "condition":{
                "exchange":request["exchange"],
                "tradingsymbol":request["tradingsymbol"],
                "trigger_values":deepcopy(request["trigger_values"]),
            },
            "orders":deepcopy(request["orders"]),
        }

    async def place_gtt(self, **request) -> dict:
        self.attempts.append(("PLACE",None))
        await asyncio.sleep(0)
        if self._next_fault=="REJECT_BEFORE_APPLY":
            self._next_fault=""
            raise FakeStopRejected("EMA_FAKE_GTT_DEFINITIVE_REFUSAL")
        trigger_id=self.next_trigger_id
        self.next_trigger_id+=1
        self._gtts[trigger_id]=self._snapshot(trigger_id,request)
        return self._fault({"trigger_id":trigger_id})

    async def modify_gtt(self, trigger_id: int, **request) -> dict:
        self.attempts.append(("TIGHTEN",trigger_id))
        await asyncio.sleep(0)
        if self._next_fault=="REJECT_BEFORE_APPLY":
            self._next_fault=""
            raise FakeStopRejected("EMA_FAKE_GTT_DEFINITIVE_REFUSAL")
        if trigger_id not in self._gtts:
            raise FakeStopRejected("EMA_FAKE_GTT_ID_NOT_FOUND")
        self._gtts[trigger_id]=self._snapshot(trigger_id,request)
        return self._fault({"trigger_id":trigger_id})

    def seed(self, trigger_id: int, request: dict) -> None:
        """Create a pre-existing mock stop without simulating a new placement."""
        if type(trigger_id) is not int or trigger_id<=0:
            raise FakeStopRejected("EMA_FAKE_GTT_ID_INVALID")
        self._gtts[trigger_id]=self._snapshot(trigger_id,request)

    async def get_gtt(self, trigger_id: int) -> dict:
        return deepcopy(self._gtts[trigger_id])

    async def get_gtts(self) -> list[dict]:
        return [deepcopy(row) for row in self._gtts.values()]


async def run_fake_stop(command_id: str, *, wire: FakeKiteGTTWire,
                        reference_bid, tick_size) -> FakeStopResult:
    """Single-use stop write through the in-memory fake only.

    Build request first, then durably claim exactly one transmission before
    touching the fake wire. If the fake wire raises, the call is cancelled,
    or the ACK cannot be validated, the outbox remains OUTCOME_UNKNOWN.
    Real KiteClient (including monkeypatched instances) is never accepted.
    """
    if type(wire) is not FakeKiteGTTWire:
        raise FakeStopRejected("EMA_FAKE_WIRE_REQUIRED_NO_REAL_ORDER_PATH")
    cmd=outbox.get(command_id)
    if cmd.account_id!=wire.account_id:
        raise FakeStopRejected("EMA_FAKE_ACCOUNT_MISMATCH")
    draft=draft_stop_command(
        command_id,reference_bid=reference_bid,tick_size=tick_size,
    )
    # SQLite FULL-sync transaction guarantees a restart cannot resend this ID.
    outbox.claim_stop_transmission(command_id)
    try:
        if cmd.kind=="PLACE":
            reply=await wire.place_gtt(**draft.request)
        elif cmd.kind=="TIGHTEN":
            reply=await wire.modify_gtt(int(draft.prior_trigger_id),**draft.request)
        else:
            raise FakeStopRejected("EMA_FAKE_CHANGE_KIND_UNSUPPORTED")
        if not isinstance(reply,dict):
            raise FakeStopRejected("EMA_FAKE_BROKER_ACK_MALFORMED")
        trigger_id=reply.get("trigger_id")
        if type(trigger_id) not in (int,str):
            raise FakeStopRejected("EMA_FAKE_BROKER_ACK_MISSING")
        saved=outbox.record_unverified_broker_ack(command_id,str(trigger_id))
        return FakeStopResult(
            command_id=command_id,status=saved.status,
            trigger_id=saved.broker_trigger_id,
        )
    except Exception as exc:
        # Error classification cannot prove nothing was sent. Preserve
        # UNKNOWN, even for the fake's definitive refusal, until an explicit
        # independent reconciliation workflow decides what comes next.
        return FakeStopResult(
            command_id=command_id,status="OUTCOME_UNKNOWN",
            reason=type(exc).__name__,
        )
