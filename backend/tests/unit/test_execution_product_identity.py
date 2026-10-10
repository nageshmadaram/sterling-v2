"""Regression tests for broker product identity across canonical projection."""
from __future__ import annotations

import pytest

from app.services.kite_engine import positions
from app.services.kite_engine.execution_lifecycle import register_pending
from app.services.kite_engine.order_journal import Intent


def _intent(*, product: str | None) -> Intent:
    payload = {} if product is None else {"product": product}
    return Intent(
        intent_key="intent-product",
        uid="u-product",
        account_id="acct-product",
        strategy_id="test-strategy",
        generation_id="g1",
        signal_id="sig-product",
        exchange="NFO",
        symbol="NIFTY26SEP25000CE",
        side="BUY",
        quantity=75,
        tag="prod-ident",
        state="SUBMITTED",
        order_id="",
        payload=payload,
        error="",
        created_ms=1,
        updated_ms=1,
    )


@pytest.mark.parametrize(
    ("product", "expected"),
    [
        ("MIS", "MIS"),
        ("mis", "MIS"),
        ("NRML", "NRML"),
        (None, "NRML"),
    ],
)
def test_register_pending_preserves_canonical_product(monkeypatch, product, expected):
    captured: list[positions.OpenPosition] = []

    monkeypatch.setattr(positions, "get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        positions,
        "register",
        lambda position: captured.append(position) or position,
    )
    monkeypatch.setattr(positions, "persist_strict", lambda *_args, **_kwargs: None)

    projected = register_pending(_intent(product=product), "OID-PRODUCT")

    assert captured == [projected]
    assert projected.product == expected
    assert projected.account_id == "acct-product"
    assert projected.exchange == "NFO"
    assert projected.symbol == "NIFTY26SEP25000CE"
    assert projected.entry_requested_qty == 75
    assert projected.entry_pending is True
