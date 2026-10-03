"""Execution runner for Adaptive Edge.

Owns the loop, the per-user session, and every path that can place an order.
Four independent things must all agree before this engine touches real money:

1. the account's **paper/live** setting (``account.is_paper``) — KiteClient
   already simulates orders internally, so paper is not a second code path here;
2. automatic opening requires BOTH the shared Kite master ``auto_execute``
   switch and Adaptive Edge's own ``AdaptiveEdgeConfig.auto_execute`` switch.
   Neither gates exits or maintenance, because a strategy that stops managing an
   open position when someone flips it to manual is worse than one that never opened it;
3. the shared **kill switch / safety** decision;
4. this engine's **promotion gate**, which no other engine has and which is
   unapproved, so live execution is refused here regardless of the other three.

Point 4 is why this engine can be enabled and on auto today: it will paper-trade
and collect the sessions its calibration needs, and it cannot reach real money
until somebody promotes it deliberately.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.core.logging import get_logger
from app.engines.adaptive_edge import AdaptiveEdgeConfig, STRATEGY_ID
from app.engines.adaptive_edge.state_machine import Event
from app.engines.adaptive_edge.execution import align_to_tick, exit_order_price, stop_from_entry
from app.engines.adaptive_edge.f110_entry_gate import EntryDecision, F110Evidence, evaluate_entry
from app.engines.adaptive_edge.f111_exit_gate import ExitDecision, F111State, evaluate_exit
from app.engines.adaptive_edge.kite_events import interval_seconds
from app.services.adaptive_edge import get_config, ist_now_ms, ist_today
from app.services.adaptive_edge_positions import (
    AdaptiveEdgePosition,
    close as close_position,
    get as get_position,
    mark_filled,
    mark_rejected,
    open_positions,
    put,
    reset as reset_positions,
)

log = get_logger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))
_locks: dict[str, asyncio.Lock] = {}
_sessions: dict[str, "Session"] = {}
_scan_states: dict[str, dict[str, Any]] = {}
_history_pacer_lock: Optional[asyncio.Lock] = None
_history_next_at = 0.0
_HISTORY_SPACING_S = 0.36


def _lock_for(uid: str) -> asyncio.Lock:
    lock = _locks.get(uid)
    if lock is None:
        lock = _locks[uid] = asyncio.Lock()
    return lock


async def _pace_historical_request() -> None:
    """Global pacing for Kite historical reads across Adaptive Edge tenants."""
    global _history_pacer_lock, _history_next_at
    if _history_pacer_lock is None:
        _history_pacer_lock = asyncio.Lock()
    async with _history_pacer_lock:
        loop = asyncio.get_running_loop()
        now = loop.time()
        if now < _history_next_at:
            await asyncio.sleep(_history_next_at - now)
            now = loop.time()
        _history_next_at = now + _HISTORY_SPACING_S


def _hhmm_now() -> str:
    return datetime.now(_IST).strftime("%H:%M")


def _is_market_open(cfg: AdaptiveEdgeConfig) -> bool:
    now = _hhmm_now()
    if datetime.now(_IST).weekday() >= 5:
        return False
    return cfg.session_start <= now < cfg.session_end


# ------------------------------------------------------------ authority

def is_paper(uid: str) -> Optional[bool]:
    """Account execution mode: True=paper, False=live, None=unknown.

    UNKNOWN must stay distinct from PAPER on operator/status surfaces. Order
    admission does not use this helper; it verifies the acquired client's exact
    boolean mode independently.
    """
    try:
        from app.services.exchanges.kite import accounts
        acct = accounts.get_active(uid)
        if acct is None:
            return None
        mode = getattr(acct, "is_paper", None)
        return mode if mode is True or mode is False else None
    except Exception as exc:                                      # noqa: BLE001
        log.warning("adaptive_edge: account mode unavailable for %s: %s", uid, exc)
        return None


def auto_execute(uid: str) -> bool:
    """True only when both master AUTO and this strategy's AUTO are armed."""
    try:
        from app.services.kite_engine import state as engine_state
        shared = bool(getattr(engine_state.get_config(uid), "auto_execute", False))
        strategy = bool(get_config(uid).auto_execute)
        return shared and strategy
    except Exception as exc:                                       # noqa: BLE001
        log.error("adaptive_edge: auto-execute state unavailable for %s: %s", uid, exc)
        return False


def promotion_blocked() -> tuple[bool, str]:
    """Whether the promotion gate refuses live execution, and why.

    Separate from the formula gate: implementing the mathematics and being
    authorized to risk money on it are different claims, and this engine has
    only the first.
    """
    from app.engines.adaptive_edge.execution_gate import evaluate_strategy_promotion_gate
    decision = evaluate_strategy_promotion_gate()
    return (not decision.authorized), (decision.reason or "strategy_promotion_required")


def _safety(uid: str, idempotency_key: Optional[str]) -> tuple[bool, str]:
    """The shared safety decision, read through its real field name.

    ``SafetyDecision`` exposes ``.allowed``. Reading a ``.ok`` that does not
    exist with a truthy default fails *open* — the kill switch stops blocking
    anything and nothing reports that it stopped working.
    """
    try:
        from app.services.live_safety import assert_safe_to_trade
        # check_daily_loss was False here, on the grounds that the breaker was
        # denominated in a different accounting unit and read zero for an INR
        # position — a gate that always passes, which is worse than no gate.
        # That is no longer how it reads: daily_loss_state(uid=...) goes through
        # _account_daily_pnl_inr to state.daily_realized_pnl_strict(uid), which
        # is INR-native and propagates a failed read instead of returning 0.
        # uid= is what routes this at the right account; omitting it is how an
        # engine escapes the check.
        decision = assert_safe_to_trade([], idempotency_key,
                                        check_daily_loss=True, uid=uid)
        # `.allowed` by name, with no permissive default. The field is called
        # allowed, so getattr(decision, "ok", True) takes the default every
        # time and passes everything the gate was added to stop.
        return bool(decision.allowed), str(decision.reason or "")
    except Exception as exc:                                       # noqa: BLE001
        # Fail closed. An unavailable safety check is not a passed one.
        log.error("adaptive_edge: safety check failed closed for %s: %s", uid, exc)
        return False, f"safety check unavailable: {exc}"




_OWNER = "adaptive_edge"


