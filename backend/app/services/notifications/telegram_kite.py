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
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from app.core.auth import DEFAULT_USER_ID
from app.core.logging import get_logger

from app.services.notifications import lifecycle_format as _lf
from app.services.notifications import telegram as _tg
from app.services.notifications.telegram_bot import _answer_cb, _btn, _edit, _send

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
_counter = 0
# (uid, destination) -> (UTC minute bucket, sent count, last monotonic send).
# Applies only to Gamma-Unwind cards, matching the uploaded client's alert
# throttles without slowing unrelated strategy notifications.
_gamma_alert_rate: Dict[tuple[str, str], tuple[int, int, float]] = {}


def _next_id() -> str:
    global _counter
    _counter += 1
    return str(_counter)


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
            )
        )
    return alerts


def _snapback_alerts(uid: str) -> list[StrategyAlert]:
    """Advisory Snapback signal cards; never attach generic trade callbacks.

    Snapback is still evidence-gated/research-only for automatic execution. The
    dashboard signal itself is nevertheless useful to an operator, so Telegram
    mirrors the current live-scan rows while preserving the row signal_id as the
    delivery identity. A scan refresh therefore cannot resend the same setup.
    """
    try:
        from app.services.snapback import snapshot

        snap = snapshot(uid)
    except Exception as exc:
        log.warning("Snapback Telegram snapshot failed for %s: %s", uid, exc)
        return []

    if not bool((snap.get("strategy") or {}).get("enabled", True)):
        return []

    manual_capability = (
        ((snap.get("capabilities") or {}).get("manual_execution") or {})
    )
    strategy_execution_available = manual_capability.get("single_leg") is True

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
        executable = row_eligible and strategy_execution_available
        detail_parts = [
            "ARMED" if state == "armed" else "WATCH",
            "quoted" if origin == "live_quote" else (origin or "signal"),
            "manual execution available" if executable else "advisory only",
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
            )
        )
    return alerts

def _gamma_move_alerts(uid: str) -> list[StrategyAlert]:
    """Reference Gamma-Unwind outbound tiers.

    The supplied client routes ENTRY and STRONG_WATCHLIST cards to Telegram;
    plain WATCHLIST remains dashboard-only. This collector is read-only and
    never attaches generic trading callbacks.
    """
    try:
        from app.engines.gamma_move import (
            TIER_ENTRY,
            TIER_STRONG_WATCHLIST,
        )
        from app.services.gamma_move import get_config
        from app.services.gamma_move_runner import session_for

        cfg = get_config(uid)
        if not cfg.enabled:
            return []
        session = session_for(uid, cfg)
    except Exception as exc:                                       # noqa: BLE001
        log.debug("Gamma Unwind Telegram collection unavailable for %s: %s", uid, exc)
        return []

    alerts: list[StrategyAlert] = []
    for signal in session.signals.values():
        if signal.tier not in {TIER_ENTRY, TIER_STRONG_WATCHLIST}:
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
            )
        )
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


def _strategy_alerts(uid: str) -> list[StrategyAlert]:
    """Actionable outbound alerts across strategy families.

    Interactive Telegram order buttons intentionally remain SuperTrend-only:
    alerting an unvalidated strategy is advisory; giving it a generic trading
    callback would bypass its own promotion/admission path.
    """
    out: list[StrategyAlert] = []
    for collector in (
        _supertrend_alerts,
        _gamma_move_alerts,
        _snapback_alerts,
    ):
        try:
            out.extend(collector(uid))
        except Exception as exc:                                   # noqa: BLE001
            log.warning("Telegram alert collector %s failed for %s: %s",
                        collector.__name__, uid, exc)
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
def kite_menu_kb() -> dict:
    return {"inline_keyboard": [
        [_btn("📡 Signals", "ksig"), _btn("📊 Positions", "kpos")],
        [_btn("💰 P&L", "kpnl"), _btn("🔄 Scan now", "kscan")],
        [_btn("⚙️ Auto-trade", "kauto"), _btn("🔔 Alerts", "kalert")],
    ]}


