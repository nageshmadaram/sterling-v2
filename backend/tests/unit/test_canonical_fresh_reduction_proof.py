"""Fail-closed tests for live broker inventory before canonical reductions.

No order submission or live network access: only the pure pre-send broker
position reader is invoked against synthetic payloads.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.execution_service import (
    CanonicalExecutionService, ExecutionRequest, ExposureEffect,
)


BASE = {"tradingsymbol": "SYNTHETIC26NOV25000CE", "exchange": "NFO", "product": "NRML"}


def broker(*rows, account_id="acct"):
    return SimpleNamespace(
        _is_paper=False, _account_id=account_id,
        get_positions_raw=AsyncMock(return_value={"net": list(rows)}),
    )


def request(*, side="SELL", quantity=10):
    return ExecutionRequest(
        uid="synthetic-user", account_id="acct", strategy_id="gamma-unwind",
        generation_id="gen-1", signal_id="synthetic-signal", exchange="NFO",
        symbol=BASE["tradingsymbol"], side=side, quantity=quantity,
        exposure_effect=ExposureEffect.REDUCE_EXPOSURE,
        payload={"product": "NRML"},
    )


@pytest.mark.parametrize("side,net", [("SELL", 10), ("BUY", -10)])
async def test_exact_signed_fresh_quantity_proves_only_closing_side(side, net):
    c = broker({**BASE, "quantity": net})
    allowed, reason = await CanonicalExecutionService().verify_broker_reduction(
        c, request(side=side),
    )
    assert allowed and "verified" in reason
    c.get_positions_raw.assert_awaited_once()


@pytest.mark.parametrize("rows,account_id", [
    ([{**BASE, "quantity": 10.9}], "acct"),  # int(10.9) incorrectly proves 10
    ([{**BASE, "quantity": True}], "acct"),  # bool is not evidence of 1 contract
    ([{**BASE, "quantity": 0, "net_quantity": 10}], "acct"),
    ([{**BASE, "quantity": 10, "net_quantity": 20}], "acct"),
    ([{**BASE, "quantity": 6}, {**BASE, "quantity": 6}], "acct"),
    ([{**BASE, "symbol": "UNRELATED", "quantity": 10}], "acct"),
    ([{**BASE, "quantity": 10}], "wrong-account"),
])
async def test_malformed_fresh_broker_book_never_proves_sell(rows, account_id):
    c = broker(*rows, account_id=account_id)
    allowed, reason = await CanonicalExecutionService().verify_broker_reduction(
        c, request(quantity=10),
    )
    assert allowed is False, (rows, account_id, reason)


async def test_other_symbol_bucket_is_not_counted_as_target_exposure():
    c = broker({**BASE, "quantity": 10},
               {"tradingsymbol": "OTHER", "exchange": "NFO", "product": "NRML", "quantity": 200})
    allowed, _ = await CanonicalExecutionService().verify_broker_reduction(c, request())
    assert allowed is True
