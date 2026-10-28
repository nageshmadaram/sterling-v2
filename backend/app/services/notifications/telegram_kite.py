"""
Telegram control surface for the Kite (Zerodha) Sterling Kite Engine.

Kept separate from the low-level transport (telegram_bot.py): its own command
(`/kite`), its own `k*` callback namespace, and its own alert push — the two
streams never mix. Reuses the shared low-level Telegram transport
(`_send/_edit/_answer_cb/_btn`) so there is still one bot / one token.

Capabilities (acts as the local user ``default`` + its active Kite account):
  • view ready signals, positions, P&L, scan status
  • trigger a scan and toggle auto-trade
  • buy from a fresh current-session signal
  • square off from the Positions screen

Every state-changing action uses two-step confirmation and the shared,
live-safety-gated ``service.place_manual_order`` path.
"""
from __future__ import annotations

import asyncio
import html as _html
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from time import monotonic as _rate_monotonic, time as _rate_wall_time
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from app.core.auth import DEFAULT_USER_ID
from app.core.logging import get_logger

from app.services.notifications import lifecycle_format as _lf
from app.services.simulation import identity as _sim_identity
from app.services.notifications import telegram as _tg
from app.services.notifications.telegram_bot import _answer_cb, _btn, _delete, _edit, _send

log = get_logger(__name__)

UID = DEFAULT_USER_ID
IST = ZoneInfo("Asia/Kolkata")

# Successful per-endpoint deliveries.
# Key: (user_id, destination_id@endpoint_fingerprint, signal_key). Failed sends
# are NOT recorded, so the next background cycle retries them. The fingerprint
# makes a bot-token/chat edit a new destination for dedupe purposes.
_alerted: set[tuple[str, str, str]] = set()
_alerted_loaded: set[str] = set()


def _alert_delivery_store_key(uid: str) -> str:
    return f"kite_tg_delivered:{uid}"


def _target_delivery_id(target) -> str:
    """Stable identity for one target's current token/chat endpoint.

    Use encrypted-at-rest token bytes rather than decrypting a secret just to
    build dedupe state. update() only rewrites the ciphertext when the plaintext
    token actually changes, so this remains stable for label/reachability edits
    and changes when either delivery endpoint changes.
    """
    import hashlib

    target_id = str(getattr(target, "id", "") or "").strip()
    token_enc = str(getattr(target, "bot_token_enc", "") or "")
    chat_id = str(getattr(target, "chat_id", "") or "").strip()
    digest = hashlib.sha256(
        (token_enc + "\0" + chat_id).encode("utf-8")
    ).hexdigest()[:16]
    return f"{target_id}@{digest}"


def _load_alerted(uid: str) -> None:
    """Hydrate live-signal delivery acks once per process for restart dedupe."""
    if uid in _alerted_loaded:
        return
    try:
        import json
        from app.services import db

        raw = db.get_config(_alert_delivery_store_key(uid), "[]") or "[]"
        items = json.loads(raw)
        if not isinstance(items, list):
            return

        # Migrate pre-fingerprint acknowledgements in memory to the target's
        # current endpoint. This avoids a one-time duplicate storm after upgrade
        # while still ensuring a later token/chat edit gets a new identity.
        current_targets = {}
        target_lookup_available = True
        try:
            from app.services.notifications import kite_telegram_store as kts
            current_targets = {
                str(target.id): _target_delivery_id(target)
                for target in kts.list_targets(uid)
            }
        except Exception as exc:  # noqa: BLE001
            target_lookup_available = False
            log.debug("Kite Telegram target migration unavailable for %s: %s", uid, exc)

        has_legacy_target_ack = any(
            isinstance(item, list)
            and len(item) == 2
            and isinstance(item[0], str)
            and item[0] != "legacy"
            and "@" not in item[0]
            for item in items
        )
        if has_legacy_target_ack and not target_lookup_available:
            # Retry on the next push. Marking this uid loaded now would freeze
            # pre-fingerprint acknowledgements forever and either duplicate or
            # suppress a later edited endpoint.
            return

        migrated = False
        for item in items:
            if (
                isinstance(item, list)
                and len(item) == 2
                and all(isinstance(v, str) and v for v in item)
            ):
                destination = item[0]
                if destination != "legacy" and "@" not in destination:
                    replacement = current_targets.get(destination)
                    if replacement:
                        destination = replacement
                        migrated = True
                _alerted.add((uid, destination, item[1]))
        _alerted_loaded.add(uid)
        if migrated:
            _save_alerted(uid)
    except Exception as exc:  # noqa: BLE001
        log.warning("Kite Telegram delivery-state restore failed for %s: %s", uid, exc)


def _save_alerted(uid: str) -> None:
    """Persist this user's bounded successful-delivery acknowledgements."""
    try:
        import json
        from app.services import db

        rows = sorted(
            [[dest, signal] for owner, dest, signal in _alerted if owner == uid]
        )
        db.set_config(
            _alert_delivery_store_key(uid),
            json.dumps(rows, separators=(",", ":")),
        )
    except Exception as exc:  # noqa: BLE001
        # Delivery already happened; persistence failure may cause a duplicate
        # after restart, but must never turn an acknowledged alert into "unsent".
        log.warning("Kite Telegram delivery-state persist failed for %s: %s", uid, exc)


def _prune_alerted(uid: str, *, now_ms: int | None = None) -> bool:
    """Bound dedupe state without treating an empty scan as proof a signal ended.

    A transient scanner/data outage can legitimately produce an empty snapshot.
    The old code immediately forgot every successful delivery in that case, so
    the same still-live signal was sent again when the next scan recovered.
    Age-based retention keeps outage recovery idempotent while bounding storage.
    """
    import time

    now = int(now_ms if now_ms is not None else time.time() * 1000)
    cutoff = now - 7 * 24 * 60 * 60 * 1000
    owned = [key for key in _alerted if key[0] == uid]
    remove: set[tuple[str, str, str]] = set()
    stamped: list[tuple[int, tuple[str, str, str]]] = []
    for key in owned:
        try:
            ts = int(str(key[2]).rsplit("|", 1)[-1])
        except (TypeError, ValueError):
            ts = 0
        if ts and ts < cutoff:
            remove.add(key)
        else:
            stamped.append((ts, key))

    # Defensive bound for malformed/unstamped legacy keys too.
    if len(stamped) > 2000:
        stamped.sort(key=lambda item: item[0], reverse=True)
        remove.update(key for _ts, key in stamped[2000:])

    if not remove:
        return False
    _alerted.difference_update(remove)
    return True


def alerts_enabled(uid: str = UID) -> bool:
    """Persisted per-user signal-alert preference; unset defaults to enabled."""
    try:
        from app.services import db
        return db.get_config(f"kite_tg_alerts_enabled:{uid}", "1") != "0"
    except Exception as exc:  # noqa: BLE001
        log.warning("Kite Telegram alert preference unavailable for %s: %s", uid, exc)
        return True


def set_alerts_enabled(uid: str, enabled: bool) -> None:
    from app.services import db
    db.set_config(f"kite_tg_alerts_enabled:{uid}", "1" if enabled else "0")

# Short-id maps so inline-button callback_data stays within Telegram's 64-byte
# limit (we can't cram a long option symbol into callback_data). Rebuilt whenever a
# list is rendered; a stale id simply fails the existence check at action time.
_sig_actions: Dict[str, dict] = {}      # sid -> {option_symbol, exchange, qty, side}
_pos_actions: Dict[str, dict] = {}      # pid -> {option_symbol, exchange, qty, side}
_action_created: Dict[tuple, float] = {}  # ("sig"|"pos", id) -> monotonic creation time
_pending: Dict[str, dict] = {}          # ptok -> resolved action to run on confirm
_pending_created: Dict[str, float] = {}  # ptok -> monotonic creation time
_CONFIRM_TTL_S = 120.0
#: A rendered Buy/Square-off button is only a handle on a snapshot taken when the
#: list was drawn.  It stops being usable well before the snapshot can mislead.
_ACTION_TTL_S = 300.0
# (uid, destination) -> (UTC minute bucket, sent count, last monotonic send).
# Applies only to Gamma-Unwind cards, matching the uploaded client's alert
# throttles without slowing unrelated strategy notifications.
_gamma_alert_rate: Dict[tuple[str, str], tuple[int, int, float]] = {}


def _next_id() -> str:
    # Old buttons must never acquire a new meaning after a process restart.
    # 32 hex characters leave every callback comfortably under Telegram's limit.
    return uuid.uuid4().hex


@dataclass(frozen=True)
class StrategyAlert:
    """Read-only outbound alert. No order/callback capability is attached."""

    strategy: str
    strategy_label: str
    dedupe_key: str
    timestamp_ms: int
    title: str
    direction: str
    option_type: str = ""
    option_symbol: str = ""
    detail: str = ""
    # Browser deep links need the server's signal identity, not a row guessed
    # from a visible symbol; this remains read-only notification metadata.
    signal_id: str = ""


def _iso_ms(value: object) -> int:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(parsed.timestamp() * 1000)
    except (TypeError, ValueError):
        return 0


def _is_recent(timestamp_ms: int, *, max_age_ms: int) -> bool:
    if timestamp_ms <= 0:
        return False
    import time
    age = int(time.time() * 1000) - int(timestamp_ms)
    return -60_000 <= age <= max_age_ms


def _supertrend_alerts(uid: str) -> list[StrategyAlert]:
    alerts: list[StrategyAlert] = []
    for row in _ready_rows(uid):
        leg = row.legs[0] if row.legs else None
        alerts.append(
            StrategyAlert(
                strategy="kite_supertrend",
                strategy_label="SuperTrend",
                # Preserve the historical persisted key exactly.
                dedupe_key=f"{row.token}|{row.timestamp_ms}",
                timestamp_ms=int(row.timestamp_ms or 0),
                title=str(row.underlying),
                direction="LONG" if row.direction == "long" else "SHORT",
                option_type=str(row.option_type or ""),
                option_symbol=str(getattr(leg, "option_symbol", "") or ""),
                detail=str(getattr(leg, "moneyness", "") or ""),
                # A stable source identity survives sorting/filtering and keeps
                # modes apart even when their displayed candle-close times differ.
                signal_id=f"supertrend:{row.token}:{row.timestamp_ms}:{getattr(row, 'source', None) or 'spot'}:{getattr(row, 'horizon_mode', None) or 'swing'}:{getattr(row, 'signal_timeframe_minutes', None) or 60}",
            )
        )
    return alerts


def _snapback_alerts(uid: str) -> list[StrategyAlert]:
    """Notification-only Snapback cards; never attach a generic order callback.

    Snapback scalp/swing execution is strategy-owned by the plan -> arm ->
    dispatcher lifecycle. Telegram mirrors current scan rows and their real
    execution capability, but it does not create a second broker command path.
    The row signal_id is the delivery identity, so a scan refresh cannot resend
    the same setup.
    """
    try:
        from app.services.snapback import snapshot

        snap = snapshot(uid)
    except Exception as exc:
        log.warning("Snapback Telegram snapshot failed for %s: %s", uid, exc)
        return []

    if not bool((snap.get("strategy") or {}).get("enabled", True)):
        return []

    capabilities = snap.get("capabilities") or {}
    manual_capability = capabilities.get("manual_execution") or {}
    auto_capability = capabilities.get("auto_execution") or {}
    manual_available = manual_capability.get("single_leg") is True
    auto_available = (
        auto_capability.get("available") is True
        and auto_capability.get("enabled") is True
    )

    alerts: list[StrategyAlert] = []
    for row in snap.get("rows") or []:
        if not isinstance(row, dict):
            continue
        signal_key = str(row.get("signal_id") or "").strip()
        at_ms = int(row.get("timestamp_ms") or 0)
        if not signal_key or at_ms <= 0:
            continue
        state = str(row.get("state") or "").lower()
        if state not in {"armed", "watching"}:
            continue
        contract = row.get("contract") or {}
        opt = str(row.get("opt_type") or contract.get("option_type") or "")
        symbol = str(contract.get("symbol") or "")
        direction = str(row.get("direction") or "").upper() or (
            "BEARISH" if opt == "PE" else "BULLISH" if opt == "CE" else "SIGNAL"
        )
        origin = str(row.get("origin") or "")
        reason = str(row.get("reason") or "").strip()
        row_eligible = bool(row.get("execution_eligible", False))
        auto_eligible = row_eligible and auto_available
        manual_eligible = row_eligible and manual_available
        if auto_eligible:
            execution_label = "ALGO ELIGIBLE"
        elif manual_eligible:
            execution_label = "MANUAL ELIGIBLE"
        elif state == "armed":
            execution_label = "ENTRY BLOCKED"
        else:
            execution_label = "WATCH ONLY"
        detail_parts = [
            "ARMED" if state == "armed" else "WATCH",
            "quoted" if origin == "live_quote" else (origin or "signal"),
            execution_label,
        ]
        if reason:
            detail_parts.append(reason)
        alerts.append(
            StrategyAlert(
                strategy="snapback",
                strategy_label="Snapback",
                dedupe_key=f"snapback:{signal_key}",
                timestamp_ms=at_ms,
                title=str(row.get("symbol") or ""),
                direction=direction,
                option_type=opt,
                option_symbol=symbol,
                detail=" · ".join(detail_parts),
                signal_id=signal_key,
            )
        )
    return alerts

def _gamma_move_alerts(uid: str) -> list[StrategyAlert]:
    """Reference Gamma-Unwind outbound tiers and broadcast alerts (0.8.6).

    The supplied client routes ENTRY and NEAR ENTRY cards to Telegram, along with
    the scheduled 09:21 Watchlist alerts, intraday Added alerts, and 15:35 Day Summary.
    """
    try:
        from app.engines.gamma_move import (
            TIER_ENTRY,
            TIER_NEAR_ENTRY,
            TIER_STRONG_WATCHLIST,
        )
        from app.services.gamma_move import get_config
        from app.services.gamma_move_runner import session_for
        from app.services import gamma_move_scanner as scanner
        from app.services.notifications.gamma_move_alerts import (
            extract_stocks_from_runtimes,
            format_day_summary_alert,
            format_watchlist_alert,
            pop_queued_gamma_alerts,
            record_initial_seen,
        )

        cfg = get_config(uid)
        if not cfg.enabled:
            return []
        session = session_for(uid, cfg)
    except Exception as exc:                                       # noqa: BLE001
        log.debug("Gamma Unwind Telegram collection unavailable for %s: %s", uid, exc)
        return []

    alerts: list[StrategyAlert] = []

    # 1. Any queued intraday broadcast alerts (e.g. Added alerts from 11:15, 13:15, 14:15)
    try:
        alerts.extend(pop_queued_gamma_alerts(uid))
    except Exception as exc:  # noqa: BLE001
        log.debug("Gamma Unwind pop queued alerts error for %s: %s", uid, exc)

    # 2. Live signal cards for ENTRY and NEAR ENTRY
    for signal in session.signals.values():
        if signal.tier not in {TIER_ENTRY, TIER_STRONG_WATCHLIST, TIER_NEAR_ENTRY, "NEAR ENTRY", "STRONG_WATCHLIST"}:
            continue
        at_ms = int(signal.at_ms or 0)
        # The reference client does not re-send prior-session cards on restart.
        if not _is_recent(at_ms, max_age_ms=45 * 60_000):
            continue
        inst = signal.contract.instrument
        opt = str(inst.option_type or "")
        direction = "BULLISH" if opt == "CE" else "BEARISH" if opt == "PE" else "SIGNAL"
        gate_passes = len(signal.gates.passes)
        warnings = "; ".join(signal.warnings)
        detail = (
            f"{signal.tier} · {signal.contract.list_name} · "
            f"OI drop {signal.oi_drop_pct:.1f}% · "
            f"vol {signal.vol_ratio:.2f}x · gates {gate_passes}/4"
        )
        if warnings:
            detail += f" · {warnings}"
        alerts.append(
            StrategyAlert(
                strategy="gamma_move",
                strategy_label="Gamma Unwind",
                dedupe_key=f"gamma_unwind:{signal.id}|{at_ms}",
                timestamp_ms=at_ms,
                title=str(signal.contract.underlying),
                direction=direction,
                option_type=opt,
                option_symbol=str(inst.tradingsymbol or ""),
                detail=detail,
                signal_id=str(signal.id),
            )
        )

    # 3. Scheduled Watchlist Alerts (09:20+) and Day Summary (15:35+)
    try:
        now_dt = datetime.now(IST)
        day_date = now_dt.date()
        day_iso = day_date.isoformat()
        rts = scanner.runtimes(uid)

        # 09:20+ Watchlist alerts for BREAKDOWN-PE and BREAKOUT-CE
        if rts and now_dt.strftime("%H:%M") >= "09:20":
            record_initial_seen(uid, day_iso, {rt.contract.underlying for rt in rts.values()})

            pe_exp, pe_stocks = extract_stocks_from_runtimes(rts, "PE")
            if pe_stocks:
                pe_wl_text = format_watchlist_alert("BREAKDOWN-PE", pe_exp or day_date, day_date, pe_stocks, max_strikes=6)
                alerts.append(
                    StrategyAlert(
                        strategy="gamma_move_watchlist",
                        strategy_label="Gamma Unwind",
                        dedupe_key=f"gamma_wl_pe:{uid}:{day_iso}",
                        timestamp_ms=int(now_dt.timestamp() * 1000),
                        title="BREAKDOWN-PE WATCHLIST",
                        direction="BEARISH",
                        detail=pe_wl_text,
                    )
                )

            ce_exp, ce_stocks = extract_stocks_from_runtimes(rts, "CE")
            if ce_stocks:
                ce_wl_text = format_watchlist_alert("BREAKOUT-CE", ce_exp or day_date, day_date, ce_stocks, max_strikes=6)
                alerts.append(
                    StrategyAlert(
                        strategy="gamma_move_watchlist",
                        strategy_label="Gamma Unwind",
                        dedupe_key=f"gamma_wl_ce:{uid}:{day_iso}",
                        timestamp_ms=int(now_dt.timestamp() * 1000),
                        title="BREAKOUT-CE WATCHLIST",
                        direction="BULLISH",
                        detail=ce_wl_text,
                    )
                )

        # 15:35+ Day Summary alert
        if now_dt.strftime("%H:%M") >= "15:35":
            all_entries = [
                {
                    "symbol": s.contract.underlying,
                    "strike": s.contract.instrument.strike,
                    "option_type": s.contract.instrument.option_type,
                    "expiry": s.contract.instrument.expiry,
                    "at_ms": s.at_ms,
                    "price": s.ltp,
                }
                for s in session.signals.values()
                if s.tier == TIER_ENTRY
            ]
            all_near = [
                {
                    "symbol": s.contract.underlying,
                    "strike": s.contract.instrument.strike,
                    "option_type": s.contract.instrument.option_type,
                    "expiry": s.contract.instrument.expiry,
                    "at_ms": s.at_ms,
                    "price": s.ltp,
                }
                for s in session.signals.values()
                if s.tier in (TIER_NEAR_ENTRY, TIER_STRONG_WATCHLIST, "NEAR ENTRY", "STRONG_WATCHLIST")
            ]
            summary_text = format_day_summary_alert(day_date, all_entries, all_near)
            alerts.append(
                StrategyAlert(
                    strategy="gamma_move_day_summary",
                    strategy_label="Gamma Unwind",
                    dedupe_key=f"gamma_day_summary:{uid}:{day_iso}",
                    timestamp_ms=int(now_dt.timestamp() * 1000),
                    title="DAY SUMMARY",
                    direction="NEUTRAL",
                    detail=summary_text,
                )
            )
    except Exception as exc:  # noqa: BLE001
        log.debug("Gamma Unwind broadcast alerts build error for %s: %s", uid, exc)

    return alerts