def _back_kb() -> dict:
    return {"inline_keyboard": [[_btn("‹ Kite menu", "kmenu")]]}


def _confirm_kb(ptok: str) -> dict:
    return {"inline_keyboard": [[_btn("✅ Confirm", f"kok|{ptok}"), _btn("✖ Cancel", f"kno|{ptok}")]]}


# ── data access ───────────────────────────────────────────────────────────────
def _snapshot(uid: str = UID):
    from app.services.kite_engine.scanner import scanner
    return scanner.snapshot(uid)


def _ready_rows(uid: str = UID) -> list:
    """Fresh current-session rows that can still represent a new entry."""
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
                int(getattr(row, "timestamp_ms", 0) or 0) / 1000,
                IST,
            ).date()
        except (TypeError, ValueError, OSError, OverflowError):
            continue
        if day == today:
            ready.append(row)
    return ready


def _signal_action_current(action: dict, uid: str = UID) -> bool:
    """Revalidate a staged signal action immediately before broker submission."""
    symbol = str(action.get("option_symbol") or "")
    exchange = str(action.get("exchange") or "").upper()
    timestamp_ms = int(action.get("signal_timestamp_ms") or 0)
    if not symbol or not timestamp_ms:
        return False
    for row in _ready_rows(uid):
        if int(getattr(row, "timestamp_ms", 0) or 0) != timestamp_ms:
            continue
        if str(getattr(row, "exchange", "") or "").upper() != exchange:
            continue
        for leg in getattr(row, "legs", []) or []:
            if str(getattr(leg, "option_symbol", "") or "") == symbol:
                return True
    return False


def _fmt(v: Optional[float], dp: int = 2) -> str:
    try:
        return f"{float(v):,.{dp}f}"
    except (TypeError, ValueError):
        return "—"


# ── text builders ─────────────────────────────────────────────────────────────
def build_kite_signals_text() -> str:
    rows = _ready_rows()
    if not rows:
        return "<b>🇮🇳 Kite signals</b>\nNo fresh current-session signals are ready."
    lines = [f"<b>🇮🇳 Kite signals · {len(rows)} ready</b>"]
    for r in rows[:20]:
        arrow = "🟢▲" if r.direction == "long" else "🔴▼"
        leg = r.legs[0] if r.legs else None
        legtxt = f" · {leg.moneyness} {leg.option_symbol}" if leg else ""
        lines.append(f"{arrow} <b>{r.underlying}</b> {r.option_type}{legtxt}")
    if len(rows) > 20:
        lines.append(f"… +{len(rows) - 20} more")
    lines.append(f"\n<i>Mode: {_mode_label()}</i>")
    return "\n".join(lines)


def signals_kb() -> dict:
    """One BUY action per fresh current-session signal.

    SELL is not a signal action. Use the Positions screen to close a broker-held
    position, where Sterling can prove the exact quantity and ownership.
    """
    _sig_actions.clear()
    for key in [k for k in _action_created if k[0] == "sig"]:
        _action_created.pop(key, None)
    kb: List[list] = []
    for r in _ready_rows()[:8]:
        leg = r.legs[0] if r.legs else None
        if not leg or not leg.option_symbol:
            continue
        sid = _next_id()
        _sig_actions[sid] = {
            "option_symbol": leg.option_symbol,
            "exchange": r.exchange,
            "qty": int(leg.lot_size or 1),
            "underlying": r.underlying,
            "signal_timestamp_ms": int(r.timestamp_ms or 0),
        }
        _action_created[("sig", sid)] = time.monotonic()
        short = leg.option_symbol[-12:]
        kb.append([_btn(f"🟢 Buy {short}", f"kbuy|{sid}")])
    kb.append([_btn("🔄 Refresh", "ksig"), _btn("‹ Menu", "kmenu")])
    return {"inline_keyboard": kb}