async def _subscribe_watched(uid: str) -> None:
    """Full-mode ticks for everything held.

    MODE_FULL rather than quote: the options-state fields the strategy reads
    (§16) only appear in full packets, and the stop needs a live premium rather
    than a delayed one.

    Subscriptions are refcounted and owner-tagged, so releasing here cannot take
    ticks away from another engine watching the same contract.
    """
    session = _sessions.get(uid)
    if session is None:
        return
    tokens = {int(p.token) for p in open_positions(uid) if int(p.token or 0) > 0}
    new = tokens - session.watched
    stale = session.watched - tokens
    if new:
        try:
            from app.services.exchanges.kite import constants as K
            from app.services.exchanges.kite import ticker_manager
            await ticker_manager.subscribe(uid, sorted(new), K.MODE_FULL, owner=_OWNER)
            session.watched |= new
        except Exception as exc:                                   # noqa: BLE001
            log.warning("adaptive_edge subscribe failed for %s: %s", uid, exc)
    if stale:
        try:
            from app.services.exchanges.kite import ticker_manager
            await ticker_manager.release(uid, sorted(stale), owner=_OWNER)
        except Exception as exc:                                   # noqa: BLE001
            log.debug("adaptive_edge release failed for %s: %s", uid, exc)
        session.watched -= stale


async def _client(uid: str):
    from app.services.exchanges.kite import accounts
    acct = accounts.get_active(uid)
    if not acct:
        raise RuntimeError("No active Kite account")
    return await accounts.acquire_client(acct)


def _position_account_matches(client, account_id: str) -> bool:
    """True only when this client may mutate the position it is being asked to manage."""
    mode = getattr(client, "_is_paper", None)
    if mode is True:
        return True
    if mode is not False:
        return False
    owner = str(account_id or "").strip()
    current = str(getattr(client, "_account_id", "") or "").strip()
    return bool(owner and current and owner == current)


async def _confirm_fill(client, order_id: str) -> tuple[str, float]:
    """(status, average_price) for an order, from the broker.

    A position is not open because we sent an order. Assuming the limit price
    was the fill is how a stop ends up sized against a price nobody traded at.
    """
    if str(order_id).startswith("PAPER-"):
        return "COMPLETE", 0.0          # simulated; the caller keeps its own price
    try:
        history = await client.get_order_history(order_id)
    except Exception as exc:                                       # noqa: BLE001
        log.warning("adaptive_edge: order status unavailable for %s: %s", order_id, exc)
        return "UNKNOWN", 0.0
    if not history:
        return "UNKNOWN", 0.0
    last = history[-1] if isinstance(history, list) else history
    status = str((last or {}).get("status") or "").upper()
    try:
        average = float((last or {}).get("average_price") or 0.0)
    except (TypeError, ValueError):
        average = 0.0
    return status, average


def realised_pnl_today(uid: str) -> float:
    """Realised rupees on positions this engine closed today."""
    from app.services.adaptive_edge_positions import load
    total = 0.0
    for pos in load(uid).values():
        if pos.is_open or pos.exit_price <= 0 or pos.entry_price <= 0:
            continue
        total += (pos.exit_price - pos.entry_price) * pos.quantity
    return total


def daily_loss_breached(uid: str, cfg: AdaptiveEdgeConfig) -> tuple[bool, str]:
    """Whether today's realised loss has reached the configured cap.

    Denominated in rupees against this engine's own closed positions, not the
    shared legacy breaker — it reads zero for an INR book, so relying
    on it would be a gate that always passes.
    """
    if cfg.max_daily_loss <= 0:
        return False, ""
    realised = realised_pnl_today(uid)
    if realised <= -abs(cfg.max_daily_loss):
        return True, (f"daily loss cap reached: {realised:,.0f} "
                      f"against a limit of {-abs(cfg.max_daily_loss):,.0f}")
    return False, ""


# ------------------------------------------------------------- session

@dataclass
class Session:
    uid: str
    started_ms: int = 0
    day: str = ""
    scans: int = 0
    signals: int = 0
    armed: int = 0
    exits: int = 0
    observations: int = 0
    blocked: dict[str, int] = field(default_factory=dict)
    watched: set = field(default_factory=set)

    def note_block(self, reason: str) -> None:
        self.blocked[reason] = self.blocked.get(reason, 0) + 1


def session_for(uid: str, cfg: Optional[AdaptiveEdgeConfig] = None) -> Session:
    today = str(ist_today())
    session = _sessions.get(uid)
    if session is None or session.day != today:
        session = _sessions[uid] = Session(uid=uid, started_ms=ist_now_ms(), day=today)
    return session


def clear(uid: Optional[str] = None) -> None:
    if uid:
        _sessions.pop(uid, None)
        _scan_states.pop(uid, None)
        reset_positions(uid)
    else:
        _sessions.clear()
        _scan_states.clear()
        reset_positions()


def scan_state(uid: str) -> dict[str, Any]:
    return _scan_states.get(uid) or {"candidates": [], "signals": []}


def session_status(uid: str) -> Optional[dict[str, Any]]:
    session = _sessions.get(uid)
    if session is None:
        return None
    blocked, reason = promotion_blocked()
    return {
        "day": session.day,
        "started_ms": session.started_ms,
        "scans": session.scans,
        "signals": session.signals,
        "armed": session.armed,
        "exits": session.exits,
        "observations": session.observations,
        "blocked": dict(session.blocked),
        "open_positions": len(open_positions(uid)),
        "is_paper": is_paper(uid),
        "auto_execute": auto_execute(uid),
        "live_blocked": blocked,
        "live_blocked_reason": reason if blocked else None,
    }


# ---------------------------------------------------------------- scan

def _history_rows(payload: Any) -> list:
    payload = payload or {}
    if isinstance(payload, dict):
        return list(payload.get("candles") or (payload.get("data") or {}).get("candles") or [])
    return []


def _candle_timestamp_ms(row: Any) -> int:
    if not isinstance(row, (list, tuple)) or len(row) < 5:
        return 0
    try:
        stamp = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=_IST)
        return int(stamp.timestamp() * 1000)
    except (TypeError, ValueError):
        return 0


def _ohlc(rows: list) -> tuple[list[float], list[float], list[float]]:
    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            high = float(row[2] or 0.0)
            low = float(row[3] or 0.0)
            close = float(row[4] or 0.0)
        except (TypeError, ValueError):
            continue
        if high > 0 and low > 0 and close > 0:
            highs.append(high)
            lows.append(low)
            closes.append(close)
    return closes, highs, lows


async def _resolution_candles(
    client: Any,
    *,
    token: int,
    interval: str,
    observed_ms: int,
    horizon_bars: int,
) -> list:
    """Full post-decision candles after a matured observation horizon."""
    if token <= 0 or observed_ms <= 0 or horizon_bars <= 0:
        return []
    try:
        step_s = interval_seconds(interval)
    except Exception:
        return []

    # The observation can occur mid-bar. Wait one extra interval and discard
    # the containing candle so no pre-decision price leaks into the outcome.
    end_ms = observed_ms + int(step_s * (horizon_bars + 1) * 1000)
    if ist_now_ms() < end_ms:
        return []

    start = datetime.fromtimestamp(observed_ms / 1000.0, tz=_IST)
    end = datetime.fromtimestamp((end_ms + step_s * 1000) / 1000.0, tz=_IST)
    await _pace_historical_request()
    payload = await client.get_historical(
        int(token),
        interval,
        start.strftime("%Y-%m-%d %H:%M:%S"),
        end.strftime("%Y-%m-%d %H:%M:%S"),
    )
    rows = [
        row for row in _history_rows(payload)
        if _candle_timestamp_ms(row) > observed_ms
    ]
    return rows[:horizon_bars]


