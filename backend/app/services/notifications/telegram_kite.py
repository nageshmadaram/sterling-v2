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

from typing import Dict, List, Optional

from app.core.auth import DEFAULT_USER_ID
from app.core.logging import get_logger

from app.services.notifications import telegram as _tg
from app.services.notifications.telegram_bot import _answer_cb, _btn, _edit, _send

log = get_logger(__name__)

UID = DEFAULT_USER_ID

# Successful per-destination deliveries.
# Key: (user_id, destination_id, token|timestamp). Failed sends are NOT recorded,
# so the next background cycle retries them.
_alerted: set[tuple[str, str, str]] = set()


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


def _next_id() -> str:
    global _counter
    _counter += 1
    return str(_counter)


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
def _alert_html(r, uid: str = UID) -> str:
    import html as _html

    arrow = "🟢▲ LONG" if r.direction == "long" else "🔴▼ SHORT"
    leg = r.legs[0] if r.legs else None
    legtxt = (
        f"\n{_html.escape(str(leg.moneyness))} · "
        f"{_html.escape(str(leg.option_symbol))}"
        if leg else ""
    )
    return (
        f"<b>🇮🇳 Kite signal · {_html.escape(str(r.underlying))}</b>\n"
        f"{arrow} · {_html.escape(str(r.option_type))}{legtxt}\n"
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
        rows = _ready_rows(uid)
        live_keys = {f"{r.token}|{r.timestamp_ms}" for r in rows}

        # Forget deliveries only after that signal is no longer live for this user.
        stale = {key for key in _alerted if key[0] == uid and key[2] not in live_keys}
        _alerted.difference_update(stale)

        try:
            targets = kts.enabled_targets(uid)
        except Exception as exc:  # noqa: BLE001
            log.warning("Kite Telegram targets unavailable for %s: %s", uid, exc)
            continue

        use_legacy = uid == UID and not targets and legacy_ok
        if not targets and not use_legacy:
            continue

        for r in rows:
            signal_key = f"{r.token}|{r.timestamp_ms}"
            html = _alert_html(r, uid)

            if targets:
                for t in targets:
                    delivery_key = (uid, str(t.id), signal_key)
                    if delivery_key in _alerted:
                        continue
                    try:
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
            elif use_legacy:
                delivery_key = (uid, "legacy", signal_key)
                if delivery_key in _alerted:
                    continue
                response = await _send(
                    html, str(_tg.TELEGRAM_CHAT_ID), kite_menu_kb()
                )
                if bool((response or {}).get("ok")):
                    _alerted.add(delivery_key)