async def build_kite_positions_text() -> str:
    acct = _active_account()
    if acct is None:
        return "<b>📊 Kite positions</b>\nNo active Kite account."
    from app.services.exchanges.kite import accounts as kite_accounts
    try:
        client = await kite_accounts.acquire_client(acct)
        positions = await client.get_positions()
    except Exception as exc:  # noqa: BLE001
        return f"<b>📊 Kite positions</b>\n⚠️ {exc}"
    live = [p for p in positions if getattr(p, "size", 0)]
    if not live:
        return "<b>📊 Kite positions</b>\nNo open positions."
    lines = [f"<b>📊 Kite positions · {len(live)}</b>"]
    for p in live:
        pnl = getattr(p, "unrealized_pnl", 0.0) or 0.0
        sign = "🟢" if pnl >= 0 else "🔴"
        lines.append(f"{sign} <b>{p.symbol}</b> {p.side} {abs(p.size)} · "
                     f"mark {_fmt(getattr(p,'mark_price',None))} · P&L {_fmt(pnl)}")
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
        for p in positions[:8]:
            ex, _, ts = p.symbol.partition(":")
            pid = _next_id()
            _pos_actions[pid] = {
                "option_symbol": ts or p.symbol, "exchange": ex or "NFO",
                "qty": abs(int(p.size)), "side": "SELL" if p.size > 0 else "BUY",
            }
            _action_created[("pos", pid)] = time.monotonic()
            kb.append([_btn(f"✖ Square off {ts[-12:]}", f"ksq|{pid}")])
    kb.append([_btn("🔄 Refresh", "kpos"), _btn("‹ Menu", "kmenu")])
    return {"inline_keyboard": kb}


async def build_kite_pnl_text() -> str:
    acct = _active_account()
    if acct is None:
        return "<b>💰 Kite P&L</b>\nNo active Kite account."
    from app.services.exchanges.kite import accounts as kite_accounts
    try:
        client = await kite_accounts.acquire_client(acct)
        positions = await client.get_positions()
    except Exception as exc:  # noqa: BLE001
        return f"<b>💰 Kite P&L</b>\n⚠️ {exc}"
    realized = sum((getattr(p, "realized_pnl", 0.0) or 0.0) for p in positions)
    unreal = sum((getattr(p, "unrealized_pnl", 0.0) or 0.0) for p in positions)
    tot = realized + unreal
    sign = "🟢" if tot >= 0 else "🔴"
    return (f"<b>💰 Kite P&L · {_mode_label()}</b>\n"
            f"Realized: {_fmt(realized)}\nUnrealized: {_fmt(unreal)}\n{sign} <b>Total: {_fmt(tot)}</b>")


def build_kite_status_text() -> str:
    from app.services.kite_engine import service, state
    cfg = state.get_config(UID)
    auto = "ON" if getattr(cfg, "auto_execute", False) else "OFF"
    running = "running" if service.is_auto_running() else "idle"
    return (f"<b>🇮🇳 Kite engine</b>\n"
            f"Mode: <b>{_mode_label()}</b>\n"
            f"Auto-trade: <b>{auto}</b> · loop {running}\n"
            f"Alerts: <b>{'ON' if alerts_enabled(UID) else 'OFF'}</b>")


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
    if kind == "order":
        return (f"<b>Confirm {action['side']} · {_mode_label()}</b>\n"
                f"{action['qty']} × <b>{action['option_symbol']}</b> ({action['exchange']}) @ market\n\n"
                f"Tap Confirm to place.")
    if kind == "auto":
        return (f"<b>Confirm auto-trade {action['to']}</b>\n"
                f"This {'ENABLES' if action['to'] == 'ON' else 'DISABLES'} automatic "
                f"order execution on {_mode_label()} signals.")
    return "Confirm?"