async def _resolve_pending_calibration(
    uid: str,
    client: Any,
    cfg: AdaptiveEdgeConfig,
    *,
    max_requests: int = 12,
) -> dict[str, int]:
    """Settle persisted observations from broker history after their horizon.

    Resolution is intentionally bounded per scan so a backlog cannot monopolize
    the historical-data rate limit. Failed lookups remain pending for the next
    scan; they are never converted into zero-return evidence.
    """
    from app.services import adaptive_edge_evidence as evidence
    from app.services import adaptive_edge_observations as observations

    resolved_options = 0
    resolved_volatility = 0
    requests = 0

    for day, row in observations.pending_all(uid, limit=max_requests * 4):
        if requests >= max_requests:
            break
        token = int(row.get("token") or 0)
        observed_ms = int(row.get("observed_ms") or 0)
        horizon = int(row.get("resolution_horizon_bars") or cfg.horizon_bars or 0)
        interval = str(row.get("resolution_interval") or cfg.decision_timeframe or "")
        premium = float(row.get("premium") or 0.0)
        if token <= 0 or observed_ms <= 0 or premium <= 0 or horizon <= 0 or not interval:
            continue
        try:
            candles = await _resolution_candles(
                client,
                token=token,
                interval=interval,
                observed_ms=observed_ms,
                horizon_bars=horizon,
            )
            requests += 1
        except Exception as exc:                                   # noqa: BLE001
            log.debug("adaptive_edge option evidence resolution failed: %s", exc)
            continue
        if not candles:
            continue
        closes, highs, lows = _ohlc(candles)
        if not closes or not highs or not lows:
            continue
        key = f"{row.get('symbol')}:{observed_ms}"
        if observations.record_outcome(
            uid,
            day,
            key,
            forward_premium=closes[-1],
            horizon_bars=horizon,
            max_favourable_pct=(max(highs) - premium) / premium * 100.0,
            max_adverse_pct=(min(lows) - premium) / premium * 100.0,
        ):
            resolved_options += 1

    remaining = max(0, max_requests - requests)
    for session, row in evidence.pending_all(uid, limit=max_requests * 4):
        if remaining <= 0:
            break
        token = int(row.get("spot_token") or 0)
        observed_ms = int(row.get("decided_ms") or 0)
        horizon = int(row.get("horizon_bars") or cfg.horizon_bars or 0)
        interval = str(row.get("interval") or cfg.decision_timeframe or "")
        spot = float(row.get("spot") or 0.0)
        if token <= 0 or observed_ms <= 0 or spot <= 0 or horizon <= 0 or not interval:
            continue
        try:
            candles = await _resolution_candles(
                client,
                token=token,
                interval=interval,
                observed_ms=observed_ms,
                horizon_bars=horizon,
            )
            remaining -= 1
        except Exception as exc:                                   # noqa: BLE001
            log.debug("adaptive_edge volatility evidence resolution failed: %s", exc)
            continue
        _closes, highs, lows = _ohlc(candles)
        if not highs or not lows:
            continue
        realised_move_bps = max(
            abs(max(highs) - spot),
            abs(spot - min(lows)),
        ) / spot * 10_000.0
        key = f"{row.get('underlying')}:{float(row.get('strike') or 0):.0f}:{observed_ms}"
        if evidence.resolve(uid, session, key, realised_move_bps):
            resolved_volatility += 1

    return {
        "option_observations": resolved_options,
        "volatility_readings": resolved_volatility,
    }


async def scan_once(uid: str) -> dict[str, Any]:
    """One full scan. Never raises into the caller — the loop must survive."""
    cfg = get_config(uid)
    session = session_for(uid, cfg)

    if not cfg.enabled:
        session.note_block("engine disabled")
        state = {"candidates": [], "signals": [], "reason": "engine disabled"}
        _scan_states[uid] = state
        return state

    if not _is_market_open(cfg):
        session.note_block("outside session")
        state = {"candidates": [], "signals": [], "reason": "outside session window"}
        _scan_states[uid] = state
        return state

    async with _lock_for(uid):
        resolved = {"option_observations": 0, "volatility_readings": 0}
        try:
            client = await _client(uid)
            resolved = await _resolve_pending_calibration(uid, client, cfg)
        except Exception as exc:                                   # noqa: BLE001
            log.warning("adaptive_edge calibration resolution unavailable for %s: %s", uid, exc)

        try:
            from app.services.adaptive_edge_scanner import scan as run_scan
            result = await run_scan(uid, cfg)
        except Exception as exc:                                   # noqa: BLE001
            log.exception("adaptive_edge scan failed for %s", uid)
            state = {"candidates": [], "signals": [], "errors": [str(exc)]}
            _scan_states[uid] = state
            return state

        session.scans += 1
        candidates = result.get("candidates") or []
        signals = _signals_from(candidates, cfg)
        session.signals += len(signals)

        # Nothing arms yet, so recording what the engine saw is the only way a
        # paper session produces anything the calibration can use. Never let a
        # storage problem take the scan down with it.
        observed = 0
        try:
            from app.services import adaptive_edge_observations as observations
            observed = observations.record(
                uid,
                str(ist_today()),
                candidates,
                observed_ms=ist_now_ms(),
                horizon_bars=cfg.horizon_bars,
                interval=cfg.decision_timeframe,
            )
            session.observations += observed
        except Exception:                                          # noqa: BLE001
            log.exception("adaptive_edge: could not record observations for %s", uid)

        # The implied-versus-realised reading is the fact every offline study of
        # this strategy was missing. Record it every scan, traded or not.
        try:
            state_for_evidence = {**result}
            recorded = _record_volatility_readings(uid, state_for_evidence, cfg)
        except Exception:                                          # noqa: BLE001
            log.exception("adaptive_edge: could not record volatility readings for %s", uid)
            recorded = 0

        state = {**result, "signals": signals, "observed": observed,
                 "volatility_recorded": recorded,
                 "calibration_resolved": resolved,
                 "server_time_ms": ist_now_ms()}
        _scan_states[uid] = state
        return state