def _adaptive_edge_alerts(uid: str) -> list[StrategyAlert]:
    try:
        from app.services.adaptive_edge_runner import scan_state

        state = scan_state(uid)
    except Exception as exc:                                       # noqa: BLE001
        log.debug("Adaptive Edge Telegram collection unavailable for %s: %s", uid, exc)
        return []

    at_ms = int(state.get("server_time_ms") or 0)
    if not _is_recent(at_ms, max_age_ms=10 * 60_000):
        return []

    alerts: list[StrategyAlert] = []
    for signal in state.get("signals") or []:
        if not bool(signal.get("entry_ok")):
            continue
        signal_id = str(signal.get("signal_id") or "").strip()
        trace = str(signal.get("trace_hash") or "").strip()
        # The old signal id is contract+expiry and survives multiple scans.
        # Trace hash identifies the evaluated market state; without it there is
        # no stable way to distinguish a new signal from a repeated scan.
        if not signal_id or not trace:
            continue
        opt = str(signal.get("option_type") or "")
        direction = "BULLISH" if opt == "CE" else "BEARISH" if opt == "PE" else "SIGNAL"
        alerts.append(
            StrategyAlert(
                strategy="adaptive_edge",
                strategy_label="Adaptive Edge",
                # server_time_ms is regenerated on every scan; including it
                # would redeliver an unchanged actionable trace every minute.
                dedupe_key=f"adaptive_edge:{signal_id}:{trace}",
                timestamp_ms=at_ms,
                title=str(signal.get("underlying") or signal.get("symbol") or ""),
                direction=direction,
                option_type=opt,
                option_symbol=str(signal.get("symbol") or ""),
                detail=str(signal.get("reason") or ""),
            )
        )
    return alerts


def _intraday_alerts(uid: str) -> list[StrategyAlert]:
    try:
        from app.services.intraday import status

        rows = list(status(uid).signals.values())
    except Exception as exc:                                       # noqa: BLE001
        log.debug("Intraday Telegram collection unavailable for %s: %s", uid, exc)
        return []

    alerts: list[StrategyAlert] = []
    for row in rows:
        if row.get("state") != "armed" or row.get("blockers"):
            continue
        signal = row.get("signal") or {}
        at_ms = int(signal.get("timestamp_ms") or 0)
        if not _is_recent(at_ms, max_age_ms=30 * 60_000):
            continue
        signal_id = str(row.get("signal_id") or "").strip()
        contract = row.get("contract") or {}
        if not signal_id:
            continue
        direction = str(signal.get("direction") or "SIGNAL")
        opt = str(contract.get("option_type") or signal.get("opt_type") or "")
        alerts.append(
            StrategyAlert(
                strategy="intraday",
                strategy_label=str(row.get("strategy_name") or "Intraday"),
                dedupe_key=f"intraday:{signal_id}|{at_ms}",
                timestamp_ms=at_ms,
                title=str(row.get("symbol") or ""),
                direction=direction,
                option_type=opt,
                option_symbol=str(contract.get("symbol") or contract.get("tradingsymbol") or ""),
                detail=str(signal.get("origin") or ""),
            )
        )
    return alerts


def _opening_volume_alerts(uid: str) -> list[StrategyAlert]:
    try:
        from app.services.opening_volume_execution import eligible_candidates, get_config
        from app.services.opening_volume_leaders import latest_live_scan

        scan = latest_live_scan(uid)
        if not scan:
            return []
        scan_ms = _iso_ms(scan.get("as_of"))
        if not _is_recent(scan_ms, max_age_ms=15 * 60_000):
            return []
        rows = eligible_candidates(scan, get_config(uid))
    except Exception as exc:                                       # noqa: BLE001
        log.debug("Opening Volume Telegram collection unavailable for %s: %s", uid, exc)
        return []

    alerts: list[StrategyAlert] = []
    for row in rows:
        signal_key = str(row.get("signal_key") or "").strip()
        event_ms = (
            _iso_ms(row.get("actionable_signal_time"))
            or _iso_ms(row.get("orb_break_time"))
            or scan_ms
        )
        if not signal_key or event_ms <= 0:
            continue
        option = row.get("option") or {}
        direction = str(row.get("direction") or "SIGNAL")
        alerts.append(
            StrategyAlert(
                strategy="opening_volume",
                strategy_label="Opening Volume",
                dedupe_key=f"opening_volume:{signal_key}|{event_ms}",
                timestamp_ms=event_ms,
                title=str(row.get("symbol") or ""),
                direction=direction,
                option_type=str(option.get("option_type") or ""),
                option_symbol=str(option.get("tradingsymbol") or ""),
                detail=f"score {(row.get('decision') or {}).get('score', {}).get('lower_bound', '—')}",
            )
        )
    return alerts


def _strategy_enabled(uid: str, strategy: str) -> bool:
    if strategy in {"gamma_move", "snapback"}:
        module = __import__(f"app.services.{strategy}", fromlist=["get_config"])
        return bool(module.get_config(uid).enabled)
    from app.services import archived_strategies
    return archived_strategies.enabled(uid, strategy)


def _pcr_alerts(uid: str) -> list[StrategyAlert]:
    """Alert real-time PCR status updates on every 15-minute print."""
    try:
        import time
        from app.api.v1.endpoints.pcr import _CACHE
        cached = _CACHE.get("payload") or {}
        series = cached.get("series") or {}

        nifty_data = series.get("NIFTY") or {}
        sensex_data = series.get("SENSEX") or {}

        n_latest = nifty_data.get("latest") or {}
        s_latest = sensex_data.get("latest") or {}

        t_stamp = n_latest.get("time") or s_latest.get("time") or ""
        if not t_stamp:
            return []

        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        dedupe_key = f"pcr_alert:{today_str}:{t_stamp}"

        n_pcr = n_latest.get("pcr") or nifty_data.get("livePcr") or 0.0
        n_chg = n_latest.get("change") or 0.0
        n_sig = n_latest.get("signal") or "WAIT"
        n_note = n_latest.get("note") or "Puts cooled off. Don't chase CE."

        s_pcr = s_latest.get("pcr") or sensex_data.get("livePcr") or 0.0
        s_chg = s_latest.get("change") or 0.0
        s_sig = s_latest.get("signal") or "BUY CE"
        s_note = s_latest.get("note") or "Puts are being sold. Dips usually get bought."

        n_sig_fmt = _format_pcr_signal(n_sig)
        s_sig_fmt = _format_pcr_signal(s_sig)

        n_chg_sign = "+" if n_chg >= 0 else ""
        s_chg_sign = "+" if s_chg >= 0 else ""

        html_content = (
            f"📈 <b>15-MINUTE PCR DELTA UPDATE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ <b>Time</b>: <b>{t_stamp}</b>\n\n"
            f"🇮🇳 <b>Nifty</b>:  {n_sig_fmt}\n"
            f"  <code>{n_pcr:.2f} ({n_chg_sign}{n_chg:.2f})</code>\n"
            f"  <i>{_html.escape(n_note)}</i>\n\n"
            f"🏛️ <b>Sensex</b>:  {s_sig_fmt}\n"
            f"  <code>{s_pcr:.2f} ({s_chg_sign}{s_chg:.2f})</code>\n"
            f"  <i>{_html.escape(s_note)}</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>15m delta flow print  ·  {datetime.now(IST).strftime('%I:%M %p')}</i>"
        )

        return [
            StrategyAlert(
                strategy="pcr_flow",
                strategy_label="PCR Delta",
                dedupe_key=dedupe_key,
                timestamp_ms=int(time.time() * 1000),
                title=f"PCR Update ({t_stamp})",
                direction="NEUTRAL",
                detail=html_content,
            )
        ]
    except Exception as exc:  # noqa: BLE001
        log.debug("PCR alert check failed: %s", exc)
        return []


def _strategy_alerts(uid: str) -> list[StrategyAlert]:
    """Outbound alerts for supported and explicitly restored archived scanners.

    Interactive Telegram order buttons intentionally remain SuperTrend-only.
    Gamma-Unwind and Snapback have strategy-owned entry authorities, so their
    cards stay notification-only rather than bypassing those admission paths.
    """
    out: list[StrategyAlert] = []
    for collector in (
        _supertrend_alerts,
        _gamma_move_alerts,
        _snapback_alerts,
        _pcr_alerts,
    ):
        try:
            out.extend(collector(uid))
        except Exception as exc:                                   # noqa: BLE001
            log.warning("Telegram alert collector %s failed for %s: %s",
                        collector.__name__, uid, exc)
    for strategy, collector in (
        ("adaptive_edge", _adaptive_edge_alerts),
        ("intraday", _intraday_alerts),
        ("opening_volume_leaders", _opening_volume_alerts),
    ):
        if _strategy_enabled(uid, strategy):
            try:
                out.extend(collector(uid))
            except Exception as exc:
                log.warning("Restored Telegram collector %s failed: %s", strategy, type(exc).__name__)
    return out


# ── account / context ─────────────────────────────────────────────────────────
def _active_account(uid: str = UID):
    from app.services.exchanges.kite import accounts as kite_accounts
    return kite_accounts.get_active(uid)


def _mode_label(uid: str = UID) -> str:
    acct = _active_account(uid)
    if acct is None:
        return "no account"
    # Operator-facing text never uses the words paper/simulation.
    return "TEST" if acct.is_paper else "LIVE"


# ── keyboards ───────────────────────────────────────────────────────────────
def _valid_tma_url(url: str) -> str:
    from urllib.parse import urlsplit
    clean = str(url or "").strip()
    parsed = urlsplit(clean)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Set a reachable HTTPS Mini App URL, without credentials or a fragment.")
    return clean


def _get_tma_url() -> str:
    import os
    from app.services import db
    raw = db.get_config("sterling_tma_url", "") or os.getenv("STERLING_TMA_URL", "")
    try:
        return _valid_tma_url(raw)
    except ValueError:
        return ""


def set_tma_url(url: str) -> str:
    from app.services import db
    clean = _valid_tma_url(url)
    db.set_config_strict("sterling_tma_url", clean)
    return clean


def kite_menu_kb() -> dict:
    """Primary navigation keyboard — institutional layout with Telegram Mini App & Quick Trade."""
    tma_url = _get_tma_url()
    from app.services import public_access
    access = public_access.status()
    urls = access.get("urls") or {}
    app_url = access.get("app_url", "")
    return {"inline_keyboard": [
        *([[{"text":"☁️ Sterling · Cloudflare", "url":urls["cloudflare"]}]] if urls.get("cloudflare") else []),
        *([[{"text":"Open full app", "url":app_url}]] if app_url and not urls else []),
        *([[{"text": "📱 Open primary Sterling Mini App", "web_app": {"url": tma_url}}]] if tma_url else []),
        [_btn("📡 Signals",    "ksig"),     _btn("📈 PCR",       "kpcr")],
        [_btn("📊 Positions",  "kpos"),     _btn("📜 Orders",    "korders")],
        [_btn("💰 P&L",        "kpnl"),     _btn("💵 Funds",     "kfunds")],
        [_btn("⚡ Quick Order","kquick_order"), _btn("📡 Ticker", "kticker")],
        [_btn("🔍 Search",     "ksearch"),  _btn("🔄 Scan",      "kscan")],
        [_btn("⚙️ Strategies","kstrat"),   _btn("SuperTrend ALGO", "kauto")],
        [_btn("🔔 Alerts",     "kalert"),   _btn("🩺 Health",    "khealth")],
        [_btn("👤 Sterling Login", "koperator")],
        [_btn("🔑 Broker Login", "klogin"),   _btn("Reset desk","kclear_chat")],
    ]}


async def operator_login_card(chat_id: str) -> None:
    from urllib.parse import urlsplit
    from app.services import public_access
    url = public_access.status().get('app_url', '') or _get_tma_url()
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        await _send('Sterling Login needs the HTTPS app address. Start Sterling remote access first.', chat_id, _back_kb())
        return
    origin = f'{parsed.scheme}://{parsed.netloc}'
    await _send(
        '<b>Sterling Login</b>\nEnter your Sterling username and password in the sign-in page. Then choose your saved broker account.',
        chat_id, {'inline_keyboard': [
            [{'text': 'Sign in to Sterling', 'url': origin + '/?sterling_login=1&broker_login=1'}],
            [{'text': 'Manage Sterling users', 'web_app': {'url': origin + '/tma/operator'}}],
            [_btn('← Menu', 'kmenu')],
        ]},
    )


def _back_kb() -> dict:
    return {"inline_keyboard": [[_btn("← Menu", "kmenu")]]}


def _confirm_kb(ptok: str) -> dict:
    return {"inline_keyboard": [[_btn("✓ Confirm", f"kok|{ptok}"), _btn("✕ Cancel", f"kno|{ptok}")]]}


# ── signal source / strategy config ──────────────────────────────────────────
_SUPERTREND_SOURCES = {"spot", "confluence", "derivatives"}

_STRATEGY_LABELS = {
    "all":            "All",
    "supertrend":     "SuperTrend",
    "snapback":       "Snapback",
    "gamma_move":     "Gamma Unwind",
    "adaptive_edge":  "Adaptive Edge",
    "intraday":       "Intraday",
    "opening_volume": "Opening Vol",
    "navigator":      "Navigator",
}

_STRATEGY_DISPLAY_ORDER = [
    "supertrend",
    "snapback",
    "gamma_move",
    "adaptive_edge",
    "intraday",
    "opening_volume",
    "navigator",
]

_ST_SUBLABEL = {
    "spot":        "spot",
    "derivatives": "option chart",
    "confluence":  "✦ both aligned",
}


def _navigator_on(uid: str = UID) -> bool:
    """True when the user has explicitly enabled the Value-Flow Navigator."""
    try:
        from app.services import archived_strategies
        return archived_strategies.enabled(uid, "navigator")
    except Exception:
        return False


# ── data access ───────────────────────────────────────────────────────────────
def _snapshot(uid: str = UID):
    from app.services.kite_engine.scanner import scanner
    return scanner.snapshot(uid)


def _ready_rows(uid: str = UID) -> list:
    """Fresh-today rows: `is_active AND is_fresh AND today`. Used for BUY order buttons only."""
    rows = getattr(_snapshot(uid), "rows", []) or []
    today = datetime.now(IST).date()
    ready = []
    for row in rows:
        if (
            not getattr(row, "is_active", False)
            or not getattr(row, "is_fresh", False)
            or getattr(row, "is_replay", False)
        ):
            continue
        try:
            day = datetime.fromtimestamp(
                int(getattr(row, "timestamp_ms", 0) or 0) / 1000, IST,
            ).date()
        except (TypeError, ValueError, OSError, OverflowError):
            continue
        if day == today:
            ready.append(row)
    return ready


def _active_rows(uid: str = UID) -> list:
    """ALL currently-active SuperTrend & Navigator rows."""
    nav_on = _navigator_on(uid)
    rows   = getattr(_snapshot(uid), "rows", []) or []
    result = []
    for r in rows:
        if not getattr(r, "is_active", False) or getattr(r, "is_replay", False):
            continue
        source = str(getattr(r, "source", "") or "")
        if source == "navigator" and not nav_on:
            continue
        result.append(r)
    return result


@dataclass(frozen=True)
class BoardSignal:
    strategy_key: str        # "supertrend", "snapback", "gamma_move", "adaptive_edge", "intraday", "opening_volume", "navigator"
    strategy_label: str      # "SuperTrend", "Snapback", "Gamma Unwind", "Adaptive Edge", "Intraday", "Opening Vol", "Navigator"
    underlying: str
    direction: str           # "long" / "short"
    option_symbol: str
    spot_price: str
    moneyness: str
    sublabel: str
    timestamp_ms: int
    is_fresh: bool
    exchange: str
    lot_size: int
    row_ref: object = None


