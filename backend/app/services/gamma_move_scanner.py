"""Kite market-data adapter for GAMMA-UNWIND client 0.8.2 / S1.6.0."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.core.logging import get_logger
from app.engines.gamma_move import (
    Candle, ContractEngine, GammaMoveConfig, InstrumentRef, WatchContract,
    compute_zones, daily_atr_pct, lists_for, pick_levels, select_strikes,
    trading_days_between,
)
from app.services.gamma_move import nfo_dump, stock_underlyings

log = get_logger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
_HISTORICAL_RATE = 2.6
_QUOTE_RATE = 0.8
_QUOTE_BATCH = 400


class Pacer:
    def __init__(self, rate: float):
        self._gap = 1.0 / max(rate, 0.01)
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if now < self._next:
                await asyncio.sleep(self._next - now)
                now = time.monotonic()
            self._next = now + self._gap


@dataclass
class RuntimeWatch:
    contract: WatchContract
    engine: ContractEngine
    closed_bars: list[Candle]
    underlying_bars: list[Candle]
    spot: float
    forming: Candle | None = None
    underlying_forming: Candle | None = None


_runtime: dict[str, dict[int, RuntimeWatch]] = {}
_stats: dict[str, dict] = {}
_issue: dict[str, str] = {}
_levels_cache: dict[tuple[str, str], tuple[dict, dict, dict, dict]] = {}


def runtimes(uid: str) -> dict[int, RuntimeWatch]:
    return _runtime.get(uid, {})


def has_watchers(uid: str) -> bool:
    return bool(_runtime.get(uid))


def clear_levels(uid: str | None = None) -> None:
    if uid is None:
        _levels_cache.clear()
        return
    for key in [k for k in _levels_cache if k[0] == uid]:
        _levels_cache.pop(key, None)


def clear_runtime(uid: str | None = None) -> None:
    if uid is None:
        _runtime.clear()
    else:
        _runtime.pop(uid, None)


def scan_stats(uid: str) -> dict:
    return _stats.get(uid, {})


def scan_issue(uid: str) -> str:
    return _issue.get(uid, "")


def _ms(raw) -> int:
    if isinstance(raw, (int, float)):
        return int(raw if raw > 10_000_000_000 else raw * 1000)
    try:
        return int(datetime.fromisoformat(str(raw)).timestamp() * 1000)
    except Exception:
        return 0


def _to_candles(rows, *, oi: bool = False) -> list[Candle]:
    out: list[Candle] = []
    for row in rows or []:
        if isinstance(row, dict):
            d = row
        elif len(row) >= 6:
            d = {
                "date": row[0], "open": row[1], "high": row[2],
                "low": row[3], "close": row[4], "volume": row[5],
                "oi": row[6] if len(row) > 6 else 0,
            }
        else:
            continue
        try:
            bar = Candle(
                ts_ms=_ms(d.get("date")),
                open=float(d.get("open") or 0),
                high=float(d.get("high") or 0),
                low=float(d.get("low") or 0),
                close=float(d.get("close") or 0),
                volume=float(d.get("volume") or 0),
                oi=float(d.get("oi") or 0) if oi else 0.0,
            )
        except (TypeError, ValueError):
            continue
        if bar.ts_ms > 0 and bar.close > 0:
            out.append(bar)
    return out


async def _historical(client, pacer: Pacer, token: int, interval: str,
                      frm: str, to: str, oi: bool) -> list:
    await pacer.wait()
    raw = await client.get_historical(
        int(token), interval, frm, to, False, oi
    )
    return (raw.get("candles") if isinstance(raw, dict) else raw) or []


async def _quote_batched(client, keys: list[str]) -> dict:
    out: dict = {}
    pacer = Pacer(_QUOTE_RATE)
    for i in range(0, len(keys), _QUOTE_BATCH):
        await pacer.wait()
        out.update(await client.get_quote(keys[i:i + _QUOTE_BATCH]) or {})
    return out


# Generic quote helpers retained because intraday.py imports them.
def _quote_of(quotes: dict, key: str) -> dict:
    return quotes.get(key) or quotes.get(key.split(":", 1)[-1]) or {}


def _spread_pct(quote: dict) -> float | None:
    depth = quote.get("depth") or {}
    bids = depth.get("buy") or []
    asks = depth.get("sell") or []
    bid = float((bids[0] or {}).get("price") or 0.0) if bids else 0.0
    ask = float((asks[0] or {}).get("price") or 0.0) if asks else 0.0
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0
    return (ask - bid) / mid * 100.0 if mid > 0 else None


def _completed(candles: list[Candle], minutes: int,
               now_ms: int) -> list[Candle]:
    if not candles:
        return []
    width = minutes * 60_000
    last = candles[-1]
    if last.ts_ms < now_ms < last.ts_ms + width:
        return candles[:-1]
    return candles


def _closed_daily(candles: list[Candle], today: date) -> list[Candle]:
    now = datetime.now(IST)
    if (candles
            and datetime.fromtimestamp(candles[-1].ts_ms / 1000, IST).date() == today
            and (now.hour, now.minute) < (15, 30)):
        return candles[:-1]
    return candles


async def _level_stage(uid: str, cfg: GammaMoveConfig, client, today: date):
    cache_key = (uid, today.isoformat())
    if cache_key in _levels_cache:
        return _levels_cache[cache_key]
    nfo = await nfo_dump(uid)
    names = stock_underlyings(nfo, cfg)
    eq = await client.search_instruments("", "NSE", limit=1_000_000)
    tokens = {
        str(r.get("tradingsymbol") or "").upper():
            int(r.get("instrument_token") or 0)
        for r in eq if r.get("segment") == "NSE"
    }
    quotes = await _quote_batched(
        client, [f"NSE:{name}" for name in names]
    ) if names else {}
    pacer = Pacer(_HISTORICAL_RATE)
    frm = (today - timedelta(
        days=max(800, cfg.zone_daily_bars * 2)
    )).isoformat()
    levels: dict = {}
    spots: dict = {}
    atrs: dict = {}
    underlying: dict = {}
    errors: list[str] = []
    for name in names:
        token = tokens.get(name)
        spot = float(
            _quote_of(quotes, f"NSE:{name}").get("last_price") or 0
        )
        if not token or spot <= 0:
            continue
        try:
            raw = await _historical(
                client, pacer, token, "day", frm, today.isoformat(), False
            )
        except Exception as exc:
            errors.append(str(exc))
            continue
        bars = _closed_daily(_to_candles(raw), today)[-cfg.zone_daily_bars:]
        if len(bars) < 2 * cfg.zone_pivot_len + cfg.zone_atr_len:
            continue
        state = compute_zones(bars, cfg)
        levels[name] = pick_levels(state, spot)
        spots[name] = spot
        atrs[name] = daily_atr_pct(bars)
        underlying[name] = (token, bars)
    if names and not levels and errors:
        _issue[uid] = f"broker daily history unavailable: {errors[0]}"
    _levels_cache.clear()
    _levels_cache[cache_key] = (levels, spots, atrs, underlying)
    return _levels_cache[cache_key]


def _approaching(levels, cfg: GammaMoveConfig) -> tuple[bool, bool]:
    support = (
        levels.support is not None
        and levels.dist_support_pct is not None
        and 0 <= levels.dist_support_pct <= cfg.stock_band_pct + 1e-9
    )
    resistance = (
        levels.resistance is not None
        and levels.dist_resistance_pct is not None
        and 0 <= levels.dist_resistance_pct <= cfg.stock_band_pct + 1e-9
    )
    return support, resistance


async def _watchlist_stage(uid: str, cfg: GammaMoveConfig, client,
                           levels: dict, spots: dict, atrs: dict,
                           underlying: dict, today: date) -> list[WatchContract]:
    dump = await nfo_dump(uid)
    by_name: dict[str, list[dict]] = {}
    for row in dump:
        if row.get("segment") == "NFO-OPT":
            by_name.setdefault(
                str(row.get("name") or "").upper(), []
            ).append(row)
    watches: list[WatchContract] = []
    for name, level in levels.items():
        support, resistance = _approaching(level, cfg)
        if not (support or resistance):
            continue
        rows = by_name.get(name, [])
        expiries = sorted({
            str(r.get("expiry") or "")[:10]
            for r in rows if r.get("expiry")
        })
        for spec in lists_for(today, expiries, cfg):
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
                if str(r.get("expiry") or "")[:10]
                    == spec.expiry_date.isoformat()
                and str(r.get("instrument_type") or "") == spec.side
            ]
            keys = [
                f"NFO:{r.get('tradingsymbol')}" for r in instrument_rows
            ]
            quotes = await _quote_batched(client, keys) if keys else {}
            selectable: list[dict] = []
            for row in instrument_rows:
                quote = _quote_of(
                    quotes, f"NFO:{row.get('tradingsymbol')}"
                )
                item = dict(row)
                item.update({
                    "option_type": spec.side,
                    "oi": quote.get("oi"),
                    "last_price": quote.get("last_price"),
                    "ltp": quote.get("last_price"),
                    # Reference is fail-open when previous-session turnover
                    # cannot be obtained. Kite quote does not provide prior volume.
                    "previous_close": None,
                    "previous_volume": None,
                })
                selectable.append(item)
            lot = (
                int(instrument_rows[0].get("lot_size") or 1)
                if instrument_rows else 1
            )
            for pick in select_strikes(
                    selectable, spec.side, spots[name], edge, lot, cfg):
                token = int(pick.get("instrument_token") or 0)
                symbol = str(pick.get("tradingsymbol") or "")
                if not token or not symbol:
                    continue
                watches.append(WatchContract(
                    underlying=name,
                    underlying_token=int(underlying[name][0]),
                    list_name=spec.list_name,
                    instrument=InstrumentRef(
                        str(token),
                        symbol,
                        spec.side,
                        float(pick.get("strike") or 0),
                        spec.expiry_date.isoformat(),
                        lot,
                        float(pick.get("tick_size") or 0.05),
                        "NFO",
                    ),
                    tag=str(pick.get("tag") or "HOI-1"),
                    oi=int(float(pick.get("oi") or 0)),
                    candidates_n=int(pick.get("candidates_n") or 0),
                    ref_price=float(spots[name]),
                    zone_edge=edge,
                    atr_pct=atrs.get(name),
                    build_oi=float(pick.get("oi") or 0),
                    chain_ltp=float(pick.get("last_price") or 0),
                    dte=trading_days_between(today, spec.expiry_date),
                ))
    return watches


async def _prime_runtime(uid: str, cfg: GammaMoveConfig, client,
                         watches: list[WatchContract], today: date,
                         underlying: dict, spots: dict) -> None:
    pacer = Pacer(_HISTORICAL_RATE)
    frm = (today - timedelta(days=7)).isoformat()
    now_ms = int(datetime.now(IST).timestamp() * 1000)
    new: dict[int, RuntimeWatch] = {}
    underlying_5m: dict[str, list[Candle]] = {}
    for watch in watches:
        try:
            raw = await _historical(
                client, pacer, int(watch.instrument.instrument_id),
                "5minute", frm, today.isoformat(), True
            )
            bars = _completed(_to_candles(raw, oi=True), 5, now_ms)
        except Exception as exc:
            log.debug("gamma_unwind option history %s: %s",
                      watch.instrument.tradingsymbol, exc)
            continue
        if len(bars) < cfg.warmup_bars:
            continue
        if watch.underlying not in underlying_5m:
            try:
                raw = await _historical(
                    client, pacer, watch.underlying_token,
                    "5minute", frm, today.isoformat(), False
                )
                underlying_5m[watch.underlying] = _completed(
                    _to_candles(raw), 5, now_ms
                )
            except Exception:
                underlying_5m[watch.underlying] = []
        engine = ContractEngine(watch, cfg)
        # Rebuild today's hidden E2 state from closed broker OI history.
        for index in range(cfg.warmup_bars - 1, len(bars)):
            engine.on_bar_close(bars[:index + 1])
        new[int(watch.instrument.instrument_id)] = RuntimeWatch(
            watch, engine, bars, underlying_5m[watch.underlying],
            spots.get(watch.underlying, watch.ref_price),
        )
    _runtime[uid] = new


async def scan_once(uid: str, cfg: GammaMoveConfig,
                    *, today: Optional[date] = None) -> dict:
    from app.services.exchanges.kite import accounts
    acct = accounts.get_active(uid)
    if not acct:
        raise RuntimeError("No active Kite account")
    client = await accounts.acquire_client(acct)
    day = today or datetime.now(IST).date()
    started = time.monotonic()
    _issue.pop(uid, None)
    if (not getattr(client, "_access_token", "")
            and getattr(client, "_is_paper", None) is not True):
        _issue[uid] = (
            "no Kite session — Gamma-Unwind requires option OI history"
        )
        _stats[uid] = {
            "last_run_ms": int(datetime.now(IST).timestamp() * 1000),
            "issue": _issue[uid],
        }
        return {"watching": 0, "watchlist": []}

    levels, spots, atrs, underlying = await _level_stage(
        uid, cfg, client, day
    )
    stage_a = time.monotonic()
    watches = await _watchlist_stage(
        uid, cfg, client, levels, spots, atrs, underlying, day
    )
    stage_b = time.monotonic()
    await _prime_runtime(
        uid, cfg, client, watches, day, underlying, spots
    )
    stage_c = time.monotonic()
    near = sum(
        1 for level in levels.values()
        if any(_approaching(level, cfg))
    )
    _stats[uid] = {
        "last_run_ms": int(datetime.now(IST).timestamp() * 1000),
        "stage_a": {
            "scanned": len(levels),
            "near_level": near,
            "seconds": round(stage_a - started, 2),
        },
        "stage_b": {
            "candidates": len(watches),
            "seconds": round(stage_b - stage_a, 2),
        },
        "stage_c": {
            "watched": len(_runtime.get(uid, {})),
            "armed": sum(
                1 for rt in _runtime.get(uid, {}).values()
                if rt.engine.state.value == "ARMED"
            ),
            "historical_requests": len(watches),
            "seconds": round(stage_c - stage_b, 2),
        },
        "total_seconds": round(stage_c - started, 2),
    }
    return {
        "watching": len(_runtime.get(uid, {})),
        "watchlist": [
            watch_view(rt) for rt in _runtime.get(uid, {}).values()
        ],
    }


def watch_view(rt: RuntimeWatch) -> dict:
    watch = rt.contract
    ev = rt.engine.last_eval
    return {
        "id": f"watch:{watch.instrument.tradingsymbol}:{watch.list_name}",
        "state": "watching",
        "tier": "WATCHLIST",
        "at_ms": rt.closed_bars[-1].ts_ms if rt.closed_bars else 0,
        "underlying": watch.underlying,
        "reason": f"{rt.engine.state.value}: waiting for E1 breakout",
        "list_name": watch.list_name,
        "tag": watch.tag,
        "strong": bool(ev and ev.strong),
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
    }


def _forming_ts(ts_ms: int) -> int:
    return (ts_ms // (5 * 60_000)) * (5 * 60_000)


def _tick_ts_ms(tick: dict, fallback: int) -> int:
    for key in ("exchange_timestamp", "timestamp", "last_trade_time"):
        if tick.get(key) is not None:
            value = _ms(tick.get(key))
            if value > 0:
                return value
    return fallback


def _roll_underlying(rt: RuntimeWatch, ts_ms: int,
                     ltp: float, cumulative: float) -> None:
    slot = _forming_ts(ts_ms)
    previous = rt.underlying_forming
    if previous is not None and slot > previous.ts_ms:
        rt.underlying_bars.append(previous)
        rt.underlying_bars = rt.underlying_bars[-500:]
        previous = None
    day = datetime.fromtimestamp(slot / 1000, IST).date()
    done = sum(
        b.volume for b in rt.underlying_bars
        if datetime.fromtimestamp(b.ts_ms / 1000, IST).date() == day
    )
    volume = max(0.0, cumulative - done) if cumulative > 0 else 0.0
    same = previous is not None and previous.ts_ms == slot
    rt.underlying_forming = Candle(
        slot,
        previous.open if same else ltp,
        max(previous.high if same else ltp, ltp),
        min(previous.low if same else ltp, ltp),
        ltp, volume, 0.0,
    )


def process_ticks(uid: str, ticks: list[dict]) -> list:
    """Feed full-mode ticks. Closed 5m bars arm E2; live option ticks fire E1."""
    watches = _runtime.get(uid, {})
    if not watches:
        return []
    fallback = int(datetime.now(IST).timestamp() * 1000)
    signals = []

    # Underlying ticks first so the E1 direction gate sees current spot and
    # every fully closed 5m underlying bar.
    for tick in ticks or []:
        token = int(tick.get("instrument_token") or 0)
        ltp = float(tick.get("last_price") or 0)
        if ltp <= 0:
            continue
        ts_ms = _tick_ts_ms(tick, fallback)
        cumulative = float(
            tick.get("volume_traded") or tick.get("volume") or 0
        )
        for rt in watches.values():
            if rt.contract.underlying_token == token:
                rt.spot = ltp
                _roll_underlying(rt, ts_ms, ltp, cumulative)

    for tick in ticks or []:
        token = int(tick.get("instrument_token") or 0)
        rt = watches.get(token)
        if rt is None:
            continue
        ltp = float(tick.get("last_price") or 0)
        if ltp <= 0:
            continue
        ts_ms = _tick_ts_ms(tick, fallback)
        slot = _forming_ts(ts_ms)
        oi = float(
            tick.get("oi")
            or (rt.closed_bars[-1].oi if rt.closed_bars else 0)
        )
        previous = rt.forming
        if previous is not None and slot > previous.ts_ms:
            rt.closed_bars.append(previous)
            rt.closed_bars = rt.closed_bars[-500:]
            rt.engine.on_bar_close(rt.closed_bars)
            previous = None

        day = datetime.fromtimestamp(slot / 1000, IST).date()
        closed_today = [
            b for b in rt.closed_bars
            if datetime.fromtimestamp(b.ts_ms / 1000, IST).date() == day
        ]
        cumulative = float(
            tick.get("volume_traded") or tick.get("volume") or 0
        )
        done = sum(b.volume for b in closed_today)
        volume = max(0.0, cumulative - done) if cumulative > 0 else 0.0
        same = previous is not None and previous.ts_ms == slot
        forming = Candle(
            slot,
            previous.open if same else ltp,
            max(previous.high if same else ltp, ltp),
            min(previous.low if same else ltp, ltp),
            ltp, volume, oi,
        )
        rt.forming = forming
        underlying_bars = list(rt.underlying_bars)
        if rt.underlying_forming is not None:
            underlying_bars.append(rt.underlying_forming)
        signal = rt.engine.breakout(
            rt.closed_bars, forming, ltp, rt.spot,
            underlying_bars, tick_ts_ms=ts_ms,
        )
        if signal is not None:
            signals.append(signal)
    return signals