def _entry_gate(candidate: dict) -> tuple[EntryDecision, str]:
    """F-110, the mandatory entry gate, run for real.

    §35 permits BUY_CE / BUY_PE only when data, directional edge, expected value,
    conservative expected value, liquidity, slippage and risk all pass.
    `f110_entry_gate` has implemented that conjunction all along and nothing
    called it, so the gate the specification calls mandatory was not gating
    anything.

    ConservativeEV is supplied only after enough resolved option-premium
    excursions exist to estimate target/stop probabilities and F-105's lower
    confidence value. Until then the field is absent and the gate fails closed.
    F-102 directional probabilities are deliberately not substituted: an
    underlying directional move and an option premium reaching its target are
    different events.
    """
    option_type = str(candidate.get("option_type") or "")
    expected_ev = candidate.get("expected_net_value")
    conservative_ev = candidate.get("conservative_ev")   # absent until premium outcomes resolve

    evidence = F110Evidence(
        data_ok=bool(candidate.get("actionable")),
        directional_edge_ok=str(candidate.get("direction") or "NEUTRAL") in ("BULLISH", "BEARISH"),
        expected_ev=None if expected_ev is None else float(expected_ev),
        conservative_ev=None if conservative_ev is None else float(conservative_ev),
        # The scanner already applied these three in reaching this list: OI and
        # volume floors, the spread ceiling, and the strike/expiry windows.
        liquidity_ok=True,
        slippage_ok=True,
        risk_ok=True,
    )
    decision = evaluate_entry(option_type, evidence)
    if decision is not EntryDecision.NO_TRADE:
        return decision, str(candidate.get("reason") or "")

    if not evidence.directional_edge_ok:
        return decision, "no directional edge"
    if evidence.expected_ev is None or evidence.expected_ev <= 0:
        return decision, "expected value not positive"
    if evidence.conservative_ev is None:
        sample_size = int(candidate.get("f105_sample_size") or 0)
        return decision, (
            "conservative EV unavailable: resolved premium-outcome evidence "
            f"is insufficient ({sample_size}/200 comparable observations)"
        )
    if evidence.conservative_ev <= 0:
        return decision, "conservative expected value not positive"
    return decision, "entry gate refused"


def _signals_from(candidates: list[dict], cfg: AdaptiveEdgeConfig) -> list[dict]:
    """Turn scored candidates into armable signals.

    Two gates in sequence, both the source's. The pipeline supplies direction and
    economics; F-110 is the mandatory conjunction that decides BUY_CE / BUY_PE /
    NO_TRADE. `entry_ok` is that decision and nothing else — it used to be a
    hardcoded false, because the engine reached no decision at all.
    """
    signals: list[dict] = []
    for candidate in candidates:
        decision, reason = _entry_gate(candidate)
        armable = decision is not EntryDecision.NO_TRADE
        signals.append({
            **candidate,
            "signal_id": f"{candidate.get('symbol')}:{candidate.get('expiry')}",
            "state": decision.value,
            "entry_ok": armable,
            "entry_decision": decision.value,
            "reason": reason,
        })
    return signals


def evidence_permits_arming(uid: str) -> tuple[bool, str]:
    """Verdict for the separate defined-risk volatility evidence stream.

    Kept for diagnostics and promotion analysis. Directional long-option
    admission does not consume this verdict; it uses its own F-105/F-110
    premium-outcome evidence.
    """
    try:
        from app.services.adaptive_edge_evidence import verdict
        v = verdict(uid)
        return v.ready, v.reason
    except Exception as exc:                                       # noqa: BLE001
        log.error("adaptive_edge: evidence unreadable for %s (%s); refusing", uid, exc)
        return False, f"evidence unavailable: {exc}"


def _record_volatility_readings(
    uid: str,
    state: dict[str, Any],
    cfg: AdaptiveEdgeConfig,
) -> int:
    """Persist this scan's implied-versus-realised readings.

    Recorded whether or not the engine intends to trade. That is the whole
    mechanism by which the gate can ever open: it learns from decisions the
    engine declined to act on.
    """
    rows = state.get("volatility") or []
    if not rows:
        return 0
    try:
        from app.services.adaptive_edge_evidence import PendingReading, record
    except Exception:                                              # noqa: BLE001
        return 0

    session = str(ist_today())
    stamp = ist_now_ms()
    written = 0
    for row in rows:
        # Record even when no structure could be priced. The ratio is the
        # measurement; the structure is optional. Skipping unpriced rows meant
        # the store stayed empty on every scan but the ~31 minutes a week when
        # a weekly expiry sits inside the hold — so the gate could never open,
        # and the one fact worth collecting was thrown away each time.
        credit = row.get("credit_bps")
        max_loss = row.get("max_loss_bps")
        try:
            written += bool(record(uid, PendingReading(
                session=session, decided_ms=stamp,
                underlying=str(row.get("underlying") or ""),
                strike=float(row.get("strike") or 0.0),
                implied_ratio=float(row.get("implied_ratio") or 0.0),
                implied_vol=float(row.get("implied_vol") or 0.0),
                realised_vol=float(row.get("realised_vol") or 0.0),
                credit_bps=None if credit is None else float(credit),
                max_loss_bps=None if max_loss is None else float(max_loss),
                forecast_bps=float(row.get("forecast_bps") or 0.0),
                spot=float(row.get("spot") or 0.0),
                spot_token=int(row.get("spot_token") or 0),
                horizon_bars=int(cfg.horizon_bars),
                interval=str(cfg.decision_timeframe))))
        except Exception:                                          # noqa: BLE001
            continue
    return written


# ----------------------------------------------------------------- arm

