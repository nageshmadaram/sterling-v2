import httpx
import os
import logging

log = logging.getLogger(__name__)

# Both names are accepted by load_sterling_env. A historical deployment that
# still uses TELEGRAM_TOKEN must not silently disable the control bot.
# Explicit TELEGRAM_BOT_TOKEN always wins when non-empty.
TELEGRAM_TOKEN    = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or "").strip()
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_REACHABLE: bool = False   # updated by test/send success; read by GET endpoint


def restore_legacy_config_from_store() -> None:
    """Restore the legacy single-bot transport without overriding env secrets.

    Production requires stored bot tokens to be encrypted. Environment values
    are authoritative; the DB exists only as a backwards-compatible fallback.
    """
    global TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_REACHABLE
    from app.core.security import decrypt
    from app.services.db import get_config

    env_token = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or "").strip()
    env_chat = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    stored_token = get_config("telegram_bot_token")
    stored_chat = get_config("telegram_chat_id")

    if env_token:
        TELEGRAM_TOKEN = env_token
    elif stored_token:
        TELEGRAM_TOKEN = decrypt(stored_token)

    if env_chat:
        TELEGRAM_CHAT_ID = env_chat
    elif stored_chat:
        TELEGRAM_CHAT_ID = str(stored_chat).strip()

    TELEGRAM_REACHABLE = get_config("telegram_verified") == "1"



async def send(text: str, parse_mode: str = "HTML",
               reply_markup: dict | None = None) -> bool:
    global TELEGRAM_REACHABLE
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    payload: dict = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
    }
    # Telegram returns 400 "object expected as reply markup" if the field is null
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(
            timeout=8,
            follow_redirects=False,
            trust_env=False,
        ) as c:
            r = await c.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json=payload,
            )
        try:
            body = r.json()
        except Exception:  # noqa: BLE001
            body = {}
        ok = r.status_code == 200 and bool(body.get("ok"))
        TELEGRAM_REACHABLE = ok
        if not ok:
            log.warning(
                "Telegram sendMessage failed %s: %s",
                r.status_code,
                str(body.get("description") or r.text)[:200],
            )
        return ok
    except Exception as exc:
        TELEGRAM_REACHABLE = False
        log.warning("Telegram send error: %s", type(exc).__name__)
        return False


async def send_lifecycle_transition(
    symbol: str,
    option_symbol: str,
    from_mode: str,
    to_mode: str,
    direction: str,
    ltp: float,
    pnl_pts: float | None = None,
    tsl: float | None = None,
    is_upgrade: bool = True,
) -> bool:
    """Send formatted Telegram alert on Adaptive Edge horizon promotion or demotion."""
    header = "⚡ <b>[ADAPTIVE EDGE] HORIZON PROMOTION</b>" if is_upgrade else "🛡️ <b>[ADAPTIVE EDGE] HORIZON DEMOTION</b>"
    arrow = "↗" if is_upgrade else "↘"
    pnl_str = f"+{pnl_pts:.2f} pts" if (pnl_pts is not None and pnl_pts >= 0) else f"{pnl_pts:.2f} pts" if pnl_pts is not None else "N/A"
    tsl_str = f"₹{tsl:.2f}" if tsl is not None else "Trailing"
    sub_note = "<i>Stop protection tightened for runner mode.</i>" if is_upgrade else "<i>Giveback protection engaged. Capital preserved.</i>"

    msg = (
        f"{header}\n"
        f"<b>Instrument:</b> {symbol} (<code>{option_symbol}</code>)\n"
        f"<b>Side:</b> {direction.upper()}\n"
        f"<b>Lifecycle:</b> <code>{from_mode} {arrow} {to_mode}</code>\n"
        f"<b>LTP:</b> ₹{ltp:.2f} | <b>Gain:</b> {pnl_str}\n"
        f"<b>Active TSL:</b> {tsl_str}\n"
        f"{sub_note}"
    )
    return await send(msg)


async def send_session_cutoff_alert(count_flattened: int = 0) -> bool:
    """Send 14:45 IST session cutoff flattening alert."""
    msg = (
        "⏰ <b>[ADAPTIVE EDGE] 14:45 IST SESSION CUTOFF</b>\n"
        f"Normal trading cutoff reached. Auto-flattening {count_flattened} active intraday positions.\n"
        "<i>New entries & promotions locked until tomorrow's session.</i>"
    )
    return await send(msg)