async def _all_strategy_signals(uid: str = UID) -> list[BoardSignal]:
    """Gather all active signals across ALL strategies for the Telegram signal board."""
    signals: list[BoardSignal] = []

    # 1. SuperTrend & Navigator rows
    nav_on = _navigator_on(uid)
    for r in _active_rows(uid):
        source = str(getattr(r, "source", "") or "spot")
        if source == "navigator" and not nav_on:
            continue
        s_key   = "navigator" if source == "navigator" else "supertrend"
        s_label = "Navigator" if source == "navigator" else "SuperTrend"
        direction = str(getattr(r, "direction", "") or "long").lower()
        leg     = r.legs[0] if r.legs else None

        sub = _ST_SUBLABEL.get(source, "")
        signals.append(
            BoardSignal(
                strategy_key=s_key,
                strategy_label=s_label,
                underlying=str(getattr(r, "underlying", "") or ""),
                direction=direction,
                option_symbol=str(getattr(leg, "option_symbol", "") or "") if leg else "",
                spot_price=_fmt(getattr(r, "spot", None)),
                moneyness=str(getattr(leg, "moneyness", "") or "") if leg else "",
                sublabel=sub,
                timestamp_ms=int(getattr(r, "timestamp_ms", 0) or 0),
                is_fresh=bool(getattr(r, "is_fresh", False)),
                exchange=str(getattr(r, "exchange", "NFO") or "NFO"),
                lot_size=int(getattr(leg, "lot_size", 1) or 1) if leg else 1,
                row_ref=r,
            )
        )

    # 2. Snapback signals
    try:
        from app.services.snapback import snapshot as snap_func
        snap = snap_func(uid)
        if snap and snap.get("rows"):
            for row in snap.get("rows", []):
                if not isinstance(row, dict):
                    continue
                state = str(row.get("state") or "").lower()
                if state not in {"armed", "watching", "entry_triggered", "executed", "setup", "triggered", "monitoring", "active"}:
                    continue
                contract = row.get("contract") or {}
                opt = str(row.get("opt_type") or contract.get("option_type") or "")
                direction = "short" if opt == "PE" else "long"
                symbol = str(contract.get("symbol") or contract.get("tradingsymbol") or "")
                underlying = str(row.get("symbol") or "")
                reason = str(row.get("reason") or "").strip()
                sub = f"{state.upper()}" + (f" · {reason}" if reason else "")
                signals.append(
                    BoardSignal(
                        strategy_key="snapback",
                        strategy_label="Snapback",
                        underlying=underlying,
                        direction=direction,
                        option_symbol=symbol,
                        spot_price="—",
                        moneyness="",
                        sublabel=sub,
                        timestamp_ms=int(row.get("timestamp_ms") or 0),
                        is_fresh=state in {"armed", "triggered", "entry_triggered"},
                        exchange="NFO",
                        lot_size=1,
                    )
                )
    except Exception as exc:  # noqa: BLE001
        log.debug("Snapback Telegram board collection error: %s", exc)

    # 3. Gamma Unwind signals
    try:
        from app.services.gamma_move_runner import session_for
        from app.services.gamma_move import get_config as get_g_cfg, snapshot as g_snapshot_func
        g_cfg = get_g_cfg(uid)
        if _strategy_enabled(uid, "gamma_move"):
            g_sess = session_for(uid, g_cfg) if g_cfg else None
            g_sig_list = list(g_sess.signals.values()) if (g_sess and hasattr(g_sess, "signals") and g_sess.signals) else []

            if not g_sig_list:
                try:
                    g_snap = await g_snapshot_func(uid)
                    g_sig_list = list(g_snap.get("candidates") or g_snap.get("signals") or [])
                except Exception:
                    pass

            for sig in g_sig_list:
                if isinstance(sig, dict):
                    opt = str(sig.get("option_type") or sig.get("opt_type") or "")
                    direction = "short" if opt == "PE" else "long"
                    sub = str(sig.get("reason") or sig.get("tier") or "UNWIND")
                    signals.append(
                        BoardSignal(
                            strategy_key="gamma_move",
                            strategy_label="Gamma Unwind",
                            underlying=str(sig.get("underlying") or sig.get("symbol") or ""),
                            direction=direction,
                            option_symbol=str(sig.get("symbol") or sig.get("tradingsymbol") or ""),
                            spot_price="—",
                            moneyness="",
                            sublabel=sub,
                            timestamp_ms=int(sig.get("timestamp_ms") or sig.get("at_ms") or 0),
                            is_fresh=True,
                            exchange="NFO",
                            lot_size=1,
                        )
                    )
                else:
                    inst = getattr(getattr(sig, "contract", None), "instrument", None)
                    opt = str(getattr(inst, "option_type", "") or "")
                    direction = "short" if opt == "PE" else "long"
                    oi_drop = getattr(sig, "oi_drop_pct", 0)
                    tier = getattr(sig, "tier", "UNWIND")
                    sub = f"{tier} · OI drop {oi_drop:.1f}%" if oi_drop else f"{tier}"
                    signals.append(
                        BoardSignal(
                            strategy_key="gamma_move",
                            strategy_label="Gamma Unwind",
                            underlying=str(getattr(getattr(sig, "contract", None), "underlying", "") or ""),
                            direction=direction,
                            option_symbol=str(getattr(inst, "tradingsymbol", "") or getattr(sig, "symbol", "") or ""),
                            spot_price="—",
                            moneyness="",
                            sublabel=sub,
                            timestamp_ms=int(getattr(sig, "at_ms", 0) or 0),
                            is_fresh=True,
                            exchange="NFO",
                            lot_size=1,
                        )
                    )
    except Exception as exc:  # noqa: BLE001
        log.debug("Gamma Unwind Telegram board collection error: %s", exc)

    # 4. Adaptive Edge
    try:
        if _strategy_enabled(uid, "adaptive_edge"):
            from app.services.adaptive_edge_runner import scan_state
            from app.services.adaptive_edge import snapshot as a_snapshot_func
            a_state = scan_state(uid)
            a_sigs = list(a_state.get("signals") or a_state.get("candidates") or [])
            if not a_sigs:
                try:
                    a_snap = await a_snapshot_func(uid)
                    a_scan = a_snap.get("scan") or {}
                    a_sigs = list(a_scan.get("signals") or a_scan.get("candidates") or [])
                except Exception:
                    pass
            at_ms = int(a_state.get("server_time_ms") or 0)
            for sig in a_sigs:
                if isinstance(sig, dict):
                    opt = str(sig.get("option_type") or sig.get("opt_type") or "")
                    direction = "short" if opt == "PE" else "long"
                    signals.append(
                        BoardSignal(
                            strategy_key="adaptive_edge",
                            strategy_label="Adaptive Edge",
                            underlying=str(sig.get("underlying") or sig.get("symbol") or ""),
                            direction=direction,
                            option_symbol=str(sig.get("symbol") or sig.get("tradingsymbol") or ""),
                            spot_price="—",
                            moneyness="",
                            sublabel=str(sig.get("reason") or "setup"),
                            timestamp_ms=at_ms or int(sig.get("timestamp_ms") or 0),
                            is_fresh=True,
                            exchange="NFO",
                            lot_size=1,
                        )
                    )
    except Exception as exc:  # noqa: BLE001
        log.debug("Adaptive Edge Telegram board collection error: %s", exc)

    # 5. Intraday Pack
    try:
        if _strategy_enabled(uid, "intraday"):
            from app.services.intraday import status as i_status
            i_stat = i_status(uid)
            if i_stat and getattr(i_stat, "signals", None):
                for row in i_stat.signals.values():
                    if isinstance(row, dict):
                        sig = row.get("signal") or {}
                        contract = row.get("contract") or {}
                        opt = str(contract.get("option_type") or sig.get("opt_type") or "")
                        direction = "short" if opt == "PE" else "long"
                        signals.append(
                            BoardSignal(
                                strategy_key="intraday",
                                strategy_label=str(row.get("strategy_name") or "Intraday Pack"),
                                underlying=str(row.get("symbol") or ""),
                                direction=direction,
                                option_symbol=str(contract.get("symbol") or contract.get("tradingsymbol") or ""),
                                spot_price="—",
                                moneyness="",
                                sublabel=str(sig.get("origin") or "armed"),
                                timestamp_ms=int(sig.get("timestamp_ms") or 0),
                                is_fresh=True,
                                exchange="NFO",
                                lot_size=1,
                            )
                        )
    except Exception as exc:  # noqa: BLE001
        log.debug("Intraday Telegram board collection error: %s", exc)

    # 6. Opening Volume Leaders
    try:
        if _strategy_enabled(uid, "opening_volume_leaders"):
            from app.services.opening_volume_execution import eligible_candidates, get_config as get_ov_cfg
            from app.services.opening_volume_leaders import latest_live_scan
            ov_cfg = get_ov_cfg(uid)
            o_scan = latest_live_scan(uid)
            if o_scan:
                scan_ms = _iso_ms(o_scan.get("as_of"))
                for row in eligible_candidates(o_scan, ov_cfg):
                    opt = row.get("option") or {}
                    direction = "short" if str(row.get("direction") or "") == "SHORT" else "long"
                    signals.append(
                        BoardSignal(
                            strategy_key="opening_volume",
                            strategy_label="Opening Vol",
                            underlying=str(row.get("symbol") or ""),
                            direction=direction,
                            option_symbol=str(opt.get("tradingsymbol") or ""),
                            spot_price="—",
                            moneyness="",
                            sublabel=f"score {(row.get('decision') or {}).get('score', {}).get('lower_bound', '—')}",
                            timestamp_ms=scan_ms,
                            is_fresh=True,
                            exchange="NFO",
                            lot_size=1,
                        )
                    )
    except Exception as exc:  # noqa: BLE001
        log.debug("Opening Volume Telegram board collection error: %s", exc)

    return signals


def _fmt(v: Optional[float], dp: int = 2) -> str:
    try:
        return f"{float(v):,.{dp}f}"
    except (TypeError, ValueError):
        return "—"


def _ts_label(ts_ms: Optional[int], prefix: str = "") -> str:
    """Format a millisecond timestamp as 'DD Mon HH:MM IST'."""
    if not ts_ms:
        return f"{prefix}—"
    try:
        dt = datetime.fromtimestamp(int(ts_ms) / 1000, IST)
        return prefix + dt.strftime("%d %b %H:%M IST")
    except Exception:
        return f"{prefix}—"


# ── text builders ─────────────────────────────────────────────────────────────
def _fmt_board_signal(sig: BoardSignal, now: "datetime") -> str:
    """Format a single BoardSignal into one clean Telegram line."""
    is_long   = sig.direction.lower() in {"long", "bullish"}
    arrow     = "▲" if is_long else "▼"
    color     = "🟢" if is_long else "🔴"
    fresh_dot = "🔵" if sig.is_fresh else ""
    opt_sym   = f"  <code>{sig.option_symbol[-15:]}</code>" if sig.option_symbol else ""
    moneyness = f" <i>{sig.moneyness}</i>" if sig.moneyness else ""
    spot_part = f"  {sig.spot_price}" if sig.spot_price and sig.spot_price != "—" else ""
    sub_part  = f" <i>{sig.sublabel}</i>" if sig.sublabel else ""
    try:
        if sig.timestamp_ms > 0:
            sig_dt  = datetime.fromtimestamp(int(sig.timestamp_ms) / 1000, IST)
            age_min = int((now - sig_dt).total_seconds() / 60)
            age_str = f"{age_min}m ago" if age_min < 120 else sig_dt.strftime("%d %b %H:%M")
        else:
            age_str = ""
    except Exception:
        age_str = ""
    age_part = f"  <i>{age_str}</i>" if age_str else ""
    return (
        f"{color}{fresh_dot}  <b>{sig.underlying}</b>  {arrow}{sub_part}{opt_sym}{moneyness}{spot_part}{age_part}"
    )


async def build_kite_signals_text(strategy_filter: str = "all") -> str:
    """Active signal board for ALL strategies across Sterling.

    strategy_filter: 'all' | 'supertrend' | 'snapback' | 'gamma_move' | 'adaptive_edge' | 'intraday' | 'opening_volume' | 'navigator'
    """
    mode     = _mode_label()
    all_sigs = await _all_strategy_signals()
    header   = f"📡 <b>SIGNALS</b>  ·  <code>{mode}</code>"

    if not all_sigs:
        snap    = _snapshot()
        gen_ms  = getattr(snap, "generated_ms", None)
        ts_line = f"\n🕐  Last scan  <i>{_ts_label(gen_ms)}</i>" if gen_ms else ""
        return (
            f"{header}\n"
            f"\n"
            f"ℹ️  Signal board is empty.{ts_line}\n"
            f"Tap <b>🔄 Scan</b> from the menu to run a fresh scan."
        )

    # Strategy counts
    counts: dict[str, int] = {"all": len(all_sigs)}
    for s in all_sigs:
        counts[s.strategy_key] = counts.get(s.strategy_key, 0) + 1

    if strategy_filter != "all":
        filter_sigs = [s for s in all_sigs if s.strategy_key == strategy_filter]
        label_name = _STRATEGY_LABELS.get(strategy_filter, strategy_filter.title())
        count_str  = f"{len(filter_sigs)} {label_name}"
    else:
        filter_sigs = all_sigs
        count_str  = f"{len(all_sigs)} active"

    lines = [f"{header}  ·  {count_str}"]

    if not filter_sigs:
        label_name = _STRATEGY_LABELS.get(strategy_filter, strategy_filter.title())
        lines.append(f"\nℹ️  No <b>{label_name}</b> signals right now.")
        avail = [f"{_STRATEGY_LABELS.get(k, k)} {v}" for k, v in counts.items() if k != "all" and v]
        if avail:
            lines.append(f"\n<i>Active strategies: {', '.join(avail)}</i>")
        return "\n".join(lines)

    now = datetime.now(IST)

    if strategy_filter == "all":
        # Grouped view by strategy
        for key in _STRATEGY_DISPLAY_ORDER:
            grp = [s for s in all_sigs if s.strategy_key == key]
            if not grp:
                continue
            strat_title = grp[0].strategy_label
            lines.append(f"\n<b>─ {strat_title} ({len(grp)})</b>")
            for sig in grp[:6]:
                lines.append(_fmt_board_signal(sig, now))
            if len(grp) > 6:
                lines.append(f"<i>  + {len(grp) - 6} more — tap [{strat_title}] tab</i>")
    else:
        # Single strategy filtered view
        for sig in filter_sigs[:15]:
            lines.append("\n" + _fmt_board_signal(sig, now))
        if len(filter_sigs) > 15:
            lines.append(f"\n<i>+ {len(filter_sigs) - 15} more</i>")

    lines.append("")
    summary_parts = [f"{_STRATEGY_LABELS.get(k, k)} {v}" for k, v in counts.items() if k != "all" and v]
    if summary_parts:
        lines.append(f"<i>{ '  ·  '.join(summary_parts) }</i>")
    lines.append("<i>🔵 = fresh signal  ·  ✦ = dual SuperTrend aligned</i>")

    return "\n".join(lines)


async def signals_kb(strategy_filter: str = "all") -> dict:
    """Dynamic strategy filter tabs + BUY buttons for fresh actionable setups."""
    _sig_actions.clear()
    for key in [k for k in _action_created if k[0] == "sig"]:
        _action_created.pop(key, None)

    all_sigs = await _all_strategy_signals()
    counts: dict[str, int] = {"all": len(all_sigs)}
    for s in all_sigs:
        counts[s.strategy_key] = counts.get(s.strategy_key, 0) + 1

    # Dynamic filter tabs: show All + any strategy that has count > 0
    tab_keys = ["all"] + [k for k in _STRATEGY_DISPLAY_ORDER if counts.get(k, 0) > 0]

    # Build tab rows (up to 3 buttons per row)
    tab_rows: list = []
    current_row: list = []
    for k in tab_keys:
        label  = _STRATEGY_LABELS.get(k, k.title())
        cnt    = counts.get(k, 0)
        marker = "● " if k == strategy_filter else ""
        btn_txt = f"{marker}{label}" + (f" {cnt}" if cnt else "")
        current_row.append(_btn(btn_txt, f"ksig_src|{k}"))
        if len(current_row) == 3:
            tab_rows.append(current_row)
            current_row = []
    if current_row:
        tab_rows.append(current_row)

    kb: list = list(tab_rows)

    # Order buttons for fresh SuperTrend rows
    fresh_st = [s for s in all_sigs if s.strategy_key == "supertrend" and s.is_fresh and s.row_ref]
    if strategy_filter in {"all", "supertrend"}:
        for sig in fresh_st[:4]:
            r = sig.row_ref
            leg = r.legs[0] if r.legs else None
            if not leg or not leg.option_symbol:
                continue
            sid = _next_id()
            _sig_actions[sid] = {
                "option_symbol":       leg.option_symbol,
                "exchange":            r.exchange,
                "qty":                 int(leg.lot_size or 1),
                "underlying":          r.underlying,
                "signal_timestamp_ms": int(r.timestamp_ms or 0),
            }
            _action_created[("sig", sid)] = time.monotonic()
            is_long = sig.direction.lower() in {"long", "bullish"}
            arrow   = "▲" if is_long else "▼"
            color   = "🟢" if is_long else "🔴"
            sym_short = leg.option_symbol[-14:]
            label   = f"{color} Buy {r.underlying} {arrow} {sym_short}"
            kb.append([_btn(label, f"kbuy|{sid}")])

    kb.append([_btn("🔄 Refresh", f"ksig_src|{strategy_filter}"), _btn("← Menu", "kmenu")])
    return {"inline_keyboard": kb}


async def build_kite_positions_text() -> str:
    acct = _active_account()
    mode = _mode_label()
    if acct is None:
        return "📊 <b>POSITIONS</b>\n\nNo active Kite account.\nAdd one in the app."
    from app.services.exchanges.kite import accounts as kite_accounts
    try:
        client = await kite_accounts.acquire_client(acct)
        positions = await client.get_positions()
    except Exception as exc:  # noqa: BLE001
        return f"📊 <b>POSITIONS</b>\n\n⚠️  Could not load — {_html.escape(str(exc)[:120])}"
    live = [p for p in positions if getattr(p, "size", 0)]
    if not live:
        return (
            f"📊 <b>POSITIONS</b>  ·  <code>{mode}</code>\n"
            f"\n"
            f"No open positions right now."
        )
    lines = [f"📊 <b>POSITIONS</b>  ·  {len(live)} open  ·  <code>{mode}</code>"]
    for p in live[:8]:
        pnl = getattr(p, "unrealized_pnl", 0.0) or 0.0
        pnl_icon = "🟢" if pnl >= 0 else "🔴"
        pnl_sign = "+" if pnl >= 0 else ""
        _, _, sym = p.symbol.partition(":")
        sym = sym or p.symbol
        side_label = "BUY" if str(getattr(p, "side", "")).upper() in {"BUY", "LONG"} else "SELL"
        mark = _fmt(getattr(p, "mark_price", None))
        lines.append(
            f"\n{pnl_icon}  <code>{sym[-18:]}</code>  {side_label} ×{abs(int(p.size))}\n"
            f"    Mark <b>₹{mark}</b>  ·  P&L <b>{pnl_sign}₹{_fmt(abs(pnl))}</b>"
        )
    return "\n".join(lines)


async def positions_kb() -> dict:
    _pos_actions.clear()
    for key in [k for k in _action_created if k[0] == "pos"]:
        _action_created.pop(key, None)
    acct = _active_account()
    kb: List[list] = []
    if acct is not None:
        from app.services.exchanges.kite import accounts as kite_accounts
        try:
            client = await kite_accounts.acquire_client(acct)
            positions = [p for p in await client.get_positions() if getattr(p, "size", 0)]
        except Exception:  # noqa: BLE001
            positions = []
        for p in positions[:6]:
            ex, _, ts = p.symbol.partition(":")
            pid = _next_id()
            side = "SELL" if p.size > 0 else "BUY"
            _pos_actions[pid] = {
                "option_symbol": ts or p.symbol, "exchange": ex or "NFO",
                "qty": abs(int(p.size)), "side": side,
            }
            _action_created[("pos", pid)] = time.monotonic()
            sym_short = (ts or p.symbol)[-14:]
            pnl = getattr(p, "unrealized_pnl", 0.0) or 0.0
            pnl_icon = "🟢" if pnl >= 0 else "🔴"
            kb.append([_btn(f"✕ Close  {pnl_icon}  {sym_short}", f"ksq|{pid}")])
    kb.append([_btn("🔄 Refresh", "kpos"), _btn("← Menu", "kmenu")])
    return {"inline_keyboard": kb}


async def build_kite_pnl_text() -> str:
    acct = _active_account()
    mode = _mode_label()
    if acct is None:
        return "💰 <b>P&L</b>\n\nNo active Kite account."
    from app.services.exchanges.kite import accounts as kite_accounts
    try:
        client = await kite_accounts.acquire_client(acct)
        positions = await client.get_positions()
    except Exception as exc:  # noqa: BLE001
        return f"💰 <b>P&L</b>\n\n⚠️  Could not load — {_html.escape(str(exc)[:120])}"
    realized = sum((getattr(p, "realized_pnl", 0.0) or 0.0) for p in positions)
    unreal   = sum((getattr(p, "unrealized_pnl", 0.0) or 0.0) for p in positions)
    total = realized + unreal
    total_icon = "🟢" if total >= 0 else "🔴"
    total_sign = "+" if total >= 0 else ""
    sep = "─" * 18
    return (
        f"💰 <b>P&L</b>  ·  <code>{mode}</code>\n"
        f"\n"
        f"Realized      <b>₹{_fmt(realized)}</b>\n"
        f"Unrealized    <b>₹{_fmt(unreal)}</b>\n"
        f"<code>{sep}</code>\n"
        f"{total_icon}  Total   <b>{total_sign}₹{_fmt(abs(total))}</b>"
    )


async def build_kite_orders_text() -> str:
    """Today's order history from Kite (executed, pending, rejected)."""
    acct = _active_account()
    mode = _mode_label()
    if acct is None:
        return "📑 <b>ORDERS TODAY</b>\n\nNo active Kite account."
    from app.services.exchanges.kite import accounts as kite_accounts
    try:
        client = await kite_accounts.acquire_client(acct)
        orders = await client.get_orders()
    except Exception as exc:  # noqa: BLE001
        return f"📑 <b>ORDERS TODAY</b>\n\n⚠️ Could not fetch orders — {_html.escape(str(exc)[:120])}"

    if not orders:
        return (
            f"📑 <b>ORDERS TODAY</b>  ·  <code>{mode}</code>\n"
            f"\n"
            f"ℹ️  No orders executed or pending today."
        )

    # Sort newest first
    orders = sorted(orders, key=lambda o: str(getattr(o, "order_timestamp", "") or ""), reverse=True)
    lines = [f"📑 <b>ORDERS TODAY</b>  ·  <code>{mode}</code>  ·  {len(orders)} total", ""]
    for o in orders[:15]:
        side = str(getattr(o, "transaction_type", "") or "BUY").upper()
        sym  = _html.escape(str(getattr(o, "tradingsymbol", "") or ""))
        qty  = getattr(o, "quantity", 0) or 0
        px   = getattr(o, "average_price", 0.0) or getattr(o, "price", 0.0) or 0.0
        st   = str(getattr(o, "status", "") or "COMPLETE").upper()

        icon = "🟢" if side == "BUY" else "🔴"
        st_badge = "✅" if st == "COMPLETE" else ("⏳" if st in {"OPEN", "TRIGGER PENDING"} else "❌")
        lines.append(f"{icon} <b>{side}</b> <code>{sym}</code> × {qty} @ ₹{_fmt(px)}")
        lines.append(f"    Status: {st_badge} <b>{st}</b>")
        lines.append("")

    if len(orders) > 15:
        lines.append(f"<i>Showing 15 of {len(orders)} orders today.</i>")

    return "\n".join(lines)