async def arm(uid: str, signal_id: str) -> dict[str, Any]:
    """Enter one signal, end to end, with the broker as the authority.

    The order is deliberate: refuse early and cheaply, send once under an
    idempotency key, persist before confirming, confirm the real fill, re-anchor
    the stop to it, and only then put protection at the broker. Every step
    before the last is recoverable; the last is what makes the position safe to
    walk away from.

    A previous version recorded the position without sending anything, which is
    worse than not trading: the board shows a position that does not exist, with
    a stop nothing enforces.
    """
    cfg = get_config(uid)
    if not cfg.enabled:
        return {"ok": False, "reason": "Adaptive Edge engine is switched off"}

    async with _lock_for(uid):
        session = session_for(uid, cfg)

        try:
            client = await _client(uid)
        except Exception as exc:                                   # noqa: BLE001
            return {"ok": False, "reason": str(exc)}
        mode = getattr(client, "_is_paper", None)
        if mode is not True and mode is not False:
            session.note_block("broker mode unknown")
            return {"ok": False, "reason": "broker mode is unknown"}
        paper = mode is True

        blocked, reason = promotion_blocked()
        if blocked and not paper:
            session.note_block(reason)
            return {"ok": False, "reason": reason,
                    "detail": "This strategy is not promoted for live execution. "
                              "Switch the account to paper to run it."}

        breached, why = daily_loss_breached(uid, cfg)
        if breached:
            session.note_block("daily loss cap")
            return {"ok": False, "reason": why}

        # Do not gate this directional long-option entry with the
        # short-volatility evidence verdict. That verdict prices a different
        # defined-risk structure. Directional admission is owned by F-105/F-110
        # on the selected option itself; live authorization remains independently
        # controlled by the promotion gate above.
        if len(open_positions(uid)) >= cfg.max_positions:
            session.note_block("max positions")
            return {"ok": False, "reason": f"already holding {cfg.max_positions} position(s)"}

        state = scan_state(uid)
        signal = next((s for s in state.get("signals") or []
                       if s.get("signal_id") == signal_id), None)
        if signal is None:
            return {"ok": False, "reason": "signal not found in the current scan"}
        if not bool(signal.get("entry_ok")):
            why = str(signal.get("reason") or "F-110 entry gate refused")
            session.note_block("F-110 entry gate")
            return {"ok": False, "reason": why}

        symbol = str(signal.get("symbol") or "")
        existing = get_position(uid, symbol)
        if existing is not None and existing.is_open:
            return {"ok": False, "reason": f"already holding {symbol}"}

        lot_size = int(signal.get("lot_size") or 0)
        if lot_size <= 0:
            return {"ok": False, "reason": "lot size unknown for this contract"}
        quantity = lot_size * max(1, cfg.lots)
        last_price = float(signal.get("last_price") or 0.0)
        if last_price <= 0:
            return {"ok": False, "reason": "no tradeable price for this contract"}

        # One key per (signal, session day). A retry after a timeout re-uses it
        # and is refused, which is the difference between a retry and a second
        # position.
        idem = f"adaptive_edge:{uid}:{signal_id}:{ist_today()}"
        allowed, safety_reason = _safety(uid, idem)
        if not allowed:
            session.note_block(safety_reason or "safety")
            return {"ok": False, "reason": safety_reason or "blocked by safety"}

        limit = align_to_tick(last_price)

        from app.services.canonical_strategy_entry import submit_entry
        execution = await submit_entry(
            client=client,
            uid=uid,
            strategy_id=STRATEGY_ID,
            generation_id=str(cfg.strategy_version),
            signal_id=signal_id,
            exchange="NFO",
            symbol=symbol,
            quantity=quantity,
            capital_price=float(limit),
            order_type="limit_order",
            limit_price=float(limit),
            product="MIS",
            tag=STRATEGY_ID,
            reason="Adaptive Edge F-105/F-110, sizing and safety gates passed",
        )
        if not execution.success:
            session.note_block(
                "entry halted" if execution.status == "HALTED" else "entry rejected"
            )
            return {
                "ok": False,
                "reason": execution.error or execution.status,
                "status": execution.status,
            }

        order_id = str(execution.order_id or "")
        if not order_id:
            session.note_block("canonical execution returned no order id")
            return {
                "ok": False,
                "reason": "canonical execution returned no order id",
            }

        # Persist BEFORE confirming. If this process dies in the next second, the
        # position that may exist at the broker is one we can find again.
        position = AdaptiveEdgePosition(
            symbol=symbol,
            token=int(signal.get("token") or 0),
            underlying=str(signal.get("underlying") or ""),
            direction=str(signal.get("option_type") or "CE"),
            quantity=quantity,
            lot_size=lot_size,
            entry_price=limit,
            stop_price=stop_from_entry(limit, cfg.stop_percent),
            target_price=align_to_tick(limit * cfg.target_multiple) if cfg.target_multiple > 0 else None,
            opened_ms=ist_now_ms(),
            peak_price=limit,
            signal_id=signal_id,
            idempotency_key=idem,
            order_id=order_id,
            account_id=str(getattr(client, "_account_id", "") or ""),
            stop_mode=cfg.stop_mode,
        )
        position.apply(Event.ORDER_SUBMITTED)
        put(uid, position)

        from app.services.canonical_fill_reconciliation import (
            mark_projected as mark_fill_projected,
            resolve_entry_fill,
        )
        resolution = await resolve_entry_fill(
            uid=uid,
            client=client,
            order_id=order_id,
            expected_quantity=quantity,
            reference_price=limit,
        )
        if resolution.filled_quantity <= 0:
            if resolution.projection_version:
                mark_fill_projected(resolution)
            if resolution.terminal:
                mark_rejected(uid, symbol, resolution.status)
                session.note_block(f"order {resolution.status.lower()}")
                return {
                    "ok": False,
                    "reason": f"order {resolution.status.lower()} at the broker",
                    "status": resolution.status,
                }
            session.note_block("entry fill unresolved")
            return {
                "ok": False,
                "reason": resolution.reason or "entry fill unresolved; reconciliation required",
                "status": "PENDING_RECONCILIATION",
                "order_id": order_id,
            }

        position.quantity = int(resolution.filled_quantity)
        put(uid, position)
        fill = resolution.average_price or limit
        partial = resolution.filled_quantity < quantity
        filled = mark_filled(
            uid,
            symbol,
            fill,
            partial=partial,
            order_id=order_id,
        )
        if filled is not None:
            # Re-anchor to what actually traded, not to what we asked for.
            filled.stop_price = stop_from_entry(fill, cfg.stop_percent)
            filled.target_price = (
                align_to_tick(fill * cfg.target_multiple)
                if cfg.target_multiple > 0
                else None
            )
            # A cancelled/rejected remainder leaves a real partial holding. Move
            # the state from PARTIALLY_FILLED to OPEN once the remainder is
            # terminal so the lifecycle says exactly what is still live.
            if partial and resolution.terminal:
                filled.apply(
                    Event.CANCELLED
                    if resolution.status == "CANCELLED"
                    else Event.REJECTED
                )
            put(uid, filled)
            position = filled

        gtt_id = await _place_protection(uid, client, position, cfg)
        if resolution.projection_version:
            mark_fill_projected(resolution)
        await _subscribe_watched(uid)
        session.armed += 1
        return {
            "ok": True,
            "order_id": order_id,
            "symbol": symbol,
            "quantity": position.quantity,
            "entry": position.entry_price,
            "stop": position.stop_price,
            "target": position.target_price,
            "gtt_id": gtt_id,
            "paper": paper,
            "state": position.state,
            "status": (
                "FILLED"
                if resolution.settled and not resolution.reconciliation_required
                else "PARTIAL_RECONCILIATION_REQUIRED"
            ),
        }


async def _place_protection(uid: str, client, position: AdaptiveEdgePosition,
                            cfg: AdaptiveEdgeConfig) -> int:
    """Broker-side stop for an open position, or 0.

    A failed GTT is logged, never fatal: the tick monitor is still watching. But
    it is not silent either — "protected" and "protected only while this process
    lives" are different states and the operator must be able to tell which one
    they are in.
    """
    if cfg.stop_mode == "monitor" or position.stop_price <= 0 or position.quantity <= 0:
        return 0
    if not _position_account_matches(client, position.account_id):
        session = _sessions.get(uid)
        if session:
            session.note_block(
                f"{position.symbol}: protection refused — broker account ownership unverified"
            )
        return 0
    try:
        from app.services.kite_engine.protective_stop import place_stop
        from app.services.snapback_family_mode import (
            INTENT_PROTECT,
            canonical_broker_capability,
        )
        with canonical_broker_capability(
            f"adaptive-edge-protect:{getattr(client, '_account_id', '') or 'paper'}:"
            f"{position.symbol}",
            intent=INTENT_PROTECT,
        ):
            gtt_id = await place_stop(
                client, tradingsymbol=position.symbol, exchange=position.exchange,
                qty=int(position.quantity), trigger_premium=float(position.stop_price),
                last_price=float(position.entry_price),
                # Every option here is BOUGHT, so the protective exit is always a
                # SELL on the downside. "PE" is the contract type, not the side.
                direction="long",
                target_premium=float(position.target_price or 0.0),
                product="MIS")
    except Exception as exc:                                       # noqa: BLE001
        log.warning("adaptive_edge: protective GTT failed for %s: %s", position.symbol, exc)
        gtt_id = None

    if gtt_id:
        position.gtt_id = int(gtt_id)
        position.gtt_at = float(position.stop_price)
        put(uid, position)
        return int(gtt_id)

    session = _sessions.get(uid)
    if session:
        session.note_block(f"{position.symbol}: no broker stop")
    return 0


