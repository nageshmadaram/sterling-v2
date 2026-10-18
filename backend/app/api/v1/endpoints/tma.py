"""Sterling Telegram Mini App (TMA) Endpoints & Web Application.

Provides the full-featured, responsive Telegram Mini App frontend and consolidated
JSON data endpoints for end-to-end trading, strategy control, PCR analytics,
positions management, live quotes, and account management directly inside Telegram.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.core.auth import DEFAULT_USER_ID, UserContext, get_current_user

from app.services.notifications.telegram_webapp_auth import tma_user

log = logging.getLogger(__name__)
router = APIRouter(tags=["telegram_mini_app"])
from .telegram_operator import router as operator_router
router.include_router(operator_router)

UID = DEFAULT_USER_ID
IST = ZoneInfo("Asia/Kolkata")


def _entry_switch_status() -> Dict[str, Any]:
    """Report global switches, separately from strategy AUTO and broker login."""
    from app.services.live_runtime import live_execution_state
    from app.services.safe_mode import SafeModeService
    from app.services.safety_supervisor import safe_mode_path

    try:
        execution = live_execution_state()
        safe = SafeModeService(safe_mode_path()).read()
        enabled = bool(execution["enabled"]) and not safe.active
        return {
            "enabled": enabled,
            "label": "Entry switches ON" if enabled else "New entries OFF",
            "reason": ("SAFE_MODE: " + safe.reason if safe.active else
                       "Account and strategy gates still apply." if enabled else
                       "Live-entry switch is off. Exits and protection remain available."),
        }
    except Exception:
        return {"enabled": False, "label": "Entry status UNKNOWN",
                "reason": "Entry controls could not be read. Do not enable trading."}


@router.get("/tma/data")
@router.get("/api/v1/tma/data")
async def get_tma_data(user: UserContext = Depends(tma_user)) -> Dict[str, Any]:
    """Consolidated real-time payload for Telegram Mini App frontend."""
    uid = user.user_id if user and user.user_id else UID
    now_ts = int(time.time() * 1000)

    # 1. Account & Status
    from app.services.exchanges.kite import accounts as kite_accounts
    from app.services.kite_engine import state as kite_state

    acct = kite_accounts.get_active(uid)
    from app.services.exchanges.kite import auth as kite_auth
    health = await kite_auth.ensure_session(uid, acct) if acct else None
    acct_info = {
        "connected": bool(health and health.connected),
        "connection_label": "No account" if not acct else "Kite temporarily unreachable" if health and health.transient else "Kite connected" if health and health.connected else "Kite offline — reconnect",
        "user_name": getattr(acct, "user_name", "") if acct else "",
        "user_id": getattr(acct, "user_id", "") if acct else "",
        "account_id": getattr(acct, "id", "") if acct else "",
        "is_paper": getattr(acct, "is_paper", False) if acct else False,
        "label": getattr(acct, "label", "Live Account") if acct else "No Account",
    }

    cfg = kite_state.get_config(uid)
    algo_enabled = bool(getattr(cfg, "auto_execute", False))

    # 2. Signals
    from app.services.notifications import telegram_kite as tk
    tk._load_alerted(uid)
    ready_signals = list(tk._ready_rows(uid))
    raw_signals = ready_signals + list(tk._active_rows(uid))

    signals_list = []
    for r in raw_signals:
        leg = r.legs[0] if getattr(r, "legs", None) else None
        opt_sym = getattr(leg, "option_symbol", "") if leg else getattr(r, "symbol", "")
        opt_type = getattr(r, "option_type", "") or getattr(r, "opt_type", "")
        signals_list.append({
            "signal_id": str(getattr(r, "token", "") or getattr(r, "signal_id", "")),
            "strategy": str(getattr(r, "strategy", "supertrend")),
            "symbol": str(getattr(r, "underlying", "") or getattr(r, "symbol", "")),
            "direction": str(getattr(r, "direction", "")).upper(),
            "option_symbol": opt_sym,
            "option_type": opt_type,
            "timestamp_ms": int(getattr(r, "timestamp_ms", 0) or 0),
            "state": str(getattr(r, "state", "ARMED")),
            "execution_eligible": bool(getattr(r, "execution_eligible", False)),
            "orderable": bool(r in ready_signals and leg and getattr(r, "execution_eligible", False)),
            "quantity": int(getattr(leg, "lot_size", 0) or 0),
            "exchange": str(getattr(r, "exchange", "")),
        })

    # 3. Positions & Orders & Margins
    positions_list = []
    orders_list = []
    from app.services.exchanges.kite.margin_summary import margin_segments
    margins_info = {"available": None, "used": None, "total": None, **margin_segments({})}
    funds_available = False
    pnl_summary = {"realized": 0.0, "unrealized": 0.0, "total": 0.0}

    broker_data_available = False
    if acct is not None and acct_info["connected"]:
        try:
            client = await kite_accounts.acquire_client(acct)

            # Positions
            raw_pos = await client.get_positions_raw()
            for p in (raw_pos or {}).get("net", []):
                qty = int(p.get("quantity", 0) or 0)
                realized = float(p.get("realised", 0) or 0)
                pnl_summary["realized"] += realized
                if qty:
                    unrealized = float(p.get("pnl", 0) or 0) - realized
                    pnl_summary["unrealized"] += unrealized
                    positions_list.append({"symbol": str(p.get("tradingsymbol", "")),
                        "exchange": str(p.get("exchange", "")), "product": str(p.get("product", "")),
                        "quantity": qty, "buy_price": float(p.get("average_price", 0) or 0),
                        "ltp": float(p.get("last_price", 0) or 0), "pnl": unrealized})

            # Orders
            raw_orders = await client.get_orders()
            for o in (raw_orders or []):
                orders_list.append({
                    "order_id": str(o.get("order_id", "")),
                    "symbol": str(o.get("tradingsymbol", "")),
                    "side": str(o.get("transaction_type", "")).upper(),
                    "quantity": int(o.get("quantity", 0) or 0),
                    "status": str(o.get("status", "")).upper(),
                    "price": float(o.get("average_price", 0.0) or o.get("price", 0.0) or 0.0),
                    "time": str(o.get("order_timestamp", "") or "")[11:19],
                })

            broker_data_available = True
        except Exception as exc:  # noqa: BLE001
            log.debug("TMA broker data fetch notice: %s", exc)

    # Funds remain readable even when a separate inventory endpoint fails.
    if acct is not None and acct_info["connected"]:
        try:
            client = await kite_accounts.acquire_client(acct)
            segments = margin_segments(await client.get_margins())
            margins_info = {**segments, "available": segments["equity"]["available"],
                "used": segments["equity"]["used"], "total": segments["equity"]["available"]}
            funds_available = any(value is not None for seg in segments.values() for value in seg.values())
        except Exception as exc:
            log.debug("TMA funds fetch notice: %s", exc)

    pnl_summary["total"] = pnl_summary["realized"] + pnl_summary["unrealized"]

    # 4. PCR Session
    from app.api.v1.endpoints.pcr import pcr_session
    pcr_data = {}
    try:
        pcr_data = await pcr_session(None)
    except Exception as exc:  # noqa: BLE001
        log.debug("TMA PCR fetch notice: %s", exc)

    # 5. Enabled Strategies
    from app.services import archived_strategies
    enabled_strats = {}
    for s_key, (s_name, s_desc) in archived_strategies.CATALOG.items():
        enabled_strats[s_key] = {
            "name": s_name,
            "description": s_desc,
            "enabled": archived_strategies.enabled(uid, s_key),
        }

    return {
        "timestamp_ms": now_ts,
        "account": acct_info,
        "algo_enabled": algo_enabled,
        "entry_controls": await asyncio.to_thread(_entry_switch_status),
        "alerts_enabled": tk.alerts_enabled(uid),
        "signals": signals_list,
        "positions": positions_list,
        "orders": orders_list[:20],  # Recent 20
        "pnl": pnl_summary,
        "funds": margins_info,
        "funds_available": funds_available,
        "broker_data_available": broker_data_available,
        "pcr": pcr_data,
        "strategies": enabled_strats,
    }


@router.get("/tma/search")
@router.get("/api/v1/tma/search")
async def tma_quote_search(
    q: str = Query("", description="Symbol query"),
    user: UserContext = Depends(tma_user),
) -> Dict[str, Any]:
    """Execute live symbol quote search for TMA frontend."""
    uid = user.user_id if user and user.user_id else UID
    from app.services.notifications import telegram_kite as tk
    formatted_text = await tk.build_kite_search_text(q, uid=uid)
    return {"query": q, "html": formatted_text}


def _kite_login_account_status(uid: str, account_id: Any) -> Dict[str, Any]:
    """Read durable login evidence without renewing or exposing broker tokens."""
    from app.services.exchanges.kite import accounts

    if not isinstance(account_id, str) or not account_id:
        raise HTTPException(422, "Open a fresh Kite login link before checking its status.")
    account = accounts.get(uid, account_id)
    if account is None:
        raise HTTPException(404, "Kite account not found. Open a fresh login link.")
    return {
        "account_id": account.id,
        "label": account.label,
        "connected": bool(not account.is_sim and account.connected and account.token_is_live),
        "user_name": account.user_name,
        "kite_user_id": account.kite_user_id,
        "last_login_at_ms": account.last_login_at_ms,
        "login_confirmation_id": accounts.get_login_confirmation_id(uid, account.id),
        "token_expires_at_ms": account.token_expires_at,
    }


@router.post("/tma/action")
@router.post("/api/v1/tma/action")
async def tma_action(
    payload: Dict[str, Any],
    request: Request,
    user: UserContext = Depends(tma_user),
) -> Dict[str, Any]:
    """Execute trading or ALGO action from TMA frontend."""
    uid = user.user_id if user and user.user_id else UID
    action_type = payload.get("action")
    if action_type == "kite_login":
        from app.api.v1.endpoints.kite import login_url
        result = await login_url(user, request=request)
        status = _kite_login_account_status(uid, result.account_id)
        return {"success": True, "login_url": result.login_url,
                "redirect_uri": result.redirect_uri, "confirmation_id": result.confirmation_id,
                **status}

    if action_type == "kite_login_status":
        return {"success": True, **_kite_login_account_status(uid, payload.get("account_id"))}

    if action_type == "kite_login_complete":
        from app.api.v1.endpoints.kite import create_session
        from app.services.exchanges.kite.models import GenerateSessionRequest
        callback_url = payload.get("callback_url")
        account_id = payload.get("account_id")
        if (not isinstance(callback_url, str)
                or not callback_url.strip().lower().startswith(("http://", "https://"))):
            raise HTTPException(422, "Paste the complete redirected browser address, including request_token and state.")
        if account_id is not None and not isinstance(account_id, str):
            raise HTTPException(422, "Choose a Kite account and open a fresh login link.")
        result = await create_session(
            GenerateSessionRequest(request_token=callback_url, account_id=account_id), user,
        )
        status = _kite_login_account_status(uid, result.account_id)
        return {"success": result.connected and status["connected"], **status,
                "message": "Kite login successful. Connected to Kite. You can return to the desk."}

    from app.services.kite_engine import service, state
    from app.services.notifications import telegram_kite as tk

    if action_type == "toggle_algo":
        target = payload.get("target", "TOGGLE")
        current = bool(state.get_config(uid).auto_execute)
        new_val = not current if target == "TOGGLE" else (target == "ON")
        if new_val:
            blockers = service.autoexec_enable_blockers(uid, was_on=current, now_on=True)
            if blockers:
                return {"success": False, "message": f"ALGO blocked: {'; '.join(blockers[:2])}"}
        cfg = state.get_config(uid)
        cfg.auto_execute = new_val
        state.set_config(uid, cfg)
        return {"success": True, "algo_enabled": new_val, "message": f"ALGO {'ON' if new_val else 'OFF'}"}

    if action_type == "toggle_alerts":
        current = tk.alerts_enabled(uid)
        new_val = not current
        tk.set_alerts_enabled(uid, new_val)
        return {"success": True, "alerts_enabled": new_val, "message": f"Alerts {'ON' if new_val else 'OFF'}"}

    if action_type == "toggle_strategy":
        strat_key = payload.get("strategy")
        from app.services import archived_strategies
        if strat_key in archived_strategies.CATALOG:
            curr = archived_strategies.enabled(uid, strat_key)
            archived_strategies.set_enabled(uid, strat_key, not curr)
            return {"success": True, "strategy": strat_key, "enabled": not curr}

    if action_type in {"square_off", "place_order"}:
        sym = payload.get("symbol")
        qty = payload.get("quantity")
        side = payload.get("side", "SELL")
        exch = payload.get("exchange", "NFO")
        if action_type == "place_order" and payload.get("signal") is True:
            row = next((r for r in tk._ready_rows(uid) if any(getattr(leg, "option_symbol", "") == sym for leg in r.legs)), None)
            if row is None: raise HTTPException(409, "Signal expired. Refresh the signal board.")
            leg = next(leg for leg in row.legs if leg.option_symbol == sym)
            qty, exch, side = int(leg.lot_size), row.exchange, "BUY"
        if not isinstance(sym, str) or not sym.strip() or type(qty) is not int or not 0 < qty <= 100000 or side not in {"BUY", "SELL"}:
            raise HTTPException(422, "Select a symbol, BUY/SELL side and positive integer quantity.")
        if action_type == "square_off":
            from app.services.exchanges.kite import accounts
            acct = accounts.get_active(uid)
            if not acct: raise HTTPException(409, "No active Kite account")
            client = await accounts.acquire_client(acct)
            positions = (await client.get_positions_raw()).get("net", [])
            match = next((p for p in positions if p.get("tradingsymbol", "") == sym and p.get("exchange", "") == exch), {})
            holding = int(match.get("quantity", 0) or 0)
            if holding == 0 or qty != abs(holding) or side != ("SELL" if holding > 0 else "BUY"):
                raise HTTPException(409, "Position changed. Refresh positions before confirming its exit.")
        res = await service.place_manual_order(uid, sym, side, qty, exch)
        return {"success": res.get("status") in {"ok", "duplicate"}, "message": res.get("reason") or res.get("message") or res.get("status"), "result": res}

    if action_type == "panic_square_off":
        from app.services.exchanges.kite import accounts
        acct = accounts.get_active(uid)
        if not acct: raise HTTPException(409, "No active Kite account")
        client = await accounts.acquire_client(acct)
        result = await service.emergency_halt(client, uid, reason="Mini App operator emergency halt")
        sq = result.get("square_off") or {}
        failed = int(sq.get("failed", 0))
        pending = int(sq.get("submitted_awaiting_fill", 0))
        return {"success": failed == 0, "status": "pending" if pending else "completed" if not failed else "failed",
                "message": f"Halt requested. Confirmed exits: {sq.get('squared_off', 0)}; awaiting fills: {pending}; failures: {failed}", "result": result}

    raise HTTPException(422, "Unknown Mini App action")


@router.get("/tma", response_class=HTMLResponse)
@router.get("/api/v1/tma", response_class=HTMLResponse)
async def tma_html_app(request: Request) -> HTMLResponse:
    """Serve the complete, responsive Sterling Telegram Mini App (TMA)."""
    html_content = _generate_tma_html(getattr(request.state, "csp_nonce", ""))
    return HTMLResponse(content=html_content, status_code=200)


def _generate_tma_html(nonce: str = "") -> str:
    """Generate state-of-the-art, rich, responsive single-page web app for Telegram Mini App."""
    content = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
  <title>Sterling — Telegram Mini App</title>
  <!-- Telegram Web App SDK -->
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <!-- Fonts -->
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&family=Outfit:wght@600;700;800;900&display=swap" rel="stylesheet">
  
  <style>
    :root {
      --bg-dark: #070a12;
      --bg-card: rgba(16, 23, 38, 0.8);
      --bg-card-hover: rgba(24, 34, 56, 0.9);
      --border-card: rgba(255, 255, 255, 0.08);
      --border-glow: rgba(99, 102, 241, 0.25);
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --primary: #6366f1;
      --primary-glow: rgba(99, 102, 241, 0.4);
      --emerald: #10b981;
      --emerald-glow: rgba(16, 185, 129, 0.35);
      --rose: #f43f5e;
      --rose-glow: rgba(244, 63, 94, 0.35);
      --amber: #f59e0b;
      --cyan: #06b6d4;
      --purple: #a855f7;
      --radius-xl: 20px;
      --radius-lg: 16px;
      --radius-md: 12px;
      --font-heading: 'Outfit', sans-serif;
      --font-body: 'Inter', sans-serif;
      --font-mono: 'JetBrains Mono', monospace;
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      -webkit-tap-highlight-color: transparent;
      user-select: none;
    }

    body {
      background-color: var(--bg-dark);
      background-image: 
        radial-gradient(circle at 15% 15%, rgba(99, 102, 241, 0.15) 0%, transparent 45%),
        radial-gradient(circle at 85% 75%, rgba(16, 185, 129, 0.12) 0%, transparent 45%),
        radial-gradient(circle at 50% 50%, rgba(168, 85, 247, 0.08) 0%, transparent 60%);
      color: var(--text-main);
      font-family: var(--font-body);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      overflow-x: hidden;
      padding-bottom: 84px;
    }

    /* Header */
    header {
      position: sticky;
      top: 0;
      z-index: 100;
      background: rgba(9, 13, 23, 0.88);
      backdrop-filter: blur(24px);
      -webkit-backdrop-filter: blur(24px);
      border-bottom: 1px solid var(--border-card);
      padding: 12px 16px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .brand-logo {
      width: 36px;
      height: 36px;
      background: linear-gradient(135deg, var(--primary), var(--purple));
      border-radius: 12px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 20px;
      box-shadow: 0 0 20px var(--primary-glow);
    }

    .brand-title {
      font-family: var(--font-heading);
      font-weight: 900;
      font-size: 21px;
      letter-spacing: -0.6px;
      background: linear-gradient(135deg, #ffffff, #cbd5e1);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }

    .status-pills {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .pill {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 5px 12px;
      border-radius: 20px;
      font-size: 11px;
      font-weight: 700;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border-card);
    }

    .pill-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
    }

    .pill-live { background: rgba(16, 185, 129, 0.15); color: var(--emerald); border-color: rgba(16, 185, 129, 0.35); }
    .pill-live .pill-dot { background: var(--emerald); box-shadow: 0 0 10px var(--emerald); animation: pulse 1.8s infinite; }
    .pill-algo-off { background: rgba(148, 163, 184, 0.15); color: var(--text-muted); }
    .pill-algo-off .pill-dot { background: var(--text-muted); }
    .pill-algo-on { background: rgba(99, 102, 241, 0.18); color: var(--primary); border-color: rgba(99, 102, 241, 0.4); }
    .pill-algo-on .pill-dot { background: var(--primary); box-shadow: 0 0 10px var(--primary); animation: pulse 1.8s infinite; }

    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.4; transform: scale(0.85); }
    }

    /* Main Container */
    main {
      padding: 16px;
      flex: 1;
      max-width: 640px;
      margin: 0 auto;
      width: 100%;
    }

    /* Top Summary Banner */
    .summary-banner {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 12px;
      margin-bottom: 16px;
    }

    .summary-card {
      background: var(--bg-card);
      border: 1px solid var(--border-card);
      border-radius: var(--radius-lg);
      padding: 14px 16px;
      backdrop-filter: blur(16px);
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
      position: relative;
      overflow: hidden;
    }

    .summary-card::before {
      content: '';
      position: absolute;
      top: 0; left: 0; right: 0;
      height: 2px;
      background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.15), transparent);
    }

    .summary-label {
      font-size: 12px;
      color: var(--text-muted);
      margin-bottom: 6px;
      display: flex;
      align-items: center;
      gap: 6px;
      font-weight: 500;
    }

    .summary-value {
      font-family: var(--font-mono);
      font-size: 19px;
      font-weight: 800;
      letter-spacing: -0.5px;
    }

    .val-green { color: var(--emerald); text-shadow: 0 0 12px var(--emerald-glow); }
    .val-red { color: var(--rose); text-shadow: 0 0 12px var(--rose-glow); }

    /* Quick Action Row */
    .quick-actions-bar {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 8px;
      margin-bottom: 16px;
    }

    .quick-btn {
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid var(--border-card);
      border-radius: var(--radius-md);
      padding: 10px 6px;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 5px;
      font-size: 11px;
      font-weight: 600;
      color: var(--text-main);
      cursor: pointer;
      transition: all 0.2s ease;
    }
    .quick-btn:active { transform: scale(0.95); background: rgba(255, 255, 255, 0.08); }
    .quick-btn-icon { font-size: 18px; }

    /* Tabs Content */
    .tab-content { display: none; }
    .tab-content.active {
      display: block;
      animation: tabSlide 0.25s cubic-bezier(0.16, 1, 0.3, 1);
    }

    @keyframes tabSlide {
      from { opacity: 0; transform: translateY(8px); }
      to { opacity: 1; transform: translateY(0); }
    }

    /* Section Card */
    .section-card {
      background: var(--bg-card);
      border: 1px solid var(--border-card);
      border-radius: var(--radius-xl);
      padding: 18px;
      margin-bottom: 16px;
      backdrop-filter: blur(20px);
      box-shadow: 0 12px 36px rgba(0, 0, 0, 0.35);
    }

    .section-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 16px;
    }

    .section-title {
      font-family: var(--font-heading);
      font-size: 17px;
      font-weight: 800;
      display: flex;
      align-items: center;
      gap: 8px;
    }

    /* Buttons */
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      padding: 11px 18px;
      border-radius: var(--radius-md);
      font-weight: 700;
      font-size: 13px;
      cursor: pointer;
      border: none;
      transition: all 0.2s ease;
      font-family: var(--font-body);
    }

    .btn-primary {
      background: linear-gradient(135deg, var(--primary), #4f46e5);
      color: #ffffff;
      box-shadow: 0 4px 16px var(--primary-glow);
    }
    .btn-primary:active { transform: scale(0.96); }

    .btn-emerald {
      background: linear-gradient(135deg, var(--emerald), #059669);
      color: #ffffff;
      box-shadow: 0 4px 16px var(--emerald-glow);
    }
    .btn-emerald:active { transform: scale(0.96); }

    .btn-danger {
      background: linear-gradient(135deg, var(--rose), #e11d48);
      color: #ffffff;
      box-shadow: 0 4px 16px var(--rose-glow);
    }
    .btn-danger:active { transform: scale(0.96); }

    .btn-outline {
      background: rgba(255, 255, 255, 0.05);
      color: var(--text-main);
      border: 1px solid var(--border-card);
    }
    .btn-outline:active { background: rgba(255, 255, 255, 0.1); transform: scale(0.96); }

    .btn-sm {
      padding: 6px 12px;
      font-size: 12px;
      border-radius: 10px;
    }

    .btn-full { width: 100%; }

    /* Signal / Position Items */
    .item-card {
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid var(--border-card);
      border-radius: var(--radius-lg);
      padding: 14px;
      margin-bottom: 12px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      transition: border-color 0.2s;
    }
    .item-card:hover { border-color: rgba(255, 255, 255, 0.15); }

    .item-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .item-sym {
      font-family: var(--font-mono);
      font-weight: 700;
      font-size: 15px;
    }

    .badge {
      padding: 3px 10px;
      border-radius: 8px;
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.4px;
    }
    .badge-bull { background: rgba(16, 185, 129, 0.2); color: var(--emerald); border: 1px solid rgba(16, 185, 129, 0.4); }
    .badge-bear { background: rgba(244, 63, 94, 0.2); color: var(--rose); border: 1px solid rgba(244, 63, 94, 0.4); }
    .badge-neutral { background: rgba(148, 163, 184, 0.2); color: var(--text-muted); border: 1px solid rgba(148, 163, 184, 0.4); }

    /* Tables */
    .data-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    .data-table th {
      text-align: left;
      padding: 10px;
      color: var(--text-muted);
      border-bottom: 1px solid var(--border-card);
      font-weight: 600;
    }
    .data-table td {
      padding: 12px 10px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.03);
      font-family: var(--font-mono);
    }

    /* Chips */
    .chip-group {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 14px;
    }
    .chip {
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border-card);
      padding: 7px 14px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s;
    }
    .chip.active, .chip:active {
      background: var(--primary);
      color: #ffffff;
      border-color: var(--primary);
      box-shadow: 0 2px 10px var(--primary-glow);
    }

    /* Bottom Navigation Bar */
    nav.bottom-nav {
      position: fixed;
      bottom: 0;
      left: 0;
      right: 0;
      z-index: 200;
      background: rgba(9, 13, 23, 0.94);
      backdrop-filter: blur(28px);
      -webkit-backdrop-filter: blur(28px);
      border-top: 1px solid var(--border-card);
      display: flex;
      justify-content: space-around;
      padding: 10px 6px 16px 6px;
    }

    .nav-item {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 4px;
      color: var(--text-muted);
      font-size: 11px;
      font-weight: 600;
      cursor: pointer;
      flex: 1;
      padding: 4px 0;
      transition: all 0.2s;
    }

    .nav-icon { font-size: 20px; }
    .nav-item.active { color: var(--primary); }
    .nav-item.active .nav-icon { transform: translateY(-3px) scale(1.1); }

    /* Search Box */
    .search-input-wrap {
      position: relative;
      margin-bottom: 14px;
    }

    .search-input {
      width: 100%;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border-card);
      border-radius: var(--radius-md);
      padding: 14px 16px 14px 44px;
      color: #ffffff;
      font-size: 14px;
      outline: none;
      font-family: var(--font-body);
    }
    .search-input:focus { border-color: var(--primary); box-shadow: 0 0 16px var(--primary-glow); }

    .search-icon-pos {
      position: absolute;
      left: 16px;
      top: 50%;
      transform: translateY(-50%);
      color: var(--text-muted);
      font-size: 18px;
    }

    /* Modal Overlay & Order Terminal */
    .modal-overlay {
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0, 0, 0, 0.82);
      backdrop-filter: blur(12px);
      z-index: 300;
      display: none;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }

    .modal-card {
      background: #0f172a;
      border: 1px solid var(--border-card);
      border-radius: var(--radius-xl);
      padding: 24px;
      width: 100%;
      max-width: 440px;
      box-shadow: 0 20px 60px rgba(0, 0, 0, 0.6);
    }

    .form-group {
      margin-bottom: 14px;
      text-align: left;
    }
    .form-label {
      font-size: 12px;
      color: var(--text-muted);
      margin-bottom: 6px;
      display: block;
      font-weight: 600;
    }
    .form-select, .form-input {
      width: 100%;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border-card);
      border-radius: var(--radius-md);
      padding: 12px;
      color: #fff;
      font-size: 14px;
      outline: none;
    }

    .option-type-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 10px;
    }
    .type-btn {
      padding: 12px;
      border-radius: var(--radius-md);
      border: 1px solid var(--border-card);
      background: rgba(255, 255, 255, 0.04);
      color: #fff;
      font-weight: 800;
      font-size: 14px;
      text-align: center;
      cursor: pointer;
    }
    .type-btn-ce.active { background: rgba(16, 185, 129, 0.25); border-color: var(--emerald); color: var(--emerald); }
    .type-btn-pe.active { background: rgba(244, 63, 94, 0.25); border-color: var(--rose); color: var(--rose); }

    /* Gauge Meter */
    .pcr-meter-bar {
      height: 8px;
      border-radius: 4px;
      background: linear-gradient(90deg, var(--rose) 0%, var(--amber) 50%, var(--emerald) 100%);
      position: relative;
      margin: 12px 0 6px 0;
    }
    .pcr-meter-pointer {
      width: 14px;
      height: 14px;
      background: #ffffff;
      border-radius: 50%;
      position: absolute;
      top: -3px;
      transform: translateX(-50%);
      box-shadow: 0 0 10px rgba(255, 255, 255, 0.8);
      transition: left 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
    }

    [data-click]:focus-visible {outline:2px solid var(--primary);outline-offset:3px;}
    /* Sparkline SVG */
    .sparkline-svg {
      width: 100%;
      height: 38px;
      stroke-width: 2.5;
      fill: none;
    }
  </style>
</head>
<body>

  <!-- Header -->
  <header>
    <div class="brand">
      <div class="brand-logo">⚡</div>
      <div class="brand-title">STERLING</div>
    </div>
    <div class="status-pills">
      <div class="pill pill-live" id="pill-broker">
        <div class="pill-dot"></div>
        <span id="txt-broker">Checking connection…</span>
      </div>
      <div class="pill pill-algo-off" id="pill-algo">
        <div class="pill-dot"></div>
        <span id="txt-algo">Checking SuperTrend ALGO…</span>
      </div>
      <div class="pill pill-algo-off" id="pill-entry" role="status">
        <span id="txt-entry">Entry status UNKNOWN</span>
      </div>
    </div>
  </header>
  <div style="padding:8px 18px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;"><a href="/" style="color:var(--text-main)">Full app</a><button class="btn btn-outline btn-sm" data-click="openKiteLogin()">Connect Kite</button><span id="sync-status" role="status" aria-live="polite">Connecting…</span></div>
  <section id="kite-login-panel" hidden aria-label="Kite broker login" style="margin:16px;padding:20px;background:var(--bg-card);border-radius:12px">
    <h2>Connect Kite from your phone</h2>
    <p>Finish Kite login in the browser. If it opens localhost and cannot connect, return here and paste the entire browser address below.</p>
    <div id="kite-login-link-controls" hidden>
      <label>Fresh Kite login URL<input id="kite-login-url" readonly style="display:block;width:100%;margin:8px 0;padding:12px" /></label>
      <button class="btn btn-outline btn-sm" data-click="copyKiteLoginUrl()">Copy login URL</button>
      <button class="btn btn-outline btn-sm" data-click="checkKiteLoginStatus()">Check login status</button>
      <p>For automatic login, register this Redirect URL in your Kite developer app:</p>
      <input id="kite-redirect-uri" readonly aria-label="Kite Redirect URL" style="display:block;width:100%;margin:8px 0;padding:12px" />
      <p>Kite uses the Redirect URL saved in its developer console. Login links expire after 15 minutes.</p>
    </div>
    <form id="kite-login-recovery-form">
      <label>Full redirected browser address<input id="kite-callback-url" type="url" required maxlength="8192" autocomplete="off" autocapitalize="none" spellcheck="false" placeholder="http://localhost:5173/api/v1/kite/callback?request_token=…&amp;state=…" style="display:block;width:100%;margin:8px 0;padding:12px" /></label>
      <button class="btn btn-emerald" type="submit">Finish Kite login</button>
    </form>
    <p id="kite-login-status" role="status" aria-live="polite"></p>
    <p id="kite-login-confirmation-details" hidden></p>
  </section>

  <!-- Main View -->
  <main>
    <!-- Summary Banner -->
    <div class="summary-banner">
      <div class="summary-card">
        <div class="summary-label"><span>💰</span> Net M2M P&L</div>
        <div class="summary-value val-green" id="summary-pnl">—</div>
      </div>
      <div class="summary-card">
        <div class="summary-label"><span>💵</span> Avail Margin</div>
        <div class="summary-value" id="summary-margin">—</div>
      </div>
    </div>

    <section id="sterling-login" hidden aria-label="Sterling sign in" style="margin:16px;padding:20px;background:var(--bg-card);border-radius:12px">
      <h2>Sign in to Sterling</h2>
      <p>Use your Sterling login, or open this desk from your linked Telegram bot.</p>
      <form id="sterling-login-form">
        <label>Username<input id="sterling-username" autocomplete="username" required style="display:block;width:100%;margin:8px 0;padding:12px" /></label>
        <label>Password<input id="sterling-password" type="password" autocomplete="current-password" required style="display:block;width:100%;margin:8px 0;padding:12px" /></label>
        <button class="btn btn-emerald" type="submit">Sign in</button>
        <p id="sterling-login-error" role="status"></p>
      </form>
      <a href="/" style="display:inline-block;margin-top:12px">Open full Sterling app ↗</a>
    </section>
    <!-- Quick Action Bar -->
    <div class="quick-actions-bar">
      <div class="quick-btn" data-click="openQuickOrderModal()">
        <span class="quick-btn-icon">⚡</span>
        <span>Quick Trade</span>
      </div>
      <div class="quick-btn" data-click="promptPanic()">
        <span class="quick-btn-icon">🚨</span>
        <span>Square Off</span>
      </div>
      <div class="quick-btn" data-click="quickSearch('NIFTY')">
        <span class="quick-btn-icon">📊</span>
        <span>Nifty Quote</span>
      </div>
      <div class="quick-btn" data-click="fetchData(); haptic('light');">
        <span class="quick-btn-icon">🔄</span>
        <span>Sync State</span>
      </div>
    </div>

    <!-- Tab 1: Signals & Overview -->
    <div class="tab-content active" id="tab-signals">
      <div class="section-card" style="padding:14px 18px;">
        <b>Market data</b><p id="market-data-note">Awaiting authenticated data. Use Search for a live broker quote.</p>
      </div>

      <div class="section-card">
        <div class="section-header">
          <div class="section-title"><span>📡</span> Live Signals Board</div>
          <button class="btn btn-outline btn-sm" data-click="fetchData()">🔄 Sync</button>
        </div>
        <div id="signals-list">
          <div style="color: var(--text-muted); font-size: 13px; text-align: center; padding: 24px;">Loading current signals…</div>
        </div>
      </div>
    </div>

    <!-- Tab 2: Positions & Orders -->
    <div class="tab-content" id="tab-positions">
      <div class="section-card">
        <div class="section-header">
          <div class="section-title"><span>📊</span> Open Positions</div>
          <button class="btn btn-danger btn-sm" data-click="promptPanic()">🚨 Square Off All</button>
        </div>
        <div id="positions-list">
          <div style="color: var(--text-muted); font-size: 13px; text-align: center; padding: 24px;">No open positions</div>
        </div>
      </div>

      <div class="section-card">
        <div class="section-header">
          <div class="section-title"><span>📜</span> Today's Orders</div>
        </div>
        <div id="orders-list">
          <div style="color: var(--text-muted); font-size: 13px; text-align: center; padding: 24px;">No broker orders available for today</div>
        </div>
      </div>
    </div>

    <!-- Tab 3: PCR Analytics -->
    <div class="tab-content" id="tab-pcr">
      <div class="section-card">
        <div class="section-header">
          <div class="section-title"><span>📈</span> Put-Call Ratio (PCR)</div>
          <span class="badge badge-bull" id="pcr-top-sentiment">BULLISH</span>
        </div>

        <!-- Meter Gauge -->
        <div style="margin-bottom: 16px;">
          <div style="display: flex; justify-content: space-between; font-size: 12px; color: var(--text-muted); font-weight: 600;">
            <span>Bearish (&lt;0.8)</span>
            <span>Neutral (1.0)</span>
            <span>Bullish (&gt;1.2)</span>
          </div>
          <div class="pcr-meter-bar">
            <div class="pcr-meter-pointer" id="pcr-pointer" style="left: 70%;"></div>
          </div>
        </div>

        <div id="pcr-overview-grid" style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 16px;">
          <!-- Dynamically populated -->
        </div>

        <div class="chip-group">
          <div class="chip active" id="chip-nifty" data-click="switchPcrSubTab('NIFTY')">📊 Nifty Table</div>
          <div class="chip" id="chip-sensex" data-click="switchPcrSubTab('SENSEX')">📊 Sensex Table</div>
          <div class="chip" id="chip-flow" data-click="switchPcrSubTab('FLOW')">🌊 Flow Tape</div>
        </div>

        <div id="pcr-detail-container">
          <!-- Dynamically populated table/flow -->
        </div>
      </div>
    </div>

    <!-- Tab 4: P&L & Funds -->
    <div class="tab-content" id="tab-funds">
      <div class="section-card">
        <div class="section-header">
          <div class="section-title"><span>💰</span> P&L & Funds Breakdown</div>
        </div>
        <table class="data-table">
          <tr><th>Realized P&L</th><td id="funds-realized" style="text-align: right;">—</td></tr>
          <tr><th>Unrealized P&L</th><td id="funds-unrealized" style="text-align: right;">—</td></tr>
        </table>
      </div>
      <div class="section-card">
        <div class="section-header"><div class="section-title">Equity</div></div>
        <table class="data-table">
          <tr><th>Margin available</th><td id="funds-avail" style="text-align:right">—</td></tr>
          <tr><th>Margins used</th><td id="funds-used" style="text-align:right">—</td></tr>
          <tr><th>Opening balance</th><td id="funds-equity-opening" style="text-align:right">—</td></tr>
        </table>
      </div>
      <div class="section-card">
        <div class="section-header"><div class="section-title">Commodity</div></div>
        <table class="data-table">
          <tr><th>Margin available</th><td id="funds-commodity-available" style="text-align:right">—</td></tr>
          <tr><th>Margins used</th><td id="funds-commodity-used" style="text-align:right">—</td></tr>
          <tr><th>Opening balance</th><td id="funds-commodity-opening" style="text-align:right">—</td></tr>
        </table>
      </div>
    </div>

    <!-- Tab 5: Search -->
    <div class="tab-content" id="tab-search">
      <div class="section-card">
        <div class="section-header">
          <div class="section-title"><span>🔍</span> Live Stock & Option Search</div>
        </div>
        <div class="search-input-wrap">
          <span class="search-icon-pos">🔍</span>
          <input type="text" class="search-input" id="search-query-input" placeholder="Search (e.g. NIFTY, RELIANCE, SENSEX 73000 CE)..." data-enter="executeSearch" />
        </div>
        <div class="chip-group">
          <div class="chip" data-click="quickSearch('NIFTY')">NIFTY</div>
          <div class="chip" data-click="quickSearch('SENSEX')">SENSEX</div>
          <div class="chip" data-click="quickSearch('RELIANCE')">RELIANCE</div>
          <div class="chip" data-click="quickSearch('HDFCBANK')">HDFCBANK</div>
          <div class="chip" data-click="quickSearch('TATAMOTORS')">TATAMOTORS</div>
        </div>
        <div id="search-results-area">
          <div style="color: var(--text-muted); font-size: 13px; text-align: center; padding: 24px;">Type a symbol or pick a quick chip above</div>
        </div>
      </div>
    </div>

    <!-- Tab 6: Controls -->
    <div class="tab-content" id="tab-controls">
      <div class="section-card">
        <div class="section-header">
          <div class="section-title"><span>⚙️</span> ALGO & Strategy Matrix</div>
        </div>
        
        <div style="background: rgba(255, 255, 255, 0.03); border: 1px solid var(--border-card); border-radius: var(--radius-lg); padding: 14px; margin-bottom: 16px;">
          <div style="display: flex; align-items: center; justify-content: space-between;">
            <div>
              <div style="font-weight: 800; font-size: 15px;">Master ALGO Execution</div>
              <div style="font-size: 12px; color: var(--text-muted);">Auto-execute signals on live Kite account</div>
            </div>
            <button class="btn btn-primary btn-sm" id="btn-toggle-algo" data-click="toggleAlgo()">Toggle ALGO</button>
          </div>
        </div>

        <div style="font-weight: 800; font-size: 15px; margin-bottom: 12px;">Archived strategy controls</div>
        <div id="strategies-toggle-list">
          <!-- Dynamically populated -->
        </div>
      </div>
    </div>
  </main>

  <!-- Bottom Nav -->
  <nav class="bottom-nav">
    <div class="nav-item active" data-click="switchTab('signals', this)">
      <span class="nav-icon">📡</span>
      <span>Signals</span>
    </div>
    <div class="nav-item" data-click="switchTab('positions', this)">
      <span class="nav-icon">📊</span>
      <span>Positions</span>
    </div>
    <div class="nav-item" data-click="switchTab('pcr', this)">
      <span class="nav-icon">📈</span>
      <span>PCR</span>
    </div>
    <div class="nav-item" data-click="switchTab('funds', this)">
      <span class="nav-icon">💰</span>
      <span>Funds</span>
    </div>
    <div class="nav-item" data-click="switchTab('search', this)">
      <span class="nav-icon">🔍</span>
      <span>Search</span>
    </div>
    <div class="nav-item" data-click="switchTab('controls', this)">
      <span class="nav-icon">⚙️</span>
      <span>Controls</span>
    </div>
  </nav>

  <!-- Manual order Modal -->
  <div class="modal-overlay" id="modal-order">
    <div class="modal-card">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
        <div style="font-family: var(--font-heading); font-size: 18px; font-weight: 800; display: flex; align-items: center; gap: 8px;">⚡ Manual order</div>
        <button class="btn btn-outline btn-sm" data-click="closeOrderModal()">✕</button>
      </div>

      <div class="form-group">
        <label class="form-label">Underlying Index</label>
        <select class="form-select" id="order-index" data-change="updateOptionSymbolPreview()">
          <option value="NIFTY">NIFTY 50</option>
          <option value="SENSEX">BSE SENSEX</option>
          <option value="BANKNIFTY">BANK NIFTY</option>
        </select>
      </div>

      <div class="form-group">
        <label class="form-label">Option Type</label>
        <div class="option-type-grid">
          <div class="type-btn type-btn-ce active" id="btn-ce" data-click="setOptionType('CE')">CALL (CE)</div>
          <div class="type-btn type-btn-pe" id="btn-pe" data-click="setOptionType('PE')">PUT (PE)</div>
        </div>
      </div>

      <div class="form-group">
        <label class="form-label">Broker contract symbol</label>
        <input type="text" class="form-input" id="order-symbol-preview" placeholder="Exact broker contract symbol" />
      </div>

      <div class="form-group">
        <label class="form-label">Quantity (units; must match broker lot size)</label>
        <input class="form-input" id="order-lots" type="number" min="1" step="1" placeholder="Quantity in units" />
      </div>

      <div class="form-group"><label class="form-label" for="order-exchange">Exchange</label><select id="order-exchange" class="form-select"><option value="NFO">NFO — NSE options</option><option value="BFO">BFO — BSE options</option></select></div>
      <button class="btn btn-emerald btn-full" style="margin-top: 10px;" data-click="dispatchQuickOrder()">🚀 Place Order</button>
    </div>
  </div>

  <!-- General Confirmation Modal -->
  <div class="modal-overlay" id="modal-confirm">
    <div class="modal-card" style="text-align: center;">
      <div style="font-size: 36px; margin-bottom: 10px;" id="modal-icon">🚨</div>
      <div style="font-family: var(--font-heading); font-size: 19px; font-weight: 800; margin-bottom: 8px;" id="modal-title">Confirm Action</div>
      <div style="font-size: 13px; color: var(--text-muted); margin-bottom: 22px;" id="modal-body">Are you sure you want to execute this action?</div>
      <div style="display: flex; gap: 12px; justify-content: center;">
        <button class="btn btn-outline" style="flex:1;" data-click="closeModal()">Cancel</button>
        <button class="btn btn-danger" style="flex:1;" id="modal-action-btn">Confirm</button>
      </div>
    </div>
  </div>

  <script>
    function htmlEscape(value) {
      return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    }
    function controlSymbol(value) {
      const symbol = String(value ?? '');
      return /^[A-Za-z0-9&_. -]+$/.test(symbol) ? htmlEscape(symbol) : '';
    }
    // Explicit event dispatch keeps inline handlers and eval out of the page.
    const actions = {closeModal, closeOrderModal, dispatchQuickOrder, executeSignal,
      fetchData, haptic, openQuickOrderModal, promptPanic, quickSearch, setOptionType,
      squareOffSingle, switchPcrSubTab, switchTab, toggleAlgo, toggleStrategy,
      updateOptionSymbolPreview, executeSearch, openKiteLogin, copyKiteLoginUrl, checkKiteLoginStatus};
    function dispatchAction(expression, element) {
      for (const call of expression.split(';')) {
        const match = call.trim().match(/^([a-zA-Z]+)\((.*)\)$/);
        if (!match || !actions[match[1]]) continue;
        const args = match[2].trim() ? match[2].split(',').map(a => {
          a = a.trim();
          if (a === 'this') return element;
          if (/^'[^']*'$/.test(a)) return a.slice(1, -1);
          if (/^-?\d+(\.\d+)?$/.test(a)) return Number(a);
          throw new Error('Invalid control argument');
        }) : [];
        Promise.resolve(actions[match[1]](...args)).catch(() => {});
      }
    }
    document.addEventListener('click', event => {
      const element = event.target.closest('[data-click]');
      if (element) dispatchAction(element.dataset.click, element);
    });
    document.addEventListener('change', event => {
      if (event.target.dataset.change) dispatchAction(event.target.dataset.change, event.target);
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Enter' && event.target.dataset.enter === 'executeSearch') executeSearch();
      else if (['Enter',' '].includes(event.key) && event.target.tagName !== 'BUTTON' && event.target.dataset.click) {event.preventDefault();dispatchAction(event.target.dataset.click,event.target);}
    });

    // Initialize Telegram Web App SDK
    const tg = window.Telegram?.WebApp;
    if (tg) {
      tg.ready();
      tg.expand();
    }

    const inTelegram = Boolean(tg?.initData);
    function notify(message) { if (inTelegram && tg?.showAlert) tg.showAlert(String(message)); else window.alert(String(message)); }
    function confirmAction(message) { return inTelegram && tg?.showConfirm ? new Promise(resolve => tg.showConfirm(message, resolve)) : Promise.resolve(window.confirm(message)); }
    let kiteLoginAccountId = null;
    let kiteLoginAttempt = null;
    function stopKiteLoginCheck() {
      if (kiteLoginAttempt) window.clearInterval(kiteLoginAttempt.poll);
      kiteLoginAttempt = null;
    }
    function showKiteLoginConfirmed(account) {
      stopKiteLoginCheck();
      document.getElementById('kite-login-status').textContent = '✓ Kite login successful — connected to Kite. You can continue using Sterling.';
      const details = [account.user_name || account.label || 'Kite account'];
      if (account.kite_user_id) details.push('Kite ID: ' + account.kite_user_id);
      if (account.token_expires_at_ms) details.push('Session valid until ' + new Date(account.token_expires_at_ms).toLocaleString('en-IN', {timeZone:'Asia/Kolkata'}) + ' IST');
      const field = document.getElementById('kite-login-confirmation-details');
      field.textContent = details.join(' · ');
      field.hidden = false;
      document.getElementById('kite-login-link-controls').hidden = true;
      document.getElementById('kite-login-recovery-form').hidden = true;
      document.getElementById('kite-login-url').value = '';
      document.getElementById('kite-callback-url').value = '';
      void fetchData();
    }
    async function checkKiteLoginStatus() {
      const attempt = kiteLoginAttempt;
      if (!attempt || attempt.checking) return;
      const status = document.getElementById('kite-login-status');
      if (Date.now() >= attempt.expiresAt) {
        stopKiteLoginCheck();
        status.textContent = 'Kite login is not confirmed. This login link expired. Select Connect Kite to try again.';
        return;
      }
      attempt.checking = true;
      try {
        const response = await tmaFetch('/api/v1/tma/action', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'kite_login_status', account_id:attempt.accountId})}, false);
        const account = await response.json();
        if (kiteLoginAttempt !== attempt) return;
        // Only this interactive handoff can confirm login. Silent renewal also
        // changes last_login_at_ms, so timestamps cannot prove completion.
        if (account.connected && attempt.confirmationId && account.login_confirmation_id === attempt.confirmationId) {
          showKiteLoginConfirmed(account);
        }
      } catch (error) {
        if (kiteLoginAttempt === attempt) status.textContent = 'Kite login is not confirmed yet. ' + (error.message || 'Unable to check the connection. Return here after login to try again.');
      } finally { attempt.checking = false; }
    }
    function checkKiteLoginOnReturn() {
      if (document.visibilityState !== 'hidden') void checkKiteLoginStatus();
    }
    window.addEventListener('focus', checkKiteLoginOnReturn);
    document.addEventListener('visibilitychange', checkKiteLoginOnReturn);
    async function copyKiteLoginUrl() {
      const field = document.getElementById('kite-login-url');
      if (!field.value) return;
      try {
        await navigator.clipboard.writeText(field.value);
        document.getElementById('kite-login-status').textContent = 'Login URL copied. Open it in your phone browser.';
      } catch {
        field.focus(); field.select();
        document.getElementById('kite-login-status').textContent = 'Press and hold the selected login URL to copy it.';
      }
    }
    async function openKiteLogin() {
      stopKiteLoginCheck();
      document.getElementById('kite-login-panel').hidden = false;
      document.getElementById('kite-login-link-controls').hidden = true;
      document.getElementById('kite-login-confirmation-details').hidden = true;
      document.getElementById('kite-login-recovery-form').hidden = false;
      document.getElementById('kite-login-url').value = '';
      document.getElementById('kite-callback-url').value = '';
      kiteLoginAccountId = null;
      document.getElementById('kite-login-status').textContent = 'Preparing a fresh Kite login…';
      const popup = inTelegram ? null : window.open('/kite-login-opening.html', '_blank');
      try {
        const response = await tmaFetch('/api/v1/tma/action', {method:'POST', headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'kite_login'})}, false);
        const data = await response.json();
        const url = new URL(data.login_url);
        if (url.origin !== 'https://kite.zerodha.com' || url.pathname !== '/connect/login' || !url.searchParams.get('api_key')) throw new Error('Unexpected Kite login address. Check your account credentials.');
        if (!data.confirmation_id) throw new Error('Could not prepare Kite login confirmation. Open a fresh login link and try again.');
        kiteLoginAccountId = data.account_id || null;
        document.getElementById('kite-login-url').value = url.href;
        document.getElementById('kite-redirect-uri').value = data.redirect_uri || window.location.origin + '/api/v1/kite/callback';
        document.getElementById('kite-login-link-controls').hidden = false;
        document.getElementById('kite-login-status').textContent = 'Waiting for Kite login confirmation. Complete login in your browser, then return here. If the redirect cannot connect, paste its full address below.';
        kiteLoginAttempt = {accountId:kiteLoginAccountId, confirmationId:data.confirmation_id, expiresAt:Date.now() + 15 * 60_000, checking:false};
        kiteLoginAttempt.poll = window.setInterval(() => { void checkKiteLoginStatus(); }, 2_000);
        if (inTelegram && tg.openLink) tg.openLink(url.href);
        else if (popup) popup.location.href = url.href;
        else document.getElementById('kite-login-status').textContent = 'The browser blocked the login window. Copy the login URL above and open it in your browser.';
      } catch (error) {
        stopKiteLoginCheck();
        if (popup) popup.close();
        const message = error.message || 'Could not start Kite login. Check your connection and try again.';
        document.getElementById('kite-login-status').textContent = message;
        notify(message);
      }
    }
    document.getElementById('kite-login-recovery-form').addEventListener('submit', async event => {
      event.preventDefault();
      const field = document.getElementById('kite-callback-url');
      const button = event.target.querySelector('button');
      const status = document.getElementById('kite-login-status');
      button.disabled = true;
      status.textContent = 'Finishing Kite login…';
      try {
        const response = await tmaFetch('/api/v1/tma/action', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'kite_login_complete', callback_url:field.value.trim(), account_id:kiteLoginAccountId})});
        const data = await response.json();
        if (!data.success || !data.connected) throw new Error('Kite login is not confirmed. Open a fresh login link and try again.');
        showKiteLoginConfirmed(data);
      } catch (error) {
        status.textContent = error.message || 'Could not finish Kite login. Open a fresh login link and try again.';
      } finally { button.disabled = false; }
    });
    let authorizationFailed = false;
    let useSterlingLogin = false;
    let refreshPending = null;
    function savedToken(key) {
      try { return sessionStorage.getItem(key) || localStorage.getItem(key) || ''; } catch { return ''; }
    }
    function saveTokens(tokens) {
      for (const [key,value] of [['sterling.auth.access',tokens.access_token],['sterling.auth.refresh',tokens.refresh_token]]) {
        if (!value) continue;
        for (const storage of [sessionStorage,localStorage]) { try {storage.setItem(key,value);} catch {} }
      }
    }
    function showSterlingLogin(message='') {
      authorizationFailed = true;
      document.getElementById('sterling-login').hidden = false;
      document.getElementById('sterling-login-error').textContent = message;
      document.getElementById('txt-broker').innerText = 'Sign in required';
      document.getElementById('signals-list').textContent = 'Sign in above to view your desk.';
    }
    async function renewSession() {
      if (refreshPending) return refreshPending;
      const refresh = savedToken('sterling.auth.refresh');
      if (!refresh) return false;
      refreshPending = (async()=>{
        const response = await fetch('/api/v1/auth/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({refresh_token:refresh})});
        if (!response.ok) return false;
        saveTokens(await response.json()); return true;
      })();
      try {return await refreshPending;} finally {refreshPending=null;}
    }
    document.getElementById('sterling-login-form').addEventListener('submit',async event=>{
      event.preventDefault();
      const button=event.target.querySelector('button');button.disabled=true;
      const password=document.getElementById('sterling-password');
      try {
        const body=new URLSearchParams({username:document.getElementById('sterling-username').value,password:password.value});
        const response=await fetch('/api/v1/auth/login',{method:'POST',body});
        if (!response.ok) throw new Error(response.status===429?'Too many attempts. Please try again later.':'Sign-in failed. Check your username and password.');
        saveTokens(await response.json());
        const remembered=await fetch('/api/v1/auth/remember',{method:'POST',headers:{Authorization:'Bearer '+savedToken('sterling.auth.access')}});
        if(remembered.ok)saveTokens(await remembered.json());
        authorizationFailed=false;useSterlingLogin=true;document.getElementById('sterling-login').hidden=true;
        password.value='';await fetchData();
      } catch(error){document.getElementById('sterling-login-error').textContent=error.message || 'Unable to connect. Try again.';}
      finally {button.disabled=false;}
    });
    async function tmaFetch(url, options = {}, notifyFailure = true) {
      if (authorizationFailed) throw new Error('Sign in to Sterling to continue.');
      const headers = new Headers(options.headers || {});
      if (tg?.initData && !useSterlingLogin) headers.set('X-Telegram-Init-Data', tg.initData);
      else {
        const token = savedToken('sterling.auth.access');
        if (token) headers.set('Authorization', 'Bearer ' + token);
      }
      let response = await fetch(url, {...options, headers});
      if(response.status===401 && (!tg?.initData || useSterlingLogin) && await renewSession()) {
        headers.set('Authorization','Bearer '+savedToken('sterling.auth.access'));
        response=await fetch(url,{...options,headers});
      }
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        const message = typeof detail.detail === 'string' ? detail.detail : 'Request failed. Refresh and try again.';
        if (response.status === 401 || response.status === 403) showSterlingLogin(message);
        else if (options.method === 'POST' && notifyFailure) notify(message);
        throw new Error(message);
      }
      return response;
    }

    function haptic(type = 'medium') {
      if (tg?.HapticFeedback) {
        tg.HapticFeedback.impactOccurred(type);
      }
    }

    let appState = null;
    let activeSubTabPcr = 'NIFTY';
    let selectedOptionType = 'CE';

    function switchTab(tabId, el) {
      haptic('light');
      document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
      document.getElementById('tab-' + tabId).classList.add('active');
      if (el) el.classList.add('active');
    }

    let dataInFlight = false;
    async function fetchData() {
      if (dataInFlight || authorizationFailed) return;
      dataInFlight = true;
      try {
        const res = await tmaFetch('/api/v1/tma/data');
        if (!res.ok) return;
        appState = await res.json();
        renderState();
      } catch (err) {
        document.getElementById("sync-status").innerText = "Connection interrupted. Retrying…";
        document.getElementById('txt-entry').innerText = 'Entry status UNKNOWN';
        document.getElementById('txt-algo').innerText = 'SuperTrend status UNKNOWN';
        document.getElementById('pill-algo').className = 'pill pill-algo-off';
      } finally { dataInFlight = false; }
    }

    function renderState() {
      if (!appState) return;

      document.getElementById("sync-status").innerText = "Checked " + new Date(appState.timestamp_ms).toLocaleTimeString("en-IN", {timeZone:"Asia/Kolkata"}) + " IST";
      // Status Pills
      const acct = appState.account || {};
      document.getElementById('txt-broker').innerText = acct.connection_label || (acct.connected ? 'Kite connected' : 'Kite offline');
      const pillAlgo = document.getElementById('pill-algo');
      const txtAlgo = document.getElementById('txt-algo');
      const entry = appState.entry_controls || {};
      document.getElementById('txt-entry').innerText = entry.label || 'Entry status UNKNOWN';
      document.getElementById('pill-entry').title = entry.reason || 'Entry controls unavailable';
      if (appState.algo_enabled && entry.enabled === true) {
        pillAlgo.className = 'pill pill-algo-on';
        txtAlgo.innerText = 'SuperTrend ALGO ON';
      } else {
        pillAlgo.className = 'pill pill-algo-off';
        txtAlgo.innerText = appState.algo_enabled ? 'SuperTrend AUTO armed · entries blocked' : 'SuperTrend ALGO OFF';
      }

      // Summary P&L
      const pnlVal = (appState.pnl && appState.pnl.total) || 0.0;
      const summaryPnl = document.getElementById('summary-pnl');
      summaryPnl.innerText = (pnlVal >= 0 ? '+₹' : '-₹') + Math.abs(pnlVal).toLocaleString('en-IN', {minimumFractionDigits: 2});
      summaryPnl.className = 'summary-value ' + (pnlVal >= 0 ? 'val-green' : 'val-red');

      // Margin
      const availMargin = (appState.funds && appState.funds.available) || 0.0;
      const fundsMoney = value => value == null ? '—' : (value < 0 ? '-₹' : '₹') + Math.abs(value).toLocaleString('en-IN', {minimumFractionDigits: 2});
      document.getElementById('summary-margin').innerText = fundsMoney(appState.funds?.equity?.available);

      // Signals
      renderSignals();

      // Positions & Orders
      renderPositions();
      renderOrders();

      // PCR
      renderPcr();

      // Funds
      document.getElementById('funds-realized').innerText = '₹' + ((appState.pnl && appState.pnl.realized) || 0).toLocaleString('en-IN', {minimumFractionDigits: 2});
      document.getElementById('funds-unrealized').innerText = '₹' + ((appState.pnl && appState.pnl.unrealized) || 0).toLocaleString('en-IN', {minimumFractionDigits: 2});
      const equityFunds = appState.funds?.equity || {};
      const commodityFunds = appState.funds?.commodity || {};
      for (const [id, value] of Object.entries({
        'funds-avail': equityFunds.available, 'funds-used': equityFunds.used,
        'funds-equity-opening': equityFunds.opening_balance,
        'funds-commodity-available': commodityFunds.available,
        'funds-commodity-used': commodityFunds.used,
        'funds-commodity-opening': commodityFunds.opening_balance,
      })) document.getElementById(id).innerText = fundsMoney(value);

      document.getElementById('market-data-note').innerText = 'Use Search for current broker quotes. PCR observations appear in the PCR tab.';
      if (!appState.broker_data_available) {
        for (const id of ['summary-pnl','funds-realized','funds-unrealized']) document.getElementById(id).innerText = '—';
        document.getElementById('positions-list').innerText = 'Broker inventory unavailable. Check Kite connection or try Sync.';
        document.getElementById('orders-list').innerText = 'Broker orders unavailable.';
      }
      // Controls
      renderControls();
    }

    function renderSignals() {
      const container = document.getElementById('signals-list');
      const sigs = appState.signals || [];
      if (!sigs.length) {
        container.innerHTML = '<div style="color: var(--text-muted); font-size: 13px; text-align: center; padding: 24px;">No active strategy signals at this time</div>';
        return;
      }
      container.innerHTML = sigs.map(s => {
        const isBull = (s.direction || '').includes('BULL');
        const badgeClass = isBull ? 'badge-bull' : 'badge-bear';
        return `
          <div class="item-card">
            <div class="item-top">
              <div class="item-sym">${htmlEscape(s.symbol)} <span style="font-size: 12px; color: var(--text-muted);">(${htmlEscape(s.strategy)})</span></div>
              <div class="badge ${badgeClass}">${htmlEscape(s.direction)}</div>
            </div>
            <div style="font-size: 14px; color: var(--text-main); font-family: var(--font-mono); font-weight:700;">${htmlEscape(s.option_symbol || s.symbol)}</div>
            <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 2px;">
              <span style="font-size: 11px; color: var(--text-muted);">${htmlEscape(s.state)} · ${new Date(s.timestamp_ms).toLocaleTimeString()}</span>
              ${s.orderable ? `<button class="btn btn-emerald btn-sm" data-click="executeSignal('${controlSymbol(s.option_symbol || s.symbol)}')">Review order</button>` : '<span>Strategy managed / informational</span>'}
            </div>
          </div>
        `;
      }).join('');
    }

    function renderPositions() {
      const container = document.getElementById('positions-list');
      const pos = appState.positions || [];
      if (!pos.length) {
        container.innerHTML = '<div style="color: var(--text-muted); font-size: 13px; text-align: center; padding: 24px;">No open positions</div>';
        return;
      }
      container.innerHTML = pos.map(p => {
        const pnlClass = p.pnl >= 0 ? 'val-green' : 'val-red';
        return `
          <div class="item-card">
            <div class="item-top">
              <div class="item-sym">${htmlEscape(p.symbol)}</div>
              <div class="summary-value ${pnlClass}" style="font-size: 15px;">${p.pnl >= 0 ? '+' : ''}₹${p.pnl.toFixed(2)}</div>
            </div>
            <div style="display: flex; justify-content: space-between; font-size: 12px; color: var(--text-muted);">
              <span>Qty: <b>${p.quantity}</b> @ ₹${p.buy_price.toFixed(2)}</span>
              <span>LTP: <b>₹${p.ltp.toFixed(2)}</b></span>
            </div>
            <div style="text-align: right; margin-top: 4px;">
              <button class="btn btn-danger btn-sm" data-click="squareOffSingle('${controlSymbol(p.symbol)}', ${p.quantity})">Square Off</button>
            </div>
          </div>
        `;
      }).join('');
    }

    function renderOrders() {
      const container = document.getElementById('orders-list');
      const orders = appState.orders || [];
      if (!orders.length) {
        container.innerHTML = '<div style="color: var(--text-muted); font-size: 13px; text-align: center; padding: 24px;">No broker orders available for today</div>';
        return;
      }
      container.innerHTML = `
        <table class="data-table">
          <thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Status</th></tr></thead>
          <tbody>
            ${orders.map(o => `
              <tr>
                <td>${htmlEscape(o.time)}</td>
                <td>${htmlEscape(o.symbol)}</td>
                <td style="color:${o.side==='BUY'?'var(--emerald)':'var(--rose)'}; font-weight:700;">${htmlEscape(o.side)} ${o.quantity}</td>
                <td><span class="badge ${o.status==='COMPLETE'?'badge-bull':'badge-neutral'}">${htmlEscape(o.status)}</span></td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    }

    function renderPcr() {
      const grid = document.getElementById('pcr-overview-grid');
      const pcrData = appState.pcr || {};
      const series = pcrData.series || {};

      const syms = ['NIFTY', 'SENSEX', 'BANKNIFTY', 'FINNIFTY'];
      grid.innerHTML = syms.map(s => {
        const item = series[s] || {};
        const livePcr = item.livePcr ?? item.latest?.pcr ?? null;
        const spot = item.spot || {};
        return `
          <div style="background: rgba(255,255,255,0.03); border:1px solid var(--border-card); padding:12px; border-radius:14px;">
            <div style="font-weight:700; font-size:12px; color:var(--text-muted);">${s}</div>
            <div style="font-family:var(--font-mono); font-size:17px; font-weight:800; margin: 4px 0;">${livePcr === null ? '—' : Number(livePcr).toFixed(2)}</div>
            <div style="font-size:11px; color:var(--text-muted);">Spot ₹${spot.ltp == null ? '—' : Number(spot.ltp).toLocaleString('en-IN')}</div>
          </div>
        `;
      }).join('');

      renderPcrDetail();
    }

    function switchPcrSubTab(type) {
      haptic('light');
      activeSubTabPcr = type;
      document.querySelectorAll('.chip-group .chip').forEach(c => c.classList.remove('active'));
      const chip = document.getElementById('chip-' + type.toLowerCase());
      if (chip) chip.classList.add('active');
      renderPcrDetail();
    }

    function renderPcrDetail() {
      const container = document.getElementById('pcr-detail-container');
      const series = appState.pcr?.series || {};
      const symbols = activeSubTabPcr === 'FLOW' ? Object.keys(series) : [activeSubTabPcr];
      const rows = symbols.flatMap(symbol => (series[symbol]?.marks || []).map(row => ({...row, symbol})));
      if (!rows.length) {
        container.innerHTML = '<p>No PCR observations available for this selection.</p>';
        return;
      }
      container.innerHTML = `<table class="data-table"><thead><tr><th>Index</th><th>Time</th><th>PCR</th></tr></thead><tbody>${rows.map(row => `<tr><td>${htmlEscape(row.symbol)}</td><td>${htmlEscape(row.hhmm)}</td><td>${htmlEscape(row.pcr)}</td></tr>`).join('')}</tbody></table>`;
    }

    function renderControls() {
      const container = document.getElementById('strategies-toggle-list');
      const strats = appState.strategies || {};
      container.innerHTML = Object.keys(strats).map(k => {
        const item = strats[k];
        return `
          <div style="display: flex; align-items: center; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid rgba(255,255,255,0.04);">
            <div>
              <div style="font-weight: 700; font-size: 14px;">${htmlEscape(item.name)}</div>
              <div style="font-size: 12px; color: var(--text-muted);">${htmlEscape(item.description)}</div>
            </div>
            <button class="btn ${item.enabled ? 'btn-emerald' : 'btn-outline'} btn-sm" data-click="toggleStrategy('${k}')">
              ${item.enabled ? 'ON' : 'OFF'}
            </button>
          </div>
        `;
      }).join('');
    }

    // Quick Order Modal Functions
    function openQuickOrderModal() {
      haptic('medium');
      updateOptionSymbolPreview();
      document.getElementById('modal-order').style.display = 'flex';
    }

    function closeOrderModal() {
      document.getElementById('modal-order').style.display = 'none';
    }

    function setOptionType(type) {
      haptic('light');
      selectedOptionType = type;
      document.getElementById('btn-ce').className = 'type-btn type-btn-ce' + (type==='CE'?' active':'');
      document.getElementById('btn-pe').className = 'type-btn type-btn-pe' + (type==='PE'?' active':'');
      updateOptionSymbolPreview();
    }

    function updateOptionSymbolPreview() {
      const idx = document.getElementById('order-index').value;
      const live = (appState?.signals || []).find(s => s.symbol.startsWith(idx) && s.option_symbol.endsWith(selectedOptionType));
      document.getElementById('order-symbol-preview').value = live?.option_symbol || '';
      document.getElementById('order-lots').value = live?.quantity || '';
      document.getElementById('order-exchange').value = live?.exchange || (idx === 'SENSEX' ? 'BFO' : 'NFO');
    }

    async function dispatchQuickOrder() {
      haptic('medium');
      const sym = document.getElementById('order-symbol-preview').value;
      const qty = Number(document.getElementById('order-lots').value);
      const exchange = document.getElementById('order-exchange').value;
      if (!sym.trim() || !Number.isInteger(qty) || qty <= 0) {notify('Enter an exact contract and positive quantity.');return;}
      if (!await confirmAction(`Submit market BUY ${sym}, ${qty} units on ${exchange}?`)) return;
      closeOrderModal();

      const res = await tmaFetch('/api/v1/tma/action', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: 'place_order', symbol: sym, quantity: qty, side: 'BUY', exchange})
      });
      const d = await res.json();
      notify(d.success ? `Buy order submitted for ${sym}. Check Orders for fill status.` : `Order failed: ${d.message||''}`);
      fetchData();
    }

    // Actions
    async function toggleAlgo() {
      if (!await confirmAction('Change SuperTrend ALGO mode?')) return;
      haptic('medium');
      const res = await tmaFetch('/api/v1/tma/action', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: 'toggle_algo'})
      });
      const data = await res.json();
      notify(data.message);
      fetchData();
    }

    async function toggleStrategy(key) {
      if (!await confirmAction('Change this archived strategy setting?')) return;
      haptic('light');
      await tmaFetch('/api/v1/tma/action', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: 'toggle_strategy', strategy: key})
      });
      fetchData();
    }

    async function executeSignal(optSym) {
      const signal = appState?.signals?.find(s => s.option_symbol === optSym && s.orderable);
      if (!signal) {notify('This signal is no longer ready for manual entry. Refresh the desk.');return;}
      if (!await confirmAction(`Submit market BUY ${optSym}, ${signal.quantity} units on ${signal.exchange}?`)) return;
      const response = await tmaFetch('/api/v1/tma/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'place_order',symbol:optSym,signal:true,side:'BUY'})});
      const result = await response.json();notify(result.success ? 'Order submitted. Check Orders for fill status.' : result.message || 'Order rejected.');fetchData();
    }

    function promptPanic() {
      haptic('warning');
      const modal = document.getElementById('modal-confirm');
      document.getElementById('modal-title').innerText = "🚨 Emergency Square Off";
      document.getElementById('modal-body').innerText = "Halt new entries and attempt reducing exits for open positions. An order submission is not a confirmed fill; review pending and failed exits afterward.";
      document.getElementById('modal-action-btn').onclick = async () => {
        closeModal();
        const res = await tmaFetch('/api/v1/tma/action', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({action: 'panic_square_off'})
        });
        const d = await res.json();
        notify(d.message);
        fetchData();
      };
      modal.style.display = 'flex';
    }

    async function squareOffSingle(symbol, qty) {
      const position = appState?.positions?.find(p => p.symbol === symbol && p.quantity === qty);
      if (!position) {notify('Position changed. Refresh positions.');return;}
      const side = qty > 0 ? 'SELL' : 'BUY';
      if (!await confirmAction(`Submit exit for ${symbol}: ${side} ${Math.abs(qty)} units on ${position.exchange}?`)) return;
      const response = await tmaFetch('/api/v1/tma/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'square_off',symbol,quantity:Math.abs(qty),side,exchange:position.exchange})});
      const result = await response.json();notify(result.success ? 'Exit submitted. Check Orders for fill status.' : result.message || 'Exit rejected.');fetchData();
    }

    function closeModal() {
      document.getElementById('modal-confirm').style.display = 'none';
    }

    async function executeSearch() {
      const q = document.getElementById('search-query-input').value;
      if (!q.trim()) return;
      haptic('light');
      const area = document.getElementById('search-results-area');
      area.innerHTML = '<div style="text-align:center; color:var(--text-muted); padding:20px;">Fetching live quote...</div>';
      try {
        const res = await tmaFetch('/api/v1/tma/search?q=' + encodeURIComponent(q));
        const data = await res.json();
        area.innerHTML = `<div style="background: rgba(0,0,0,0.25); padding: 14px; border-radius: 12px; font-size: 13px; white-space:pre-line; border:1px solid var(--border-card);">${data.html}</div>`;
      } catch (e) {
        area.innerHTML = '<div style="color:var(--rose); padding:10px;">Search failed</div>';
      }
    }

    function quickSearch(sym) {
      document.getElementById('search-query-input').value = sym;
      switchTab('search', document.querySelectorAll('.nav-item')[4]);
      executeSearch();
    }

    // Auto Refresh Loop
    fetchData();
    setInterval(fetchData, 4000);
  </script>
</body>
</html>"""

    import re
    content = re.sub(r'<div ([^>]*data-click=[^>]*)>', r'<div role="button" tabindex="0" \1>', content)
    return content.replace('<script', f'<script nonce="{nonce}"')