async def _run_action(action: dict) -> str:
    from app.services.kite_engine import service, state
    kind = action["kind"]
    if kind == "order":
        if action.get("origin") == "signal" and not _signal_action_current(action):
            return "🚫 Signal expired. Refresh signals."
        res = await service.place_manual_order(
            UID, action["option_symbol"], action["side"], action["qty"], action["exchange"])
        st = res.get("status")
        if st == "ok":
            return f"✅ {action['side']} {action['option_symbol']} placed (#{res.get('order_id','')})."
        if st == "duplicate":
            return "ℹ️ Already submitted (duplicate)."
        if st == "blocked":
            return f"🚫 Blocked: {res.get('reason')}"
        return f"⚠️ Failed: {res.get('message')}"
    if kind == "auto":
        cfg = state.get_config(UID)
        cfg.auto_execute = (action["to"] == "ON")
        state.set_config(UID, cfg)
        return f"⚙️ Auto-trade <b>{action['to']}</b>."
    return "Done."


# ── command + callback handlers (called by telegram_bot dispatcher) ───────────
async def handle_kite_command(chat_id: str) -> None:
    await _send(build_kite_status_text(), chat_id, kite_menu_kb())


async def handle_kite_callback(chat_id: str, message_id: int, cb_id: str, data: str) -> None:
    parts = data.split("|")
    kind = parts[0]
    arg = parts[1] if len(parts) > 1 else ""
    await _answer_cb(cb_id)

    if kind == "kmenu":
        await _edit(chat_id, message_id, build_kite_status_text(), kite_menu_kb())
    elif kind == "ksig":
        await _edit(chat_id, message_id, build_kite_signals_text(), signals_kb())
    elif kind == "kpos":
        await _edit(chat_id, message_id, await build_kite_positions_text(), await positions_kb())
    elif kind == "kpnl":
        await _edit(chat_id, message_id, await build_kite_pnl_text(), _back_kb())
    elif kind == "kscan":
        await _edit(chat_id, message_id, "🔄 Scanning…", _back_kb())
        msg = await _run_scan()
        await _edit(chat_id, message_id, msg, kite_menu_kb())
    elif kind == "kalert":
        set_alerts_enabled(UID, not alerts_enabled(UID))
        await _edit(chat_id, message_id, build_kite_status_text(), kite_menu_kb())
    elif kind == "kauto":
        from app.services.kite_engine import state
        cur = getattr(state.get_config(UID), "auto_execute", False)
        action = {"kind": "auto", "to": "OFF" if cur else "ON"}
        ptok = _stage(action)
        await _edit(chat_id, message_id, _confirm_text(action), _confirm_kb(ptok))
    elif kind == "kbuy":
        a = _fresh_action("sig", arg)
        if not a or not _signal_action_current(a):
            await _edit(
                chat_id,
                message_id,
                "⚠️ Signal expired. Refresh signals.",
                _back_kb(),
            )
            return
        action = {
            "kind": "order",
            "origin": "signal",
            "side": "BUY",
            "option_symbol": a["option_symbol"],
            "exchange": a["exchange"],
            "qty": a["qty"],
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
            await _edit(chat_id, message_id, "⚠️ Expired — try again.", _back_kb())
            return
        result = await _run_action(action)
        await _edit(chat_id, message_id, result, kite_menu_kb())
    elif kind == "kno":
        _cancel_pending(arg)
        await _edit(chat_id, message_id, "Cancelled.", kite_menu_kb())


async def _run_scan() -> str:
    from app.services.exchanges.kite import accounts as kite_accounts
    from app.services.kite_engine import service
    acct = _active_account()
    if acct is None:
        return "⚠️ No active Kite account."
    try:
        client = await kite_accounts.acquire_client(acct)
        n = await service.scan_user(client, UID)
        return f"✅ Scan complete — {len(_ready_rows())} active signal(s)."
    except Exception as exc:  # noqa: BLE001
        return f"⚠️ Scan failed: {exc}"


# ── alert push ────────────────────────────────────────────────────────────────
def _alert_html(alert: StrategyAlert, uid: str = UID) -> str:
    """Signal card in the same layout as every lifecycle alert (see lifecycle_format)."""
    upper = alert.direction.upper()
    arrow = (
        "🟢▲"
        if upper in {"LONG", "BULLISH", "UP"}
        else "🔴▼"
        if upper in {"SHORT", "BEARISH", "DOWN"}
        else "🟡•"
    )
    kind = _lf.instrument_kind(alert.option_symbol) or alert.option_type
    instrument = " · ".join(
        part for part in (_lf.esc(kind), _lf.esc(alert.option_symbol)) if part
    )
    lines = [
        _lf.header(alert.strategy_label, "SIGNAL"),
        f"<b>{_lf.esc_clip(alert.title, 300)}</b> · {arrow} {_lf.esc(alert.direction)}",
    ]
    if instrument:
        lines.append(instrument)
    if alert.detail:
        lines.append(f"<i>{_lf.esc_clip(alert.detail, 1500)}</i>")
    lines.append(_lf.footer(_mode_label(uid), alert.timestamp_ms))
    return "\n".join(lines)


def _lifecycle_html(event, uid: str = UID) -> str:
    """Lifecycle card: canonical wording, IST time, HTML-safe, <= 4096 chars."""
    return _lf.render_lifecycle(
        strategy=getattr(event, "strategy", ""),
        event_type=getattr(event, "event_type", "UPDATE"),
        title=getattr(event, "title", ""),
        detail_text=getattr(event, "detail", ""),
        severity=getattr(event, "severity", "INFO"),
        created_at_ms=getattr(event, "created_at_ms", 0),
        mode=_mode_label(uid),
    )


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
    now_wall = int(time.time())
    bucket = now_wall // 60
    now_mono = time.monotonic()

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
        now_mono = time.monotonic()

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


async def _drain_lifecycle(uid: str, targets: list, use_legacy: bool) -> None:
    """Deliver due lifecycle events; durable, per-destination, never blocks trading.

    Each destination acknowledges separately.  A failed destination keeps the
    event queued with bounded backoff (one failed attempt per event per cycle,
    however many destinations failed).  All SQLite work runs in a worker thread
    so a busy database cannot stall the event loop that is managing positions.
    """
    from app.services.notifications import telegram_lifecycle

    try:
        events = await asyncio.to_thread(telegram_lifecycle.due, uid)
    except Exception as exc:  # noqa: BLE001
        log.warning("Lifecycle Telegram outbox unavailable for %s: %s", uid, exc)
        return
    if not events:
        return

    if use_legacy:
        destinations = [("legacy", None)]
    else:
        destinations = [(_target_delivery_id(t), t) for t in targets]
    destination_ids = [d for d, _ in destinations]

    from app.services.notifications import kite_telegram_store as kts

    for event in events:
        html = _lifecycle_html(event, uid)
        delivered = set(event.delivered_to)
        failures: list[str] = []
        deferred = False
        for destination_id, target in destinations:
            if destination_id in delivered:
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
                if target is None:
                    response = await _send(html, str(_tg.TELEGRAM_CHAT_ID))
                    ok = bool((response or {}).get("ok"))
                else:
                    ok = bool(await kts.send_via(target.bot_token, target.chat_id, html))
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
    try:
        targets = kts.enabled_targets(uid)
    except Exception as exc:  # noqa: BLE001
        log.warning("Kite Telegram targets unavailable for %s: %s", uid, exc)
        return

    use_legacy = uid == UID and not targets and legacy_ok
    if not targets and not use_legacy:
        _warn_undeliverable(uid)
        return

    # 1. Trade lifecycle first and unconditionally.
    await _drain_lifecycle(uid, targets, use_legacy)

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
                        r.strategy == "gamma_move"
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
                r.strategy == "gamma_move"
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