async def _broker_long_quantity(client, position: AdaptiveEdgePosition) -> Optional[int]:
    """Broker-held MIS long quantity, or None when live inventory is unreadable.

    Authorization is bucket-specific: a same-symbol NRML holding cannot authorize
    an MIS SELL, and a same-symbol row on another exchange cannot authorize either.
    """
    if getattr(client, "_is_paper", None) is True:
        return int(position.quantity)
    if not _position_account_matches(client, position.account_id):
        return None
    try:
        book = await client.get_positions()
    except Exception as exc:
        log.warning("adaptive_edge: broker quantity unavailable for %s: %s",
                    position.symbol, exc)
        return None

    rows = (book.get("net") or []) if isinstance(book, dict) else (book or [])
    want_symbol = str(position.symbol or "").strip().upper()
    want_exchange = str(position.exchange or "").strip().upper()
    want_product = "MIS"
    matched = False
    net_qty = 0

    for row in rows:
        if isinstance(row, dict):
            sym = str(row.get("tradingsymbol") or row.get("symbol") or "").strip().upper()
            exchange = str(row.get("exchange") or "").strip().upper()
            product = str(row.get("product") or row.get("position_type") or "").strip().upper()
            raw_qty = row.get("quantity")
            if raw_qty is None:
                raw_qty = row.get("net_quantity")
            if raw_qty is None:
                raw_qty = row.get("size")
        else:
            sym = str(getattr(row, "symbol", "") or "").strip().upper()
            exchange = str(getattr(row, "exchange", "") or "").strip().upper()
            product = str(
                getattr(row, "product", "")
                or getattr(row, "position_type", "")
                or ""
            ).strip().upper()
            raw_qty = getattr(row, "quantity", None)
            if raw_qty is None:
                raw_qty = getattr(row, "size", 0)

        if sym != want_symbol or exchange != want_exchange or product != want_product:
            continue
        try:
            qty = int(float(raw_qty or 0))
        except (TypeError, ValueError):
            return None
        matched = True
        net_qty += qty

    return max(0, net_qty) if matched else 0


async def _cancel_protection(uid: str, client, position: AdaptiveEdgePosition) -> bool:
    """Prove the old GTT cannot race a direct SELL before forgetting it."""
    if not position.gtt_id:
        return _position_account_matches(client, position.account_id)

    if not _position_account_matches(client, position.account_id):
        return False

    from app.services.kite_engine import protective_stop as pstop
    from app.services.snapback_family_mode import (
        INTENT_REDUCE,
        canonical_broker_capability,
    )

    gid = int(position.gtt_id)
    try:
        with canonical_broker_capability(
            f"adaptive-edge-exit-disarm:{getattr(client, '_account_id', '') or 'paper'}:"
            f"{position.symbol}:{gid}",
            intent=INTENT_REDUCE,
        ):
            outcome = await pstop.cancel_stop_result(client, gid)
    except Exception as exc:                                       # noqa: BLE001
        log.warning("adaptive_edge: could not cancel GTT %s for %s: %s",
                    gid, position.symbol, exc)
        outcome = pstop.UNKNOWN

    if outcome != pstop.CANCELLED:
        status = await pstop.stop_status(
            client,
            gid,
            tradingsymbol=position.symbol,
            direction="long",
            exchange=position.exchange,
            product="MIS",
            qty=int(position.quantity),
        )
        if status != pstop.STOP_ABSENT:
            session = _sessions.get(uid)
            if session:
                session.note_block(
                    f"{position.symbol}: exit held — broker GTT #{gid} is {status}"
                )
            return False

    position.gtt_id = 0
    position.gtt_at = 0.0
    put(uid, position)
    return True


async def _sync_trail(uid: str, client, position: AdaptiveEdgePosition,
                      cfg: AdaptiveEdgeConfig) -> None:
    """Modify the resting stop in place; never cancel protection just to trail."""
    if not position.gtt_id or position.stop_price <= 0:
        return
    if abs(position.stop_price - position.gtt_at) < 0.05:
        return
    if not _position_account_matches(client, position.account_id):
        session = _sessions.get(uid)
        if session:
            session.note_block(
                f"{position.symbol}: trail refused — broker account ownership unverified"
            )
        return
    try:
        from app.services.kite_engine.protective_stop import move_stop
        from app.services.snapback_family_mode import (
            INTENT_PROTECT,
            canonical_broker_capability,
        )
        with canonical_broker_capability(
            f"adaptive-edge-trail:{getattr(client, '_account_id', '') or 'paper'}:"
            f"{position.symbol}:{position.gtt_id}",
            intent=INTENT_PROTECT,
        ):
            moved = await move_stop(
                client,
                trigger_id=int(position.gtt_id),
                tradingsymbol=position.symbol,
                exchange=position.exchange,
                qty=int(position.quantity),
                trigger_premium=float(position.stop_price),
                last_price=float(position.entry_price),
                direction="long",
                target_premium=float(position.target_price or 0.0),
                product="MIS",
            )
        if moved:
            position.gtt_at = float(position.stop_price)
            put(uid, position)
        else:
            session = _sessions.get(uid)
            if session:
                session.note_block(f"{position.symbol}: broker stop trail was not confirmed")
    except Exception as exc:                                       # noqa: BLE001
        log.warning("adaptive_edge: could not move GTT %s for %s: %s",
                    position.gtt_id, position.symbol, exc)


