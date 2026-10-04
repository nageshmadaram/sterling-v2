"""Historical Gamma-Unwind day reconstruction.

Live E1 is tick-driven and cannot be reproduced exactly after the fact because
Kite has no historical tick endpoint. This adapter deliberately keeps the
strategy implementation unchanged and replays the SAME ContractEngine using the
finest broker history available:

* daily bars strictly before the selected day -> zones;
* historical 5-minute bars -> scheduled watchlists and E2 closes;
* historical 1-minute option bars -> read-only E1 proxy from minute HIGH plus minute-end volume;
* the same four-gate tiering inside ContractEngine.breakout().

A reconstructed row is always labelled historical_replay / 1minute_ohlcv_proxy.
The minute high proves that a breakout tick existed somewhere inside the minute,
while minute-end volume only proves the volume threshold was satisfied by the end
of that minute; intraminute ordering is unknowable. Historical rows are therefore
read-only and are never execution evidence.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.engines.gamma_move import (
    Candle,
    ContractEngine,
    GammaMoveConfig,
    GammaSignal,
    InstrumentRef,
    WatchContract,
    compute_zones,
    daily_atr_pct,
    lists_for,
    pick_levels,
    select_strikes,
    trading_days_between,
)
from app.engines.gamma_move.selection import (
    default_tick,
    in_band,
    is_trading_day,
)
from app.services.gamma_move import nfo_dump, stock_underlyings
from app.services.gamma_move_scanner import Pacer, _historical, _to_candles

IST = timezone(timedelta(hours=5, minutes=30))
_HIST_RATE = 2.6
_SESSION_OPEN = "09:15"
_SESSION_END = "15:30"
_MAX_CURRENT_SERIES_DTE = 35


@dataclass
class UnderlyingReplay:
    name: str
    token: int
    daily: list[Candle]
    five: list[Candle]
    zones: Any
    atr_pct: float | None


def _clock_ms(day: date, hhmm: str) -> int:
    hh, mm = (int(v) for v in hhmm.split(":"))
    return int(datetime(
        day.year, day.month, day.day, hh, mm, tzinfo=IST
    ).timestamp() * 1000)


def _bar_close_ms(bar: Candle, minutes: int) -> int:
    return int(bar.ts_ms + minutes * 60_000)


def _last_closed(bars: list[Candle], at_ms: int, minutes: int) -> Candle | None:
    rows = [b for b in bars if _bar_close_ms(b, minutes) <= at_ms]
    return rows[-1] if rows else None


def _day_rows(bars: list[Candle], day: date) -> list[Candle]:
    return [
        b for b in bars
        if datetime.fromtimestamp(b.ts_ms / 1000, IST).date() == day
    ]


def _normalize_master_row(row: dict) -> dict:
    item = dict(row)
    expiry = item.get("expiry")
    if expiry is not None and not isinstance(expiry, str):
        try:
            item["expiry"] = expiry.isoformat()
        except Exception:
            item["expiry"] = str(expiry)
    return item


async def _instrument_master_for_day(
    uid: str,
    client,
    day: date,
) -> tuple[list[dict], list[dict], list[str]]:
    """Date-effective NFO/NSE rows when SterlingLake retained that day's master.

    Kite's live master contains only currently listed derivatives. A dated
    SterlingLake snapshot preserves the exact names/tokens/expiries selected on
    the replay date. It still cannot make Kite serve expired option candles, so
    callers keep explicit coverage notes for those unavailable histories.
    """
    notes: list[str] = []
    try:
        from kitelake.instruments import load_instrument_master
        table = load_instrument_master(on=day)
        rows = [_normalize_master_row(row) for row in table.to_pylist()]
        nfo = [
            row for row in rows
            if str(row.get("exchange") or "").upper() == "NFO"
        ]
        nse = [
            row for row in rows
            if str(row.get("exchange") or "").upper() == "NSE"
        ]
        if nfo and nse:
            notes.append(
                f"using dated SterlingLake instrument master for {day.isoformat()}"
            )
            return nfo, nse, notes
    except Exception:
        pass

    from app.services.gamma_move import nfo_dump
    nfo = await nfo_dump(uid)
    nse = await client.search_instruments("", "NSE", limit=1_000_000)
    notes.append(
        "dated instrument master unavailable; replay uses the current live "
        "master, so expired option series cannot be reconstructed"
    )
    return list(nfo or []), list(nse or []), notes


async def _underlyings(uid: str, cfg: GammaMoveConfig, client, day: date):
    """Date-effective cash context using only evidence available by that day."""
    dump, eq, master_notes = await _instrument_master_for_day(
        uid, client, day
    )
    names = stock_underlyings(dump, cfg)
    tokens = {
        str(r.get("tradingsymbol") or "").upper(): int(r.get("instrument_token") or 0)
        for r in eq
        if str(r.get("segment") or "").upper() == "NSE"
        or str(r.get("exchange") or "").upper() == "NSE"
    }
    pacer = Pacer(_HIST_RATE)
    out: dict[str, UnderlyingReplay] = {}
    errors: list[str] = []
    daily_from = (day - timedelta(days=max(800, cfg.zone_daily_bars * 2))).isoformat()
    intraday_from = f"{day.isoformat()} 09:15:00"
    intraday_to = f"{day.isoformat()} 15:30:00"

    for name in names:
        token = int(tokens.get(name) or 0)
        if not token:
            continue
        try:
            raw_daily = await _historical(
                client, pacer, token, "day", daily_from, day.isoformat(), False
            )
            daily = [
                b for b in _to_candles(raw_daily)
                if datetime.fromtimestamp(b.ts_ms / 1000, IST).date() < day
            ][-cfg.zone_daily_bars:]
            if len(daily) < 2 * cfg.zone_pivot_len + cfg.zone_atr_len:
                continue
            raw_five = await _historical(
                client, pacer, token, "5minute",
                intraday_from, intraday_to, False
            )
            five = _day_rows(_to_candles(raw_five), day)
            if not five:
                continue
            out[name] = UnderlyingReplay(
                name=name,
                token=token,
                daily=daily,
                five=five,
                zones=compute_zones(daily, cfg),
                atr_pct=daily_atr_pct(daily),
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
    return dump, out, errors, master_notes


def _structural_candidates(rows: list[dict], side: str, spot: float,
                           edge: float, cfg: GammaMoveConfig) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        if str(row.get("instrument_type") or "").upper() != side:
            continue
        try:
            strike = float(row.get("strike") or 0.0)
        except (TypeError, ValueError):
            continue
        if strike <= 0:
            continue
        if side == "PE" and not strike < spot:
            continue
        if side == "CE" and not strike > spot:
            continue
        if not in_band(strike, edge, cfg.strike_band_pct):
            continue
        out.append(row)
    return out


async def _historical_contract_quote(client, pacer: Pacer, row: dict,
                                     day: date, at_ms: int) -> dict | None:
    token = int(row.get("instrument_token") or 0)
    if not token:
        return None
    frm = f"{day.isoformat()} 09:15:00"
    to = datetime.fromtimestamp(at_ms / 1000, IST).strftime("%Y-%m-%d %H:%M:%S")
    try:
        raw = await _historical(client, pacer, token, "5minute", frm, to, True)
    except Exception:
        return None
    bars = _day_rows(_to_candles(raw, oi=True), day)
    last = _last_closed(bars, at_ms, 5)
    if last is None or last.oi <= 0 or last.close <= 0:
        return None
    item = dict(row)
    item.update({
        "option_type": str(row.get("instrument_type") or "").upper(),
        "oi": last.oi,
        "last_price": last.close,
        "ltp": last.close,
        "previous_close": None,
        "previous_volume": None,
    })
    return item


async def _watches_at(uid: str, cfg: GammaMoveConfig, client, day: date,
                      dump: list[dict], contexts: dict[str, UnderlyingReplay],
                      build_ms: int):
    """Rebuild the reference client watchlists at one scheduled build time."""
    by_name: dict[str, list[dict]] = {}
    for row in dump:
        if str(row.get("segment") or "") == "NFO-OPT":
            by_name.setdefault(str(row.get("name") or "").upper(), []).append(row)

    watches: list[WatchContract] = []
    coverage: list[str] = []
    pacer = Pacer(_HIST_RATE)

    for name, ctx in contexts.items():
        spot_bar = _last_closed(ctx.five, build_ms, 5)
        if spot_bar is None or spot_bar.close <= 0:
            continue
        spot = float(spot_bar.close)
        level = pick_levels(ctx.zones, spot)
        support = (
            level.support is not None
            and level.dist_support_pct is not None
            and 0 <= level.dist_support_pct <= cfg.stock_band_pct + 1e-9
        )
        resistance = (
            level.resistance is not None
            and level.dist_resistance_pct is not None
            and 0 <= level.dist_resistance_pct <= cfg.stock_band_pct + 1e-9
        )
        if not (support or resistance):
            continue

        rows = by_name.get(name, [])
        expiries = sorted({
            str(r.get("expiry") or "")[:10]
            for r in rows if r.get("expiry")
        })
        specs = lists_for(day, expiries, cfg)
        if not specs and rows:
            coverage.append(f"{name}: no listed expiry is available for {day.isoformat()}")
            continue
        if specs:
            first_dte = trading_days_between(day, specs[0].expiry_date)
            if first_dte > _MAX_CURRENT_SERIES_DTE:
                coverage.append(
                    f"{name}: active instrument master starts at {specs[0].expiry_date.isoformat()} "
                    f"({first_dte} trading days after replay day); earlier expiry series is unavailable"
                )
                continue

        for spec in specs:
            if spec.side == "PE" and not support:
                continue
            if spec.side == "CE" and not resistance:
                continue
            edge = float(
                level.support.top if spec.side == "PE"
                else level.resistance.bottom
            )
            instrument_rows = [
                r for r in rows
                if str(r.get("expiry") or "")[:10] == spec.expiry_date.isoformat()
            ]
            structural = _structural_candidates(
                instrument_rows, spec.side, spot, edge, cfg
            )
            selectable: list[dict] = []
            for row in structural:
                item = await _historical_contract_quote(
                    client, pacer, row, day, build_ms
                )
                if item is not None:
                    selectable.append(item)
            if structural and not selectable:
                coverage.append(
                    f"{name}/{spec.list_name}: option contracts existed in the "
                    "date-effective master but historical option candles/OI are "
                    "unavailable from Kite (commonly expired series)"
                )

            lot_raw = instrument_rows[0].get("lot_size") if instrument_rows else None
            try:
                lot = int(lot_raw) if lot_raw not in (None, "") else None
            except (TypeError, ValueError):
                lot = None
            for pick in select_strikes(
                selectable, spec.side, spot, edge, lot, cfg
            ):
                token = int(pick.get("instrument_token") or 0)
                symbol = str(pick.get("tradingsymbol") or "")
                if not token or not symbol:
                    continue
                watches.append(WatchContract(
                    underlying=name,
                    underlying_token=ctx.token,
                    list_name=spec.list_name,
                    instrument=InstrumentRef(
                        instrument_id=str(token),
                        tradingsymbol=symbol,
                        option_type=spec.side,
                        strike=float(pick.get("strike") or 0.0),
                        expiry=spec.expiry_date.isoformat(),
                        lot_size=int(lot or 0),
                        tick_size=float(
                            pick.get("tick_size") or default_tick(spot)
                        ),
                        exchange="NFO",
                    ),
                    tag=str(pick.get("tag") or "HOI-1"),
                    oi=int(float(pick.get("oi") or 0)),
                    candidates_n=int(pick.get("candidates_n") or 0),
                    ref_price=spot,
                    zone_edge=edge,
                    atr_pct=ctx.atr_pct,
                    build_oi=float(pick.get("oi") or 0),
                    chain_ltp=float(pick.get("last_price") or 0),
                    dte=trading_days_between(day, spec.expiry_date),
                ))
    return watches, coverage


def _underlying_spot(one_minute: list[Candle], event_ms: int,
                     fallback: float) -> float:
    current = fallback
    for bar in one_minute:
        if _bar_close_ms(bar, 1) > event_ms:
            break
        current = bar.close
    return float(current)


async def _replay_watch(client, cfg: GammaMoveConfig, watch: WatchContract,
                        day: date, start_ms: int, end_ms: int):
    """Replay one selected contract over one watchlist interval."""
    pacer = Pacer(_HIST_RATE)
    warm_from = (day - timedelta(days=7)).isoformat()
    end_text = datetime.fromtimestamp(end_ms / 1000, IST).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    day_from = f"{day.isoformat()} 09:15:00"

    raw5 = await _historical(
        client, pacer, int(watch.instrument.instrument_id),
        "5minute", warm_from, end_text, True
    )
    five = _to_candles(raw5, oi=True)
    if len(five) < cfg.warmup_bars:
        return [], None

    raw1 = await _historical(
        client, pacer, int(watch.instrument.instrument_id),
        "minute", day_from, end_text, True
    )
    one = _day_rows(_to_candles(raw1, oi=True), day)
    if not one:
        return [], None

    raw_u5 = await _historical(
        client, pacer, int(watch.underlying_token),
        "5minute", warm_from, end_text, False
    )
    under5 = _to_candles(raw_u5)
    raw_u1 = await _historical(
        client, pacer, int(watch.underlying_token),
        "minute", day_from, end_text, False
    )
    under1 = _day_rows(_to_candles(raw_u1), day)

    engine = ContractEngine(watch, cfg)
    closed: list[Candle] = []
    five_idx = 0

    # Prime exactly to the scheduled build time using only already-closed bars.
    while five_idx < len(five) and _bar_close_ms(five[five_idx], 5) <= start_ms:
        closed.append(five[five_idx])
        if len(closed) >= cfg.warmup_bars:
            engine.on_bar_close(closed)
        five_idx += 1

    day_minutes = [
        b for b in one
        if start_ms < _bar_close_ms(b, 1) <= end_ms
    ]
    if not day_minutes:
        return [], engine

    oi_peak = max(
        (b.oi for b in one if _bar_close_ms(b, 1) <= start_ms and b.oi > 0),
        default=0.0,
    )
    forming: Candle | None = None
    signals: list[GammaSignal] = []

    for minute in day_minutes:
        event_ms = _bar_close_ms(minute, 1)
        oi_peak = max(oi_peak, float(minute.oi or 0.0))
        slot = (minute.ts_ms // (5 * 60_000)) * (5 * 60_000)
        same = forming is not None and forming.ts_ms == slot
        forming = Candle(
            ts_ms=slot,
            open=forming.open if same else minute.open,
            high=max(forming.high if same else minute.high, minute.high),
            low=min(forming.low if same else minute.low, minute.low),
            close=minute.close,
            volume=(forming.volume if same else 0.0) + minute.volume,
            oi=minute.oi,
        )

        # This minute belongs to the 5m slot that ends at event_ms. Evaluate a
        # breakout against the E2 state that existed BEFORE this 5m close. Only
        # after that may this completed 5m candle arm the next E2 cycle.
        spot = _underlying_spot(under1, event_ms, watch.ref_price)
        # The reference E1 fires on a live tick above the base. Historical
        # minute candles preserve whether such a tick existed via HIGH, not
        # whether price happened to remain above the base at minute close.
        #
        # Volume is only known at minute close, so this remains an explicitly
        # approximate/read-only proxy: OHLCV cannot prove whether the volume
        # threshold was reached before or after the intraminute high.
        proxy_ltp = float(minute.high or minute.close)
        signal = engine.breakout(
            closed,
            forming,
            proxy_ltp,
            spot,
            under5,
            tick_ts_ms=event_ms,
            oi_now=float(minute.oi or 0.0),
            oi_peak_value=float(oi_peak or minute.oi or 0.0),
        )
        if signal is not None:
            signals.append(signal)

        while five_idx < len(five) and _bar_close_ms(five[five_idx], 5) <= event_ms:
            bar = five[five_idx]
            closed.append(bar)
            closed = closed[-500:]
            oi_peak = max(oi_peak, float(bar.oi or 0.0))
            if len(closed) >= cfg.warmup_bars:
                engine.on_bar_close(
                    closed,
                    oi_now=float(bar.oi or 0.0),
                    oi_peak_value=float(oi_peak or bar.oi or 0.0),
                )
            five_idx += 1
        if event_ms % (5 * 60_000) == 0:
            forming = None

    return signals, engine


def _watch_row(watch: WatchContract, engine: ContractEngine, at_ms: int) -> dict[str, Any]:
    """Board row for the final reconstructed watch state."""
    ev = engine.last_eval
    return {
        "id": f"watch:{watch.instrument.tradingsymbol}:{watch.list_name}",
        "state": "watching",
        "tier": "WATCHLIST",
        "at_ms": int(at_ms),
        "underlying": watch.underlying,
        "reason": f"{engine.state.value}: historical watch after replay interval",
        "list_name": watch.list_name,
        "tag": watch.tag,
        "strong": bool(ev and ev.strong),
        "source": "historical_replay",
        "instrument": {
            "instrument_id": watch.instrument.instrument_id,
            "tradingsymbol": watch.instrument.tradingsymbol,
            "option_type": watch.instrument.option_type,
            "strike": watch.instrument.strike,
            "expiry": watch.instrument.expiry,
            "lot_size": watch.instrument.lot_size,
            "tick_size": watch.instrument.tick_size,
            "exchange": watch.instrument.exchange,
        },
        "watch": {
            "underlying_token": watch.underlying_token,
            "ref_price": watch.ref_price,
            "zone_edge": watch.zone_edge,
            "atr_pct": watch.atr_pct,
            "oi": watch.oi,
            "candidates_n": watch.candidates_n,
            "dte": watch.dte,
            "build_oi": watch.build_oi,
            "chain_ltp": watch.chain_ltp,
            "carried": watch.carried,
        },
        "metrics": ev.as_dict() if ev else None,
        "gates": None,
        "levels": {
            "ltp": watch.chain_ltp,
            "entry": None,
            "stop": ev.consol_lo if ev else None,
            "t1": None,
            "t2": None,
        },
        "snapshot": {
            "historical_replay": True,
            "replay_resolution": "1minute_ohlcv_proxy",
        },
    }


async def replay_day(uid: str, day: date, cfg: GammaMoveConfig) -> dict[str, Any]:
    """Reconstruct one trading day and return board-ready rows.

    Exact live tick parity is impossible historically. This intentionally uses
    one-minute HIGH as evidence that E1 price crossed and minute-end volume as
    evidence that the volume threshold was eventually satisfied in that minute.
    Intraminute ordering remains unknown, so it is never execution-eligible.
    """
    if not is_trading_day(day):
        return {
            "day": day.isoformat(),
            "source": "historical_replay",
            "resolution": "1minute_ohlcv_proxy",
            "watchlist": [],
            "signals": [],
            "scan": {},
            "issue": "selected date is not an NSE trading day",
            "coverage": [],
            "complete": False,
        }

    from app.services.exchanges.kite import accounts

    acct = accounts.get_active(uid)
    if not acct:
        raise RuntimeError("No active Kite account")
    client = await accounts.acquire_client(acct)

    started = time.monotonic()
    dump, contexts, errors, master_notes = await _underlyings(
        uid, cfg, client, day
    )
    if not contexts:
        return {
            "day": day.isoformat(),
            "source": "historical_replay",
            "resolution": "1minute_ohlcv_proxy",
            "watchlist": [],
            "signals": [],
            "scan": {},
            "issue": (
                errors[0] if errors
                else "no date-effective underlying history was available"
            ),
            "coverage": [*master_notes, *errors],
            "complete": False,
        }

    schedule = [
        (clock, _clock_ms(day, clock))
        for clock in cfg.build_watchlists_times
    ]
    schedule.sort(key=lambda item: item[1])
    end_of_day = _clock_ms(day, _SESSION_END)
    signal_map: dict[str, dict] = {}
    watch_map: dict[str, dict] = {}
    coverage: list[str] = [*master_notes, *errors]
    replay_failures: list[str] = list(errors)
    request_watches = 0
    successful_replays = 0

    for idx, (_clock, build_ms) in enumerate(schedule):
        if build_ms >= end_of_day:
            continue
        next_ms = (
            schedule[idx + 1][1]
            if idx + 1 < len(schedule)
            else end_of_day
        )
        watches, coverage_notes = await _watches_at(
            uid, cfg, client, day, dump, contexts, build_ms
        )
        coverage.extend(coverage_notes)
        replay_failures.extend(coverage_notes)
        request_watches += len(watches)

        for watch in watches:
            try:
                signals, engine = await _replay_watch(
                    client, cfg, watch, day, build_ms, next_ms
                )
            except Exception as exc:  # noqa: BLE001
                message = (
                    f"{watch.instrument.tradingsymbol}: replay unavailable ({exc})"
                )
                coverage.append(message)
                replay_failures.append(message)
                continue

            successful_replays += 1
            if engine is not None:
                row = _watch_row(watch, engine, next_ms)
                row["reason"] = (
                    f"{row.get('reason') or 'historical watch'} "
                    f"from {_clock} watchlist"
                )
                watch_map[row["id"]] = row

            emitted_symbols = {
                signal.contract.instrument.tradingsymbol
                for signal in signals
            }
            for signal in signals:
                row = signal.as_dict()
                snap = dict(row.get("snapshot") or {})
                snap.update({
                    "historical_replay": True,
                    "replay_resolution": "1minute_ohlcv_proxy",
                    "replay_build_time": _clock,
                })
                row["snapshot"] = snap
                row["source"] = "historical_replay"
                row["reason"] = (
                    str(row.get("reason") or "")
                    + " · reconstructed from 1-minute OHLCV"
                )
                signal_map[row["id"]] = row
            for watch_id, watch_row in list(watch_map.items()):
                if str((watch_row.get("instrument") or {}).get("tradingsymbol") or "") in emitted_symbols:
                    watch_map.pop(watch_id, None)

    elapsed = round(time.monotonic() - started, 2)
    unique_coverage = list(dict.fromkeys(v for v in coverage if v))
    issue = ""
    if request_watches == 0:
        issue = (
            next((str(v) for v in replay_failures if v), "")
            or "reference watchlist found no date-effective contracts near zones"
        )
    elif successful_replays == 0:
        issue = (
            next((str(v) for v in replay_failures if v), "")
            or "all selected Gamma-Unwind contracts failed historical replay"
        )
    return {
        "day": day.isoformat(),
        "source": "historical_replay",
        "resolution": "1minute_ohlcv_proxy",
        "watchlist": list(watch_map.values()),
        "signals": sorted(
            signal_map.values(),
            key=lambda row: int(row.get("at_ms") or 0),
        ),
        "scan": {
            "last_run_ms": int(datetime.now(IST).timestamp() * 1000),
            "stage_a": {
                "scanned": len(contexts),
                "near_level": None,
                "seconds": None,
            },
            "stage_b": {
                "candidates": request_watches,
                "seconds": None,
            },
            "stage_c": {
                "watched": len(watch_map),
                "armed": len(signal_map),
                "historical_requests": request_watches,
                "seconds": None,
            },
            "total_seconds": elapsed,
            "replay_resolution": "1minute_ohlcv_proxy",
        },
        "issue": issue,
        "coverage": unique_coverage,
        "complete": not bool(issue),
    }