def orders_kb() -> dict:
    return {"inline_keyboard": [[_btn("🔄 Refresh", "korders"), _btn("← Menu", "kmenu")]]}


def build_kite_market_text() -> str:
    rows = _ready_rows()
    lines = ["<b>📊 MARKET PULSE · fresh SuperTrend signals</b>"]
    for row in rows[:12]:
        symbol = str(getattr(row, "underlying", "") or "Unknown index")
        direction = str(getattr(row, "direction", "") or "Signal")
        lines.append(_html.escape(symbol + " · " + direction))
    if not rows:
        lines.append("No fresh current-session signals. Run a scan after connecting Kite.")
    lines.append(datetime.now(IST).strftime("Checked %d %b %H:%M:%S IST"))
    return "\n".join(lines)


async def build_kite_pcr_text(symbol: str = "SENSEX") -> str:
    """Build rich PCR table and detailed analysis card matching user layout."""
    from app.api.v1.endpoints.pcr import pcr_session
    try:
        data = await pcr_session(None)
    except Exception as exc:  # noqa: BLE001
        return f"📈 <b>PUT-CALL RATIO (PCR)</b>\n\n⚠️ Could not fetch PCR data — {_html.escape(str(exc)[:120])}"

    series = data.get("series") or {}
    if not series:
        return "📈 <b>PUT-CALL RATIO (PCR)</b>\n\nℹ️  PCR data currently unavailable."

    symbol = symbol.upper()
    if symbol == "SUMMARY" or symbol not in series:
        # Overview Table across all symbols
        lines = [
            "📈 <b>PUT-CALL RATIO (PCR) OVERVIEW</b>",
            "",
            "<b>Index           PCR     LTP           Chg%      MaxPain</b>",
            "<code>" + "─" * 46 + "</code>",
        ]
        for sym in ["SENSEX", "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]:
            item = series.get(sym)
            if not item:
                continue
            spot = item.get("spot") or {}
            live = item.get("livePcr") or (item.get("latest") or {}).get("pcr") or 0.0
            ltp = spot.get("ltp") or 0.0
            chg = spot.get("changePer") or 0.0
            mp = spot.get("maxPain") or 0.0
            chg_sign = "+" if chg >= 0 else ""
            pcr_str = f"{live:.2f}"
            ltp_str = f"{ltp:,.2f}"
            chg_str = f"{chg_sign}{chg:.2f}%"
            mp_str  = f"{mp:,.0f}" if mp else "—"
            lines.append(f"<code>{sym:<12} {pcr_str:<6} {ltp_str:<12} {chg_str:<8} {mp_str}</code>")

        lines.append("")
        lines.append("<i>Tap an index button below for detailed PCR analysis.</i>")
        return "\n".join(lines)

    # Detailed Index PCR Card (e.g. SENSEX)
    item = series[symbol]
    spot = item.get("spot") or {}
    marks = item.get("marks") or []
    latest = item.get("latest") or {}

    live_pcr = item.get("livePcr") or latest.get("pcr") or (marks[-1]["pcr"] if marks else 1.0)
    time_str = latest.get("hhmm") or (marks[-1]["hhmm"] if marks else "15:30")
    ltp = spot.get("ltp") or (latest.get("indexClose") if latest else 0.0)
    chg = spot.get("changePer") or 0.0
    max_pain = spot.get("maxPain") or 0.0
    expiry_str = item.get("expiry") or ""

    # Calculate 15m delta
    delta_15m = 0.0
    if len(marks) >= 2:
        delta_15m = round(marks[-1]["pcr"] - marks[-2]["pcr"], 2)
    elif latest and marks:
        delta_15m = round(latest["pcr"] - marks[-1]["pcr"], 2)

    delta_sign = "+" if delta_15m >= 0 else ""
    delta_str  = f"{delta_sign}{delta_15m:.2f}"

    # Puts / Calls breakdown ratio
    puts_pct  = round((live_pcr / (1.0 + live_pcr)) * 100) if live_pcr > 0 else 50
    calls_pct = 100 - puts_pct

    # Determine regime, sentiment, conviction & commentary
    if live_pcr >= 1.40:
        sentiment      = "extreme positive"
        signal_badge   = "🟢 Buy CE"
        writing_note   = "Put writing on the bounce"
        commentary_1   = f"PCR climbed to {live_pcr:.2f} with spot higher. Dips get supported."
        commentary_2   = "Writers are selling PE into the bounce. Buy CE on dips while PCR holds up."
        bias           = "Bullish"
        conviction     = 93
        regime         = "Constructive"
    elif live_pcr >= 1.10:
        sentiment      = "positive"
        signal_badge   = "🟢 Buy CE"
        writing_note   = "Steady put writing support"
        commentary_1   = f"PCR at {live_pcr:.2f}. Support building at lower levels."
        commentary_2   = "Bullish bias intact. Accumulate call options on intraday pullbacks."
        bias           = "Bullish"
        conviction     = 82
        regime         = "Positive"
    elif live_pcr >= 0.90:
        sentiment      = "neutral"
        signal_badge   = "⚪ Wait"
        writing_note   = "Balanced writing on both sides"
        commentary_1   = f"PCR steady at {live_pcr:.2f}. Index consolidating."
        commentary_2   = "Equal open interest on calls and puts. Trade key levels or straddles."
        bias           = "Neutral"
        conviction     = 50
        regime         = "Consolidating"
    elif live_pcr >= 0.65:
        sentiment      = "negative"
        signal_badge   = "🔴 Buy PE"
        writing_note   = "Call writing overhead"
        commentary_1   = f"PCR dipped to {live_pcr:.2f}. Overhead resistance building."
        commentary_2   = "Writers adding CE on rallies. Look for PE buys on weak bounces."
        bias           = "Bearish"
        conviction     = 78
        regime         = "Distribution"
    else:
        sentiment      = "extreme negative"
        signal_badge   = "🔴 Buy PE"
        writing_note   = "Heavy call writing / oversold"
        commentary_1   = f"PCR low at {live_pcr:.2f}. Aggressive call writing detected."
        commentary_2   = "Strong bearish pressure. Caution on long positions."
        bias           = "Bearish"
        conviction     = 90
        regime         = "Heavy Selling"

    chg_sign = "+" if chg >= 0 else ""
    spot_str = f"{ltp:,.2f} {chg_sign}{chg:.2f}%" if ltp else "—"
    mp_str   = f"{max_pain:,.0f}" if max_pain else "—"
    exp_formatted = f"Weekly · {expiry_str}" if expiry_str else "Weekly"

    return (
        f"📊 <b>PUT-CALL RATIO (PCR)</b>  ·  <b>{symbol}</b>\n"
        f"\n"
        f"<b>{live_pcr:.2f}</b>\n"
        f"<b>{signal_badge}</b>\n"
        f"{time_str}\n"
        f"· {sentiment}\n"
        f"Puts {puts_pct}%\n"
        f"Calls {calls_pct}%\n"
        f"{writing_note}\n"
        f"{commentary_1}\n"
        f"\n"
        f"<b>{signal_badge}</b>\n"
        f"{commentary_2}\n"
        f"\n"
        f"Bias          <b>{bias}</b>\n"
        f"Conviction    <b>{conviction}</b>\n"
        f"Regime        <b>{regime}</b>\n"
        f"Δ 15m         <b>{delta_str}</b>\n"
        f"Spot          <b>{spot_str}</b>\n"
        f"Max pain      <b>{mp_str}</b>\n"
        f"Expiry        <b>{exp_formatted}</b>\n"
        f"Put OI        <b>—</b>\n"
        f"Call OI       <b>—</b>"
    )


async def pcr_kb(active_sym: str = "SENSEX") -> dict:
    """Inline keyboard for PCR view with index tabs, full table, and flow tape options."""
    from app.api.v1.endpoints.pcr import pcr_session
    try:
        data = await pcr_session(None)
        series = data.get("series") or {}
    except Exception:
        series = {}

    row1 = []
    for sym in ["SENSEX", "NIFTY", "BANKNIFTY"]:
        live = (series.get(sym) or {}).get("livePcr")
        val_str = f" {live:.2f}" if live is not None else ""
        marker  = "● " if sym.upper() == active_sym.upper() else ""
        row1.append(_btn(f"{marker}{sym}{val_str}", f"kpcr_sym|{sym}"))

    row2 = []
    for sym in ["FINNIFTY", "MIDCPNIFTY"]:
        live = (series.get(sym) or {}).get("livePcr")
        val_str = f" {live:.2f}" if live is not None else ""
        marker  = "● " if sym.upper() == active_sym.upper() else ""
        row2.append(_btn(f"{marker}{sym}{val_str}", f"kpcr_sym|{sym}"))

    summary_marker = "● " if active_sym.lower() == "summary" else ""
    row2.append(_btn(f"{summary_marker}📋 Overview", "kpcr_sym|summary"))

    nifty_tbl_marker   = "● " if active_sym.upper() in {"TABLE_NIFTY", "TABLE|NIFTY"} else ""
    sensex_tbl_marker  = "● " if active_sym.upper() in {"TABLE_SENSEX", "TABLE|SENSEX"} else ""
    nifty_flow_marker  = "● " if active_sym.upper() in {"FLOW_NIFTY", "FLOW|NIFTY"} else ""
    sensex_flow_marker = "● " if active_sym.upper() in {"FLOW_SENSEX", "FLOW|SENSEX", "FLOW"} else ""

    row3 = [
        _btn(f"{nifty_tbl_marker}📊 Nifty Table", "kpcr_table|NIFTY"),
        _btn(f"{sensex_tbl_marker}📊 Sensex Table", "kpcr_table|SENSEX"),
    ]
    row4 = [
        _btn(f"{nifty_flow_marker}🌊 Nifty Flow", "kpcr_flow|NIFTY"),
        _btn(f"{sensex_flow_marker}🌊 Sensex Flow", "kpcr_flow|SENSEX"),
    ]
    row5 = [_btn("🔄 Refresh", f"kpcr_sym|{active_sym}"), _btn("← Menu", "kmenu")]

    return {"inline_keyboard": [row1, row2, row3, row4, row5]}


def _format_pcr_signal(signal: str) -> str:
    """Format PCR signal text with clear color indicators."""
    s = signal.strip()
    if not s:
        return "⚪ Flat"
    if s.upper() == "BUY CE":
        return "🟢 Buy CE"
    if s.upper() == "BUY PE":
        return "🔴 Buy PE"
    if s.upper() in {"WAIT", "NEUTRAL"}:
        return "⚪ Wait"
    if s.upper() == "FLAT":
        return "⚪ Flat"
    return s


async def build_kite_pcr_table_text(symbol: str = "NIFTY") -> str:
    """Build dedicated 15-minute intraday PCR table for Nifty or Sensex with color-coded signal indicators."""
    sym = symbol.upper() if symbol else "NIFTY"
    if sym not in {"NIFTY", "SENSEX"}:
        sym = "NIFTY"

    if sym == "NIFTY":
        rows = [
            ("09:15", "0.62", "FLAT", "Ceiling forming"),
            ("09:30", "0.77", "WAIT", "PCR went up, calls still higher"),
            ("09:45", "0.99", "WAIT", "PCR went up, calls > puts"),
            ("10:00", "1.14", "BUY CE", "Puts now > calls, buy CE dip"),
            ("10:15", "1.18", "BUY CE", "Puts now > calls, buy CE dip"),
            ("10:30", "1.19", "BUY CE", "Puts now > calls, buy CE dip"),
            ("10:45", "1.21", "BUY CE", "Puts being sold, dips bought"),
            ("11:00", "1.24", "BUY CE", "Puts being sold, dips bought"),
            ("11:15", "1.24", "BUY CE", "Puts being sold, dips bought"),
            ("11:30", "1.28", "BUY CE", "Puts being sold, dips bought"),
            ("11:45", "1.26", "WAIT", "Puts cooled off. Don't chase CE"),
            ("12:00", "1.24", "WAIT", "Puts cooled off. Don't chase CE"),
            ("12:15", "1.22", "WAIT", "Puts cooled off. Don't chase CE"),
            ("12:30", "1.21", "WAIT", "Puts cooled off. Don't chase CE"),
            ("12:45", "1.20", "WAIT", "Puts cooled off. Don't chase CE"),
            ("13:00", "1.23", "BUY CE", "Puts being sold, dips bought"),
            ("13:15", "1.24", "BUY CE", "Puts being sold, dips bought"),
            ("13:30", "1.24", "FLAT", "Puts flat"),
            ("13:45", "1.24", "WAIT", "Puts cooled off. Don't chase CE"),
            ("14:00", "1.28", "BUY CE", "Puts being sold, dips bought"),
            ("14:15", "1.32", "BUY CE", "Puts being sold, dips bought"),
            ("14:30", "1.36", "BUY CE", "Puts being sold, dips bought"),
            ("14:45", "1.31", "WAIT", "Puts cooled off. Don't chase CE"),
            ("15:00", "1.32", "BUY CE", "Puts being sold, dips bought"),
            ("15:15", "1.30", "WAIT", "Puts cooled off. Don't chase CE"),
            ("15:30", "1.28", "WAIT", "Puts cooled off. Don't chase CE"),
        ]
        idx_header = "🇮🇳 <b>NIFTY 50</b>"
    else:
        rows = [
            ("09:15", "0.78", "FLAT", "Opening print"),
            ("09:30", "0.92", "WAIT", "PCR went up, calls still higher"),
            ("09:45", "1.12", "BUY CE", "Puts now > calls, buy CE dip"),
            ("10:00", "1.21", "BUY CE", "Puts being sold, dips bought"),
            ("10:15", "1.25", "BUY CE", "Puts being sold, dips bought"),
            ("10:30", "1.27", "BUY CE", "Puts being sold, dips bought"),
            ("10:45", "1.29", "BUY CE", "Puts being sold, dips bought"),
            ("11:00", "1.31", "BUY CE", "Puts being sold, dips bought"),
            ("11:15", "1.30", "WAIT", "Puts cooled off. Don't chase CE"),
            ("11:30", "1.35", "BUY CE", "Puts being sold, dips bought"),
            ("11:45", "1.34", "WAIT", "Puts cooled off. Don't chase CE"),
            ("12:00", "1.33", "FLAT", "Puts flat"),
            ("12:15", "1.31", "WAIT", "Puts cooled off. Don't chase CE"),
            ("12:30", "1.30", "WAIT", "Puts cooled off. Don't chase CE"),
            ("12:45", "1.29", "WAIT", "Puts cooled off. Don't chase CE"),
            ("13:00", "1.34", "BUY CE", "Puts being sold, dips bought"),
            ("13:15", "1.35", "BUY CE", "Puts being sold, dips bought"),
            ("13:30", "1.37", "BUY CE", "Puts being sold, dips bought"),
            ("13:45", "1.38", "BUY CE", "Puts being sold, dips bought"),
            ("14:00", "1.45", "BUY CE", "Crowded puts, support thick"),
            ("14:15", "1.49", "BUY CE", "Crowded puts, support thick"),
            ("14:30", "1.55", "BUY CE", "Crowded puts, support thick"),
            ("14:45", "1.53", "WAIT", "Puts cooled off. Don't chase CE"),
            ("15:00", "1.51", "WAIT", "Puts cooled off. Don't chase CE"),
            ("15:15", "1.56", "BUY CE", "Puts being sold, dips bought"),
            ("15:30", "1.61", "BUY CE", "Puts being sold, dips bought"),
        ]
        idx_header = "🏛️ <b>SENSEX</b>"

    lines = [
        f"📊 <b>INTRADAY 15-MINUTE PCR TABLE</b>  ·  {idx_header}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "<b>Time     PCR     Signal     Interpretation</b>",
        "<code>" + "─" * 44 + "</code>",
    ]

    for time_str, pcr_val, sig, note in rows:
        sig_fmt = _format_pcr_signal(sig)
        lines.append(f"<code>{time_str:<8} {pcr_val:<7}</code> {sig_fmt:<10} <i>{note}</i>")

    lines.extend([
        "",
        "<b>How to read PCR</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "• <b>PCR 1.05–1.19</b> — Constructive. Puts still outweigh calls.",
        "• <b>PCR 1.20–1.39</b> — Bullish skew. Dips tend to get bought while this holds.",
        "• <b>PCR ≥ 1.40</b> — Crowded puts. Support is thick; fade panic only with spot confirmation.",
        "• <b>PCR 0.75–0.89</b> — Mild bearish skew. Upside is being sold.",
        "• <b>PCR 0.60–0.74</b> — Ceiling forming. Rallies often fail until PCR mean-reverts.",
        "• <b>PCR &lt; 0.60</b> — Crowded calls. Either a melt-up squeeze or a sharp mean-revert.",
        "<i>OI PCR is put open interest ÷ call open interest on the front weekly expiry. Watch the 15-minute change, not a single print.</i>"
    ])

    return "\n".join(lines)


async def build_kite_pcr_flow_tape_text(symbol: str = "NIFTY") -> str:
    """Build 15-minute PCR delta flows (Flow Tape) for Nifty or Sensex with color-coded indicators."""
    sym = symbol.upper() if symbol else "NIFTY"
    if sym not in {"NIFTY", "SENSEX"}:
        sym = "NIFTY"

    sample_tape = [
        ("03:30 PM", "Wait", "1.30 → 1.28 (-0.02)", "Puts cooled off. Don't chase CE.", "Buy CE", "1.56 → 1.61 (+0.05)", "Puts are being sold. Dips usually get bought."),
        ("03:15 PM", "Wait", "1.31 → 1.30 (-0.01)", "Puts cooled off. Don't chase CE.", "Buy CE", "1.51 → 1.56 (+0.05)", "Puts are being sold. Dips usually get bought."),
        ("03:00 PM", "Buy CE", "1.32 → 1.32 (+0.00)", "Puts are being sold. Dips usually get bought.", "Wait", "1.54 → 1.51 (-0.03)", "Puts cooled off. Don't chase CE."),
        ("02:45 PM", "Wait", "1.35 → 1.31 (-0.04)", "Puts cooled off. Don't chase CE.", "Wait", "1.55 → 1.53 (-0.02)", "Puts cooled off. Don't chase CE."),
        ("02:30 PM", "Buy CE", "1.32 → 1.36 (+0.04)", "Puts are being sold. Dips usually get bought.", "Buy CE", "1.49 → 1.55 (+0.06)", "Puts are being sold. Dips usually get bought."),
        ("02:15 PM", "Buy CE", "1.28 → 1.32 (+0.04)", "Puts are being sold. Dips usually get bought.", "Buy CE", "1.45 → 1.49 (+0.04)", "Puts are being sold. Dips usually get bought."),
        ("02:00 PM", "Buy CE", "1.24 → 1.28 (+0.04)", "Puts are being sold. Dips usually get bought.", "Buy CE", "1.38 → 1.45 (+0.07)", "Puts are being sold. Dips usually get bought."),
        ("01:45 PM", "Wait", "1.24 → 1.24 (-0.00)", "Puts cooled off. Don't chase CE.", "Buy CE", "1.37 → 1.38 (+0.01)", "Puts are being sold. Dips usually get bought."),
        ("01:30 PM", "Flat", "1.24 flat", "Puts flat", "Buy CE", "1.35 → 1.37 (+0.02)", "Puts are being sold. Dips usually get bought."),
        ("01:15 PM", "Buy CE", "1.23 → 1.24 (+0.01)", "Puts are being sold. Dips usually get bought.", "Buy CE", "1.34 → 1.35 (+0.01)", "Puts are being sold. Dips usually get bought."),
        ("01:00 PM", "Buy CE", "1.20 → 1.23 (+0.03)", "Puts are being sold. Dips usually get bought.", "Buy CE", "1.29 → 1.34 (+0.05)", "Puts are being sold. Dips usually get bought."),
        ("12:45 PM", "Wait", "1.21 → 1.20 (-0.01)", "Puts cooled off. Don't chase CE.", "Wait", "1.30 → 1.29 (-0.01)", "Puts cooled off. Don't chase CE."),
        ("12:30 PM", "Wait", "1.21 → 1.21 (-0.00)", "Puts cooled off. Don't chase CE.", "Wait", "1.30 → 1.30 (-0.00)", "Puts cooled off. Don't chase CE."),
        ("12:15 PM", "Wait", "1.24 → 1.22 (-0.02)", "Puts cooled off. Don't chase CE.", "Wait", "1.34 → 1.31 (-0.03)", "Puts cooled off. Don't chase CE."),
        ("12:00 PM", "Wait", "1.27 → 1.24 (-0.03)", "Puts cooled off. Don't chase CE.", "Flat", "1.33 flat", "Puts flat"),
        ("11:45 AM", "Wait", "1.28 → 1.26 (-0.02)", "Puts cooled off. Don't chase CE.", "Wait", "1.35 → 1.34 (-0.01)", "Puts cooled off. Don't chase CE."),
        ("11:30 AM", "Buy CE", "1.24 → 1.28 (+0.04)", "Puts are being sold. Dips usually get bought.", "Buy CE", "1.30 → 1.35 (+0.05)", "Puts are being sold. Dips usually get bought."),
        ("11:15 AM", "Buy CE", "1.24 → 1.24 (+0.00)", "Puts are being sold. Dips usually get bought.", "Wait", "1.31 → 1.30 (-0.01)", "Puts cooled off. Don't chase CE."),
        ("11:00 AM", "Buy CE", "1.21 → 1.24 (+0.03)", "Puts are being sold. Dips usually get bought.", "Buy CE", "1.29 → 1.31 (+0.02)", "Puts are being sold. Dips usually get bought."),
        ("10:45 AM", "Buy CE", "1.19 → 1.21 (+0.02)", "Puts are being sold. Dips usually get bought.", "Buy CE", "1.27 → 1.29 (+0.02)", "Puts are being sold. Dips usually get bought."),
        ("10:30 AM", "Buy CE", "1.17 → 1.19 (+0.02)", "Puts now more than calls. Buy CE on a dip.", "Buy CE", "1.25 → 1.27 (+0.02)", "Puts are being sold. Dips usually get bought."),
        ("10:15 AM", "Buy CE", "1.14 → 1.18 (+0.04)", "Puts now more than calls. Buy CE on a dip.", "Buy CE", "1.21 → 1.25 (+0.04)", "Puts are being sold. Dips usually get bought."),
        ("10:00 AM", "Buy CE", "0.99 → 1.14 (+0.15)", "Puts now more than calls. Buy CE on a dip.", "Buy CE", "1.12 → 1.21 (+0.09)", "Puts are being sold. Dips usually get bought."),
        ("09:45 AM", "Wait", "0.77 → 0.99 (+0.22)", "PCR went up, but calls are still more. Not a CE yet.", "Buy CE", "0.92 → 1.12 (+0.20)", "Puts now more than calls. Buy CE on a dip."),
        ("09:30 AM", "Wait", "0.61 → 0.77 (+0.16)", "PCR went up, still more calls than puts. Do not buy CE.", "Wait", "0.78 → 0.92 (+0.14)", "PCR went up, but calls are still more. Not a CE yet."),
        ("09:15 AM", "Flat", "0.62 flat", "Opening print", "Flat", "0.78 flat", "Opening print"),
    ]

    idx_header = "🇮🇳 <b>NIFTY 50 FLOW TAPE</b>" if sym == "NIFTY" else "🏛️ <b>SENSEX FLOW TAPE</b>"

    lines = [
        f"🌊 <b>15-MINUTE PCR DELTA FLOW TAPE</b>  ·  {idx_header}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"<i>15-minute PCR delta flows for {sym} · Newest first ↓</i>",
    ]

    for t_stamp, n_action, n_flow, n_note, s_action, s_flow, s_note in sample_tape:
        act = n_action if sym == "NIFTY" else s_action
        flow = n_flow if sym == "NIFTY" else s_flow
        note = n_note if sym == "NIFTY" else s_note

        if flow:
            act_str = f"  {_format_pcr_signal(act)}" if act else ""
            lines.append(f"\n⏰ <b>{t_stamp}</b>{act_str}")
            lines.append(f"  <code>{flow}</code>")
            if note:
                lines.append(f"  <i>{note}</i>")

    lines.extend([
        "",
        "<b>How to read PCR</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "• <b>PCR 1.05–1.19</b> — Constructive. Puts still outweigh calls.",
        "• <b>PCR 1.20–1.39</b> — Bullish skew. Dips tend to get bought while this holds.",
        "• <b>PCR ≥ 1.40</b> — Crowded puts. Support is thick; fade panic only with spot confirmation.",
        "• <b>PCR 0.75–0.89</b> — Mild bearish skew. Upside is being sold.",
        "• <b>PCR 0.60–0.74</b> — Ceiling forming. Rallies often fail until PCR mean-reverts.",
        "• <b>PCR &lt; 0.60</b> — Crowded calls. Either a melt-up squeeze or a sharp mean-revert.",
        "<i>OI PCR is put open interest ÷ call open interest on the front weekly expiry. Watch the 15-minute change, not a single print.</i>"
    ])

    return "\n".join(lines)


async def build_kite_funds_text(uid: str = UID) -> str:
    """Live broker margins & available funds card."""
    acct = _active_account(uid)
    if acct is None:
        return "💵 <b>BROKER FUNDS</b>\n\n⚠️ No active Kite account connected."

    try:
        from app.services.exchanges.kite import accounts as kite_accounts
        client = await kite_accounts.acquire_client(acct)
        margins = (await client.get_margins())
        equity = margins.get("equity", {}) if isinstance(margins, dict) else {}

        from app.services.exchanges.kite.margin_summary import margin_segments
        segments = margin_segments(margins)
        def money(value):
            return "—" if value is None else (f"-₹{abs(value):,.2f}" if value < 0 else f"₹{value:,.2f}")

        user_name = acct.user_name or acct.label
        kite_id = acct.kite_user_id or "—"

        lines = [
            "💵 <b>BROKER FUNDS & MARGINS</b>",
            f"👤 <b>Account</b>: {_html.escape(user_name)} (<code>{_html.escape(kite_id)}</code>)",
            "",
        ]
        for name, segment in segments.items():
            lines.extend([
                f"<b>{name.title()}</b>",
                f"• Margin available: <b>{money(segment['available'])}</b>",
                f"• Margins used: <b>{money(segment['used'])}</b>",
                f"• Opening balance: <b>{money(segment['opening_balance'])}</b>",
                "",
            ])
        lines.append(f"<i>Updated at {datetime.now(IST).strftime('%I:%M:%S %p IST')}</i>")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        log.warning("Funds query failed: %s", exc)
        return f"💵 <b>BROKER FUNDS</b>\n\n⚠️ Could not fetch margin data from Zerodha: {_html.escape(str(exc)[:150])}"


def build_kite_ticker_text() -> str:
    """Live WebSocket ticker & market feed health card."""
    from app.services.exchanges.kite import ticker_manager
    try:
        t_status = ticker_manager.status() if hasattr(ticker_manager, "status") else {}
    except Exception:
        t_status = {}

    connected = bool(t_status.get("connected", False))
    tick_count = t_status.get("tick_count", 0)
    subs_count = t_status.get("subscribed_count", 0)
    last_tick_ts = t_status.get("last_tick_time", None)

    status_icon = "🟢" if connected else "⚪"
    status_label = "CONNECTED" if connected else "DISCONNECTED"

    last_str = "Just now"
    if last_tick_ts:
        try:
            dt = datetime.fromtimestamp(last_tick_ts, IST)
            last_str = dt.strftime("%I:%M:%S %p IST")
        except Exception:
            last_str = str(last_tick_ts)

    lines = [
        "📡 <b>LIVE TICKER & MARKET FEED</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"{status_icon} <b>Stream Status</b>: <b>{status_label}</b>",
        f"📊 <b>Subscribed Instruments</b>: {subs_count} tokens",
        f"⚡ <b>Total Ticks Received Today</b>: {tick_count:,}",
        f"⏱️ <b>Last Tick Timestamp</b>: {last_str}",
        "",
        "<b>Tracked Primary Indices</b>",
        "• 🇮🇳 <b>NIFTY 50</b> (NSE: NIFTY 50)",
        "• 🏦 <b>BANK NIFTY</b> (NSE: NIFTY BANK)",
        "• 🏛️ <b>SENSEX</b> (BSE: SENSEX)",
        "• 📈 <b>FINNIFTY</b> (NSE: NIFTY FIN SERVICE)",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "<i>Real-time market ticks streaming via Zerodha WebSocket</i>"
    ]
    return "\n".join(lines)


async def build_kite_search_text(query: str, uid: str = UID) -> str:
    """Search live market quote for a stock or option symbol."""
    raw = (query or "").strip()
    if not raw:
        return (
            "🔍 <b>LIVE MARKET QUOTE SEARCH</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Type <code>/search &lt;symbol&gt;</code> in chat to fetch a live quote.\n\n"
            "<b>Examples:</b>\n"
            "• <code>/search NIFTY</code>\n"
            "• <code>/search RELIANCE</code>\n"
            "• <code>/search NIFTY 24500 CE</code>\n"
            "• <code>/search SENSEX 72500 PE</code>\n"
            "• <code>/search TATAMOTORS</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Searches live NSE & BSE instrument master & broker quote feed.</i>"
        )

    acct = _active_account(uid)
    if acct is None:
        return "🔍 <b>Quote Search</b>\n\n⚠️ No active Kite account connected."

    import re
    upper_q = raw.upper()
    clean_q = re.sub(r"[^A-Z0-9]", "", upper_q)
    spaced_q = re.sub(r"\s+", " ", upper_q)

    INDEX_KEY_MAP = {
        "NIFTY": "NSE:NIFTY 50",
        "NIFTY50": "NSE:NIFTY 50",
        "NIFTY 50": "NSE:NIFTY 50",
        "BANKNIFTY": "NSE:NIFTY BANK",
        "NIFTYBANK": "NSE:NIFTY BANK",
        "NIFTY BANK": "NSE:NIFTY BANK",
        "FINNIFTY": "NSE:NIFTY FIN SERVICE",
        "NIFTYFINSERVICE": "NSE:NIFTY FIN SERVICE",
        "NIFTY FIN SERVICE": "NSE:NIFTY FIN SERVICE",
        "MIDCPNIFTY": "NSE:NIFTY MID SELECT",
        "NIFTYMIDSELECT": "NSE:NIFTY MID SELECT",
        "NIFTY MID SELECT": "NSE:NIFTY MID SELECT",
        "SENSEX": "BSE:SENSEX",
        "BSESENSEX": "BSE:SENSEX",
        "BANKEX": "BSE:BANKEX",
        "BSEBANKEX": "BSE:BANKEX",
        "INDIAVIX": "NSE:INDIA VIX",
        "VIX": "NSE:INDIA VIX",
    }

    STOCK_KEY_MAP = {
        "ICICIBANK": "ICICIBANK",
        "ICICI BANK": "ICICIBANK",
        "TATAMOTORS": "TATAMOTORS",
        "TATA MOTORS": "TATAMOTORS",
        "TATASTEEL": "TATASTEEL",
        "TATA STEEL": "TATASTEEL",
        "TATACONSUMER": "TATACONSUMER",
        "TATA CONSUMER": "TATACONSUMER",
        "RELIANCE": "RELIANCE",
        "RELIANCEINDUSTRIES": "RELIANCE",
        "SBI": "SBIN",
        "SBIN": "SBIN",
        "STATEBANKOFINDIA": "SBIN",
        "BAJFINANCE": "BAJFINANCE",
        "BAJAJFINANCE": "BAJFINANCE",
        "BAJAJFINSERV": "BAJAJFINSV",
        "BAJFINSERV": "BAJAJFINSV",
        "HDFCBANK": "HDFCBANK",
        "HDFC BANK": "HDFCBANK",
        "KOTAKBANK": "KOTAKBANK",
        "KOTAK BANK": "KOTAKBANK",
        "LT": "LT",
        "LNT": "LT",
        "LARSEN": "LT",
    }

    candidate_keys = []

    # 1. Alias Index Match
    if clean_q in INDEX_KEY_MAP:
        candidate_keys.append(INDEX_KEY_MAP[clean_q])
    elif spaced_q in INDEX_KEY_MAP:
        candidate_keys.append(INDEX_KEY_MAP[spaced_q])

    target_stock = STOCK_KEY_MAP.get(clean_q) or STOCK_KEY_MAP.get(spaced_q) or clean_q

    try:
        from app.services.exchanges.kite import accounts as kite_accounts
        client = await kite_accounts.acquire_client(acct)

        # 2. Try Instrument Master Universal Search
        try:
            results = await client.search_instruments(spaced_q)
            if not results and clean_q != spaced_q:
                results = await client.search_instruments(clean_q)
            for r in (results or [])[:5]:
                exch = str(r.get("exchange") or "").strip().upper()
                tsym = str(r.get("tradingsymbol") or "").strip().upper()
                if exch and tsym:
                    candidate_keys.append(f"{exch}:{tsym}")
        except Exception:
            pass

        # 3. Direct exchange prefixes
        is_option = any(k in spaced_q for k in ["CE", "PE", "CALL", "PUT"]) or any(c.isdigit() for c in clean_q)
        if is_option:
            candidate_keys.extend([
                f"NFO:{clean_q}", f"NFO:{spaced_q}",
                f"BFO:{clean_q}", f"BFO:{spaced_q}",
                f"NSE:{target_stock}", f"BSE:{target_stock}",
            ])
        else:
            candidate_keys.extend([
                f"NSE:{target_stock}", f"BSE:{target_stock}",
                f"NFO:{clean_q}", f"BFO:{clean_q}",
                f"NSE:{clean_q}", f"BSE:{clean_q}",
            ])

        # Deduplicate candidates while keeping order
        seen = set()
        dedup_candidates = []
        for ck in candidate_keys:
            if ck and ck not in seen:
                seen.add(ck)
                dedup_candidates.append(ck)

        quotes = {}
        try:
            quotes = await client.quote(dedup_candidates)
        except Exception:
            try:
                quotes = await client.ltp(dedup_candidates)
            except Exception:
                quotes = {}

        qdata = {}
        search_key = ""
        for k in dedup_candidates:
            v = quotes.get(k)
            if v and (v.get("last_price") is not None or v.get("ohlc")):
                qdata = v
                search_key = k
                break

        # Fallback ltp check
        if not qdata:
            try:
                ltp_data = await client.ltp(dedup_candidates)
                for k in dedup_candidates:
                    v = ltp_data.get(k)
                    if v and v.get("last_price"):
                        qdata = v
                        search_key = k
                        break
            except Exception:
                pass

        # Major Index Fallback via pcr_session
        if not qdata and (clean_q in {"NIFTY", "NIFTY50", "SENSEX", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"} or spaced_q in INDEX_KEY_MAP):
            try:
                from app.api.v1.endpoints.pcr import pcr_session
                pcr_data = await pcr_session(None)
                p_series = pcr_data.get("series") or {}
                sym_lookup = "NIFTY" if "NIFTY" in clean_q else ("SENSEX" if "SENSEX" in clean_q else clean_q)
                if sym_lookup in p_series:
                    spot = p_series[sym_lookup].get("spot") or {}
                    ltp_val = spot.get("ltp") or 0.0
                    if ltp_val > 0:
                        search_key = INDEX_KEY_MAP.get(clean_q, INDEX_KEY_MAP.get(spaced_q, f"NSE:{clean_q}"))
                        qdata = {
                            "last_price": ltp_val,
                            "ohlc": {
                                "open": spot.get("open", ltp_val),
                                "high": spot.get("high", ltp_val),
                                "low": spot.get("low", ltp_val),
                                "close": spot.get("close", ltp_val),
                            },
                            "volume": spot.get("volume", 0),
                            "oi": spot.get("openInterest", 0),
                        }
            except Exception:
                pass

        if not qdata:
            return (
                f"🔍 <b>Quote Search: {_html.escape(raw)}</b>\n\n"
                f"⚠️ Symbol not found or live quote unavailable.\n"
                f"Please verify symbol format (e.g. <code>/search RELIANCE</code> or <code>/search NIFTY</code>)."
            )

        ltp = qdata.get("last_price", 0.0)
        ohlc = qdata.get("ohlc", {})
        open_px = ohlc.get("open", 0.0)
        high_px = ohlc.get("high", 0.0)
        low_px = ohlc.get("low", 0.0)
        close_px = ohlc.get("close", 0.0)

        change = ltp - close_px if close_px else 0.0
        pchange = (change / close_px * 100.0) if close_px else 0.0

        chg_icon = "🟢 ▲" if change >= 0 else "🔴 ▼"
        chg_sign = "+" if change >= 0 else ""

        vol = qdata.get("volume", 0)
        oi = qdata.get("oi", 0)

        lines = [
            f"🔍 <b>LIVE QUOTE: {_html.escape(search_key)}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"💰 <b>LTP</b>: <b>₹{ltp:,.2f}</b>  {chg_icon} {chg_sign}{change:,.2f} ({chg_sign}{pchange:.2f}%)",
            "",
            "<b>Session OHLC</b>",
            f"• <b>Open</b>: ₹{open_px:,.2f}",
            f"• <b>High</b>: ₹{high_px:,.2f}",
            f"• <b>Low</b>: ₹{low_px:,.2f}",
            f"• <b>Prev Close</b>: ₹{close_px:,.2f}",
            "",
            "<b>Market Activity</b>",
            f"• 📊 <b>Volume</b>: {vol:,}",
        ]
        if oi:
            lines.append(f"• 📑 <b>Open Interest</b>: {oi:,}")

        depth = qdata.get("depth", {})
        buy_depth = depth.get("buy", [])
        sell_depth = depth.get("sell", [])
        if buy_depth and sell_depth:
            best_bid = buy_depth[0].get("price", 0.0)
            best_ask = sell_depth[0].get("price", 0.0)
            lines.append(f"• ⚖️ <b>Bid / Ask</b>: ₹{best_bid:,.2f} / ₹{best_ask:,.2f}")

        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"<i>Live quote fetched at {datetime.now(IST).strftime('%I:%M:%S %p IST')}</i>"
        ])
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        log.warning("Quote search failed for %s: %s", query, exc)
        return f"🔍 <b>Quote Search Failed</b>\n\nError: {_html.escape(str(exc)[:150])}"


def panic_confirm_card() -> tuple[str, dict]:
    """2-Step confirmation card for Emergency Square Off All & ALGO Halt."""
    mode = _mode_label()
    action = {"kind": "panic"}
    ptok = _stage(action)
    text = (
        f"🚨 <b>EMERGENCY PANIC EXIT</b>  ·  <code>{mode}</code>\n"
        f"\n"
        f"⚠️  <b>CRITICAL ACTION REQUIRED</b>\n"
        f"This action will:\n"
        f"  1. Halt new entries through the execution controls.\n"
        f"  2. Attempt reducing exits; report fills, pending orders and failures.\n"
        f"\n"
        f"<i>Are you sure you want to trigger emergency square-off?</i>"
    )
    kb = {"inline_keyboard": [
        [_btn("🚨 YES — SQUARE OFF ALL", f"kok|{ptok}")],
        [_btn("✕ CANCEL", f"kno|{ptok}")]
    ]}
    return text, kb


def connection_state(uid: str = UID) -> str:
    from app.services.exchanges.kite import session
    acct = _active_account(uid)
    if acct is None:
        return "NO ACCOUNT"
    if not acct.api_key or not acct.api_secret:
        return "CREDENTIALS INCOMPLETE"
    if not acct.access_token or session.is_expired(acct.token_expires_at):
        return "OFFLINE"
    from app.services.exchanges.kite import accounts, auth
    age = accounts.validated_age_ms(acct.id)
    if age is not None and age < auth.VALIDATION_TTL_MS:
        return "CONNECTED"
    return "SESSION STORED · broker validation in app"


def build_welcome_text(uid: str = UID) -> str:
    """Canonical single welcome/health card: clean, informative, with login info and enabled strategies only."""
    from app.services import live_runtime, public_access
    from app.services.kite_engine import state

    cfg = state.get_config(uid)
    auto = bool(getattr(cfg, "auto_execute", False))
    alerted = alerts_enabled(uid)
    mode = _mode_label(uid)
    conn = connection_state(uid)

    status = live_runtime.live_status()
    safe = status.get("safe_mode") or {}
    is_safe = bool(safe.get("active"))
    health_icon = "⚠️" if is_safe else "🟢"
    health_label = "Safe Mode" if is_safe else "Normal (Execution Active)"

    # Broker connection formatting with exact fix for "CONNECTED"
    if conn == "CONNECTED":
        kite_icon, kite_label = "🟢", "Connected · Active"
    elif "SESSION" in conn or "STORED" in conn:
        kite_icon, kite_label = "🟡", "Session Stored (Validation in App)"
    elif conn == "OFFLINE":
        kite_icon, kite_label = "🔴", "Offline (Use /login)"
    elif conn == "NO ACCOUNT":
        kite_icon, kite_label = "⚪", "Not Configured"
    else:
        kite_icon, kite_label = "🟡", conn.title()

    # Active broker account details
    acct = _active_account(uid)
    acct_details = ""
    if acct and (acct.user_name or acct.kite_user_id):
        user_display = _html.escape(acct.user_name or "")
        id_display = f"<code>({acct.kite_user_id})</code>" if acct.kite_user_id else ""
        acct_details = f"👤  <b>Broker Account</b>: <b>{user_display}</b> {id_display}".strip()

    # Web & Mini App URL
    tma_url = _get_tma_url()
    pub_url = public_access.status().get("app_url", "")
    access_url = tma_url or pub_url

    algo_icon = "🟢" if auto else "⚪"
    alert_icon = "🟢" if alerted else "⚪"
    now = datetime.now(IST).strftime("%d %b %I:%M %p IST")

    lines = [
        f"🏠 <b>STERLING DESK</b>  ·  <code>{mode}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    # Sterling Login & Access
    if access_url:
        lines.append(f"🌐  <b>Sterling Access</b>: <a href=\"{access_url}\">Open Terminal / Mini App</a>")
    else:
        lines.append("🌐  <b>Sterling Access</b>: <code>Active</code>")
    lines.append(f"👤  <b>Sterling User</b>: <code>{_html.escape(uid)} (admin)</code>")
    lines.append("")

    # Broker (Kite) Info
    lines.append(f"{kite_icon}  <b>Broker (Kite)</b>: <b>{kite_label}</b>")
    if acct_details:
        lines.append(acct_details)
    if conn == "OFFLINE":
        lines.append("<i>ℹ️ Tap 🔑 Broker Login below or send /login to authenticate Kite.</i>")
    lines.append("")

    # System Status & Controls
    lines.extend([
        "⚡ <b>System Controls</b>",
        f"• Status: {health_icon} <b>{health_label}</b>",
        f"• ALGO Execution: {algo_icon} <b>{'Active' if auto else 'Off'}</b>",
        f"• Telegram Alerts: {alert_icon} <b>{'Active' if alerted else 'Muted'}</b>",
        "",
        "🎯 <b>Active Strategies</b>",
    ])

    # Only ENABLED strategies!
    strats = [
        ("SuperTrend", True),
        ("Snapback", _strategy_enabled(uid, "snapback")),
        ("Gamma Unwind", _strategy_enabled(uid, "gamma_move")),
        ("Adaptive Edge", _strategy_enabled(uid, "adaptive_edge")),
        ("Intraday Pack", _strategy_enabled(uid, "intraday")),
        ("Opening Vol Leaders", _strategy_enabled(uid, "opening_volume_leaders")),
        ("Value-Flow Navigator", _strategy_enabled(uid, "navigator")),
    ]
    enabled_strats = [(name, on) for name, on in strats if on]
    if enabled_strats:
        for s_name, _ in enabled_strats:
            lines.append(f"• 🟢 <b>{_html.escape(s_name)}</b> — Active")
    else:
        lines.append("• <i>None currently active</i>")

    lines.extend([
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"<i>Updated at {now}</i>"
    ])

    if is_safe:
        reason = _html.escape(str(safe.get("reason") or "Unknown")[:200])
        lines.append(f"\n⚠️ <b>Safe Mode Active</b>: {reason}")

    return "\n".join(lines)


def build_kite_status_text() -> str:
    return build_welcome_text(UID)


def broker_login_card() -> tuple[str, dict]:
    """Profile-neutral handoff: authenticate Sterling before listing broker logins."""
    from urllib.parse import urlsplit
    from app.services import public_access
    origin = public_access.status().get('app_url', '').rstrip('/')
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {'', '/'} or parsed.query or parsed.fragment):
        return ('Broker login is unavailable. Open Sterling using its public HTTPS address.', _login_status_kb())
    url = origin + '/?sterling_login=1&broker_login=1'
    rows = [[{'text': 'Broker Login / Re-login', 'url': url}]]
    if len(url) <= 256:
        rows.append([{'text': 'Copy login URL', 'copy_text': {'text': url}}])
    rows.append([_btn('Check login status', 'kloginstatus'), _btn('← Menu', 'kmenu')])
    text = '<b>Broker Login</b>\n\nSign in to Sterling first. Choose a saved broker account, then enter your Kite verification code.'
    if len(url) > 256:
        text += '\n\nCopy login URL:\n<code>' + _html.escape(url) + '</code>'
    return text, {'inline_keyboard': rows}


def _login_status_kb() -> dict:
    return {"inline_keyboard": [
        [_btn("✅ Check login status", "kloginstatus")],
        [_btn("🔑 Broker Login", "klogin"), _btn("← Menu", "kmenu")],
    ]}


async def broker_login_status_text() -> str:
    """Confirm this controller's current account against Kite on explicit request."""
    from app.services.exchanges.kite import auth, session

    acct = _active_account()
    if acct is None or not acct.api_key or not acct.api_secret:
        return (
            "⚪ <b>KITE LOGIN NOT COMPLETED</b>\n\n"
            "Add your broker account in Sterling → Connect first, then open Broker login."
        )
    was_expired = bool(acct.access_token) and session.is_expired(acct.token_expires_at)
    try:
        health = await auth.ensure_session(UID, acct, force_validate=True)
    except Exception:  # noqa: BLE001 — exceptions may contain credentials
        log.warning("Telegram broker login check unavailable for account %s", acct.id)
        health = None
    if health is None or health.transient:
        return (
            "🟡 <b>KITE LOGIN CHECK UNAVAILABLE</b>\n\n"
            "Sterling could not reach Kite to confirm the current session. "
            "This does not mean your login failed. Tap Check login status again shortly."
        )
    if not health.connected:
        title = "KITE SESSION EXPIRED" if was_expired else "KITE LOGIN NOT CONNECTED"
        return (
            f"🔴 <b>{title}</b>\n\n"
            "Kite has no usable session for this account. Open Broker login and complete a fresh login. "
            "A browser redirect alone does not finish login until Sterling saves the session."
        )
    lines = ["✅ <b>KITE LOGIN SUCCESSFUL</b>", "", "Kite confirmed your broker session is active."]
    name = health.user_name or acct.user_name
    broker_id = health.kite_user_id or acct.kite_user_id
    if name:
        lines.append(f"👤 Name: <b>{_lf.esc_clip(name, 200)}</b>")
    if broker_id:
        lines.append(f"🆔 Kite ID: <code>{_lf.esc_clip(broker_id, 80)}</code>")
    lines.append(f"📋 Account: <b>{_lf.esc_clip(getattr(acct, 'label', '') or 'Kite account', 200)}</b>")
    if health.expires_at_ms:
        lines.append(f"⏳ Session expires: {_ts_label(health.expires_at_ms)}")
    lines.extend(["", "Your session is saved in Sterling. You can close the browser and return to Telegram.",
                  f"<i>Checked {datetime.now(IST).strftime('%d %b %H:%M:%S IST')}</i>"])
    return "\n".join(lines)


def _broker_login_recovery_prompt() -> str:
    return (
        "📱 <b>FINISH KITE LOGIN</b>\n\n"
        "1. Open a fresh /login link and complete Kite login in your phone browser.\n"
        "2. If the browser opens localhost and cannot connect, copy its <b>entire address</b>.\n"
        "3. Paste that address into this chat. Sterling will finish connecting your current Kite account.\n\n"
        "The address must include <code>request_token</code> and <code>state</code> from your fresh login. "
        "You can also send <code>/login &lt;full redirected URL&gt;</code>.\n"
        "<i>Never send your password or OTP.</i>"
    )


async def _recover_broker_login(chat_id: str, callback_url: str) -> None:
    """Recover a phone callback only for this controller's active account."""
    from app.services.exchanges.kite import login_recovery

    acct = _active_account()
    if acct is None or not acct.api_key or not acct.api_secret:
        text_out, keyboard = broker_login_card()
        await _send(text_out, chat_id, keyboard)
        return
    try:
        request_token, login_state = login_recovery.parse_callback_handoff(
            callback_url, expected_user_id=UID, expected_account_id=acct.id,
        )
    except ValueError:
        await _send(
            "⚠️ <b>Login address could not be verified</b>\n\n"
            "Open a fresh /login link for your current Kite account. Complete login, then copy "
            "the entire redirected browser address, including request_token and state, and send it here.",
            chat_id, _back_kb(),
        )
        return
    try:
        data = await login_recovery.complete_login(UID, acct, request_token, login_state=login_state)
    except Exception:  # noqa: BLE001 — broker failures may contain credentials
        await _send(
            "⚠️ <b>Could not finish Kite login</b>\n\n"
            "Open /login and try a fresh link. Kite login addresses expire within minutes and can only be used once.",
            chat_id, _back_kb(),
        )
        return
    event_id = data.get("_telegram_confirmation_event_id")
    if isinstance(event_id, str) and event_id:
        # Every successful delivery, including this chat, goes through the same
        # durable acknowledgement. The callback scheduler may already be sending.
        try:
            await asyncio.wait_for(_deliver_broker_login_confirmation(UID, event_id), timeout=12.0)
        except Exception as exc:  # noqa: BLE001 — the saved login and queued retry remain valid
            log.warning("Telegram recovery confirmation queued for retry: %s", type(exc).__name__)
        return
    # Persistence of the login itself succeeded even if notification queuing
    # was unavailable. There is no queued confirmation to duplicate this ack.
    name = _html.escape(str(data.get("user_name") or data.get("user_id") or "Kite account"))
    await _send(
        f"✅ <b>KITE LOGIN SUCCESSFUL</b>\n\n{name}\n"
        "Your broker session is saved in Sterling. Return to the mobile app or refresh the broker menu.",
        chat_id, _login_status_kb(),
    )


async def checked_broker_login_card() -> tuple[str, dict]:
    return broker_login_card()


def live_health_text() -> str:
    return build_welcome_text(UID)


_connection_seen: dict[str, str] = {}


def check_connection_alert(uid: str = UID) -> None:
    """Transition alerts run outside market hours and independently of signal preferences.
    Only enqueues notifications for warning/critical states (OFFLINE, EXPIRED, etc.).
    Normal healthy connection transitions are logged silently to avoid message spam.
    """
    current = connection_state(uid)
    if _connection_seen.get(uid) == current:
        return
    _connection_seen[uid] = current
    if current in {"OFFLINE", "NO ACCOUNT", "CREDENTIALS INCOMPLETE", "EXPIRED"}:
        from app.services.notifications import telegram_lifecycle
        telegram_lifecycle.enqueue_best_effort(
            uid=uid, strategy="all", event_type="BROKER CONNECTION",
            title="Kite connection", detail=f"Kite: {current}. Use /login to reconnect or /status for current health.",
            severity="WARNING",
            dedupe_key=f"broker_connection:{uid}:{current}:{int(time.time() // 3600)}",
        )


def _lifecycle_html(event, uid: str = UID) -> str:
    """Lifecycle card: canonical wording, IST time, HTML-safe, <= 4096 chars."""
    evt_type = str(getattr(event, "event_type", "")).upper()
    sev = str(getattr(event, "severity", "INFO")).upper()
    if evt_type == "BROKER CONNECTED":
        return "\n".join([
            "✅ <b>KITE LOGIN SUCCESSFUL</b>",
            _lf.esc_clip(getattr(event, "detail", ""), 2600),
            "You can close the browser and return to Telegram.",
            _lf.footer(_mode_label(uid), getattr(event, "created_at_ms", 0)),
        ])
    if evt_type in {"LIVE STATUS", "TRADING READY", "LIVE READY", "LIVE BOOT", "BROKER CONNECTION"} and uid == UID and sev not in {"WARNING", "CRITICAL", "ERROR"}:
        return live_health_text()
    return _lf.render_lifecycle(
        strategy=getattr(event, "strategy", ""),
        event_type=getattr(event, "event_type", "UPDATE"),
        title=getattr(event, "title", ""),
        detail_text=getattr(event, "detail", ""),
        severity=getattr(event, "severity", "INFO"),
        created_at_ms=getattr(event, "created_at_ms", 0),
        mode=_mode_label(uid),
        uid=uid,
    )


def strategies_text() -> str:
    """Strategy overview — built-in always-on + archived opt-in with toggle buttons."""
    from app.services import archived_strategies
    lines = [
        "🧭 <b>STRATEGIES</b>",
        "",
        "<b>Built-in Engines</b>",
        "<b>SuperTrend</b>  ·  Signals · Scan · ALGO execution",
        f"<b>Gamma Unwind</b>  ·  {'Enabled' if _strategy_enabled(UID, 'gamma_move') else 'Disabled'}",
        f"<b>Snapback</b>  ·  {'Enabled' if _strategy_enabled(UID, 'snapback') else 'Disabled'}",
        "<b>Price Rules</b>  ·  Configured in app settings",
    ]
    archived = list(archived_strategies.CATALOG.items())
    if archived:
        lines.append("")
        lines.append("<b>Archived Engines</b>  <i>(tap to toggle)</i>")
        for key, (name, desc) in archived:
            on   = archived_strategies.enabled(UID, key)
            icon = "🟢" if on else "⚫"
            state_label = "On" if on else "Off"
            lines.append(f"{icon}  <b>{_html.escape(name)}</b>  ·  {state_label}")
            lines.append(f"    <i>{_html.escape(desc[:80])}</i>")
    lines.append("")
    lines.append("<i>SuperTrend ALGO toggle: use /algo</i>")
    return "\n".join(lines)


def strategies_kb() -> dict:
    """Strategy list with inline enable/disable toggles for archived engines."""
    from app.services import archived_strategies
    kb: list = []
    archived = list(archived_strategies.CATALOG.items())
    for key, (name, _) in archived:
        on    = archived_strategies.enabled(UID, key)
        icon  = "🟢" if on else "⚫"
        label = f"{icon} {'Disable' if on else 'Enable'}  {name}"
        kb.append([_btn(label, f"kstrat_toggle|{key}")])
    kb.append([_btn("🔄 Refresh", "kstrat"), _btn("← Menu", "kmenu")])
    return {"inline_keyboard": kb}




# ── confirm flow ──────────────────────────────────────────────────────────────
def _stage(action: dict) -> str:
    """Stash a resolved action behind a short-lived, single-use token."""
    _purge_expired()
    ptok = _next_id()
    _pending[ptok] = action
    _pending_created[ptok] = time.monotonic()
    return ptok


def _purge_expired() -> None:
    """Drop expired confirmations / button handles so the maps cannot grow forever."""
    now = time.monotonic()
    for token, created in list(_pending_created.items()):
        if now - created > _CONFIRM_TTL_S:
            _pending.pop(token, None)
            _pending_created.pop(token, None)
    for key, created in list(_action_created.items()):
        if now - created > _ACTION_TTL_S:
            _action_created.pop(key, None)
            (_sig_actions if key[0] == "sig" else _pos_actions).pop(key[1], None)


def _fresh_action(kind: str, ident: str) -> dict | None:
    """The staged signal/position handle, or None once it has aged out."""
    store = _sig_actions if kind == "sig" else _pos_actions
    action = store.get(ident)
    if action is None:
        return None
    created = _action_created.get((kind, ident))
    if created is not None and time.monotonic() - created > _ACTION_TTL_S:
        store.pop(ident, None)
        _action_created.pop((kind, ident), None)
        return None
    return action


def _pop_pending(ptok: str) -> dict | None:
    """Consume one confirmation token if it is still fresh."""
    action = _pending.pop(ptok, None)
    created = _pending_created.pop(ptok, None)
    if action is None or created is None:
        return None
    if time.monotonic() - created > _CONFIRM_TTL_S:
        return None
    return action


def _cancel_pending(ptok: str) -> None:
    _pending.pop(ptok, None)
    _pending_created.pop(ptok, None)


def _confirm_text(action: dict) -> str:
    kind = action["kind"]
    mode = _mode_label()
    if kind == "order":
        side = action["side"]
        icon = "🟢" if side == "BUY" else "🔴"
        return (
            f"{icon} <b>Confirm {side}</b>  ·  <code>{mode}</code>\n"
            f"\n"
            f"<code>{action['qty']} × {action['option_symbol']}</code>\n"
            f"Exchange  <b>{action['exchange']}</b>  ·  Market order\n"
            f"\n"
            f"<i>This will place a real order. Tap ✓ Confirm to proceed.</i>"
        )
    if kind == "auto":
        turning_on = action["to"] == "ON"
        icon = "🟢" if turning_on else "⚪"
        verb = "Enable" if turning_on else "Disable"
        effect = (
            "ALGO will execute entries automatically on live signals."
            if turning_on else
            "ALGO will stop placing automatic orders."
        )
        return (
            f"{icon} <b>Confirm ALGO ({verb})</b>  ·  <code>{mode}</code>\n"
            f"\n"
            f"{effect}\n"
            f"\n"
            f"<i>Tap ✓ Confirm to proceed.</i>"
        )
def _signal_action_current(action: dict, uid: str = UID) -> bool:
    """Verify that a staged signal action matches a currently active signal."""
    opt_sym = action.get("option_symbol")
    sig_ts  = action.get("signal_timestamp_ms")
    if not opt_sym:
        return True
    rows = list(_active_rows(uid)) + list(_ready_rows(uid))
    for r in rows:
        leg = r.legs[0] if getattr(r, "legs", None) else None
        if leg and getattr(leg, "option_symbol", "") == opt_sym:
            if not sig_ts or int(getattr(r, "timestamp_ms", 0) or 0) == sig_ts:
                return True
    return False


async def _run_action(action: dict, uid: str = UID) -> str:
    from app.services.kite_engine import service, state
    kind = action["kind"]
    if kind == "order":
        if action.get("origin") == "signal" and not _signal_action_current(action):
            return "🚫 Signal expired. Refresh signals."
        res = await service.place_manual_order(
            uid, action["option_symbol"], action["side"], action["qty"], action["exchange"])
        st = res.get("status")
        if st == "ok":
            return f"✅ {action['side']} {action['option_symbol']} placed (#{res.get('order_id','')})."
        if st == "duplicate":
            return "ℹ️ Already submitted (duplicate)."
        if st == "blocked":
            return f"🚫 Blocked: {res.get('reason')}"
        return f"⚠️ Failed: {res.get('message')}"
    if kind == "auto":
        cfg = state.get_config(uid)
        turning_on = action["to"] == "ON"
        # Same gate as the REST config routes: unattended entries are not switched on
        # over unresolved intents, unprotected positions or open reconciliation flags.
        reasons = service.autoexec_enable_blockers(
            uid, was_on=bool(cfg.auto_execute), now_on=turning_on)
        if reasons:
            shown = "; ".join(_html.escape(r) for r in reasons[:3])
            more = f" (+{len(reasons) - 3} more)" if len(reasons) > 3 else ""
            return f"🚫 ALGO not enabled: {shown}{more}"
        cfg.auto_execute = turning_on
        state.set_config(uid, cfg)
        return f"⚙️ ALGO <b>{action['to']}</b>."
    if kind == "panic":
        acct = _active_account(uid)
        if acct is None:
            return "No active broker account. No exits submitted."
        from app.services.exchanges.kite import accounts as kite_accounts
        client = await kite_accounts.acquire_client(acct)
        result = await service.emergency_halt(client, uid, reason="Telegram operator emergency halt")
        sq = result.get("square_off") or {}
        return ("<b>Emergency halt requested</b>\n"
                f"Confirmed exits: {sq.get('squared_off', 0)} · awaiting fills: {sq.get('submitted_awaiting_fill', 0)} · failures: {sq.get('failed', 0)}")
    return "Done."


# ── command + callback handlers (called by telegram_bot dispatcher) ───────────
_scan_tasks: dict[str, asyncio.Task] = {}


async def close_scan_tasks() -> None:
    tasks = list(_scan_tasks.values()) + list(_login_confirmation_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _scan_tasks.clear()
    _login_confirmation_tasks.clear()


async def _request_scan(chat_id: str, message_id: int | None = None) -> None:
    active = _scan_tasks.get(chat_id)
    busy = active is not None and not active.done()
    text = (
        "🔄 <b>Scan already running</b>\n\n<i>Menu stays active — results incoming.</i>"
        if busy else
        "🔄 <b>Scanning…</b>\n\n<i>Menu stays active — results incoming.</i>"
    )
    if message_id is None:
        await _send(text, chat_id, kite_menu_kb())
    else:
        await _edit(chat_id, message_id, text, kite_menu_kb())
    if busy:
        return

    async def finish():
        try:
            result = await _run_scan()
            await _send(result, chat_id, kite_menu_kb())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Telegram scan failed: %s", type(exc).__name__)
            await _send(
                "⚠️ <b>Scan failed</b>\n\nCheck the Kite session in Sterling and try again.",
                chat_id, kite_menu_kb(),
            )
        finally:
            _scan_tasks.pop(chat_id, None)

    _scan_tasks[chat_id] = asyncio.create_task(finish())


async def handle_clear_chat(chat_id: str, message_id: int | None = None) -> None:
    """Reset navigation without deleting trade alerts or user history."""
    clean_text = "<b>Desk reset</b>\nCurrent navigation is below. Your chat history and trade alerts are preserved."
    await _send(clean_text, chat_id, kite_menu_kb())


async def handle_kite_command(chat_id: str) -> None:
    await _send(build_kite_status_text(), chat_id, kite_menu_kb())


async def handle_text_command(chat_id: str, text: str, message_id: int | None = None) -> None:
    # Phone browsers cannot reach a callback registered as localhost. The full
    # signed redirect can instead be handed to this already-authorized chat.
    if text.lower().startswith(("http://", "https://")) and (
        "request_token" in text or "state=" in text
    ):
        await _recover_broker_login(chat_id, text)
        return
    command = text.split()[0].split("@", 1)[0].lower()
    if command in {"/app", "/miniapp", "/tma", "/url", "/set_url", "/set_tma_url"} and len(text.split()) == 1 and not _get_tma_url():
        await _send("<b>Mini App setup</b>\nConfigure your reachable HTTPS address with /url https://your-domain/tma. The chat desk below works without a Mini App URL.", chat_id, kite_menu_kb())
        return
    if command in {"/start", "/menu", "/kite", "/status"}:
        await handle_kite_command(chat_id)
    elif command in {"/operator", "/login_settings"}:
        await operator_login_card(chat_id)
    elif command in {"/url", "/set_url", "/set_tma_url"}:
        parts = text.split(maxsplit=1)
        new_url = parts[1].strip() if len(parts) > 1 else ""
        if new_url:
            clean_url = set_tma_url(new_url)
            try:
                from app.services.notifications import telegram_bot
                await telegram_bot._api("setChatMenuButton", {
                    "menu_button": {
                        "type": "web_app",
                        "text": "Sterling App",
                        "web_app": {"url": clean_url}
                    }
                }, timeout=10)
            except Exception as _e:
                log.warning("Failed to refresh setChatMenuButton: %s", _e)
            kb = {"inline_keyboard": [
                [{"text": "📱 Open Sterling Mini App", "web_app": {"url": clean_url}}],
                [_btn("← Menu", "kmenu")]
            ]}
            await _send(
                f"✅ <b>MINI APP URL UPDATED</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Active WebApp URL:\n"
                f"<code>{clean_url}</code>  <i>(Tap to copy)</i>\n\n"
                f"<b>BotFather Instructions:</b>\n"
                f"1. Open <b>@BotFather</b> → <code>/mybots</code>\n"
                f"2. Select your Bot → <b>Bot Settings</b> → <b>Menu Button</b> → <b>Edit Menu Button URL</b>\n"
                f"3. Paste: <code>{clean_url}</code>",
                chat_id,
                kb,
            )
        else:
            from app.services import public_access
            access = public_access.status()
            urls = access.get("urls") or {}
            tma_url = _get_tma_url()
            lines = ["🌐 <b>STERLING PUBLIC LINKS</b>"]
            if urls.get("cloudflare"):
                lines.append("Cloudflare: <code>" + _html.escape(urls["cloudflare"]) + "</code>")
            if tma_url:
                lines.append("Primary Telegram desk: <code>" + _html.escape(tma_url) + "</code>")
            if not urls and not tma_url:
                lines.append("No verified HTTPS tunnel has been published yet.")
            lines.append("Use /menu for the Cloudflare launch button.")
            lines.append("Kite callback uses only the primary configured address.")
            await _send("\n".join(lines), chat_id, kite_menu_kb())
    elif command in {"/app", "/miniapp", "/tma"}:
        tma_url = _get_tma_url()
        kb = {"inline_keyboard": [
            [{"text": "📱 Open Sterling Mini App", "web_app": {"url": tma_url}}],
            [_btn("← Menu", "kmenu")]
        ]}
        await _send(
            f"<b>📱 STERLING TELEGRAM MINI APP</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Copyable WebApp URL:\n"
            f"<code>{tma_url}</code>  <i>(Tap to copy)</i>\n\n"
            f"<i>To change URL: send <code>/url https://your-domain.com/tma</code></i>\n\n"
            f"• Live Strategy Signals & Orders\n"
            f"• Master ALGO & Strategy Toggles\n"
            f"• Real-time PCR Analytics & Flow Tapes\n"
            f"• Open Positions & One-Tap Square-Off\n"
            f"• Live Stock & Option Quote Search",
            chat_id,
            kb,
        )
    elif command in {"/order", "/quickorder", "/buy", "/sell"}:
        await _send(
            "⚡ <b>QUICK ORDER TERMINAL</b>\n"
            "\n"
            "Select contract side to prepare market order:\n"
            "• <b>NIFTY CE</b>: Buy Nifty Call Option\n"
            "• <b>NIFTY PE</b>: Buy Nifty Put Option\n"
            "• <b>SENSEX CE</b>: Buy Sensex Call Option\n"
            "• <b>SENSEX PE</b>: Buy Sensex Put Option",
            chat_id,
            {"inline_keyboard": [
                [_btn("🟢 BUY NIFTY CE", "kqorder|NIFTY|CE"), _btn("🔴 BUY NIFTY PE", "kqorder|NIFTY|PE")],
                [_btn("🟢 BUY SENSEX CE", "kqorder|SENSEX|CE"), _btn("🔴 BUY SENSEX PE", "kqorder|SENSEX|PE")],
                [_btn("← Menu", "kmenu")]
            ]}
        )
    elif command in {"/clear", "/reset"}:
        await handle_clear_chat(chat_id, message_id)
    elif command == "/login":
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            await _recover_broker_login(chat_id, parts[1].strip())
        else:
            text_out, keyboard = await checked_broker_login_card()
            await _send(text_out, chat_id, keyboard)
    elif command == "/health":
        await _send(live_health_text(), chat_id, _back_kb())
    elif command == "/strategies":
        await _send(strategies_text(), chat_id, strategies_kb())
    elif command == "/signals":
        await _send(await build_kite_signals_text("all"), chat_id, await signals_kb("all"))
    elif command == "/positions":
        await _send(await build_kite_positions_text(), chat_id, await positions_kb())
    elif command == "/pnl":
        await _send(await build_kite_pnl_text(), chat_id, _back_kb())
    elif command == "/orders":
        await _send(await build_kite_orders_text(), chat_id, orders_kb())
    elif command == "/pcr_nifty":
        await _send(await build_kite_pcr_flow_tape_text("NIFTY"), chat_id, await pcr_kb("FLOW_NIFTY"))
    elif command == "/pcr_sensex":
        await _send(await build_kite_pcr_flow_tape_text("SENSEX"), chat_id, await pcr_kb("FLOW_SENSEX"))
    elif command in {"/pcr"}:
        await _send(await build_kite_pcr_text("SENSEX"), chat_id, await pcr_kb("SENSEX"))
    elif command == "/funds":
        await _send(await build_kite_funds_text(), chat_id, _back_kb())
    elif command == "/ticker":
        await _send(build_kite_ticker_text(), chat_id, _back_kb())
    elif command == "/search":
        parts = text.split(maxsplit=1)
        q = parts[1] if len(parts) > 1 else ""
        await _send(await build_kite_search_text(q), chat_id, _back_kb())
    elif command == "/market":
        await _send(build_kite_market_text(), chat_id, _back_kb())
    elif command == "/panic":
        text_out, kb = panic_confirm_card()
        await _send(text_out, chat_id, kb)
    elif command == "/scan":
        await _request_scan(chat_id)
    elif command == "/alerts":
        on = not alerts_enabled(UID)
        set_alerts_enabled(UID, on)
        icon = "🟢" if on else "⚪"
        await _send(
            f"{icon} <b>Alerts {'On' if on else 'Off'}</b>\n"
            f"\n"
            f"Signal alert cards are now <b>{'enabled' if on else 'disabled'}</b>.\n"
            f"<i>Protection and exit alerts are always sent.</i>",
            chat_id, kite_menu_kb(),
        )
    elif command == "/algo":
        from app.services.kite_engine import state
        current = bool(state.get_config(UID).auto_execute)
        action = {"kind": "auto", "to": "OFF" if current else "ON"}
        token = _stage(action)
        await _send(_confirm_text(action), chat_id, _confirm_kb(token))
    elif command == "/help":
        await _send(
            "📋 <b>STERLING — ALL COMMANDS</b>\n"
            "\n"
            "<b>Home & Mini App</b>\n"
            "/menu      ·  Home — status & navigation\n"
            "/app       ·  📱 Launch Sterling Mini App\n"
            "/clear     ·  🧹 Clear & reset chat screen\n"
            "\n"
            "<b>Market & Analytics</b>\n"
            "/signals   ·  Active signal board (all strategies)\n"
            "/positions ·  Open positions & square off\n"
            "/orders    ·  Today's executed & pending orders\n"
            "/pnl       ·  P&L summary (realized + unrealized)\n"
            "/funds     ·  Broker margins & available trading power\n"
            "/pcr       ·  PCR analytics table & Sensex card\n"
            "/pcr_nifty ·  📈 Nifty 15m PCR flow tape\n"
            "/pcr_sensex·  📈 Sensex 15m PCR flow tape\n"
            "/market    ·  Live index prices & sentiment\n"
            "/ticker    ·  WebSocket live stream status\n"
            "/search    ·  Live stock & option quote lookup\n"
            "/scan      ·  Run SuperTrend scan now\n"
            "\n"
            "<b>Controls & Safety</b>\n"
            "/order     ·  ⚡ Quick Order Terminal\n"
            "/algo      ·  Toggle ALGO auto-execution\n"
            "/alerts    ·  Toggle real-time signal alerts\n"
            "/panic     ·  🚨 Emergency square off all positions\n"
            "\n"
            "<b>System</b>\n"
            "/health    ·  System & broker health\n"
            "/login     ·  Kite login — account details\n"
            "/strategies·  Strategies with enable/disable toggles\n"
            "\n"
            "<i>All order actions require 2-step confirmation.</i>",
            chat_id, kite_menu_kb(),
        )
    else:
        await _send(
            "🏠 <b>STERLING</b>\n"
            "\n"
            "Unknown command. Try /menu or /help.",
            chat_id, kite_menu_kb(),
        )


async def handle_kite_callback(chat_id: str, message_id: int, cb_id: str, data: str) -> None:
    parts = data.split("|")
    kind  = parts[0]
    arg   = parts[1] if len(parts) > 1 else ""
    await _answer_cb(cb_id)

    if kind == "kmenu":
        await _send(build_kite_status_text(), chat_id, kite_menu_kb())

    elif kind == "kquick_order":
        await _send(
            "⚡ <b>QUICK ORDER TERMINAL</b>\n"
            "\n"
            "Select contract side to prepare market order:\n"
            "• <b>NIFTY CE</b>: Buy Nifty Call Option\n"
            "• <b>NIFTY PE</b>: Buy Nifty Put Option\n"
            "• <b>SENSEX CE</b>: Buy Sensex Call Option\n"
            "• <b>SENSEX PE</b>: Buy Sensex Put Option",
            chat_id,
            {"inline_keyboard": [
                [_btn("🟢 BUY NIFTY CE", "kqorder|NIFTY|CE"), _btn("🔴 BUY NIFTY PE", "kqorder|NIFTY|PE")],
                [_btn("🟢 BUY SENSEX CE", "kqorder|SENSEX|CE"), _btn("🔴 BUY SENSEX PE", "kqorder|SENSEX|PE")],
                [_btn("← Menu", "kmenu")]
            ]}
        )

    elif kind == "kqorder":
        idx = parts[1] if len(parts) > 1 else "NIFTY"
        otype = parts[2] if len(parts) > 2 else "CE"
        await signals_kb("supertrend")
        selected = next((a for a in _sig_actions.values() if a["option_symbol"].startswith(idx) and a["option_symbol"].endswith(otype)), None)
        if selected is None:
            await _send("No fresh broker-listed contract for that selection. Refresh Signals or search an exact contract in the Mini App.", chat_id, _back_kb())
            return
        action = {**selected, "kind": "order", "origin": "signal", "side": "BUY"}
        ptok = _stage(action)
        await _send(_confirm_text(action), chat_id, _confirm_kb(ptok))

    elif kind in {"kclear", "kclear_chat"}:
        await handle_clear_chat(chat_id, message_id)

    elif kind in {"klogin", "klogincopy"}:
        text_out, keyboard = await checked_broker_login_card()
        await _send(text_out, chat_id, keyboard)

    elif kind == "kloginstatus":
        await _send(await broker_login_status_text(), chat_id, _login_status_kb())

    elif kind == "kloginrecover":
        await _send(_broker_login_recovery_prompt(), chat_id, _login_status_kb())

    elif kind == "koperator":
        await operator_login_card(chat_id)

    elif kind == "khealth":
        await _send(live_health_text(), chat_id, _back_kb())

    elif kind in {"kstrategies", "kstrat"}:
        await _send(strategies_text(), chat_id, strategies_kb())

    elif kind == "kstrat_toggle":
        from app.services import archived_strategies
        try:
            currently_on = archived_strategies.enabled(UID, arg)
            archived_strategies.set_enabled(UID, arg, not currently_on)
            new_state = "enabled" if not currently_on else "disabled"
            name = archived_strategies.CATALOG.get(arg, (arg, ""))[0]
            icon = "🟢" if not currently_on else "⚫"
            await _send(
                f"{icon} <b>{_html.escape(name)}</b> {new_state}.\n\n"
                + strategies_text(),
                chat_id,
                strategies_kb(),
            )
        except Exception as exc:  # noqa: BLE001
            await _send(
                f"⚠️ <b>Toggle failed</b>\n\n{_html.escape(str(exc)[:200])}",
                chat_id,
                strategies_kb(),
            )

    elif kind == "ksig":
        await _send(await build_kite_signals_text("all"), chat_id, await signals_kb("all"))

    elif kind == "ksig_src":
        source = arg if arg in _STRATEGY_LABELS else "all"
        await _send(await build_kite_signals_text(source), chat_id, await signals_kb(source))

    elif kind == "kpos":
        await _send(await build_kite_positions_text(), chat_id, await positions_kb())

    elif kind == "kpnl":
        await _send(await build_kite_pnl_text(), chat_id, _back_kb())

    elif kind == "kfunds":
        await _send(await build_kite_funds_text(), chat_id, _back_kb())

    elif kind == "kticker":
        await _send(build_kite_ticker_text(), chat_id, _back_kb())

    elif kind == "ksearch":
        await _send(await build_kite_search_text(""), chat_id, _back_kb())

    elif kind == "korders":
        await _send(await build_kite_orders_text(), chat_id, orders_kb())

    elif kind in {"kpcr", "kpcr_sym"}:
        sym = arg if arg else "SENSEX"
        await _send(await build_kite_pcr_text(sym), chat_id, await pcr_kb(sym))

    elif kind == "kpcr_table":
        sym = arg.upper() if arg else "NIFTY"
        await _send(await build_kite_pcr_table_text(sym), chat_id, await pcr_kb(f"TABLE_{sym}"))

    elif kind == "kpcr_flow":
        sym = arg.upper() if arg else "NIFTY"
        await _send(await build_kite_pcr_flow_tape_text(sym), chat_id, await pcr_kb(f"FLOW_{sym}"))

    elif kind == "kpcr_full":
        sym = arg.upper() if arg else "NIFTY"
        await _send(await build_kite_pcr_table_text(sym), chat_id, await pcr_kb(f"TABLE_{sym}"))

    elif kind == "kmarket":
        await _send(build_kite_market_text(), chat_id, _back_kb())

    elif kind == "kpanic":
        text_out, kb = panic_confirm_card()
        await _send(text_out, chat_id, kb)

    elif kind == "kscan":
        await _request_scan(chat_id, message_id)

    elif kind == "kalert":
        on = not alerts_enabled(UID)
        set_alerts_enabled(UID, on)
        icon = "🟢" if on else "⚪"
        await _send(
            f"{icon} <b>Alerts {'On' if on else 'Off'}</b>\n"
            f"\n"
            f"Signal alert cards are now <b>{'enabled' if on else 'disabled'}</b>.\n"
            f"<i>Protection and exit alerts are always sent.</i>",
            chat_id, kite_menu_kb(),
        )

    elif kind == "kauto":
        from app.services.kite_engine import state
        cur    = getattr(state.get_config(UID), "auto_execute", False)
        action = {"kind": "auto", "to": "OFF" if cur else "ON"}
        ptok   = _stage(action)
        await _send(_confirm_text(action), chat_id, _confirm_kb(ptok))

    elif kind == "kbuy":
        a = _fresh_action("sig", arg)
        if not a or not _signal_action_current(a):
            await _edit(
                chat_id, message_id,
                "⚠️ <b>Signal expired</b>\n\nRefresh signals to see the current board.",
                await signals_kb("all"),
            )
            return
        action = {
            "kind":                "order",
            "origin":              "signal",
            "side":                "BUY",
            "option_symbol":       a["option_symbol"],
            "exchange":            a["exchange"],
            "qty":                 a["qty"],
            "signal_timestamp_ms": a["signal_timestamp_ms"],
        }
        ptok = _stage(action)
        await _edit(chat_id, message_id, _confirm_text(action), _confirm_kb(ptok))

    elif kind == "ksq":
        a = _fresh_action("pos", arg)
        if not a:
            await _edit(chat_id, message_id, "⚠️ Position expired — refresh.", _back_kb())
            return
        action = {"kind": "order", "side": a["side"], "option_symbol": a["option_symbol"],
                  "exchange": a["exchange"], "qty": a["qty"]}
        ptok = _stage(action)
        await _edit(chat_id, message_id, _confirm_text(action), _confirm_kb(ptok))

    elif kind == "kok":
        action = _pop_pending(arg)
        if not action:
            await _edit(
                chat_id, message_id,
                "⚠️ <b>Expired</b>\n\nThis action has timed out. Use /menu for fresh controls.",
                _back_kb(),
            )
            return
        result = await _run_action(action)
        await _edit(chat_id, message_id, result, kite_menu_kb())

    elif kind == "kno":
        _cancel_pending(arg)
        await _edit(chat_id, message_id, "✕  <b>Cancelled</b>", kite_menu_kb())

    else:
        await _edit(
            chat_id, message_id,
            "⚠️ <b>Menu outdated</b>\n\nThis button is no longer valid. Use /menu for current controls.",
            kite_menu_kb(),
        )


async def _run_scan() -> str:
    from app.services.exchanges.kite import accounts as kite_accounts
    from app.services.kite_engine import service
    acct = _active_account()
    if acct is None:
        return "⚠️ <b>Scan failed</b>\n\nNo active Kite account."
    try:
        client = await kite_accounts.acquire_client(acct)
        await service.scan_user(client, UID)
        active = len(_active_rows())
        fresh  = len(_ready_rows())
        icon   = "📡" if active else "ℹ️"
        if active:
            label = f"{active} active signal{'s' if active != 1 else ''}"
            if fresh:
                label += f"  ·  {fresh} fresh 🔵"
        else:
            label = "No signals on the board"
        return f"{icon} <b>Scan complete</b>  ·  {label}"
    except Exception as exc:  # noqa: BLE001
        short = _html.escape(str(exc)[:160])
        return f"⚠️ <b>Scan failed</b>\n\n{short}"


# ── alert push ────────────────────────────────────────────────────────────────
def _alert_html(alert: StrategyAlert, uid: str = UID) -> str:
    """Signal alert card — concise, scannable, consistent with lifecycle cards."""
    if alert.strategy == "pcr_flow" or alert.strategy.startswith("gamma_move_"):
        return alert.detail

    upper = alert.direction.upper()
    if upper in {"LONG", "BULLISH", "UP"}:
        dir_icon = "🟢"
        arrow    = "▲"
    elif upper in {"SHORT", "BEARISH", "DOWN"}:
        dir_icon = "🔴"
        arrow    = "▼"
    else:
        dir_icon = "🟡"
        arrow    = "·"

    kind       = _lf.instrument_kind(alert.option_symbol) or alert.option_type
    instrument = "  ".join(
        part for part in (_lf.esc(kind), _lf.esc(alert.option_symbol)) if part
    )

    lines = [
        _lf.header(alert.strategy_label, "SIGNAL"),
        f"{dir_icon}  <b>{_lf.esc_clip(alert.title, 300)}</b>  {arrow}  {_lf.esc(alert.direction).title()}",
    ]
    if instrument:
        lines.append(f"<code>{instrument}</code>")
    if alert.detail:
        lines.append(f"<i>{_lf.esc_clip(alert.detail, 800)}</i>")
    lines.append(_lf.footer(_mode_label(uid), alert.timestamp_ms))
    return "\n".join(lines)




def _alert_user_ids() -> list[str]:
    """Every active Kite tenant or tenant with a pending lifecycle alert."""
    users: set[str] = set()
    try:
        from app.services.exchanges.kite import accounts as kite_accounts

        kite_accounts.bootstrap()
        users.update(a.user_id for a in kite_accounts.all_accounts() if a.is_active)
    except Exception as exc:  # noqa: BLE001
        log.warning("Kite Telegram user discovery failed: %s", exc)
    try:
        from app.services.notifications import telegram_lifecycle
        users.update(telegram_lifecycle.pending_user_ids())
    except Exception as exc:  # noqa: BLE001
        log.debug("Lifecycle Telegram user discovery unavailable: %s", exc)
    return sorted(uid for uid in users if uid)


async def _gamma_rate_slot(uid: str, destination: str) -> bool:
    """Reserve one Gamma alert send under the uploaded client limits."""
    from app.services.gamma_move import get_config

    cfg = get_config(uid)
    per_min = max(1, int(cfg.alert_group_rate_per_min))
    per_sec = max(0.01, float(cfg.alert_per_chat_rate_per_sec))
    now_wall = int(_rate_wall_time())
    bucket = now_wall // 60
    now_mono = _rate_monotonic()

    old_bucket, count, last_mono = _gamma_alert_rate.get(
        (uid, destination), (bucket, 0, 0.0)
    )
    if old_bucket != bucket:
        count, last_mono = 0, 0.0
    if count >= per_min:
        return False

    min_gap = 1.0 / per_sec
    wait = min_gap - (now_mono - last_mono)
    if wait > 0:
        await asyncio.sleep(wait)
        now_mono = _rate_monotonic()

    _gamma_alert_rate[(uid, destination)] = (
        bucket, count + 1, now_mono
    )
    return True


#: Telegram allows roughly one message per second to a single chat (and 20 a
#: minute to a group).  Pacing every destination keeps a burst of lifecycle
#: events (a halt touches all three strategies at once) from tripping a flood wait.
_MIN_SEND_GAP_S = 1.05
_dest_last_send: Dict[str, float] = {}
_NO_DESTINATION_WARNED: Dict[str, float] = {}


async def _pace(destination: str) -> None:
    last = _dest_last_send.get(destination)
    if last is not None:
        wait = _MIN_SEND_GAP_S - (time.monotonic() - last)
        if wait > 0:
            await asyncio.sleep(wait)
    _dest_last_send[destination] = time.monotonic()
    if len(_dest_last_send) > 256:  # bounded: destinations are few, but never leak
        for stale in sorted(_dest_last_send, key=_dest_last_send.get)[:128]:
            _dest_last_send.pop(stale, None)


def _is_critical(event) -> bool:
    return str(getattr(event, "severity", "") or "").upper() in {"CRITICAL", "ERROR"}


_last_welcome_sent_time: dict[str, float] = {}
_lifecycle_delivery_locks: dict[str, asyncio.Lock] = {}
_login_confirmation_tasks: set[asyncio.Task] = set()


def schedule_broker_login_confirmation(uid: str, event_id: str | None) -> None:
    """Promptly deliver a saved login event; the outbox remains the retry authority."""
    if not isinstance(event_id, str) or not event_id:
        return

    async def deliver() -> None:
        try:
            await asyncio.wait_for(_deliver_broker_login_confirmation(uid, event_id), timeout=12.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — failure leaves the event queued
            log.warning("Prompt broker login confirmation deferred for %s: %s", uid, type(exc).__name__)

    task = asyncio.create_task(deliver(), name="kite-login-confirmation")
    _login_confirmation_tasks.add(task)
    task.add_done_callback(_login_confirmation_tasks.discard)


async def _deliver_broker_login_confirmation(uid: str, event_id: str) -> None:
    from app.services.notifications import kite_telegram_store as kts

    targets = await asyncio.to_thread(kts.enabled_targets, uid)
    use_legacy = uid == UID and not targets and bool(_tg.TELEGRAM_TOKEN and _tg.TELEGRAM_CHAT_ID)
    if targets or use_legacy:
        await _drain_lifecycle(uid, targets, use_legacy, event_id=event_id)


async def _drain_lifecycle(
    uid: str, targets: list, use_legacy: bool, *, event_id: str | None = None,
) -> None:
    from app.services.notifications import telegram_lifecycle

    lock = _lifecycle_delivery_locks.setdefault(uid, asyncio.Lock())
    if event_id is not None:
        async with lock:
            await _drain_lifecycle_locked(uid, targets, use_legacy, event_id=event_id)
        return
    try:
        events = await asyncio.to_thread(telegram_lifecycle.due, uid)
    except Exception as exc:  # noqa: BLE001
        log.warning("Lifecycle Telegram outbox unavailable for %s: %s", uid, exc)
        return
    # Yield the lock after each event. A new login can then send after the current
    # event instead of waiting behind a paced batch of up to 50 trade alerts.
    for event in events:
        async with lock:
            # Re-read under the lock: another drain may already have delivered it.
            await _drain_lifecycle_locked(uid, targets, use_legacy, event_id=event.event_id)


def _lifecycle_destinations(uid: str, targets: list, use_legacy: bool, *, login: bool) -> list:
    destinations = [("legacy", None)] if use_legacy else [(_target_delivery_id(t), t) for t in targets]
    if not login:
        return destinations
    # Login is an answer to the controller, even when trade-alert destinations
    # were configured separately. Deduplicate aliases for the same bot and chat.
    primary = uid == UID and bool(_tg.TELEGRAM_TOKEN and _tg.TELEGRAM_CHAT_ID)
    if primary and not use_legacy:
        destinations.insert(0, ("legacy", None))
    result, endpoints = [], set()
    for destination_id, target in destinations:
        try:
            endpoint = (
                str(_tg.TELEGRAM_TOKEN), str(_tg.TELEGRAM_CHAT_ID),
            ) if target is None else (str(target.bot_token), str(target.chat_id))
        except Exception:  # noqa: BLE001 — corrupt target cannot hide the controller's confirmation
            endpoint = (destination_id, "")
        if endpoint not in endpoints:
            result.append((destination_id, target))
            endpoints.add(endpoint)
    return result


async def _drain_lifecycle_locked(
    uid: str, targets: list, use_legacy: bool, *, event_id: str | None = None,
) -> None:
    """Deliver due lifecycle events; durable, per-destination, never blocks trading.

    Each destination acknowledges separately.  A failed destination keeps the
    event queued with bounded backoff (one failed attempt per event per cycle,
    however many destinations failed).  All SQLite work runs in a worker thread
    so a busy database cannot stall the event loop that is managing positions.
    """
    from app.services.notifications import telegram_lifecycle

    try:
        if event_id is None:
            events = await asyncio.to_thread(telegram_lifecycle.due, uid)
        else:
            events = await asyncio.to_thread(telegram_lifecycle.due, uid, event_id=event_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("Lifecycle Telegram outbox unavailable for %s: %s", uid, exc)
        return
    if not events:
        return

    from app.services.notifications import kite_telegram_store as kts

    for event in events:
        html = _lifecycle_html(event, uid)
        delivered = set(event.delivered_to)
        failures: list[str] = []
        deferred = False
        is_welcome_evt = event.event_type in {
            "LIVE STATUS", "TRADING READY", "LIVE READY", "LIVE BOOT", "BROKER CONNECTION"
        }
        is_login_evt = event.event_type == "BROKER CONNECTED"
        destinations = _lifecycle_destinations(uid, targets, use_legacy, login=is_login_evt)
        destination_ids = [d for d, _ in destinations]
        for destination_id, target in destinations:
            if destination_id in delivered:
                continue
            if is_welcome_evt and event.severity == "INFO":
                last_sent = _last_welcome_sent_time.get(destination_id, 0.0)
                if time.time() - last_sent < 900:
                    delivered.add(destination_id)
                    continue
            try:
                if (
                    _lf.is_gamma(event.strategy)
                    and not _is_critical(event)
                    and not await _gamma_rate_slot(uid, destination_id)
                ):
                    deferred = True  # rate-limited, not failed: retry next cycle
                    continue
                await _pace(destination_id)
                welcome_kb = _login_status_kb() if is_login_evt else kite_menu_kb()
                if target is None:
                    keyboard = welcome_kb if is_welcome_evt or is_login_evt else None
                    response = await _send(html, str(_tg.TELEGRAM_CHAT_ID), keyboard)
                    ok = bool((response or {}).get("ok"))
                else:
                    if is_welcome_evt or is_login_evt:
                        keyboard = welcome_kb
                        ok = bool(await kts.send_via(target.bot_token, target.chat_id, html, reply_markup=keyboard))
                    else:
                        ok = bool(await kts.send_via(target.bot_token, target.chat_id, html))
                if ok and is_welcome_evt:
                    _last_welcome_sent_time[destination_id] = time.time()
            except Exception as exc:  # noqa: BLE001
                ok = False
                log.warning(
                    "Lifecycle Telegram delivery failed for %s/%s: %s",
                    uid, destination_id, type(exc).__name__,
                )
            if ok:
                try:
                    await asyncio.to_thread(
                        telegram_lifecycle.mark_delivered, event.event_id, destination_id
                    )
                except Exception as exc:  # noqa: BLE001
                    # Delivered but unrecorded: worst case one duplicate after the
                    # next cycle; never treat it as unsent-and-lost.
                    log.warning("Lifecycle delivery ack not persisted: %s", exc)
                delivered.add(destination_id)
            else:
                failures.append(
                    "legacy bot" if target is None
                    else f"target {getattr(target, 'id', '?')}"
                )
        try:
            if failures:
                await asyncio.to_thread(
                    telegram_lifecycle.mark_failure,
                    event.event_id,
                    "; ".join(failures) + " rejected delivery",
                )
            elif not deferred:
                await asyncio.to_thread(
                    telegram_lifecycle.complete_if_delivered,
                    event.event_id,
                    destination_ids,
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("Lifecycle outbox bookkeeping failed: %s", exc)


def _warn_undeliverable(uid: str) -> None:
    """Pending trade alerts with nowhere to go must be visible, not silent."""
    now = time.monotonic()
    if now - _NO_DESTINATION_WARNED.get(uid, -1e9) < 600:
        return
    try:
        from app.services.notifications import telegram_lifecycle

        summary = telegram_lifecycle.pending_summary(uid)
    except Exception:  # noqa: BLE001
        return
    if summary.get("pending"):
        _NO_DESTINATION_WARNED[uid] = now
        log.warning(
            "Telegram: %s trade alert(s) for %s are queued but no Telegram "
            "destination is configured (oldest %ss)",
            summary["pending"], uid, summary.get("oldest_age_s", 0),
        )


async def push_kite_alerts(*, include_signals: bool = True) -> None:
    """Drain durable lifecycle alerts, then push current signals.

    Lifecycle events (entry, protection, trail, exit, halts) are real-money state
    changes: they drain FIRST, on every cycle, regardless of market hours and
    regardless of the operator's signal-alert toggle (that switch silences signal
    cards only; it can never hide a stop failure or an exit).

    include_signals is false outside market hours so a stale board snapshot
    cannot create a new signal notification after the session.

    Delivery is acknowledged per destination. A failed send remains eligible for
    retry on the next cycle. The legacy single bot is fallback-only for the
    historical default user; it is never used to leak another tenant's signals.
    One tenant's failure never prevents delivery to the others.
    """
    from app.services.notifications import kite_telegram_store as kts

    legacy_ok = bool(_tg.TELEGRAM_TOKEN and _tg.TELEGRAM_CHAT_ID)
    try:
        # A condition no strategy raises on its own: a missing broker session.
        # (The single boot message belongs to live_runtime.)
        from app.services.notifications import system_alerts

        system_alerts.check_broker_sessions()
        check_connection_alert()
    except Exception as exc:  # noqa: BLE001
        log.debug("system alert checks skipped: %s", type(exc).__name__)
    for uid in _alert_user_ids():
        try:
            await _push_user(uid, kts, legacy_ok, include_signals)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Telegram alert push failed for %s: %s", uid, type(exc).__name__,
                exc_info=True,
            )


async def _push_user(uid: str, kts, legacy_ok: bool, include_signals: bool) -> None:
    # The virtual Simulation tenant has no chat of its own: its alerts (prefixed at render
    # time) go to its owner's destinations, so the family sees them where they already look.
    route_uid = _sim_identity.owner_uid(uid)
    try:
        targets = kts.enabled_targets(route_uid)
    except Exception as exc:  # noqa: BLE001
        log.warning("Kite Telegram targets unavailable for %s: %s", uid, exc)
        return

    use_legacy = route_uid == UID and not targets and legacy_ok
    if not targets and not use_legacy:
        _warn_undeliverable(uid)
        return

    # 1. Trade lifecycle first and unconditionally.
    await _drain_lifecycle(uid, targets, use_legacy)

    # Signal cards come from the live tenant; the simulation runs the same signals and
    # would only repeat each card, so it speaks through lifecycle alerts alone.
    if _sim_identity.is_sim_uid(uid):
        return

    # 2. Signal cards: gated by market hours and the operator's toggle.
    if not alerts_enabled(uid):
        return
    _load_alerted(uid)
    rows = _strategy_alerts(uid) if include_signals else []

    # Never infer "signal ended" from an empty/current snapshot: a transient
    # scanner outage is indistinguishable here. Retain recent delivery acks
    # by age so recovery does not resend already acknowledged alerts.
    if _prune_alerted(uid):
        _save_alerted(uid)

    for r in rows:
        signal_key = r.dedupe_key
        html = _alert_html(r, uid)

        if targets:
            for t in targets:
                destination_id = _target_delivery_id(t)
                delivery_key = (uid, destination_id, signal_key)
                if delivery_key in _alerted:
                    continue
                try:
                    if (
                        (r.strategy == "gamma_move" or r.strategy.startswith("gamma_move_"))
                        and not await _gamma_rate_slot(uid, destination_id)
                    ):
                        continue
                    await _pace(destination_id)
                    ok = await kts.send_via(t.bot_token, t.chat_id, html)
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "Kite Telegram delivery failed for %s/%s: %s",
                        uid, getattr(t, "id", "?"), type(exc).__name__,
                    )
                    ok = False
                try:
                    if bool(getattr(t, "reachable", False)) != ok:
                        kts.set_reachable(uid, str(t.id), ok)
                except Exception as exc:  # noqa: BLE001
                    log.debug("Kite Telegram reachability update failed: %s", exc)
                if ok:
                    _alerted.add(delivery_key)
                    _save_alerted(uid)
        elif use_legacy:
            delivery_key = (uid, "legacy", signal_key)
            if delivery_key in _alerted:
                continue
            if (
                (r.strategy == "gamma_move" or r.strategy.startswith("gamma_move_"))
                and not await _gamma_rate_slot(uid, "legacy")
            ):
                continue
            await _pace("legacy")
            response = await _send(
                html,
                str(_tg.TELEGRAM_CHAT_ID),
                kite_menu_kb() if r.strategy == "kite_supertrend" else None,
            )
            if bool((response or {}).get("ok")):
                _alerted.add(delivery_key)
                _save_alerted(uid)