async def _exit_position(uid: str, client, position: AdaptiveEdgePosition,
                         cfg: AdaptiveEdgeConfig, *, price: float, reason: str) -> bool:
    """The single exit path. Every caller goes through here.

    Takes the position's `exiting` claim for the duration, so the tick monitor
    and the square-off cannot both sell the same position. A caller that finds
    the claim already taken does nothing and says so by returning False.

    `reason` is a parameter rather than something inferred from the price: a
    stop and a square-off can happen at the same number, and recording the wrong
    one makes the exit ledger useless for calibration.
    """
    if position.exiting:
        return False
    position.exiting = True
    put(uid, position)

    broker_qty = await _broker_long_quantity(client, position)
    if broker_qty is None or broker_qty < int(position.quantity):
        position.exiting = False
        put(uid, position)
        session = _sessions.get(uid)
        if session:
            session.note_block(
                f"{position.symbol}: exit held — broker quantity "
                f"{broker_qty if broker_qty is not None else 'unknown'} is below "
                f"local quantity {position.quantity}"
            )
        return False

    if not await _cancel_protection(uid, client, position):
        position.exiting = False
        put(uid, position)
        return False

    limit = exit_order_price(price, position.tick_size)
    try:
        from app.services.snapback_family_mode import (
            INTENT_REDUCE,
            canonical_broker_capability,
        )
        with canonical_broker_capability(
            f"adaptive-edge-exit:{getattr(client, '_account_id', '') or 'paper'}:"
            f"{position.symbol}:{position.signal_id}",
            intent=INTENT_REDUCE,
        ):
            result = await client.place_order(
                f"{position.exchange}:{position.symbol}", "sell", float(position.quantity),
                order_type="limit_order", limit_price=float(limit),
                product="MIS", tag="adaptive_edge-exit")
        order_id = str((result or {}).get("order_id")
                       or ((result or {}).get("data") or {}).get("order_id") or "")
    except Exception as exc:                                       # noqa: BLE001
        order_id, failure = "", str(exc)
        position.exiting = False
        put(uid, position)
        # Re-arm what we cancelled. Leaving it down would turn a failed exit
        # into an unprotected position.
        await _place_protection(uid, client, position, cfg)
        log.error("adaptive_edge: exit rejected for %s: %s", position.symbol, failure)
        return False

    if not order_id:
        position.exiting = False
        put(uid, position)
        await _place_protection(uid, client, position, cfg)
        log.error("adaptive_edge: exit for %s returned no order id", position.symbol)
        return False

    close_position(uid, position.symbol, limit, reason, closed_ms=ist_now_ms())
    await _subscribe_watched(uid)          # drops the token we no longer hold
    session = _sessions.get(uid)
    if session:
        session.exits += 1
    return True


async def on_ticks(uid: str, ticks: list) -> str:
    """Drive open positions from live prices. Returns a short status word.

    F-111 is the canonical exit gate and decides HOLD / UPDATE_STOP / EXIT. It
    was implemented and uncalled, so the exit rule the specification owns was not
    the one running.

    `conservative_continuation_value` is passed as absent for the same reason
    ConservativeEV is absent at entry: it is a bound that needs the fitted
    probability model. The gate handles absence safely — it does not force an
    exit on a missing value — so the protective and session conditions still
    decide, and the trail still ratchets.

    The profit target is checked separately and labelled as such. F-111 has no
    notion of a target; its exits are a protective breach, session termination,
    or continuation value falling to zero. Folding a target into
    `protective_condition_breached` would file a take-profit as a stop-out and
    make the exit ledger lie about why positions closed.
    """
    cfg = get_config(uid)
    holdings = open_positions(uid)
    if not holdings:
        return "idle"

    by_token = {int(t.get("instrument_token") or 0): t for t in ticks or []}
    session_over = not _is_market_open(cfg)
    client = None
    acted = "watching"

    for position in holdings:
        tick = by_token.get(int(position.token or 0))
        if not tick:
            continue
        ltp = float(tick.get("last_price") or 0.0)
        if ltp <= 0:
            continue

        # Ratchet the trail on the way up before asking the gate anything.
        stop_improved = False
        if ltp > position.peak_price:
            position.peak_price = ltp
            if cfg.profit_lock_fraction > 0:
                locked = position.entry_price + (
                    (position.peak_price - position.entry_price) * cfg.profit_lock_fraction)
                raised = align_to_tick(locked)
                # A stop only ever moves up. Widening it would be an expansion of
                # risk the position was never authorized to take.
                if raised > position.stop_price:
                    position.stop_price = raised
                    stop_improved = True
            put(uid, position)

        decision = evaluate_exit(F111State(
            protective_condition_breached=ltp <= position.stop_price,
            conservative_continuation_value=None,   # needs the fitted model
            emergency_reversal=False,               # no reversal detector wired
            session_termination=session_over,
            stop_improved=stop_improved,
        ))

        reason = ""
        if decision is ExitDecision.EXIT:
            reason = "session_end" if session_over else "stop"
        elif position.target_price and ltp >= position.target_price:
            # Configured rule, not an F-111 exit. Named separately so the ledger
            # records a take-profit as a take-profit.
            reason = "target"

        if not reason:
            client = client or await _client(uid)
            if decision is ExitDecision.UPDATE_STOP:
                await _sync_trail(uid, client, position, cfg)
            continue

        client = client or await _client(uid)
        if await _exit_position(uid, client, position, cfg, price=ltp, reason=reason):
            acted = "exited"

    return acted


async def square_off_all(uid: str) -> dict[str, Any]:
    """Flatten everything this engine holds. Used at the session boundary.

    Runs regardless of manual/auto: auto gates opening, and a position that is
    already open must be closed either way.
    """
    cfg = get_config(uid)
    holdings = open_positions(uid)
    out: dict[str, Any] = {"closed": 0, "failed": 0, "errors": []}
    if not holdings:
        return out
    try:
        client = await _client(uid)
    except Exception as exc:                                       # noqa: BLE001
        out["errors"].append(str(exc))
        return out

    for position in holdings:
        price = position.peak_price or position.entry_price
        try:
            quote = await client.get_quote([f"{position.exchange}:{position.symbol}"])
            live = float((quote or {}).get(f"{position.exchange}:{position.symbol}", {})
                         .get("last_price") or 0.0)
            if live > 0:
                price = live
        except Exception:                                          # noqa: BLE001
            pass
        if await _exit_position(uid, client, position, cfg, price=price, reason="square_off"):
            out["closed"] += 1
        else:
            out["failed"] += 1
    return out


async def adopt(uid: str, symbol: str, quantity: int, entry_price: float) -> dict[str, Any]:
    """Take responsibility for a position this engine did not open.

    Protection is placed immediately. A hand-placed position that this engine is
    now managing but has not protected is the worst of both worlds — nobody is
    watching it and everybody assumes somebody is.
    """
    cfg = get_config(uid)
    async with _lock_for(uid):
        if quantity <= 0 or entry_price <= 0:
            return {"ok": False, "reason": "quantity and entry_price must be positive"}
        existing = get_position(uid, symbol)
        if existing is not None and existing.is_open:
            return {"ok": False, "reason": f"already managing {symbol}"}

        position = AdaptiveEdgePosition(
            symbol=symbol, token=0, underlying="", direction="CE",
            quantity=int(quantity), lot_size=int(quantity),
            entry_price=float(entry_price),
            stop_price=stop_from_entry(float(entry_price), cfg.stop_percent),
            target_price=align_to_tick(float(entry_price) * cfg.target_multiple)
            if cfg.target_multiple > 0 else None,
            opened_ms=ist_now_ms(), peak_price=float(entry_price),
            stop_mode=cfg.stop_mode, notes=("adopted",),
        )
        try:
            client = await _client(uid)
        except Exception as exc:                                   # noqa: BLE001
            return {"ok": False, "reason": f"broker unavailable: {exc}"}
        position.account_id = str(getattr(client, "_account_id", "") or "")

        broker_qty = await _broker_long_quantity(client, position)
        if broker_qty is None or broker_qty < int(quantity):
            return {
                "ok": False,
                "reason": (
                    f"cannot adopt {symbol}: broker quantity "
                    f"{broker_qty if broker_qty is not None else 'unknown'} "
                    f"is below requested {quantity}"
                ),
            }

        position.apply(Event.ORDER_SUBMITTED)
        position.apply(Event.FILL)
        put(uid, position)

        gtt_id = await _place_protection(uid, client, position, cfg)
        return {"ok": True, "symbol": symbol, "quantity": quantity,
                "stop": position.stop_price, "gtt_id": gtt_id}


