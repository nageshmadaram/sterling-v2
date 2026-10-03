"""
Telegram control surface for the Kite (Zerodha) Sterling Kite Engine.

Kept separate from the low-level transport (telegram_bot.py): its own command
(`/kite`), its own `k*` callback namespace, and its own alert push — the two
streams never mix. Reuses the shared low-level Telegram transport
(`_send/_edit/_answer_cb/_btn`) so there is still one bot / one token.

Capabilities (acts as the local user ``default`` + its active Kite account):
  • view ready signals, positions, P&L, scan status
  • trigger a scan, toggle auto-trade
  • place option BUY/SELL and square-off positions — every state-changing action
    is gated by a TWO-TAP CONFIRM and runs through the shared, live-safety-gated
    ``service.place_manual_order`` path (identical to the REST/UI order path).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from app.core.auth import DEFAULT_USER_ID
from app.core.logging import get_logger

from app.services.notifications import telegram as _tg
from app.services.notifications.telegram_bot import _answer_cb, _btn, _edit, _send

log = get_logger(__name__)

UID = DEFAULT_USER_ID

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
_pending: Dict[str, dict] = {}          # ptok -> resolved action to run on confirm
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
                strategy_label="Kite signal",
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
        _adaptive_edge_alerts,
        _intraday_alerts,
        _opening_volume_alerts,
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
    return "PAPER" if acct.is_paper else "LIVE"


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
    """Active (running/ready) signal rows from the latest scan."""
    rows = getattr(_snapshot(uid), "rows", []) or []
    return [r for r in rows if getattr(r, "is_active", False)]


def _fmt(v: Optional[float], dp: int = 2) -> str:
    try:
        return f"{float(v):,.{dp}f}"
    except (TypeError, ValueError):
        return "—"


# ── text builders ─────────────────────────────────────────────────────────────
def build_kite_signals_text() -> str:
    rows = _ready_rows()
    if not rows:
        return "<b>🇮🇳 Kite signals</b>\nNo active signals in the latest scan."
    lines = [f"<b>🇮🇳 Kite signals · {len(rows)} active</b>"]
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
    """One action row per (top) signal: Buy / Sell its primary leg."""
    _sig_actions.clear()
    kb: List[list] = []
    for r in _ready_rows()[:8]:
        leg = r.legs[0] if r.legs else None
        if not leg or not leg.option_symbol:
            continue
        sid = _next_id()
        _sig_actions[sid] = {
            "option_symbol": leg.option_symbol, "exchange": r.exchange,
            "qty": int(leg.lot_size or 1), "underlying": r.underlying,
        }
        short = leg.option_symbol[-12:]
        kb.append([_btn(f"🟢 Buy {short}", f"kbuy|{sid}"),
                   _btn(f"🔴 Sell {short}", f"ksell|{sid}")])
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
    """Stash a resolved action; return its confirm token."""
    ptok = _next_id()
    _pending[ptok] = action
    return ptok


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
    elif kind in ("kbuy", "ksell"):
        a = _sig_actions.get(arg)
        if not a:
            await _edit(chat_id, message_id, "⚠️ Signal expired — refresh.", _back_kb())
            return
        action = {"kind": "order", "side": "BUY" if kind == "kbuy" else "SELL",
                  "option_symbol": a["option_symbol"], "exchange": a["exchange"], "qty": a["qty"]}
        ptok = _stage(action)
        await _edit(chat_id, message_id, _confirm_text(action), _confirm_kb(ptok))
    elif kind == "ksq":
        a = _pos_actions.get(arg)
        if not a:
            await _edit(chat_id, message_id, "⚠️ Position expired — refresh.", _back_kb())
            return
        action = {"kind": "order", "side": a["side"], "option_symbol": a["option_symbol"],
                  "exchange": a["exchange"], "qty": a["qty"]}
        ptok = _stage(action)
        await _edit(chat_id, message_id, _confirm_text(action), _confirm_kb(ptok))
    elif kind == "kok":
        action = _pending.pop(arg, None)
        if not action:
            await _edit(chat_id, message_id, "⚠️ Expired — try again.", _back_kb())
            return
        result = await _run_action(action)
        await _edit(chat_id, message_id, result, kite_menu_kb())
    elif kind == "kno":
        _pending.pop(arg, None)
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
    import html as _html

    arrow = (
        "🟢▲"
        if alert.direction.upper() in {"LONG", "BULLISH", "UP"}
        else "🔴▼"
        if alert.direction.upper() in {"SHORT", "BEARISH", "DOWN"}
        else "🟡•"
    )
    instrument = " · ".join(
        part
        for part in (
            _html.escape(alert.option_type),
            _html.escape(alert.option_symbol),
        )
        if part
    )
    detail = f"\n<i>{_html.escape(alert.detail)}</i>" if alert.detail else ""
    instrument_line = f"\n{instrument}" if instrument else ""
    return (
        f"<b>🇮🇳 {_html.escape(alert.strategy_label)} · "
        f"{_html.escape(alert.title)}</b>\n"
        f"{arrow} {_html.escape(alert.direction)}"
        f"{instrument_line}{detail}\n"
        f"<i>{_html.escape(_mode_label(uid))}</i>"
    )


def _alert_user_ids() -> list[str]:
    """Every active Kite tenant that can own a live scanner snapshot."""
    try:
        from app.services.exchanges.kite import accounts as kite_accounts

        kite_accounts.bootstrap()
        return sorted({a.user_id for a in kite_accounts.all_accounts() if a.is_active})
    except Exception as exc:  # noqa: BLE001
        log.warning("Kite Telegram user discovery failed: %s", exc)
        return []


async def _gamma_rate_slot(uid: str, destination: str) -> bool:
    """Reserve one Gamma alert send under the uploaded client limits."""
    import time
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


async def push_kite_alerts() -> None:
    """Push each active signal to each tenant's configured destination exactly once.

    Delivery is acknowledged per destination. A failed send remains eligible for
    retry on the next cycle. The legacy single bot is fallback-only for the
    historical default user; it is never used to leak another tenant's signals.
    """
    from app.services.notifications import kite_telegram_store as kts

    legacy_ok = bool(_tg.TELEGRAM_TOKEN and _tg.TELEGRAM_CHAT_ID)
    for uid in _alert_user_ids():
        if not alerts_enabled(uid):
            continue
        _load_alerted(uid)
        rows = _strategy_alerts(uid)

        # Never infer "signal ended" from an empty/current snapshot: a transient
        # scanner outage is indistinguishable here. Retain recent delivery acks
        # by age so recovery does not resend already acknowledged alerts.
        if _prune_alerted(uid):
            _save_alerted(uid)

        try:
            targets = kts.enabled_targets(uid)
        except Exception as exc:  # noqa: BLE001
            log.warning("Kite Telegram targets unavailable for %s: %s", uid, exc)
            continue

        use_legacy = uid == UID and not targets and legacy_ok
        if not targets and not use_legacy:
            continue

        for r in rows:
            signal_key = r.dedupe_key
            html = _alert_html(r, uid)

            if targets:
                for t in targets:
                    delivery_key = (uid, _target_delivery_id(t), signal_key)
                    if delivery_key in _alerted:
                        continue
                    try:
                        destination_id = _target_delivery_id(t)
                        if (
                            r.strategy == "gamma_move"
                            and not await _gamma_rate_slot(uid, destination_id)
                        ):
                            continue
                        token = t.bot_token
                        ok = await kts.send_via(token, t.chat_id, html)
                    except Exception as exc:  # noqa: BLE001
                        log.warning(
                            "Kite Telegram delivery failed for %s/%s: %s",
                            uid, getattr(t, "id", "?"), exc,
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
                response = await _send(
                    html,
                    str(_tg.TELEGRAM_CHAT_ID),
                    kite_menu_kb() if r.strategy == "kite_supertrend" else None,
                )
                if bool((response or {}).get("ok")):
                    _alerted.add(delivery_key)
                    _save_alerted(uid)
