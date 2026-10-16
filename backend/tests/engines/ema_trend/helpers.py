"""Synthetic completed data and independently controlled decision features."""
from datetime import datetime, timezone
from decimal import Decimal

from app.engines.ema_trend.config import EmaTrendConfig
from app.engines.ema_trend.contracts import Bar, Campaign, Feature, Lot, Pivot

BASE_MS = int(datetime(2026, 9, 30, 4, 30, tzinfo=timezone.utc).timestamp() * 1000)


def bar(index, close=100.0, *, open_=None, high=None, low=None, minutes=1,
        available_delay=0):
    open_ = close if open_ is None else open_
    return Bar(
        open_ms=BASE_MS + index * minutes * 60_000,
        close_ms=BASE_MS + (index + 1) * minutes * 60_000,
        available_ms=BASE_MS + (index + 1) * minutes * 60_000 + available_delay,
        open=open_, close=close,
        high=max(open_, close) + 1 if high is None else high,
        low=min(open_, close) - 1 if low is None else low,
        volume=100_000,
    )


def feature(index, close=110.0, *, open_=None, ema10=110.0, ema21=105.0,
            pivots=None, **kwargs):
    return Feature(
        index=index, bar=daily_bar(index, close, open_=open_), ema10=ema10,
        ema21=ema21, dema50=100.0, atr10=5.0, st_line=95.0,
        st_direction="UP", context_ema10=110.0, context_ema21=105.0,
        context_st="UP", context_count=100, ready=True,
        pivots=pivots or [], **kwargs,
    )


def daily_bar(index, close=100.0, **kwargs):
    from app.core.session_calendar import next_trading_session
    day = datetime(2026, 9, 30, tzinfo=timezone.utc).date()
    for _ in range(index):
        day = next_trading_session(day, "NSE")
    start = int(datetime(day.year, day.month, day.day, 4, 30, tzinfo=timezone.utc).timestamp()*1000)
    return bar(index, close, **kwargs).model_copy(update={
        "open_ms": start, "close_ms": start+60_000, "available_ms": start+60_000,
    })


def pivot(side, price, occurred_index, confirmed_index=None):
    confirmed_index = occurred_index + 2 if confirmed_index is None else confirmed_index
    return Pivot(
        side=side, price=price, occurred_index=occurred_index,
        confirmed_index=confirmed_index,
        occurred_ms=daily_bar(occurred_index).close_ms,
        confirmed_ms=daily_bar(confirmed_index).close_ms,
    )


def config(**kwargs):
    values = dict(allocation_inr=Decimal("2000000"), fee_bps=0,
                  slippage_bps=0, gap_reserve_atr=0)
    values.update(kwargs)
    return EmaTrendConfig(**values)


def campaign(qty=100, *, price="100", stop="95", budget="5000", **kwargs):
    cfg = config()
    return Campaign(
        id="campaign-test", uid="unit-user", account_id="paper-unit",
        symbol="NSE:TEST", mode="swing", product="CNC",
        config_hash=cfg.config_hash, config_revision=1,
        config_snapshot=cfg.model_dump(mode="json"), budget=Decimal(budget),
        initial_stop=Decimal(stop), stop=Decimal(stop), entry_index=0,
        lots=[Lot(fill_id="initial", qty=qty, price=Decimal(price))],
        **kwargs,
    )