async def reconcile(uid: str) -> dict[str, Any]:
    """Bring our view of positions back in line with the broker's.

    Runs regardless of manual/auto: auto gates opening, and a position that is
    already open must be managed either way.

    Two divergences matter. A position we think is open but the broker does not
    hold was closed behind our back — by a GTT, by hand, or by the exchange — and
    leaving it open in our ledger blocks the next entry and reports a P&L that
    is not real. A position we hold with no broker stop is unprotected, and
    re-arming it is the whole point of running this on a restart.
    """
    cfg = get_config(uid)
    out: dict[str, Any] = {"checked": 0, "closed": 0, "reprotected": 0, "errors": []}
    holdings = open_positions(uid)
    if not holdings:
        return out

    try:
        client = await _client(uid)
        broker = await client.get_positions()
    except Exception as exc:                                       # noqa: BLE001
        # Unknown broker state is not an empty one. Closing our records here
        # would abandon real positions on a transient API failure.
        out["errors"].append(f"broker positions unavailable: {exc}")
        return out

    held: dict[tuple[str, str], int] = {}
    for row in (broker or {}).get("net", []) if isinstance(broker, dict) else (broker or []):
        if isinstance(row, dict):
            symbol = str(row.get("tradingsymbol") or row.get("symbol") or "").strip().upper()
            exchange = str(row.get("exchange") or "").strip().upper()
            product = str(row.get("product") or row.get("position_type") or "").strip().upper()
            raw_qty = row.get("quantity")
            if raw_qty is None:
                raw_qty = row.get("net_quantity")
            if raw_qty is None:
                raw_qty = row.get("size")
        else:
            symbol = str(getattr(row, "symbol", "") or "").strip().upper()
            exchange = str(getattr(row, "exchange", "") or "").strip().upper()
            if not exchange:
                underlying = str(getattr(row, "underlying", "") or "")
                exchange = underlying.split(":", 1)[0].strip().upper() if ":" in underlying else ""
            product = str(
                getattr(row, "product", "")
                or getattr(row, "position_type", "")
                or ""
            ).strip().upper()
            raw_qty = getattr(row, "quantity", None)
            if raw_qty is None:
                raw_qty = getattr(row, "size", 0)
        if not symbol or not exchange or product != "MIS":
            continue
        try:
            qty = int(float(raw_qty or 0))
        except (TypeError, ValueError):
            continue
        key = (exchange, symbol)
        held[key] = held.get(key, 0) + qty

    for position in holdings:
        out["checked"] += 1
        if not _position_account_matches(client, position.account_id):
            out["errors"].append(
                f"{position.symbol}: broker account ownership unverified; reconcile skipped"
            )
            continue
        key = (
            str(position.exchange or "").strip().upper(),
            str(position.symbol or "").strip().upper(),
        )
        broker_qty = held.get(key, 0)
        if broker_qty <= 0:
            close_position(uid, position.symbol, position.peak_price or position.entry_price,
                           "closed_at_broker", closed_ms=ist_now_ms())
            out["closed"] += 1
            continue
        if not position.gtt_id and cfg.stop_mode != "monitor":
            if await _place_protection(uid, client, position, cfg):
                out["reprotected"] += 1
    return out


async def _auto_enter(uid: str) -> int:
    """Open positions automatically, if and only if the account says auto."""
    if not auto_execute(uid):
        return 0
    cfg = get_config(uid)
    entered = 0
    for signal in scan_state(uid).get("signals") or []:
        if not signal.get("entry_ok"):
            continue
        if len(open_positions(uid)) >= cfg.max_positions:
            break
        result = await arm(uid, str(signal.get("signal_id")))
        if result.get("ok"):
            entered += 1
    return entered


def _kite_user_ids() -> list[str]:
    """Every connected account the loop should scan.

    `bootstrap()` first: accounts are loaded lazily, so without it a freshly
    started process sees none and the loop quietly does nothing.

    This previously called a function that does not exist on the accounts module,
    inside a bare except that returned an empty list — so the scan loop
    enumerated nobody every sixty seconds and logged nothing. A no-op that
    reports success is worse than a crash, and the except is narrowed here so the
    next one is visible.
    """
    try:
        from app.services.exchanges.kite import accounts
        accounts.bootstrap()
        return sorted({a.user_id for a in accounts.all_accounts() if a.is_active})
    except Exception as exc:                                       # noqa: BLE001
        log.error("adaptive_edge: could not enumerate Kite accounts (%s)", exc)
        return []


def past_square_off(cfg: AdaptiveEdgeConfig) -> bool:
    return _hhmm_now() >= cfg.square_off_time


async def scan_all_once() -> dict[str, str]:
    """One pass for every connected account.

    Maintenance first, then scanning. Reconcile before opening anything so a
    restart cannot open a second position in a contract the broker already
    holds, and square off before scanning so the last minutes of the session
    cannot arm something that is about to be flattened anyway.
    """
    out: dict[str, str] = {}
    for uid in _kite_user_ids():
        try:
            cfg = get_config(uid)
            await reconcile(uid)

            if past_square_off(cfg):
                result = await square_off_all(uid)
                out[uid] = (f"squared off {result['closed']}"
                            if result["closed"] or result["failed"] else "flat")
                continue

            state = await scan_once(uid)
            await _auto_enter(uid)
            out[uid] = f"{len(state.get('signals') or [])} signal(s)"
        except Exception as exc:                                   # noqa: BLE001
            log.exception("adaptive_edge scan_all failed for %s", uid)
            out[uid] = f"error: {exc}"
    return out


async def auto_scan_loop(interval: int = 60) -> None:
    """Background loop. Survives every per-iteration failure by design."""
    log.info("adaptive_edge auto scan loop started (interval=%ss)", interval)
    while True:
        try:
            await scan_all_once()
        except asyncio.CancelledError:
            raise
        except Exception:                                          # noqa: BLE001
            log.exception("adaptive_edge auto scan iteration failed")
        await asyncio.sleep(max(5, int(interval)))
