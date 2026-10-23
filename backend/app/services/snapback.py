"""Runtime for Snapback: config, the daily candle fetch, and the scan.

Every rule lives in ``app.engines.snapback`` and is reachable from here without
a broker object, so the replay that produced the numbers and the live scan run
the same code.

Nothing in this module places an order. Live entry is the plan producer's, the
dispatcher's and the live executor's (``snapback_plan_producer``,
``snapback_dispatcher``, ``snapback_live_executor``): a row built here is a
description of a signal, and whether it can be traded RIGHT NOW is decided at
serve time from live conditions. The generic Kite order route belongs to the
SuperTrend lifecycle and must not be used as a Snapback execution path.

**This engine scans DAILY bars.** That is the single biggest difference from
every other engine here, and it has one practical consequence worth stating: a
signal is produced by a session's CLOSE, so the scan is worth running once after
the close and once before the next open, not every five minutes. The catch-up
window exists because a rule that fires on one bar is a rule whose signal is
lost forever if the scan that would have seen it did not run.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from datetime import time as dt_time
from typing import Any, Optional

from app.core.logging import get_logger
from app.engines.option_contracts import canonical, spec_for
from app.engines.snapback import (CONTRACT_VERSION, SnapbackConfig, STRATEGY_ID,
                                  TUPLE_FIELDS, descriptor, evaluate, evaluate_at,
                                  to_bars)
from app.engines.snapback.contracts import lots_for, moneyness_label
from app.engines.snapback.intraday_models import DepthLevel, RawQuoteEvent
from app.services.snapback_hedge_contract import HedgeContractError, select_hedge_future
from app.services.snapback_beta_provenance import (canonical_index_beta,
                                                   causal_beta_with_provenance,
                                                   record_beta_snapshot)
from app.services.snapback_entry_observation import record_attempt, session_continuity
from app.services.snapback_market_data import (evaluate_underlying_context_quote,
                                               spread_pct as canonical_spread_pct)
from app.services.snapback_instrument_identity import IdentityError, identity_from_opportunity
from app.services.snapback_venue import (
    VenueUnknown,
    quote_key,
    resolve_position_venue,
    venue_from_opportunity,
)
from app.engines.snapback.policy import (RUNNER_EXPIRY_BUFFER_CALENDAR_DAYS,
                                          runner_should_exit_for_expiry)
from app.engines.snapback.pricing import bs_delta, bs_price
from app.services.snapback_capacity import observed_hedge_margin
from app.services.snapback_health import record_cycle
from app.services.snapback_quote_evidence import record_quote_attempt

log = get_logger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))
_CONFIG_KEY = "snapback_config"
#: Daily bars fetched per instrument. The binding input is a 20-session EMA plus
#: a trailing volatility rank; 900 matches the replay/history window and
#: is one request per instrument.
LOOKBACK_BARS = 900
_CANDLE_TTL_S = 900.0
#: A daily tape whose last bar is final cannot change until the next close, so
#: re-reading it every cycle only spends the broker's historical-data budget
#: (3 requests/second across every strategy). Four hours covers a session; the
#: close-crossing rule below still forces a re-read at 15:30.
_CANDLE_LONG_TTL_S = 4 * 3600.0
#: The broker may not have finalised the day's bar when the session has just
#: closed, so a read taken in this window keeps the short TTL.
_POST_CLOSE_SETTLE = timedelta(hours=2)
#: Closed sessions the scan looks back over. A daily rule fires on ONE bar, and
#: a scan that does not run on the day it fired never sees it again.
CATCHUP_SESSIONS = 3

__all__ = ["get_config", "set_config", "descriptor", "snapshot", "scan_once",
           "status", "evaluate_symbol", "recent_signals", "ist_now_ms",
           "STRATEGY_ID"]


def _wall_now() -> datetime:
    """The IST wall clock. One seam, so every closed-bar decision shares it."""
    return datetime.now(_IST)


def ist_now_ms() -> int:
    return int(_wall_now().timestamp() * 1000)


def ist_today() -> date:
    return datetime.now(_IST).date()


# ------------------------------------------------------------------ config

def get_config(uid: str | None = None) -> SnapbackConfig:
    """The configuration in force.

    Three outcomes, deliberately different. **Nothing stored** gives the real
    defaults. **Stored but invalid** gives defaults with the engine OFF, because
    a config that will not validate must never become a trading config. **Store
    unreachable** also gives OFF — an engine that has lost its settings must not
    start scanning the shipped universe with the operator's choices discarded.
    """
    key = f"{_CONFIG_KEY}:{uid}" if uid else _CONFIG_KEY
    raw: Any = None
    try:
        from app.services import db
        if hasattr(db, "is_available") and not db.is_available():
            log.warning("%s: config store unavailable; defaults OFF", STRATEGY_ID)
            return SnapbackConfig(enabled=False)
        raw = db.get_config(key)
        # Only the historical single-user identity may inherit the old global
        # row. A new tenant with no config gets real defaults, never another
        # operator's strategy settings.
        if not raw and uid == "default":
            raw = db.get_config(_CONFIG_KEY)
    except Exception:                                              # noqa: BLE001
        log.warning("%s: config store unavailable; defaults OFF", STRATEGY_ID)
        return SnapbackConfig(enabled=False)
    if not raw:
        return SnapbackConfig()
    try:
        from app.engines.snapback import validate
        stored = json.loads(raw) if isinstance(raw, str) else raw
        known = set(SnapbackConfig().as_dict())
        return validate({k: v for k, v in dict(stored).items() if k in known})
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        log.error("Stored %s config is invalid (%s); defaults OFF", STRATEGY_ID, exc)
        return SnapbackConfig(enabled=False)


def get_effective_config(uid: str | None = None) -> SnapbackConfig:
    from app.services.shared_risk import snapback_config
    return snapback_config(uid, get_config(uid))


class FrozenExperimentConfigError(RuntimeError):
    """The hypothesis cannot change while a prospective experiment is running."""


def prospective_experiment_active() -> bool:
    """Whether a frozen prospective experiment is collecting evidence right now.

    An edit mid-experiment silently splits the sample into two strategies, and the
    evidence then answers a question nobody asked.
    """
    explicit = (os.environ.get("STERLING_PROSPECTIVE_EXPERIMENT") or "").strip().lower()
    if explicit in ("1", "true", "yes", "on"):
        return True
    if explicit in ("0", "false", "no", "off"):
        return False

    try:
        from app.services.snapback_family_mode import family_mode_enabled

        if family_mode_enabled():
            return True
    except Exception:
        pass

    # A dedicated prospective evidence database is itself a running experiment, unless THIS
    # process found it cannot run the collector (lifespan records that when the runner's
    # preflight fails). The freeze protects a sample being collected; where nothing is being
    # collected it only locked the operator out of enabling live Snapback, with
    # SNAPBACK_CONFIG_FROZEN, on any machine whose environment names the documented
    # ``prospective_*.db`` path (the ``.env.example`` default). Family Mode and an explicit
    # STERLING_PROSPECTIVE_EXPERIMENT above are not affected.
    db_path = os.environ.get("STERLING_OBSERVATIONS_DB_PATH") or ""
    if "prospective" not in db_path.lower():
        return False
    try:
        from app.services.snapback_health import has_error

        return not has_error("prospective_runner_unavailable")
    except Exception:                                              # noqa: BLE001
        return True


def set_config(values: dict[str, Any], uid: str | None = None) -> SnapbackConfig:
    """Persist a partial change. Validation is the engine's, not a second copy.

    Refused outright while an experiment is active: operational control lives in the
    family STOP switch, which halts new exposure without touching the hypothesis.
    """
    from app.services.simulation.identity import is_sim_uid

    # The freeze protects the live prospective sample; the virtual Simulation tenant's config is its
    # own row and feeds no evidence, so it stays editable (its mode switch writes it).
    if prospective_experiment_active() and not is_sim_uid(uid):
        raise FrozenExperimentConfigError(
            "Snapback configuration is frozen for the active prospective experiment; "
            "use STOP NEW TRADES for operational control"
        )

    from app.engines.snapback import validate
    from app.engines.snapback.config import MODE_DEFAULTS
    current = get_config(uid)
    if values.get('trading_mode', current.trading_mode) != current.trading_mode:
        values = {**MODE_DEFAULTS.get(values['trading_mode'], {}), **values}
    cfg = validate(dict(values), base=current)
    from app.services import db
    db.set_config(f"{_CONFIG_KEY}:{uid}" if uid else _CONFIG_KEY,
                  json.dumps(cfg.as_dict(), separators=(",", ":")))
    # A row's eligibility and quote plan belong to the configuration that
    # produced it, including its timeframe. Never reuse it after a settings edit.
    if uid:
        st = status(uid)
        st.rows = []
        st.signals = {}
        st.candles = {}
        st.candle_wall = {}
        st.preview_rows = []
        st.config_generation += 1
        _save_last_rows(uid, [])
    return cfg


# ------------------------------------------------------------------- state

@dataclass
class ScanState:
    """What the last scan found, per user. In memory by design — these rows are
    a view of a tape stored elsewhere, and persisting them would create a second
    source of truth for one fact."""

    rows: list[dict] = field(default_factory=list)
    scanning: bool = False
    last_scan_ms: int = 0
    last_error: Optional[str] = None
    scanned: int = 0
    failures: list[str] = field(default_factory=list)
    candles: dict[Any, tuple[float, list]] = field(default_factory=dict)
    #: Wall-clock (IST epoch seconds) at which each cached tape was fetched, so a
    #: tape cached before the 15:30 close is never read as the finished session.
    candle_wall: dict[Any, float] = field(default_factory=dict)
    signals: dict[str, dict] = field(default_factory=dict)
    config_generation: int = 0
    preview_rows: list[dict] = field(default_factory=list)


_state: dict[str, ScanState] = {}


_LAST_ROWS_KEY = "snapback.last_rows.v1"


def status(uid: str) -> ScanState:
    st = _state.get(uid)
    if st is None:
        st = _state[uid] = ScanState()
        st.rows = _restored_rows(uid)
    return st


def _restored_rows(uid: str) -> list[dict]:
    """The previous process's last scan, shown read-only until the first scan lands.

    Without this the board sat empty for the ~10 minutes the first scan takes after
    every restart. Restored rows can never be acted on: ``_aged_rows`` refuses them.
    """
    try:
        from app.services import db
        raw = json.loads(db.get_config(f"{_LAST_ROWS_KEY}:{uid}", "{}") or "{}")
    except Exception:                                              # noqa: BLE001
        return []
    rows = []
    for r in raw.get("rows") or []:
        if isinstance(r, dict):
            rows.append({**r, "restored": True, "execution_eligible": False})
    return rows


def _save_last_rows(uid: str, rows: list[dict]) -> None:
    try:
        from app.services import db
        db.set_config(f"{_LAST_ROWS_KEY}:{uid}",
                      json.dumps({"saved_ms": ist_now_ms(), "rows": rows}, default=str))
    except Exception as exc:                                       # noqa: BLE001
        log.debug("%s: last rows not saved: %s", STRATEGY_ID, exc)


def signal_id_for(symbol: str, side: str, bar_ms: int) -> str:
    return f"{STRATEGY_ID}:{side}:{canonical(symbol)}:{int(bar_ms)}"


# ---------------------------------------------------------------- evaluate

class UnreadableCandles(RuntimeError):
    """Candles arrived and none of them parsed.

    An exception rather than an empty list, because the two are indistinguishable
    downstream and mean opposite things. A shape change in the feed once made
    every one of 200 instruments produce an empty tape, and the scan reported
    "nothing fired" — which is what a quiet market looks like.
    """


def evaluate_symbol(candles, cfg: SnapbackConfig, symbol: str, *, market_gate=None) -> list:
    """Signals on the last few CLOSED sessions of one instrument."""
    bars = to_bars(candles)
    if cfg.trading_mode != "swing":
        from app.engines.snapback.intraday import evaluate_intraday
        return evaluate_intraday(bars, cfg, symbol, asof=datetime.now(_IST).timestamp())
    if len(bars) == 0:
        if candles:
            raise UnreadableCandles(
                f"{len(candles)} candles arrived and none parsed "
                f"(first is {type(candles[0]).__name__})")
        return []
    from app.engines.snapback import entry_indices, features
    f = features(bars, cfg)
    out = []
    for side in cfg.sides():
        for i in entry_indices(bars, cfg, side, market_gate):
            if int(i) < len(bars) - CATCHUP_SESSIONS:
                continue
            out.extend(_named(sig, symbol) for sig in evaluate_at(bars, cfg, int(i), f)
                       if sig.side == side)
    return out


def _drop_forming(candles: list) -> list:
    """Keep one closed, valid exchange session per day for every candle shape."""
    from app.services.daily_sessions import closed_daily_candles
    return closed_daily_candles(candles, _wall_now())


def resolve_universe(cfg: SnapbackConfig, *, nfo, bfo, equities) -> list:
    """The instruments this scan covers.

    ``curated`` defers to the shared builder, so Snapback scans exactly what
    every other engine scans. ``fno`` builds its own list from the dump, because
    ``build_universe`` applies the fourteen-name high-liquidity registry BEFORE
    anyone downstream can widen it — pointing this engine at it and asking for
    the F&O list returned eighteen instruments and no signals.

    Liquidity is deliberately NOT a name-level filter in ``fno`` mode. What
    decides whether a trade is fillable is the spread and the open interest on
    the CONTRACT, and both are measured per contract during the scan. A static
    list of liquid underlyings answers a different question and answers it out
    of date: between July and September 2026 this rule fired ninety times across
    the F&O list and not once on those fourteen names.

    Indices come first so ``max_universe`` trims the tail of the stock list
    rather than a random slice.
    """
    from app.engines.option_contracts import spec_for
    from app.services.kite_engine.universe import (UniverseItem, build_universe,
                                                   select_scan_universe)
    built = build_universe(nfo_instruments=nfo, bfo_instruments=bfo,
                           equities=equities)
    if cfg.universe_mode == "curated":
        return list(select_scan_universe(
            built, indices=cfg.scan_indices, stocks=cfg.scan_stocks,
            all_stocks=False, stock_contracts=cfg.scan_stock_contracts))

    by_symbol: dict[str, dict] = {}
    for e in equities or ():
        sym = str(e.get("tradingsymbol", "") if isinstance(e, dict)
                  else getattr(e, "tradingsymbol", "") or "")
        if sym and sym not in by_symbol:
            by_symbol[sym] = e if isinstance(e, dict) else e.__dict__

    selected_indices = {canonical(n) for n in cfg.scan_indices}
    out: list = [i for i in built if i.is_index and canonical(i.name) in selected_indices]
    seen: set[str] = {canonical(i.name) for i in out}
    if cfg.scan_stock_contracts:
        stocks: list = []
        for rows, option_exchange in ((nfo, "NFO"), (bfo, "BFO")):
            for r in rows or ():
                d = r if isinstance(r, dict) else getattr(r, "__dict__", {})
                if d.get("instrument_type") not in ("CE", "PE"):
                    continue
                name = str(d.get("name") or "").strip().upper()
                if not name or name in seen or spec_for(name) is None:
                    continue
                equity = by_symbol.get(name)
                if not equity:
                    # No spot listing means no daily tape, and the rule is
                    # stated on the underlying's closes.
                    continue
                seen.add(name)
                stocks.append(UniverseItem(
                    name=name, tradingsymbol=name,
                    token=int(equity.get("instrument_token", 0) or 0),
                    exchange=str(equity.get("exchange", "NSE")),
                    option_exchange=option_exchange))
        stocks.sort(key=lambda i: i.name)
        out += stocks
    return out[:max(int(cfg.max_universe), 1)]


# ---------------------------------------------------------------- contracts

def _pick_by_delta(chain: list, *, spot: float, option_type: str, iv: float,
                   cfg: SnapbackConfig) -> Optional[dict]:
    """The listed strike whose modelled delta is nearest the target.

    Over the REAL chain, not an arithmetic ladder: the engine's own
    ``strike_for_delta`` inverts Black-Scholes to a price, and the exchange
    lists what it lists. Choosing from the chain means a signal can never name a
    strike that is not tradable.

    ``chain_rows_for`` yields DICTS whose option side is spelled "call"/"put"
    and whose expiry key is ``expiry_date``. Reading them with ``getattr`` and
    "CE"/"PE" — as the first version did — matches nothing and every row comes
    back "no listed contract", which reads as an expiry problem rather than a
    shape mismatch.
    """
    call = option_type == "CE"
    want = "call" if call else "put"
    best, best_gap = None, 1e9
    for row in chain:
        if str(row.get("option_type")) != want:
            continue
        dte = int(row.get("dte") or 0)
        if not (cfg.min_dte <= dte <= cfg.max_dte):
            continue
        strike = float(row.get("strike") or 0.0)
        if strike <= 0:
            continue
        d = abs(float(bs_delta(spot, strike, dte / 365.0, iv, call=call)))
        gap = abs(d - cfg.target_delta)
        # Ties break towards the SHORTER expiry: same delta, less premium tied
        # up for the same ten-session horizon.
        if gap < best_gap - 1e-9 or (abs(gap - best_gap) <= 1e-9 and best
                                     and dte < int(best.get("dte") or 999)):
            best_gap = gap
            best = {"symbol": str(row.get("instrument_name") or ""),
                    "strike": strike, "option_type": option_type,
                    "expiry": str(row.get("expiry_date") or ""),
                    "dte": dte,
                    "lot_size": int(row.get("lot_size") or 0),
                    "token": int(row.get("token") or 0),
                    "delta": d if call else -d,
                    "moneyness": moneyness_label(spot, strike, call)}
    return best


async def _contract_for(client, name: str, option_exchange: str, spot: float,
                        option_type: str, iv: float, cfg: SnapbackConfig,
                        chain_cache: dict) -> Optional[dict]:
    from app.services.kite_engine.strikes import (
        chain_rows_for,
        filter_chain_by_expiry_types,
    )
    try:
        rows = chain_cache.get(option_exchange)
        if rows is None:
            rows = await client.search_instruments("", option_exchange,
                                                   limit=1_000_000)
            chain_cache[option_exchange] = rows
        today = ist_today()
        chain = chain_rows_for(rows, name, today)
        if not chain:
            return None
        spec = spec_for(name)
        expiry_types = (
            cfg.expiry_series_indices
            if spec is not None and spec.is_index
            else cfg.expiry_series_stocks
        )
        chain = filter_chain_by_expiry_types(chain, expiry_types, today)
        if not chain:
            return None
        pick = _pick_by_delta(chain, spot=spot, option_type=option_type, iv=iv,
                              cfg=cfg)
        if pick:
            pick["exchange"] = option_exchange
        return pick
    except Exception as exc:                                       # noqa: BLE001
        log.debug("%s: contract resolution failed for %s: %s", STRATEGY_ID,
                  name, exc)
        return None


def _entry_session_passed(sig) -> Optional[str]:
    """A reason when the daily signal's one entry session is already behind us.

    The rule fills at the NEXT session's open and no later. The scan looks back
    ``CATCHUP_SESSIONS`` so a missed run does not lose the signal, but a signal two
    sessions old is a record, not an opportunity, and must not read as armed.
    An unknown calendar says nothing: the plan producer re-checks it independently.
    """
    try:
        from app.services.navigator.calendar import next_trading_day

        signalled = datetime.fromtimestamp(sig.timestamp_ms / 1000.0, _IST).date()
        entry_session = next_trading_day(signalled)
    except Exception:                                              # noqa: BLE001
        return None
    if ist_today() > entry_session:
        return f"entry session {entry_session.isoformat()} has passed"
    return None


async def _quote_for(client, contract: dict, cfg: SnapbackConfig) -> Optional[dict]:
    """The contract's own premium and book, so an armed row can be sized.

    Fetched during the scan and not at arm time: a row without a premium cannot
    be sized, cannot be stopped and cannot be bought, and discovering that after
    clicking Buy is the wrong moment.
    """
    key = f"{contract['exchange']}:{contract['symbol']}"
    try:
        quotes = await client.get_quote([key])
    except Exception as exc:                                       # noqa: BLE001
        return {"premium": None, "blockers": [f"quote unavailable: {exc}"]}
    q = (quotes or {}).get(key) or {}
    def number(value):
        try:
            v = float(value or 0.0)
            return v if math.isfinite(v) else 0.0
        except (TypeError, ValueError, OverflowError):
            return 0.0
    depth = (q.get("depth") or {})
    bid = number((depth.get("buy") or [{}])[0].get("price"))
    ask = number((depth.get("sell") or [{}])[0].get("price"))
    ltp = number(q.get("last_price"))
    has_book = bid > 0 and ask > 0 and ask >= bid
    mid = (bid + ask) / 2.0 if has_book else ltp
    spread_pct = canonical_spread_pct(bid=bid, ask=ask)
    blockers: list[str] = []
    if not has_book:
        blockers.append("fresh two-sided book unavailable")
    stamp = q.get("timestamp") or q.get("last_trade_time") or q.get("exchange_timestamp")
    quote_ts_ms: Optional[int] = None
    observed_ms = ist_now_ms()
    if not stamp:
        blockers.append("quote timestamp unavailable")
    else:
        from app.engines.snapback.models import _epoch_seconds
        ts = _epoch_seconds(stamp)
        if ts is not None and math.isfinite(ts):
            quote_ts_ms = int(ts * 1000)
        age = (_wall_now().timestamp() - ts) if (ts is not None and math.isfinite(ts)) else float("inf")
        if not math.isfinite(age) or age < -5 or age > (10 if cfg.trading_mode != "swing" else 60):
            blockers.append("quote is stale or has an invalid timestamp")
    if mid <= 0:
        blockers.append("no premium quoted")
    elif mid < cfg.min_option_premium:
        blockers.append(f"premium {mid:.2f} below the {cfg.min_option_premium:g} floor")
    if spread_pct is not None and spread_pct > cfg.max_spread_pct:
        blockers.append(f"spread {spread_pct:.1f}% wider than "
                        f"{cfg.max_spread_pct:g}%")
    oi = number(q.get("oi"))
    if cfg.min_option_oi > 0 and oi < cfg.min_option_oi:
        blockers.append(f"open interest {oi:,.0f} below {cfg.min_option_oi:,.0f}")
    # Long options pay the ask. Mid-price sizing understated cash and risk.
    executable_premium = ask if has_book and not blockers else None
    return {"premium": executable_premium, "bid": bid or None, "ask": ask or None,
            "ltp": ltp or None, "oi": oi or None, "spread_pct": spread_pct,
            #: When the quote describes (exchange time) and when this process read
            #: it. A row outlives its scan, so without these a ten-minute-old
            #: book is indistinguishable from the one read a second ago.
            "quote_ts_ms": quote_ts_ms, "observed_ms": observed_ms,
            "blockers": blockers}


# --------------------------------------------------------------------- row

def _row(sig, cfg: SnapbackConfig, *, contract: Optional[dict],
         quote: Optional[dict], blocked: Optional[str],
         underlying_token: int) -> dict:
    """One board row. Every price that is MODELLED says so.

    The premium a row shows is the quoted one when there is a quote and the
    modelled one when there is not, and the two are never merged into one
    unlabelled number — this repo has shipped a modelled premium that read as a
    price, and an operator cannot size a trade off a figure whose provenance is
    ambiguous.
    """
    if cfg.trading_mode != "swing":
        return _intraday_row(sig, cfg, contract=contract, quote=quote,
                             blocked=blocked, underlying_token=underlying_token)
    spec = spec_for(sig.symbol)
    lot = int((contract or {}).get("lot_size") or (spec.lot_size if spec else 0))
    quoted = (quote or {}).get("premium")
    modelled = None
    if contract:
        modelled = float(bs_price(sig.entry, float(contract["strike"]),
                                  max(int(contract.get("dte") or cfg.min_dte), 1) / 365.0,
                                  sig.assumed_iv,
                                  call=sig.option_type == "CE"))
    premium = quoted if quoted else modelled
    # Rounded DOWN to the 0.05 tick, as the plan producer does, so the SL on the
    # board is the SL on the plan and the order, not a price off the tick grid.
    stop = (max(0.05, round(math.floor(premium * (1.0 - cfg.premium_stop_pct / 100.0)
                                       / 0.05 + 1e-9) * 0.05, 2)) if premium else None)
    # The thesis' own objective, priced: what this contract is worth if spot
    # returns to the mean over the holding period. Modelled at the ENTRY's vol
    # like everything else here, so it is a plan and not a forecast — but a
    # target column that is always "—" tells an operator nothing about what the
    # trade is FOR.
    target = None
    if contract and premium:
        years = max(int(contract.get("dte") or cfg.min_dte) - cfg.hold_days, 1) / 365.0
        modelled_target = float(bs_price(sig.mean_target, float(contract["strike"]), years,
                                         sig.assumed_iv, call=sig.option_type == "CE"))
        if quoted and modelled:
            # The entry is a real ask and the target a Black-Scholes value; the
            # two are not on one scale (market vol is not the assumed vol), so a
            # raw model value can land BELOW the price it is meant to beat. Carry
            # the modelled MOVE onto the real entry instead.
            target = quoted + (modelled_target - modelled)
        else:
            target = modelled_target
        target = round(target, 2) if target > premium else None
    # A trail only exists when one is configured. Showing the stop in the trail
    # column because the trail is off would claim a ratchet that is not running.
    trail = (round(premium * (1.0 - cfg.premium_trail_pct / 100.0), 2)
             if premium and cfg.premium_trail_pct > 0 else None)
    # The price at which this trade stops being a horizon trade. Above it the
    # position is held past ``hold_days`` under the give-back ratchet instead of
    # being closed, so an operator reading the row needs the number rather than
    # the rule.
    runner = (round(premium * cfg.runner_mult, 2)
              if premium and cfg.runner_mult > 0 else None)
    lots = 0
    if premium and lot:
        from app.engines.snapback.contracts import Pick
        lots = lots_for(Pick(underlying=sig.symbol, symbol=(contract or {}).get("symbol", ""),
                             option_type=sig.option_type,
                             strike=float((contract or {}).get("strike") or 0.0),
                             lot_size=lot, exchange=(contract or {}).get("exchange", ""),
                             dte=(contract or {}).get("dte"),
                             expiry=(contract or {}).get("expiry"),
                             premium=float(premium), delta=0.0, moneyness="",
                             modelled=quoted is None), cfg)
    return {
        "signal_id": signal_id_for(sig.symbol, sig.side, sig.timestamp_ms),
        "strategy": STRATEGY_ID,
        "trading_mode": getattr(cfg, "trading_mode", "swing"),
        "side": sig.side,
        "symbol": sig.symbol,
        "state": "watching" if blocked else "armed",
        "reason": blocked,
        # A valid quoted signal is NOT the same thing as a safe broker entry.
        # Snapback does not yet own a canonical position/protection lifecycle;
        # exposing the shared Kite ticket would route the trade through the
        # SuperTrend-owned manual protection path and attach the wrong strategy
        # semantics. Keep the signal armed/advisory, but fail closed on entry.
        "execution_eligible": False,
        "origin": "live_quote" if quoted is not None else "modelled",
        "direction": sig.direction,
        "opt_type": sig.option_type,
        "timestamp_ms": sig.timestamp_ms,
        "spot": round(sig.entry, 2),
        "mean_target": round(sig.mean_target, 2),
        "distance_pct": round(sig.distance_pct, 2),
        "stretch": round(sig.stretch, 2),
        "level": round(sig.level, 2),
        "strength": sig.strength,
        "realized_vol_pct": round(sig.realized_vol * 100, 1),
        "assumed_iv_pct": round(sig.assumed_iv * 100, 1),
        "assumed_vrp": cfg.assumed_vrp,
        "hold_days": cfg.hold_days,
        "underlying_token": underlying_token,
        "contract": contract,
        "premium": round(float(premium), 2) if premium else None,
        "premium_is_modelled": bool(premium and quoted is None),
        "modelled_premium": round(modelled, 2) if modelled else None,
        "quote": quote,
        "stop_premium": stop,
        "target_premium": target,
        "target_basis": "modelled_move_on_quote" if (target and quoted) else "model",
        "trail_premium": trail,
        "runner_premium": runner,
        #: Filled for a replayed row by ``recent_signals``: what the trade
        #: actually did. A history row without an outcome leaves the exit, the
        #: result and the LTP columns empty, which reads as missing data rather
        #: than as a closed trade.
        "outcome": None,
        "lots": lots,
        "quantity": lots * lot if lots and lot else 0,
        "deployed_inr": round(float(premium) * lots * lot, 2)
        if premium and lots and lot else None,
        #: What ONE lot costs. The load-bearing number for this engine, and the
        #: one an operator needs before anything else: a 0.70-delta monthly put
        #: on a single stock is one to two LAKH of premium per lot, so at the
        #: shipped 2%-of-1-lakh budget nothing can ever arm. Reporting only
        #: "cannot afford it" leaves the reader to work out by how much.
        "min_outlay_inr": round(float(premium) * lot, 2)
        if premium and lot else None,
        "reasons": list(sig.reasons),
        # The mean the stretch is measured from is configurable; the board words
        # it from here rather than assuming the 20-session default.
        "metrics": {**dict(sig.metrics), "mean_ema": cfg.mean_touch_ema},
    }


# -------------------------------------------------------------------- scan

def _intraday_row(sig, cfg, *, contract, quote, blocked, underlying_token):
    """An observed-quote plan, never an executable promise of protection."""
    from app.engines.snapback.intraday import make_plan
    premium = (quote or {}).get("premium")
    lot = int((contract or {}).get("lot_size") or 0)
    spread = max(0.0, float((quote or {}).get("ask") or 0)
                 - float((quote or {}).get("bid") or 0))
    plan = make_plan(float(premium), lot, cfg, spread_points=spread) if premium and lot else {}
    lots = int(plan.get("lots") or 0)
    quantity = int(plan.get("quantity") or 0)
    reasons = list(plan.get("rejection_reasons") or [])
    reason = blocked or (reasons[0] if reasons else None)
    if reason is None and not (premium and lots > 0):
        reason = "no executable premium plan"
    return {
        "signal_id": signal_id_for(sig.symbol, sig.side, sig.timestamp_ms) + (
            f':{cfg.trading_mode}:{cfg.scalp_timeframe_minutes}m'
            if cfg.trading_mode in ('ultra_scalping', 'intraday', 'overnight') else ''),
        "strategy": STRATEGY_ID, "trading_mode": cfg.trading_mode,
        # A minute-bar row arms when its plan is accepted on a clean observed book,
        # exactly as a daily row does. It was hard-coded "watching", so the scalp
        # lane could never present an armed row to the live plan producer.
        "side": sig.side, "symbol": sig.symbol,
        "state": "watching" if reason else "armed",
        "reason": reason, "execution_eligible": False,
        "origin": "live_quote" if premium else "unavailable",
        "direction": sig.direction, "opt_type": sig.option_type,
        "timestamp_ms": sig.timestamp_ms, "spot": round(sig.entry, 2),
        "mean_target": round(sig.mean_target, 2), "distance_pct": round(sig.distance_pct, 2),
        "stretch": round(sig.stretch, 2), "level": round(sig.level, 2),
        "strength": sig.strength, "realized_vol_pct": round(sig.realized_vol * 100, 1),
        "assumed_iv_pct": round(sig.assumed_iv * 100, 1), "assumed_vrp": cfg.assumed_vrp,
        "hold_days": 0, "underlying_token": underlying_token, "contract": contract,
        "premium": premium, "premium_is_modelled": False, "modelled_premium": None,
        "quote": quote, "stop_premium": plan.get("stop"),
        # The stop is the INITIAL stop. It becomes a trail only once the close
        # clears the lock level, so the trail column stays empty until then and
        # the lock/break-even levels get their own fields.
        "target_premium": plan.get("target"), "trail_premium": None,
        "profit_lock_premium": plan.get("profit_lock"),
        "breakeven_premium": plan.get("breakeven"),
        "signal_close_ms": (sig.metrics or {}).get("signal_close_timestamp_ms"),
        "runner_premium": plan.get("runner_trigger"), "outcome": None,
        "lots": lots, "quantity": quantity,
        "deployed_inr": round(premium * quantity, 2) if premium and quantity else None,
        "min_outlay_inr": round(premium * lot, 2) if premium and lot else None,
        "timeframe_minutes": cfg.scalp_timeframe_minutes,
        "max_hold_bars": cfg.scalp_max_hold_bars, "runner_max_bars": cfg.scalp_runner_max_bars,
        "trail_points": cfg.scalp_trail_points, "lock_points": cfg.scalp_lock_points,
        "round_trip_cost_points": plan.get("estimated_variable_cost_points"),
        "fixed_cost_inr": cfg.scalp_fixed_cost_inr,
        "planned_risk_inr": plan.get("estimated_stop_loss_inr"),
        "net_target_inr": plan.get("estimated_net_target_inr"),
        "reasons": list(sig.reasons), "metrics": {**dict(sig.metrics), **plan},
    }

async def scan_once(uid: str, *, record_evidence: bool = False, mode_override: str | None = None) -> dict:
    """One universe pass: daily candles -> signals -> rows.

    This is a board/display scan by default. It must not write authoritative
    prospective evidence. The dedicated post-close prospective scanner owns that
    artifact and captures immutable instrument identity with each signal.

    A symbol that throws is recorded as a failure rather than ending the scan:
    one delisted token must not blank the board.
    """
    cfg = get_effective_config(uid)
    st = status(uid) if mode_override is None else ScanState(config_generation=status(uid).config_generation)
    if mode_override is not None:
        from app.engines.snapback.config import MODE_DEFAULTS
        cfg = replace(cfg, trading_mode=mode_override, **MODE_DEFAULTS.get(mode_override, {}))
    if not cfg.enabled:
        st.rows = []
        st.last_error = "engine disabled"
        return snapshot(uid)
    if st.scanning or (mode_override is None and uid in _interactive_scans and asyncio.current_task() is not _interactive_scans[uid]):
        return snapshot(uid)

    st.scanning = True
    start_gen = st.config_generation
    st.failures = []
    st.last_error = None
    st.rows = [] if cfg.trading_mode != "swing" else st.rows
    # Cleared UP FRONT, not on success: a scan that throws halfway would
    # otherwise leave armed rows behind that nothing has re-checked.
    st.signals = {}
    try:
        from app.services.exchanges.kite import accounts
        from app.services.kite_engine.universe import build_universe, select_scan_universe  # noqa: F401
        acct = accounts.get_active(uid)
        if not acct:
            raise RuntimeError("No active Kite account")
        client = await accounts.acquire_client(acct)
        nfo, bfo, nse, bse = await asyncio.gather(
            client.search_instruments("", "NFO", limit=1_000_000),
            client.search_instruments("", "BFO", limit=1_000_000),
            client.search_instruments("", "NSE", limit=1_000_000),
            client.search_instruments("", "BSE", limit=1_000_000),
        )
        universe = resolve_universe(cfg, nfo=nfo, bfo=bfo, equities=nse + bse)
        from app.engines.snapback.regime import MARKET_SYMBOL, gate_for
        market_gate = None
        if cfg.trading_mode == "swing" and cfg.market_filter != "off":
            index = next((i for i in universe if canonical(i.name) == MARKET_SYMBOL), None)
            if index is None:
                raise UnreadableCandles("NIFTY daily tape is required for the market filter")
            market_bars = to_bars(_drop_forming(await _candles(
                client, st, index.token, index.tradingsymbol)))
            market_gate = gate_for({MARKET_SYMBOL: market_bars},
                                   market_filter=cfg.market_filter, ema_period=cfg.market_ema)
            if not market_gate:
                raise UnreadableCandles("NIFTY daily tape cannot establish the market filter")
        # The latest session whose daily bar is closed, used to separate a live signal
        # from a catch-up row.
        from app.services.navigator.calendar import is_trading_day as _is_trading_day
        latest_closed_session = ist_today()
        for _ in range(10):
            try:
                if _is_trading_day(latest_closed_session):
                    break
            except Exception:
                break
            latest_closed_session = latest_closed_session - timedelta(days=1)

        chain_cache: dict[str, list] = {"NFO": nfo, "BFO": bfo}
        # Kite's historical endpoint is 3 requests/second. Four concurrent
        # fetchers over a 200-name universe spent their budget on 429
        # retries; three sits under the published limit.
        sem = asyncio.Semaphore(3)
        rows: list[dict] = []

        async def one(item) -> None:
            async with sem:
                try:
                    if cfg.trading_mode == "swing":
                        raw = _drop_forming(await _candles(client, st, item.token,
                                                           item.tradingsymbol))
                    else:
                        raw = await _intraday_candles(client, st, item.token,
                                                      item.tradingsymbol, cfg)
                    if not raw:
                        st.failures.append(f"{item.name}: no {cfg.trading_mode} candles")
                        return
                    # Per-symbol indicator math over ~200 names: off the event loop.
                    from app.core.async_work import run_blocking
                    sigs = await run_blocking(
                        lambda: list(evaluate_symbol(raw, cfg, item.name, market_gate=market_gate)))
                    for sig in sigs:
                        if record_evidence:
                            # Explicit opt-in only. Production background/UI scans
                            # keep this false; the post-close prospective scanner is
                            # the sole authoritative writer because it captures the
                            # instrument identity together with the signal.
                            try:
                                from app.services.snapback_authority import (
                                    classify_signal_authority, dataset_start,
                                )
                                from app.services.snapback_prospective_collector import (
                                    SnapbackProspectiveCollector,
                                )

                                verdict = classify_signal_authority(
                                    signal_timestamp_ms=int(
                                        getattr(sig, "timestamp_ms", 0) or 0
                                    ),
                                    latest_closed_session=latest_closed_session,
                                    dataset_start=dataset_start(),
                                )
                                collector = SnapbackProspectiveCollector()
                                collector.record_signal_at_close(
                                    sig, cfg, source=verdict.source
                                )
                            except Exception as collector_exc:
                                log.warning(
                                    "Snapback optional evidence write failed for %s: %s",
                                    item.name,
                                    collector_exc,
                                )

                        blocked = None
                        contract = await _contract_for(
                            client, item.name, item.option_exchange, sig.entry,
                            sig.option_type, sig.assumed_iv, cfg, chain_cache)
                        quote = None
                        if contract is None:
                            blocked = (f"no listed {sig.option_type} between "
                                       f"{cfg.min_dte} and {cfg.max_dte} days out")
                        else:
                            # Read the book whether or not the row can be armed. A
                            # row that cannot arm (spread, hedge) still describes a
                            # real contract, and showing a Black-Scholes number in
                            # its place under the default hedged config would put an
                            # assumption where the operator can see a price.
                            quote = await _quote_for(client, contract, cfg)
                            # The index-futures hedge is NOT a reason to withhold arming:
                            # it is the validated configuration, and the live plan
                            # producer sizes and places it (and refuses when it cannot).
                            # Marking every hedged row "watching" meant the default
                            # swing lane could never produce an armed row at all.
                            # Spreads have no live path, so they stay unarmed.
                            late = (_entry_session_passed(sig)
                                    if cfg.trading_mode == "swing" else None)
                            if late:
                                blocked = late
                            elif cfg.trading_mode == "swing" and cfg.short_leg_delta > 0:
                                blocked = "Spread entries are not placed live"
                            elif quote and quote["blockers"]:
                                blocked = quote["blockers"][0]
                        row = _row(sig, cfg, contract=contract, quote=quote,
                                   blocked=blocked,
                                   underlying_token=int(item.token or 0))
                        if row["state"] == "armed" and not row["lots"]:
                            need = row.get("min_outlay_inr")
                            budget = cfg.capital_inr * cfg.premium_pct_of_capital / 100
                            row["state"] = "watching"
                            row["reason"] = (
                                f"one lot is "
                                f"{('₹%.0f' % need) if need else 'more'} of premium "
                                f"against a ₹{budget:,.0f} budget "
                                f"({cfg.premium_pct_of_capital:g}% of "
                                f"₹{cfg.capital_inr:,.0f})")
                        rows.append(row)
                except Exception as exc:                           # noqa: BLE001
                    st.failures.append(f"{item.name}: {exc}")

        await asyncio.gather(*(one(i) for i in universe))
        if st.config_generation != start_gen:
            st.rows = []
            st.signals = {}
            log.info("%s: config changed during scan; discarding stale scan result", STRATEGY_ID)
            return snapshot(uid)
        rows.sort(key=lambda r: (r["state"] != "armed", -abs(r["stretch"]),
                                 r["symbol"]))
        st.rows = rows
        st.signals = {r["signal_id"]: r for r in rows if r["state"] == "armed"}
        st.scanned = len(universe)
        st.last_scan_ms = ist_now_ms()
        from app.core.async_work import run_blocking
        await run_blocking(_save_last_rows, uid, rows)

        # The interactive scan observes only. Entry, risk and end-of-day processing
        # belong to the autonomous runner: a browser refresh must never advance a
        # position's lifecycle at a time unrelated to the authoritative runtime.
    except Exception as exc:                                       # noqa: BLE001
        st.last_error = str(exc)
        stale_rows = []
        for row in st.rows:
            stale = dict(row)
            stale["state"] = "error"
            stale["stale"] = True
            stale["execution_eligible"] = False
            stale["reason"] = str(exc)
            stale_rows.append(stale)
        st.rows = stale_rows
        st.signals = {}
        log.error("%s scan failed: %s", STRATEGY_ID, exc)
    finally:
        st.scanning = False
    if mode_override is not None:
        return {"rows": st.rows, "scanned": st.scanned, "last_error": st.last_error}
    return snapshot(uid)


def extract_raw_quote_event(contract_id: str, quote_dict: dict) -> Optional[RawQuoteEvent]:
    """Build a genuine RawQuoteEvent from Kite quote dictionary without fallbacks or placeholders."""
    if not quote_dict:
        return None

    stamp = quote_dict.get("timestamp") or quote_dict.get("exchange_timestamp") or quote_dict.get("last_trade_time")
    if not stamp:
        return None
    from app.engines.snapback.models import _epoch_seconds
    ts_sec = _epoch_seconds(stamp)
    if ts_sec is None or not math.isfinite(ts_sec):
        return None
    exchange_ts_ms = int(ts_sec * 1000)

    depth = quote_dict.get("depth") or {}
    buy_list = depth.get("buy") or []
    sell_list = depth.get("sell") or []

    # Strict two-sided depth extraction — NO fallback to last_price!
    best_bid = float((buy_list[0] if buy_list else {}).get("price") or 0.0)
    best_ask = float((sell_list[0] if sell_list else {}).get("price") or 0.0)
    bid_qty = int((buy_list[0] if buy_list else {}).get("quantity") or 0)
    ask_qty = int((sell_list[0] if sell_list else {}).get("quantity") or 0)

    ltp = float(quote_dict.get("last_price") or 0.0)
    oi = int(quote_dict.get("oi") or 0)

    # Every provider-observed level, never a ladder fabricated from the last price.
    def _ladder(levels) -> tuple:
        out = []
        for level in levels or []:
            try:
                price = float(level.get("price") or 0.0)
                quantity = int(level.get("quantity") or 0)
            except (AttributeError, TypeError, ValueError):
                continue
            if price > 0 and quantity > 0:
                out.append(DepthLevel(price=price, quantity=quantity))
        return tuple(out)

    now_ms = int(time.time() * 1000)
    return RawQuoteEvent(
        contract_id=contract_id,
        exchange_timestamp_ms=exchange_ts_ms,
        received_at_ms=now_ms,
        best_bid=best_bid,
        best_ask=best_ask,
        bid_quantity=bid_qty,
        ask_quantity=ask_qty,
        last_price=ltp,
        open_interest=oi,
        bid_depth=_ladder(buy_list),
        ask_depth=_ladder(sell_list),
    )


async def _causal_daily_bars(
    client, symbol: str, upto: date, *, cache: dict, exchange: str = "NSE"
) -> list:
    """Daily candles for `symbol` on `exchange`, truncated at `upto` (inclusive).

    Truncation is what keeps the hedge causal: beta at entry may only use sessions
    that had already closed when the signal fired.

    ``exchange`` is passed rather than assumed. A SENSEX underlying is listed on
    BSE, and searching NSE for it returns nothing — which previously surfaced as
    an empty bar series, indistinguishable from a genuinely quiet instrument.
    """
    venue = str(exchange or "NSE").strip().upper()
    key = quote_key(venue, symbol)
    rows = cache.get(key)
    if rows is None:
        instruments = cache.get(f"__{venue.lower()}__")
        if instruments is None:
            instruments = await client.search_instruments("", venue, limit=1_000_000)
            cache[f"__{venue.lower()}__"] = instruments
        token = 0
        for inst_row in instruments or []:
            if str(getattr(inst_row, "tradingsymbol", "") or (inst_row or {}).get("tradingsymbol", "")) == symbol:
                token = int(getattr(inst_row, "instrument_token", 0) or (inst_row or {}).get("instrument_token", 0) or 0)
                break
        if not token:
            cache[key] = []
            return []
        from app.schemas.instruments import InstrumentMeta
        meta = InstrumentMeta(underlying=symbol, tick_size=0.05, strike_step=1.0,
                              exchange_currency="INR", index_name=symbol,
                              has_options=True, exchange="zerodha",
                              zerodha_token=token)
        rows = await client.get_candles(meta, "1D", LOOKBACK_BARS) or []
        cache[key] = rows

    cutoff = upto.isoformat()
    out = []
    for bar in rows:
        if isinstance(bar, dict):
            ts = bar.get("timestamp_ms") or bar.get("time")
        else:
            ts = getattr(bar, "timestamp_ms", None) or getattr(bar, "time", None)
        if ts is None:
            continue
        try:
            bar_day = datetime.fromtimestamp(float(ts) / 1000.0, tz=_IST).date().isoformat()
        except Exception:
            continue
        if bar_day <= cutoff:
            out.append(bar)
    return out


async def process_prospective_pending_entries(client, cfg: SnapbackConfig) -> int:
    """Phase A: Process pending T+1 entries during opening window (09:15-09:45 IST) or on tick.

    This is an origination boundary, not merely a runner helper.  Any caller that
    hands it a disabled configuration gets zero new positions, even if it bypasses
    the unattended runner's own enabled gate.
    """
    if not bool(getattr(cfg, "enabled", False)):
        return 0

    processed = 0
    try:
        from app.services.snapback_prospective_collector import SnapbackProspectiveCollector, OptionCandidateInfo
        from app.services.navigator.calendar import next_trading_day
        from app.services.kite_engine.futures import pick_futures_contract
        from app.services.kite_engine.strikes import chain_rows_for, filter_chain_by_expiry_types, _expiry_date_set
        collector = SnapbackProspectiveCollector()

        pending = collector.warehouse.get_pending_opportunities()
        if not pending or not client:
            return 0

        chain_cache: dict[str, list] = {}
        daily_bar_cache: dict[str, list] = {}
        for opp in pending:
            opp_id = opp["opportunity_id"]
            symbol = opp["symbol"]
            sig_ts_str = opp.get("signal_timestamp") or opp.get("provider_timestamp") or ""
            if not sig_ts_str:
                continue

            try:
                sig_date = datetime.fromisoformat(sig_ts_str.replace("Z", "+00:00")).date()
                expected_t1_date = next_trading_day(sig_date)
            except Exception:
                continue

            curr_date = datetime.now(_IST).date()
            if curr_date < expected_t1_date:
                # Still Day T close — skip execution until Day T+1 session
                continue

            if curr_date > expected_t1_date:
                collector.warehouse.update_opportunity_status(opp_id, "INCONCLUSIVE")
                collector.warehouse.record_decision(
                    decision_id=f"DECISION-{opp_id}",
                    opportunity_id=opp_id,
                    symbol=symbol,
                    decision="INCONCLUSIVE",
                    reason=f"Missed T+1 entry window (expected next trading session {expected_t1_date}, current date {curr_date})",
                    provider_timestamp=datetime.now(timezone.utc).isoformat(),
                )
                processed += 1
                continue

            # An opportunity fills at most once, ever.
            if collector.warehouse.opportunity_already_filled(opp_id):
                log.debug("Skipping %s: already filled", opp_id)
                continue

            # The opening window must have been observed continuously. A backend that
            # started at 09:40 cannot know whether a qualifying quote existed at 09:18,
            # so it must not fill on the first quote it happens to see.
            session_open_dt = datetime.combine(
                curr_date, dt_time(9, 15)
            ).replace(tzinfo=_IST)
            continuity = session_continuity(
                collector.warehouse,
                session_date=str(curr_date),
                session_open=session_open_dt,
                now=datetime.now(_IST),
            )
            if not continuity.continuous:
                record_attempt(
                    collector.warehouse,
                    opportunity_id=opp_id,
                    session_date=str(curr_date),
                    continuous=False,
                    decision=continuity.reasons[0],
                    reason_codes=continuity.reasons,
                )
                collector.warehouse.update_opportunity_status(opp_id, continuity.reasons[0])
                log.warning(
                    "Skipping T+1 fill for %s: opening window not continuously observed (%s)",
                    opp_id, ", ".join(continuity.reasons),
                )
                processed += 1
                continue

            # Atomically lock opportunity using lease token (guards against duplicate processing)
            locked, lease_token = collector.warehouse.try_lock_pending_opportunity(opp_id)
            if not locked:
                log.debug("Skipping pending opportunity %s: already processing or non-pending", opp_id)
                continue

            try:
                # Address the broker with the identity captured on Day-T. Rebuilding
                # "NSE:{symbol}" is wrong for SENSEX (BSE/BFO) and for any index whose
                # provider symbol differs from its canonical name.
                try:
                    identity = identity_from_opportunity(opp)
                except IdentityError as ident_exc:
                    log.warning("Skipping T+1 fill for %s: %s", opp_id, ident_exc)
                    collector.warehouse.update_opportunity_status(opp_id, "INCONCLUSIVE_IDENTITY")
                    collector.warehouse.record_decision(
                        decision_id=f"DECISION-{opp_id}-IDENTITY-{now_ms_for_decision()}",
                        opportunity_id=opp_id, symbol=symbol,
                        decision="INCONCLUSIVE_IDENTITY", reason=str(ident_exc),
                        provider_timestamp=datetime.now(timezone.utc).isoformat(),
                    )
                    processed += 1
                    continue

                spot_key = identity.cash_quote_key()
                spot_quotes = await client.get_quote([spot_key])
                q_spot_raw = (spot_quotes or {}).get(spot_key) or {}

                # The spot that sizes the hedge is evidence, not a bare float: it must
                # be identified, timestamped and as fresh as the option and futures
                # quotes it will be combined with.
                spot_event = extract_raw_quote_event(identity.cash_tradingsymbol, q_spot_raw)
                spot_decision = (
                    evaluate_underlying_context_quote(spot_event, now_ms=int(time.time() * 1000))
                    if spot_event else None
                )
                record_quote_attempt(
                    collector.warehouse, opportunity_id=opp_id, phase="ENTRY",
                    leg="UNDERLYING", contract_id=identity.cash_tradingsymbol,
                    required_for_economics=True, quote_present=bool(spot_event),
                    bid=None, ask=None,
                    provider_timestamp=str(getattr(spot_event, "exchange_timestamp_ms", "") or ""),
                    age_ms=int(getattr(spot_decision, "quote_age_ms", 0) or 0),
                    accepted=bool(spot_decision and spot_decision.accepted_for_execution),
                    reason_codes=list(getattr(spot_decision, "reason_codes", []) or ["missing_quote"]),
                    symbol=symbol,
                )
                if not spot_decision or not spot_decision.accepted_for_execution:
                    log.warning(
                        "Skipping T+1 fill for %s: INCONCLUSIVE_UNDERLYING_QUOTE (%s)",
                        opp_id, getattr(spot_decision, "reason_codes", ["missing_quote"]),
                    )
                    collector.warehouse.update_opportunity_status(opp_id, "INCONCLUSIVE_UNDERLYING_QUOTE")
                    record_attempt(
                        collector.warehouse, opportunity_id=opp_id,
                        session_date=str(curr_date), continuous=True,
                        decision="INCONCLUSIVE_UNDERLYING_QUOTE",
                        reason_codes=list(getattr(spot_decision, "reason_codes", []) or ["missing_quote"]),
                    )
                    processed += 1
                    continue

                spot_price = float(spot_event.last_price)

                # Resolve option candidates from Kite instrument dump
                option_exchange = identity.option_exchange
                rows = chain_cache.get(option_exchange)
                if rows is None:
                    rows = await client.search_instruments("", option_exchange, limit=1_000_000)
                    chain_cache[option_exchange] = rows

                chain = chain_rows_for(rows or [], symbol, ist_today())
                if not chain:
                    collector.warehouse.update_opportunity_status(opp_id, "NO_FILL")
                    processed += 1
                    continue

                # Filter chain to ONLY verified monthly expiries
                chain = filter_chain_by_expiry_types(chain, ["monthly"], ist_today())
                if not chain:
                    collector.warehouse.update_opportunity_status(opp_id, "NO_FILL")
                    processed += 1
                    continue

                monthly_date_sets = _expiry_date_set(chain, ist_today())

                sig_side = opp.get("signal_side") or ("fade_up" if "FADE_UP" in opp.get("signal_type", "") else "fade_down")
                want_type = "PE" if sig_side == "fade_up" else "CE"
                # The Day-T signal's own assumed_iv, or nothing. Substituting a
                # house vol here silently reprices the whole contract selection:
                # strike, delta and premium all move, and the resulting trade
                # looks like evidence about the frozen rule when it is evidence
                # about our guess. Refuse the opportunity instead.
                raw_signal_iv = opp.get("signal_iv")
                try:
                    iv_proxy = float(raw_signal_iv) if raw_signal_iv is not None else 0.0
                except (TypeError, ValueError):
                    iv_proxy = 0.0
                if not math.isfinite(iv_proxy) or iv_proxy <= 0.0:
                    log.warning(
                        "INCONCLUSIVE_SIGNAL_IV for %s: stored signal_iv is %r; refusing "
                        "to price the chain on a substituted vol",
                        opp_id, raw_signal_iv,
                    )
                    collector.warehouse.update_opportunity_status(opp_id, "INCONCLUSIVE")
                    processed += 1
                    continue

                cand_infos: List[OptionCandidateInfo] = []
                cand_symbols: List[str] = []
                for row in chain:
                    opt_t = str(row.get("option_type") or "").upper()
                    if opt_t.lower() != ("call" if want_type == "CE" else "put") and opt_t != want_type:
                        continue
                    dte = int(row.get("dte") or 0)
                    if not (cfg.min_dte <= dte <= cfg.max_dte):
                        continue
                    strike = float(row.get("strike") or 0.0)
                    if strike <= 0:
                        continue
                    sym_name = str(row.get("instrument_name") or row.get("tradingsymbol") or "")
                    cand_symbols.append(f"{option_exchange}:{sym_name}")

                    cand_d_raw = float(bs_delta(spot_price, strike, dte / 365.0, iv_proxy, call=(want_type == "CE")))
                    cand_d_val = -abs(cand_d_raw) if want_type == "PE" else abs(cand_d_raw)

                    exp_date_str = str(row.get("expiry_date") or "")
                    is_monthly_exp = "monthly" in monthly_date_sets.get(exp_date_str[:10], set())

                    cand_infos.append(OptionCandidateInfo(
                        symbol=sym_name,
                        expiry=exp_date_str,
                        strike=strike,
                        option_type=want_type,
                        dte=dte,
                        is_monthly=is_monthly_exp,
                        theoretical_delta=cand_d_val,
                        provider_symbol=sym_name,
                        instrument_token=str(row.get("token") or ""),
                        lot_size=int(row.get("lot_size") or 0),
                    ))

                if not cand_symbols:
                    collector.warehouse.update_opportunity_status(opp_id, "NO_FILL")
                    processed += 1
                    continue

                opt_quotes_raw = await client.get_quote(cand_symbols)
                opt_quote_events: Dict[str, RawQuoteEvent] = {}
                for c_sym_full in cand_symbols:
                    sym_short = c_sym_full.split(":")[-1]
                    q_raw = (opt_quotes_raw or {}).get(c_sym_full) or {}
                    q_ev = extract_raw_quote_event(sym_short, q_raw)
                    if q_ev:
                        opt_quote_events[sym_short] = q_ev

                # The hedge is selected AFTER the option, and must outlive it. A
                # near-month future can expire while a 40-60 DTE option is still open,
                # which would silently leave the position unhedged.
                candidate_expiries = []
                for cand in cand_infos:
                    try:
                        candidate_expiries.append(date.fromisoformat(str(cand.expiry)[:10]))
                    except Exception:
                        continue
                if not candidate_expiries:
                    collector.warehouse.update_opportunity_status(opp_id, "NO_FILL")
                    processed += 1
                    continue

                try:
                    hedge = select_hedge_future(
                        rows or [], option_expiry=max(candidate_expiries), name="NIFTY",
                    )
                except HedgeContractError as hedge_exc:
                    log.warning("Skipping T+1 fill for %s: %s", opp_id, hedge_exc)
                    collector.warehouse.update_opportunity_status(opp_id, "INCONCLUSIVE_HEDGE_CONTRACT")
                    collector.warehouse.record_decision(
                        decision_id=f"DECISION-{opp_id}-HEDGE-{now_ms_for_decision()}",
                        opportunity_id=opp_id,
                        symbol=symbol,
                        decision="INCONCLUSIVE_HEDGE_CONTRACT",
                        reason=str(hedge_exc),
                        provider_timestamp=datetime.now(timezone.utc).isoformat(),
                    )
                    processed += 1
                    continue

                futures_symbol = hedge.tradingsymbol
                try:
                    venue = venue_from_opportunity(opp)
                except VenueUnknown as venue_exc:
                    log.warning("Skipping T+1 fill for %s: %s", opp_id, venue_exc)
                    collector.warehouse.update_opportunity_status(opp_id, "INCONCLUSIVE_VENUE_IDENTITY")
                    processed += 1
                    continue
                fut_key = venue.derivative_key(futures_symbol)
                fut_quotes = await client.get_quote([fut_key])
                q_fut_raw = (fut_quotes or {}).get(fut_key) or {}
                fut_event = extract_raw_quote_event(futures_symbol, q_fut_raw)
                if not fut_event:
                    log.warning("Skipping T+1 fill for %s: missing genuine futures exchange quote event for %s", opp_id, futures_symbol)
                    collector.warehouse.update_opportunity_status(opp_id, "INCONCLUSIVE")
                    processed += 1
                    continue

                fut_lot_size = int(hedge.lot_size or 0)

                # Causal Beta Calculation: Only NIFTY itself defaults to 1.0; BANKNIFTY/FINNIFTY & stocks compute trailing rolling_beta
                # Beta is persisted as provenance BEFORE it is used, and the entry
                # then uses exactly the persisted number — no second computation.
                sig_date_str = sig_date.strftime("%Y-%m-%d")
                if symbol == "NIFTY":
                    beta_snapshot = canonical_index_beta(
                        symbol=symbol, signal_session=sig_date_str,
                    )
                else:
                    try:
                        stock_candles = await _causal_daily_bars(
                            client, symbol, sig_date, cache=daily_bar_cache,
                            exchange=venue_from_opportunity(opp).cash_exchange,
                        )
                        nifty_candles = await _causal_daily_bars(client, "NIFTY 50", sig_date, cache=daily_bar_cache)
                        if not nifty_candles:
                            nifty_candles = await _causal_daily_bars(client, "NIFTY", sig_date, cache=daily_bar_cache)
                        beta_snapshot = causal_beta_with_provenance(
                            to_bars(stock_candles) if stock_candles else None,
                            to_bars(nifty_candles) if nifty_candles else None,
                            signal_session=sig_date_str, symbol=symbol,
                        )
                    except Exception as beta_exc:
                        log.warning("Failed to compute causal beta for %s on %s: %s", symbol, sig_date, beta_exc)
                        beta_snapshot = causal_beta_with_provenance(
                            None, None, signal_session=sig_date_str, symbol=symbol,
                        )

                try:
                    record_beta_snapshot(
                        collector.warehouse, opportunity_id=opp_id, snapshot=beta_snapshot,
                    )
                except Exception as record_exc:
                    log.warning("Could not persist beta provenance for %s: %s", opp_id, record_exc)

                causal_beta = beta_snapshot.clamped_beta
                if (
                    beta_snapshot.status != "OK"
                    or causal_beta is None
                    or not math.isfinite(causal_beta)
                    or causal_beta <= 0
                ):
                    log.warning(
                        "Skipping T+1 fill for %s: %s (%s)",
                        opp_id, beta_snapshot.status, beta_snapshot.reason_codes,
                    )
                    collector.warehouse.update_opportunity_status(opp_id, "INCONCLUSIVE_CAUSAL_BETA")
                    processed += 1
                    continue

                # Frozen evaluation capital and broker-observed hedge margin. Neither
                # is guessed: without them the entry is INCONCLUSIVE_CAPACITY.
                frozen_capital = float(getattr(cfg, "capital_inr", 0) or 0) or None
                hedge_margin = await observed_hedge_margin(
                    client,
                    tradingsymbol=futures_symbol,
                    quantity=int(fut_lot_size or 0),
                )

                entry_result = collector.execute_pending_entry(
                    opportunity_id=opp_id,
                    cfg=cfg,
                    available_capital=frozen_capital,
                    hedge_margin_observed=hedge_margin,
                    t1_spot_price=spot_price,
                    futures_quote_event=fut_event,
                    futures_symbol=futures_symbol,
                    option_candidates=cand_infos,
                    option_quote_events=opt_quote_events,
                    causal_beta=causal_beta,
                    futures_lot_size=fut_lot_size,
                    execution_timestamp_ms=int(time.time() * 1000),
                    entry_iv=iv_proxy,
                    processing_token=lease_token,
                )

                # Append the resolved attempt: the refusals before a fill are what
                # make "first executable quote" a measurable claim.
                record_attempt(
                    collector.warehouse,
                    opportunity_id=opp_id,
                    session_date=str(curr_date),
                    continuous=True,
                    decision=str((entry_result or {}).get("status") or "UNKNOWN"),
                    reason_codes=[str((entry_result or {}).get("reason") or "")],
                )
                processed += 1

            except Exception as opp_exc:
                log.debug("Pending prospective entry processing failed for %s: %s", opp_id, opp_exc)
                opp_st = collector.warehouse.get_opportunity_by_id(opp_id)
                if opp_st and opp_st.get("status") == "PROCESSING_ENTRY":
                    collector.warehouse.release_locked_opportunity(opp_id, processing_token=lease_token)

    except Exception as exc:
        log.warning("Prospective pending entry cycle skipped: %s", exc)
    return processed


async def process_prospective_daily_mtm_and_exits(client, cfg: SnapbackConfig) -> int:
    """Phase B: Process active open position MTM, rebalances & exit lifecycle from persisted state ledger (strictly idempotent per session)."""
    processed = 0
    try:
        from app.services.snapback_prospective_collector import SnapbackProspectiveCollector
        collector = SnapbackProspectiveCollector()

        active_positions = collector.warehouse.get_active_paper_positions()
        if not active_positions or not client:
            return 0

        now_ms = int(time.time() * 1000)
        session_date = datetime.now(_IST).strftime("%Y-%m-%d")
        venue_cache: dict = {}

        for pos in active_positions:
            if pos.get("status") == "EXIT_PENDING":
                # Exit decision was already latched by the intraday risk monitor.
                # Only process_prospective_intraday_risk() may complete liquidation.
                continue

            opp_id = pos["opportunity_id"]

            # Idempotency guard: if MTM already recorded for (opp_id, session_date), skip duplicate run!
            if collector.warehouse.has_daily_mtm_for_session(opp_id, session_date):
                log.debug("Skipping daily MTM/exit cycle for %s on %s: already recorded for session", opp_id, session_date)
                continue

            symbol = pos["symbol"]
            opt_sym = pos["option_symbol"]
            fut_sym = pos["futures_symbol"]
            try:
                try:
                    venue = resolve_position_venue(collector.warehouse, pos, cache=venue_cache)
                except VenueUnknown as venue_exc:
                    # A position whose venue is unknown cannot be marked at all.
                    # Guessing NSE/NFO here is exactly how a BFO position gets
                    # priced against an instrument that does not exist.
                    log.warning("Skipping EOD MTM for %s: %s", opp_id, venue_exc)
                    continue
                opt_key = venue.derivative_key(opt_sym)
                opt_quotes = await client.get_quote([opt_key])
                q_opt_raw = (opt_quotes or {}).get(opt_key) or {}
                opt_ev = extract_raw_quote_event(opt_sym, q_opt_raw)

                fut_key = venue.derivative_key(fut_sym)
                fut_quotes = await client.get_quote([fut_key])
                q_fut_raw = (fut_quotes or {}).get(fut_key) or {}
                fut_ev = extract_raw_quote_event(fut_sym, q_fut_raw)

                spot_key = venue.spot_key()
                spot_quotes = await client.get_quote([spot_key])
                q_spot_raw = (spot_quotes or {}).get(spot_key) or {}
                curr_spot = float(q_spot_raw.get("last_price") or 0.0)

                if not opt_ev or not fut_ev or curr_spot <= 0 or not math.isfinite(curr_spot):
                    # Record the missing observation before refusing, so coverage can
                    # count it. A refusal that leaves no trace is unmeasurable.
                    for leg, contract, present in (("OPTION", opt_sym, bool(opt_ev)),
                                                   ("FUTURES", fut_sym, bool(fut_ev))):
                        record_quote_attempt(
                            collector.warehouse, opportunity_id=opp_id, phase="EOD_MTM",
                            leg=leg, contract_id=contract, required_for_economics=True,
                            quote_present=present, bid=None, ask=None,
                            provider_timestamp=None, age_ms=None, accepted=False,
                            reason_codes=["missing_quote" if not present else "missing_spot"],
                            symbol=symbol,
                        )
                    log.debug("Skipping daily MTM/exit cycle for %s: missing quotes or spot price", opp_id)
                    continue

                receive_now_ms = int(time.time() * 1000)

                # Revalidate quote quality before MTM, hedge rebalance, or exit using receive wall-clock time
                from app.services.snapback_market_data import evaluate_quote_quality
                q_opt_eval = evaluate_quote_quality(opt_ev, cfg, now_ms=receive_now_ms)
                q_fut_eval = evaluate_quote_quality(fut_ev, cfg, now_ms=receive_now_ms)
                for leg, contract, ev, verdict in (
                    ("OPTION", opt_sym, opt_ev, q_opt_eval),
                    ("FUTURES", fut_sym, fut_ev, q_fut_eval),
                ):
                    record_quote_attempt(
                        collector.warehouse, opportunity_id=opp_id, phase="EOD_MTM",
                        leg=leg, contract_id=contract, required_for_economics=True,
                        quote_present=True, bid=ev.best_bid, ask=ev.best_ask,
                        provider_timestamp=str(getattr(ev, "exchange_timestamp_ms", "") or ""),
                        age_ms=int(getattr(verdict, "age_ms", 0) or 0),
                        accepted=bool(verdict.accepted_for_execution),
                        reason_codes=list(getattr(verdict, "reason_codes", []) or []),
                        symbol=symbol,
                    )

                if not q_opt_eval.accepted_for_execution or not q_fut_eval.accepted_for_execution:
                    log.warning("Skipping MTM/exit cycle for %s: quote quality evaluation rejected", opp_id)
                    continue

                # Observability only: a real quote passed execution-quality validation.
                record_cycle("market_data")

                opt_type_str = "PE" if "PE" in opt_sym else "CE"
                opt_strike_val = float(pos.get("option_strike") or 0.0)
                entry_dte_val = int(pos.get("entry_dte") or 45)
                entry_ts_str = str(pos.get("entry_timestamp") or "")
                try:
                    entry_date_val = datetime.fromisoformat(entry_ts_str.replace("Z", "+00:00")).date()
                    days_elapsed = max(0, (datetime.now(_IST).date() - entry_date_val).days)
                except Exception:
                    days_elapsed = 1
                rem_dte = max(1, entry_dte_val - days_elapsed)
                # This vol produces curr_opt_delta, which sizes the hedge. A
                # substituted 0.20 therefore mis-sizes a real hedge on an open
                # position, so an unknown entry vol must stop the rebalance
                # rather than guess through it.
                raw_entry_iv = pos.get("entry_iv")
                try:
                    curr_iv = float(raw_entry_iv) if raw_entry_iv is not None else 0.0
                except (TypeError, ValueError):
                    curr_iv = 0.0
                if not math.isfinite(curr_iv) or curr_iv <= 0.0:
                    log.warning(
                        "INCONCLUSIVE_ENTRY_IV for %s: stored entry_iv is %r; refusing to "
                        "rebalance a hedge sized on a substituted vol",
                        opp_id, raw_entry_iv,
                    )
                    processed += 1
                    continue
                curr_d_raw = float(bs_delta(curr_spot, opt_strike_val, rem_dte / 365.0, curr_iv, call=(opt_type_str == "CE")))
                curr_opt_delta = -abs(curr_d_raw) if opt_type_str == "PE" else abs(curr_d_raw)

                # Execute MTM, hedge rebalancing, and frozen exit rule evaluation
                mtm_res = collector.rebalance_and_mtm(
                    opportunity_id=opp_id,
                    session_date=session_date,
                    symbol=symbol,
                    current_spot=curr_spot,
                    current_option_delta=curr_opt_delta,
                    option_bid=opt_ev.best_bid,
                    futures_quote_event=fut_ev,
                    option_entry_price=float(pos["option_entry_price"]),
                    option_quantity=int(pos["option_qty"]),
                    current_futures_lots=int(pos["current_futures_lots"]),
                    futures_lot_size=int(pos["futures_lot_size"]),
                    causal_beta=float(pos["causal_beta"]),
                    prior_realized_futures_pnl=float(pos["realized_futures_pnl"]),
                    prior_avg_futures_entry_price=float(pos["avg_futures_entry_price"]),
                    option_quote_event=opt_ev,
                    cfg=cfg,
                )

                exit_reason = mtm_res.get("exit_reason")
                should_close = bool(exit_reason)
                if not should_close and runner_should_exit_for_expiry(
                    remaining_calendar_days=rem_dte
                ):
                    # Canonical buffer: RUNNER_EXPIRY_BUFFER_CALENDAR_DAYS, shared with
                    # the backtest rather than re-stated here.
                    should_close = True
                    exit_reason = "EXPIRY_APPROACHING"

                if should_close:
                    collector.close_opportunity(
                        opportunity_id=opp_id,
                        symbol=symbol,
                        exit_reason=exit_reason,
                        entry_ts=entry_ts_str,
                        exit_ts=datetime.now(timezone.utc).isoformat(),
                        entry_spot=float(pos.get("entry_spot") or curr_spot),
                        exit_spot=curr_spot,
                        selected_strike=opt_strike_val,
                        entry_dte=entry_dte_val,
                        exit_dte=rem_dte,
                        iv_proxy=curr_iv,
                        option_entry_price=float(pos["option_entry_price"]),
                        option_exit_bid=opt_ev.best_bid,
                        futures_entry_price=float(pos.get("avg_futures_entry_price") or fut_ev.best_bid),
                        futures_exit_bid=fut_ev.best_bid,
                        option_quantity=int(pos["option_qty"]),
                        futures_quantity=int(pos["current_futures_lots"]) * int(pos.get("futures_lot_size") or 65),
                        nifty_futures_entry=float(pos.get("avg_futures_entry_price") or fut_ev.best_bid),
                        nifty_futures_exit=fut_ev.best_bid,
                        option_type=opt_type_str,
                        accumulated_costs=float(mtm_res.get("accumulated_costs") or pos.get("accumulated_costs") or 0.0),
                    )
                processed += 1
            except Exception as mtm_exc:
                log.debug("Prospective MTM update failed for %s: %s", opp_id, mtm_exc)

    except Exception as exc:
        log.warning("Prospective MTM cycle skipped: %s", exc)
    return processed


async def process_prospective_intraday_risk(client, cfg: SnapbackConfig) -> int:
    """Phase B: Intraday Risk Monitor (Market hours 09:15-15:30 IST).
    
    Monitors active positions on fresh market quotes for premium stop and runner giveback rules.
    Does NOT write daily MTM, perform daily hedge rebalance, or increment session count.
    Once an intraday exit rule triggers, the position is immediately closed in the ledger.
    """
    processed = 0
    try:
        from app.services.snapback_prospective_collector import SnapbackProspectiveCollector
        from app.services.snapback_market_data import evaluate_quote_quality
        collector = SnapbackProspectiveCollector()

        active_positions = collector.warehouse.get_active_paper_positions()
        if not active_positions or not client:
            return 0

        receive_now_ms = int(time.time() * 1000)
        venue_cache: dict = {}

        for pos in active_positions:
            opp_id = pos["opportunity_id"]
            symbol = pos["symbol"]
            opt_sym = pos["option_symbol"]
            fut_sym = pos["futures_symbol"]
            try:
                try:
                    venue = resolve_position_venue(collector.warehouse, pos, cache=venue_cache)
                except VenueUnknown as venue_exc:
                    log.warning("Skipping intraday risk for %s: %s", opp_id, venue_exc)
                    continue
                is_already_pending = (pos.get("status") == "EXIT_PENDING") or bool(pos.get("pending_exit_reason"))
                if is_already_pending:
                    exit_reason = pos.get("pending_exit_reason") or "PREMIUM_STOP"
                    curr_bid = float(pos.get("pending_exit_option_bid") or pos.get("option_entry_price"))
                    curr_spot = float(pos.get("entry_spot") or 0.0)
                    try:
                        spot_key = venue.spot_key()
                        spot_quotes = await client.get_quote([spot_key])
                        q_spot_raw = (spot_quotes or {}).get(spot_key) or {}
                        spot_val = float(q_spot_raw.get("last_price") or 0.0)
                        if spot_val > 0 and math.isfinite(spot_val):
                            curr_spot = spot_val
                    except Exception:
                        pass
                else:
                    opt_key = venue.derivative_key(opt_sym)
                    opt_quotes = await client.get_quote([opt_key])
                    q_opt_raw = (opt_quotes or {}).get(opt_key) or {}
                    opt_ev = extract_raw_quote_event(opt_sym, q_opt_raw)

                    spot_key = venue.spot_key()
                    spot_quotes = await client.get_quote([spot_key])
                    q_spot_raw = (spot_quotes or {}).get(spot_key) or {}
                    curr_spot = float(q_spot_raw.get("last_price") or 0.0)

                    if not opt_ev or curr_spot <= 0 or not math.isfinite(curr_spot):
                        continue

                    q_opt_eval = evaluate_quote_quality(opt_ev, cfg, now_ms=receive_now_ms)
                    record_quote_attempt(
                        collector.warehouse, opportunity_id=opp_id, phase="INTRADAY_RISK",
                        leg="OPTION", contract_id=opt_sym, required_for_economics=True,
                        quote_present=True, bid=opt_ev.best_bid, ask=opt_ev.best_ask,
                        provider_timestamp=str(getattr(opt_ev, "exchange_timestamp_ms", "") or ""),
                        age_ms=int(getattr(q_opt_eval, "age_ms", 0) or 0),
                        accepted=bool(q_opt_eval.accepted_for_execution),
                        reason_codes=list(getattr(q_opt_eval, "reason_codes", []) or []),
                        symbol=symbol,
                    )
                    if not q_opt_eval.accepted_for_execution:
                        continue

                    # Observability only: a real quote passed execution-quality validation.
                    record_cycle("market_data")

                    entry_price = float(pos["option_entry_price"])
                    curr_bid = opt_ev.best_bid
                    is_runner = bool(pos.get("is_runner"))
                    exit_reason = None

                    if is_runner:
                        # The trail is measured against the stored end-of-day peak. An
                        # intraday tick may trigger the give-back but must never raise
                        # the peak, or the trail ratchets against the position.
                        peak_bid = float(pos.get("peak_option_bid") or entry_price)
                        runner_trail_pct = float(getattr(cfg, "runner_trail_pct", 25.0) or 25.0)
                        trail_price = peak_bid * (1.0 - runner_trail_pct / 100.0)
                        if curr_bid <= trail_price:
                            exit_reason = "RUNNER_TRAIL_STOP"
                    else:
                        stop_price = entry_price * (1.0 - cfg.premium_stop_pct / 100.0)
                        if curr_bid <= stop_price:
                            exit_reason = "PREMIUM_STOP"

                    if exit_reason:
                        # LATCH: immediately persist EXIT_PENDING status so position can NEVER recover to OPEN strategy evaluation
                        now_iso = datetime.now(timezone.utc).isoformat()
                        collector.warehouse.set_paper_position_pending_exit(
                            opportunity_id=opp_id,
                            pending_exit_reason=exit_reason,
                            pending_exit_option_bid=curr_bid,
                            pending_exit_ts=now_iso,
                        )
                        pos["status"] = "EXIT_PENDING"
                        pos["pending_exit_reason"] = exit_reason
                        pos["pending_exit_option_bid"] = curr_bid

                if exit_reason:
                    # Fetch live futures quote and validate quote quality before completing intraday exit
                    futures_exit_bid = float(pos.get("avg_futures_entry_price") or 0.0)
                    if int(pos.get("current_futures_lots") or 0) > 0 and fut_sym:
                        try:
                            fut_key = venue.derivative_key(fut_sym)
                            fut_quotes = await client.get_quote([fut_key])
                            q_fut_raw = (fut_quotes or {}).get(fut_key) or {}
                            fut_ev = extract_raw_quote_event(fut_sym, q_fut_raw)
                            q_fut_eval = evaluate_quote_quality(fut_ev, cfg, now_ms=receive_now_ms) if fut_ev else None
                            if not fut_ev or not q_fut_eval or not q_fut_eval.accepted_for_execution or fut_ev.best_bid <= 0:
                                log.warning("Intraday exit pending for %s (%s): missing or invalid futures exit quote for %s", opp_id, exit_reason, fut_sym)
                                processed += 1
                                continue
                            futures_exit_bid = fut_ev.best_bid
                        except Exception as fut_exc:
                            log.warning("Failed to fetch live futures exit quote for %s (%s): %s", opp_id, exit_reason, fut_exc)
                            processed += 1
                            continue

                    opt_type_str = "PE" if "PE" in opt_sym else "CE"
                    opt_strike_val = float(pos.get("option_strike") or 0.0)
                    entry_dte_val = int(pos.get("entry_dte") or 45)
                    entry_ts_str = str(pos.get("entry_timestamp") or "")

                    # Remaining DTE at the actual intraday exit (counterfactual diagnostic only)
                    try:
                        expiry_date = date.fromisoformat(str(pos.get("option_expiry") or "")[:10])
                        today_ist = datetime.now(_IST).date()
                        exit_dte = max(0, (expiry_date - today_ist).days)
                    except Exception:
                        exit_dte = entry_dte_val

                    # The vol this position was actually entered at. A hardcoded
                    # 0.20 here would price the exit diagnostics on a house vol
                    # and present the result as observed economics.
                    raw_entry_iv = pos.get("entry_iv")
                    try:
                        exit_iv_proxy = float(raw_entry_iv) if raw_entry_iv is not None else 0.0
                    except (TypeError, ValueError):
                        exit_iv_proxy = 0.0
                    if not math.isfinite(exit_iv_proxy) or exit_iv_proxy <= 0.0:
                        log.warning(
                            "INCONCLUSIVE_ENTRY_IV for %s (%s): stored entry_iv is %r; "
                            "refusing to close on a substituted vol",
                            opp_id, exit_reason, raw_entry_iv,
                        )
                        processed += 1
                        continue

                    collector.close_opportunity(
                        opportunity_id=opp_id,
                        symbol=symbol,
                        exit_reason=exit_reason,
                        entry_ts=entry_ts_str,
                        exit_ts=datetime.now(timezone.utc).isoformat(),
                        entry_spot=float(pos.get("entry_spot") or curr_spot),
                        exit_spot=curr_spot,
                        selected_strike=opt_strike_val,
                        entry_dte=entry_dte_val,
                        exit_dte=exit_dte,
                        iv_proxy=exit_iv_proxy,
                        option_entry_price=float(pos["option_entry_price"]),
                        option_exit_bid=curr_bid,
                        futures_entry_price=float(pos.get("avg_futures_entry_price") or 0.0),
                        futures_exit_bid=futures_exit_bid,
                        option_quantity=int(pos["option_qty"]),
                        futures_quantity=int(pos["current_futures_lots"]) * int(pos.get("futures_lot_size") or 65),
                        nifty_futures_entry=float(pos.get("avg_futures_entry_price") or 0.0),
                        nifty_futures_exit=futures_exit_bid,
                        option_type=opt_type_str,
                        accumulated_costs=float(pos.get("accumulated_costs") or 0.0),
                    )
                    log.info("Intraday risk monitor closed %s due to %s (futures_exit_bid=%.2f)", opp_id, exit_reason, futures_exit_bid)
                processed += 1
            except Exception as r_exc:
                log.debug("Intraday risk check failed for %s: %s", opp_id, r_exc)
    except Exception as exc:
        log.warning("Intraday risk monitor cycle skipped: %s", exc)
    return processed


def now_ms_for_decision() -> int:
    return int(time.time() * 1000)


def _deprecated(fn):
    fn.__deprecated__ = True
    return fn


@_deprecated
async def process_prospective_pending_entries_and_mtm(client, cfg: SnapbackConfig) -> None:
    """DEPRECATED: never call from an interactive path.

    Combining the entry and end-of-day phases behind one call is what allowed a UI
    scan to mutate the position lifecycle. The autonomous runner calls the phases
    individually, each in its own window.
    """
    """Master entrypoint: process pending entries followed by intraday risk and daily MTM."""
    await process_prospective_pending_entries(client, cfg)
    await process_prospective_intraday_risk(client, cfg)
    await process_prospective_daily_mtm_and_exits(client, cfg)


def _crossed_session_close(fetched_wall: float, now_wall: float) -> bool:
    """Whether a 15:30 IST session close fell between a fetch and now.

    A daily tape fetched during the session ends in a FORMING bar. Once the
    clock passes the close, ``closed_daily_candles`` treats that bar as final,
    so a cached copy would hand the rule a half-finished close as the day's.
    """
    if fetched_wall <= 0:
        return True            # no fetch time on record: cannot prove it was after the close
    start = datetime.fromtimestamp(fetched_wall, _IST)
    end = datetime.fromtimestamp(now_wall, _IST)
    day = start.date()
    while day <= end.date():
        close = datetime.combine(day, dt_time(15, 30), tzinfo=_IST)
        if start < close <= end:
            return True
        day += timedelta(days=1)
    return False


def _candle_ttl_s(fetched_wall: float) -> float:
    """How long a daily tape read at ``fetched_wall`` may be reused."""
    if fetched_wall <= 0:
        return 0.0
    read = datetime.fromtimestamp(fetched_wall, _IST)
    close = datetime.combine(read.date(), dt_time(15, 30), tzinfo=_IST)
    if close <= read < close + _POST_CLOSE_SETTLE:
        return _CANDLE_TTL_S
    return _CANDLE_LONG_TTL_S


async def _candles(client, st: ScanState, token: int, name: str) -> list:
    hit = st.candles.get(token)
    now_wall = _wall_now().timestamp()
    fetched_wall = st.candle_wall.get(token, 0.0)
    if (hit and (time.monotonic() - hit[0]) < _candle_ttl_s(fetched_wall)
            and not _crossed_session_close(fetched_wall, now_wall)):
        return hit[1]
    from app.schemas.instruments import InstrumentMeta
    inst = InstrumentMeta(underlying=name, tick_size=0.05, strike_step=1.0,
                          exchange_currency="INR", index_name=name,
                          has_options=True, exchange="zerodha",
                          zerodha_token=int(token))
    rows = await client.get_candles(inst, "1D", LOOKBACK_BARS)
    st.candles[token] = (time.monotonic(), rows)
    st.candle_wall[token] = now_wall
    return rows


async def _intraday_candles(client, st: ScanState, token: int, name: str,
                           cfg: SnapbackConfig) -> list:
    resolution = f"{cfg.scalp_timeframe_minutes}m"
    key = (token, resolution)
    hit = st.candles.get(key)
    if hit and time.monotonic() - hit[0] < 5.0:
        return hit[1]
    from app.schemas.instruments import InstrumentMeta
    inst = InstrumentMeta(underlying=name, tick_size=0.05, strike_step=1.0,
                          exchange_currency="INR", index_name=name,
                          has_options=True, exchange="zerodha", zerodha_token=int(token))
    rows = await client.get_candles(inst, resolution, 400)
    st.candles[key] = (time.monotonic(), rows)
    return rows


HISTORY_SESSIONS = 30
_history_cache: dict[str, tuple[float, list[dict]]] = {}
_HISTORY_TTL_S = 300.0


def recent_signals(uid: str, *, sessions: int = HISTORY_SESSIONS) -> list[dict]:
    """What this engine fired over the last few weeks, from STORED bars.

    Works with the market closed, with no broker session, and before any live
    scan has run. It exists for the same reason the intraday pack's does: every
    rule here fires on ONE session and the live scan only looks at the last
    three, so the board is blank almost always — and a blank board cannot be
    told apart from a broken one.

    The contract is ESTIMATED: the strike is computed from the instrument's
    published step and the expiry is left unknown rather than guessed. Every row
    carries ``historical: True`` so nothing downstream can mistake a replayed
    signal for a live one.
    """
    cfg = get_effective_config(uid)
    if cfg.trading_mode != "swing":
        # Daily modelled outcomes must never appear as intraday performance.
        # Observed-option research results are returned by option-tape replay.
        return []
    key = f"{uid}:{sessions}:{ist_today()}:{json.dumps(cfg.as_dict(), sort_keys=True)}"
    hit = _history_cache.get(key)
    if hit and (time.monotonic() - hit[0]) < _HISTORY_TTL_S:
        return hit[1]

    from app.engines.snapback import entry_indices, features, ist_day, Bars
    from app.engines.snapback.regime import MARKET_SYMBOL, gate_for
    from app.services import ohlcv_store
    import numpy as np

    def tape(symbol: str) -> Optional[Bars]:
        rows = _drop_forming(ohlcv_store.get_candles(symbol, "1d", limit=900))
        if not rows or len(rows) < cfg.warmup_bars() + 2:
            return None
        a = np.array([[r["time"], r["open"], r["high"], r["low"], r["close"],
                       r.get("volume", 0.0)] for r in rows], dtype=float)
        return Bars(a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5])

    selected_names = {canonical(n) for n in _history_universe(cfg)}
    names = sorted(selected_names | {MARKET_SYMBOL})
    tapes: dict[str, Bars] = {}
    for n in names:
        b = tape(n)
        if b is not None:
            tapes[n] = b
    if not tapes:
        _history_cache[key] = (time.monotonic(), [])
        return []

    gate = gate_for(tapes, market_filter=cfg.market_filter,
                    ema_period=cfg.market_ema)
    market = tapes.get(MARKET_SYMBOL)
    if cfg.market_filter != "off" and not gate:
        raise UnreadableCandles("NIFTY daily tape cannot establish the history market filter")
    if cfg.hedge_mode != "none" and (market is None or not len(market)):
        raise UnreadableCandles("NIFTY daily tape is required for the configured hedge")
    cutoff = ""
    if market is not None and len(market) > sessions:
        cutoff = ist_day(float(market.time[-int(sessions)]))

    # The outcome is computed by the SAME replay the backtest uses, so a
    # history row's exit and a measured trade's exit cannot come from two
    # different rules. Without it the row's exit, result and LTP columns are all
    # empty, which reads as missing data rather than as a closed trade.
    from dataclasses import replace as _replace
    from app.engines.snapback.backtest import replay
    # The position cap is LIFTED for this view, deliberately. The board is
    # showing what each SIGNAL did, not which of them a five-position book would
    # have had room for — with the cap on, 73% of candidates are dropped and
    # their rows come back with an empty exit, which reads as missing data
    # rather than as "the book was full". What a capped portfolio actually earns
    # is the walk-forward harness's question, and it answers it separately.
    # ONE LOT, and the cap lifted. Both for the same reason: this view is what
    # each SIGNAL did, not what the operator's account could afford or what a
    # five-position book had room for. With the account's own premium budget
    # applied, every outcome came back empty — the sizer refused all 881 of
    # them at 2% of a lakh — and an empty exit column reads as missing data.
    book = replay(tapes, _replace(cfg, max_open_positions=10 ** 6,
                                  one_position_per_underlying=False,
                                  sizing_mode="LOTS", lots=1))
    done = {(t.symbol, t.entry_day): t for t in book.trades}

    out: list[dict] = []
    for sym, bars in tapes.items():
        if sym not in selected_names:
            continue
        f = features(bars, cfg)
        for side in cfg.sides():
            for i in entry_indices(bars, cfg, side, gate):
                day = ist_day(float(bars.time[int(i)]))
                if cutoff and day < cutoff:
                    continue
                for sig in evaluate_at(bars, cfg, int(i), f):
                    if sig.side != side:
                        continue
                    named = _named(sig, sym)
                    row = _row(named, cfg, contract=_estimated(sig, sym, cfg),
                               quote=None, blocked=None, underlying_token=0)
                    # The trade is keyed on the FILL session, one bar after the
                    # signal — the same convention ``Trade.entry_day`` uses.
                    fill_day = (ist_day(float(bars.time[int(i) + 1]))
                                if int(i) + 1 < len(bars) else day)
                    t = done.get((canonical(sym), fill_day))
                    if t is None:
                        # A signal the replay did not trade is NOT a closed
                        # position. Rendering it as one — state "ended", every
                        # outcome column empty — is what "no targets, no exited,
                        # no LTP, nothing" looked like from the board.
                        row["state"] = "watching"
                        row["reason"] = book.untraded.get(
                            (canonical(sym), fill_day),
                            # The newest signal has no fill session yet: this
                            # engine fills at the NEXT session's open, so a
                            # signal on the last stored bar has nothing to
                            # replay. It is the most recent row on the board,
                            # so the message is worth getting right.
                            "signalled on the last stored session — the fill is "
                            "the next session's open"
                            if int(i) + 1 >= len(bars) else
                            book.skipped.get("NIFTY", "the replay did not take this signal"))
                        row["historical"] = True
                        out.append(row)
                        continue
                    # ``tape_ended`` is a trade that is STILL OPEN, not
                    # one that closed: the replay sets it only when the tape
                    # ran out before any exit rule fired. Calling it ended
                    # would put a realised exit on a position the operator
                    # may be holding right now.
                    still_open = t.reason == "tape_ended"
                    row["outcome"] = {
                        "open": bool(still_open),
                        "exit_premium": round(t.fill_out, 2),
                        "entry_premium": round(t.fill_in, 2),
                        "exit_day": t.exit_day,
                        "exit_ms": t.exit_ms,
                        "reason": t.reason,
                        "held_days": t.held_days,
                        "net": round(t.net, 2),
                        "return_pct": round(t.ret * 100, 1),
                        "spot_out": round(t.spot_out, 2),
                        "sessions_left": max(cfg.hold_days - t.held_days, 0),
                        # What the hedge did, kept separate from the net so
                        # a reader can see whether a result came from the
                        # edge or from the hedge being on the right side.
                        "beta": t.beta or None,
                        "market_pnl": round(t.market_pnl, 2) if t.beta else None,
                        "hedge_cost": round(t.hedge_cost, 2) if t.beta else None,
                        "net_unhedged": round(t.gross_unhedged, 2) if t.beta else None,
                    }
                    # These prices belong to the strike chosen at the next OPEN,
                    # which can differ from the indicative signal-close contract.
                    from app.engines.snapback.backtest import _value
                    from app.engines.snapback.pricing import bs_delta
                    row["contract"] = row["contract"] or {"underlying": sym, "option_type": t.option_type,
                                                             "lot_size": t.qty // t.lots, "expiry": None}
                    row["contract"].update(
                        strike=t.strike, short_strike=t.short_strike,
                        symbol=f"{sym} {t.strike:g} {t.option_type}" +
                               (f" / short {t.short_strike:g} {t.option_type}" if t.short_strike else ""),
                        premium=t.premium_in, dte=t.dte_in, modelled=True,
                        moneyness=moneyness_label(t.spot_in, t.strike, t.option_type == "CE"),
                        delta=float(bs_delta(t.spot_in, t.strike, t.dte_in / 365, t.iv,
                                             call=t.option_type == "CE")))
                    row["lots"], row["quantity"] = t.lots, t.qty
                    row["deployed_inr"] = round(t.fill_in * t.qty, 2)
                    row["min_outlay_inr"] = round(t.fill_in * (t.qty // t.lots), 2)
                    row["modelled_premium"] = round(t.premium_in, 2)
                    row["target_premium"] = round(_value(
                        sig.mean_target, t.strike, t.short_strike,
                        max(t.dte_in - cfg.hold_days, 1) / 365, t.iv,
                        t.option_type == "CE", cfg.smile_slope, t.spot_in, cfg.smile_itm_slope), 2)
                    row["trail_premium"] = (round(t.fill_in * (1 - cfg.premium_trail_pct / 100), 2)
                                            if cfg.premium_trail_pct else None)
                    row["runner_premium"] = round(t.fill_in * cfg.runner_mult, 2) if cfg.runner_mult else None
                    # A closed trade's "last price" IS what it closed at.
                    row["premium"] = round(t.fill_in, 2)
                    row["stop_premium"] = round(
                        t.fill_in * (1 - cfg.premium_stop_pct / 100), 2)
                    o = row["outcome"]
                    row["state"] = "running" if (o and o["open"]) else "ended"
                    row["historical"] = True
                    row["reason"] = None
                    out.append(row)
    out.sort(key=lambda r: (-r["timestamp_ms"], r["symbol"]))
    _history_cache[key] = (time.monotonic(), out)
    return out


def _history_universe(cfg: SnapbackConfig) -> tuple[str, ...]:
    """Names to replay. In ``fno`` mode the store decides, because a name with
    no stored tape has no history to show whatever the dump says."""
    if cfg.universe_mode == "curated":
        return cfg.universe()
    from app.engines.option_contracts import spec_for
    from app.services import ohlcv_store
    try:
        rows = ohlcv_store.get_status()
    except Exception:                                              # noqa: BLE001
        return cfg.universe()
    names = {str(r.get("symbol")) for r in rows
             if str(r.get("resolution")) == "1d"}
    indices = tuple(canonical(n) for n in cfg.scan_indices if spec_for(canonical(n)) is not None)
    stocks = tuple(sorted(n for n in names if spec_for(n) is not None and not spec_for(n).is_index))
    selected = indices + (stocks[:max(int(cfg.max_universe), 1)] if cfg.scan_stock_contracts else ())
    return tuple(dict.fromkeys(selected)) or cfg.universe()


def _named(sig, symbol: str):
    from dataclasses import replace as _replace
    return _replace(sig, symbol=canonical(symbol))


def _estimated(sig, symbol: str, cfg: SnapbackConfig) -> Optional[dict]:
    """The contract a replayed signal would have bought, as far as it is known.

    The expiry is None ON PURPOSE. It is determined by a calendar this function
    does not have, and a date computed from a rule would be wrong for part of
    the history it is labelling.
    """
    from app.engines.snapback.contracts import pick_for
    pick = pick_for(_named(sig, symbol), cfg, dte=cfg.min_dte)
    if pick is None:
        return None
    d = pick.as_dict()
    d["expiry"] = None
    return d


def clear_history_cache() -> None:
    _history_cache.clear()


#: A cached row's quote may be this old before the row stops claiming to be
#: armed. The board loop rescans every 15 minutes, so two missed cycles for the
#: daily lane; the minute-bar lane's books are meaningless after two minutes.
_ROW_QUOTE_MAX_AGE_S = {"swing": 1800.0, "short": 120.0}


def _aged_rows(rows: list[dict], cfg: SnapbackConfig, *, conditions=None,
               judge: bool = False) -> list[dict]:
    """Rows as served: honest about how old their quote is and about whether a
    click would be accepted.

    Rows live in memory between scans, and the API serves them as they were
    written. A quote read twenty minutes ago would otherwise sit under a green
    "armed" state, because nothing re-checked it after the scan returned.

    ``execution_eligible`` is never copied from the cached row: it is decided here,
    now, by the same function the plan producer uses, from live conditions (kill
    switch, safe mode, recovery state, daily-loss halt, health, the session) and the
    row's own freshness. ``judge`` is False for callers that only want the aging.
    """
    now_ms = ist_now_ms()
    limit = _ROW_QUOTE_MAX_AGE_S["swing" if cfg.trading_mode == "swing" else "short"]
    out: list[dict] = []
    for r in rows:
        row = {**r, "execution_eligible": False}
        q = row.get("quote") or {}
        ref = q.get("quote_ts_ms") or q.get("observed_ms")
        if ref:
            age = max(0.0, (now_ms - int(ref)) / 1000.0)
            row["quote_age_s"] = round(age, 1)
            if age > limit:
                row["stale"] = True
                if row.get("state") == "armed":
                    row["state"] = "watching"
                    row["reason"] = (f"quote is {age / 60:.0f} min old; "
                                     "rescan before acting")
        if judge:
            from app.services.snapback_readiness import row_execution_state

            eligible, why = row_execution_state(row, cfg, conditions)
            if row.get("restored"):
                # From before the last restart: show it, never act on it.
                eligible = False
                why = ["restored_from_last_scan", *why]
                if row.get("state") == "armed":
                    row["state"] = "watching"
                    row["reason"] = "from the last scan before a restart; rescanning now"
            row["execution_eligible"] = bool(eligible)
            row["execution_block_reasons"] = why[:6]
            from app.services.snapback_readiness import reason_text
            row["execution_block_reason"] = "; ".join(reason_text(code) for code in why[:6]) or None
        out.append(row)
    return out


def _live_capabilities(uid: str, cfg: SnapbackConfig, conditions) -> dict:
    """What the live path can do under THIS configuration right now, from real state."""
    from app.services.snapback_plan_producer import lane_for, unsupported_config_reasons
    from app.services.snapback_readiness import reason_text

    unsupported = unsupported_config_reasons(cfg)
    lane = lane_for(cfg)
    supported = lane is not None and not unsupported
    blockers = list(conditions.blockers) if conditions is not None else ["conditions_unreadable"]
    session = list(conditions.session) if conditions is not None else []
    reasons = [*unsupported, *blockers]
    manual_ok = supported and not blockers
    auto_ok = manual_ok
    hedged = supported and lane == "swing" and cfg.hedge_mode == "index_futures"
    if reasons:
        auto_reason = "; ".join(reason_text(r) for r in reasons[:3])
    elif session:
        auto_reason = "; ".join(reason_text(r) for r in session[:2])
    elif not cfg.auto_execute:
        auto_reason = "Automatic entry is available; switch AUTO on to use it."
    else:
        auto_reason = ""
    manual_reason = "; ".join(reason_text(r) for r in reasons[:3]) if not manual_ok else ""
    return {
        "auto_execution": {
            "available": bool(auto_ok),
            "enabled": bool(cfg.auto_execute),
            "reason": auto_reason,
            "blockers": reasons,
            "session": session,
        },
        "manual_execution": {
            "single_leg": bool(manual_ok),
            "spread": False,
            "hedge": bool(manual_ok and hedged),
            "protection_lifecycle": bool(supported),
            "reason": manual_reason,
        },
    }


def snapshot(uid: str) -> dict:
    """Config, what the last scan found, and every reason nothing is armed."""
    cfg = get_effective_config(uid)
    st = status(uid)
    conditions = None
    try:
        from app.services.snapback_readiness import live_conditions

        conditions = live_conditions(uid, cfg)
    except Exception as exc:                                       # noqa: BLE001
        log.warning("%s: live conditions unreadable: %s", STRATEGY_ID, exc)
    rows = _aged_rows(st.rows, cfg, conditions=conditions, judge=True) + getattr(st, "preview_rows", [])
    armed = [r for r in rows if r.get("state") == "armed"]
    from app.services.snapback_validation import auto_execution_blocker, evidence_advisory
    live = _live_capabilities(uid, cfg, conditions)
    capabilities = {
        "live_scan": True,
        "historical_model": cfg.trading_mode == "swing",
        "replay": cfg.trading_mode == "swing",
        "option_tape_replay": cfg.trading_mode != "swing",
        **live,
    }
    dispatcher_state: dict = {}
    positions: list[dict] = []
    positions_error = None
    try:
        from app.services.snapback_dispatcher import get_dispatcher

        dispatcher_state = get_dispatcher().status(uid)
    except Exception:                                              # noqa: BLE001
        pass
    try:
        from app.services.snapback_live_positions import default_store
        # All open positions remain visible even when their setup aged out of
        # the scanner. Closed records are bounded; history is a separate view.
        owned = default_store().list(uid)
        positions = [p.as_dict() for p in owned if p.is_open]
        positions.extend(p.as_dict() for p in [p for p in owned if not p.is_open][-100:])
    except Exception as exc:  # noqa: BLE001 - unknown inventory must be explicit
        positions_error = f"Owned Snapback positions unavailable: {type(exc).__name__}"
        log.warning("%s", positions_error)
    return {
        "strategy": {**descriptor(cfg), "enabled": cfg.enabled},
        "config": cfg.as_dict(),
        "contract_version": CONTRACT_VERSION,
        "rows": rows,
        "positions": positions,
        "positions_error": positions_error,
        "armed": len(armed),
        "scanned": st.scanned,
        "scanning": st.scanning or uid in _interactive_scans,
        "scan_cancellable": uid in _interactive_scans,
        "last_scan_ms": st.last_scan_ms,
        "last_error": st.last_error,
        "failures": list(st.failures[:20]),
        "warnings": cfg.warnings(),
        "auto_execution_blocker": auto_execution_blocker(cfg),
        "evidence_advisory": evidence_advisory(cfg),
        "live": {
            "conditions": conditions.as_dict() if conditions is not None else None,
            "dispatcher": dispatcher_state,
        },
        "catchup_sessions": CATCHUP_SESSIONS if cfg.trading_mode == "swing" else 0,
        "capabilities": capabilities,
    }


_interactive_scans: dict[str, asyncio.Task] = {}

async def scan_modes(uid: str, modes: list[str] | None = None) -> dict:
    if uid in _interactive_scans or status(uid).scanning:
        return snapshot(uid)
    selected = list(dict.fromkeys(modes or [get_config(uid).trading_mode]))
    if not selected or any(mode not in ('swing', 'scalp', 'intraday', 'ultra_scalping', 'overnight') for mode in selected):
        raise ValueError('Choose supported Snapback scan modes')
    async def run():
        st = status(uid)
        generation = st.config_generation
        st.preview_rows = []
        for mode in selected:
            if mode == get_config(uid).trading_mode:
                await scan_once(uid)
            else:
                result = await scan_once(uid, mode_override=mode)
                if status(uid).config_generation != generation:
                    return
                for row in result['rows']:
                    row = dict(row, execution_eligible=False,
                               execution_block_reason='Preview mode; execution uses the configured strategy mode.')
                    row['signal_id'] = str(row['signal_id']) + ':preview:' + mode
                    st.preview_rows.append(row)
    task = asyncio.create_task(run())
    _interactive_scans[uid] = task
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        if _interactive_scans.get(uid) is task:
            _interactive_scans.pop(uid, None)
    return snapshot(uid)

async def stop_scan(uid: str) -> dict:
    task = _interactive_scans.get(uid)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if _interactive_scans.get(uid) is task:
            _interactive_scans.pop(uid, None)
    return snapshot(uid)
