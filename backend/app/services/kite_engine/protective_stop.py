"""Broker GTT protection for a verified position and product bucket.

Connect GTTs submit priced LIMIT orders when triggered. A registered trigger
survives an application disconnect, but registration and triggering do not prove
an exit fill. Gaps or illiquidity can leave a triggered order unfilled. Callers
must reconcile broker fills and treat a failed protection request as unprotected
exposure. The tick monitor coordinates trails and exits with this broker state.
"""
from __future__ import annotations

import os
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from math import isfinite
from typing import Callable, Optional

from app.core.logging import get_logger
from app.services.exchanges.kite import constants as K

log = get_logger(__name__)


GTT_LIMIT_BUFFER_ENV = "STERLING_GTT_LIMIT_BUFFER_PCT"
_DEFAULT_GTT_LIMIT_BUFFER_PCT = 2.0


def _gtt_limit_buffer() -> Decimal:
    """How far through its trigger a GTT exit's limit price sits (a fraction).

    A limit too close to the trigger can sit unfilled in a gap; one too far gives
    away slippage. Default 2%; override with STERLING_GTT_LIMIT_BUFFER_PCT
    (clamped to 0.1-25). The live tick monitor remains the second line of defence.
    """
    raw = os.environ.get(GTT_LIMIT_BUFFER_ENV, "").strip()
    try:
        pct = float(raw) if raw else _DEFAULT_GTT_LIMIT_BUFFER_PCT
    except ValueError:
        pct = _DEFAULT_GTT_LIMIT_BUFFER_PCT
    if not isfinite(pct):
        pct = _DEFAULT_GTT_LIMIT_BUFFER_PCT
    return Decimal(str(min(max(pct, 0.1), 25.0))) / Decimal(100)


def _exit_order(
    tradingsymbol: str,
    exchange: str,
    qty: int,
    direction: str = "long",
    product: str = K.PRODUCT_NRML,
    price: float = 0.0,
) -> dict:
    """One GTT order leg for the exact broker position bucket."""
    txn = K.TXN_SELL if direction == "long" else K.TXN_BUY
    return {
        "exchange": exchange,
        "tradingsymbol": tradingsymbol,
        "transaction_type": txn,
        "quantity": int(qty),
        "order_type": K.ORDER_TYPE_LIMIT,
        "price": float(price),
        "product": str(product or K.PRODUCT_NRML).upper(),
    }


async def verify_protection_inventory(
    client,
    *,
    tradingsymbol: str,
    exchange: str,
    qty: int,
    direction: str = "long",
    product: str = "",
) -> tuple[bool, str, str]:
    """Prove a protective order cannot exceed fresh broker inventory."""
    try:
        wanted_qty = int(qty)
    except (TypeError, ValueError):
        return False, "invalid protection quantity", ""
    if wanted_qty <= 0:
        return False, "protection quantity must be positive", ""

    direction = str(direction or "").strip().lower()
    if direction not in {"long", "short"}:
        return False, f"unknown protection direction: {direction}", ""

    requested_product = str(product or "").strip().upper()
    mode = getattr(client, "_is_paper", None)
    if mode is True:
        return True, "paper", requested_product or K.PRODUCT_NRML
    if mode is not False:
        return False, "cannot verify broker mode for protection", ""

    reader = getattr(client, "get_positions_raw", None)
    if not callable(reader):
        reader = getattr(client, "get_positions", None)
    if not callable(reader):
        return False, "fresh broker positions unavailable for protection", ""

    try:
        raw = await reader()
    except Exception as exc:  # noqa: BLE001
        return False, f"fresh broker positions unavailable for protection: {exc}", ""

    rows = (raw.get("net") or []) if isinstance(raw, dict) else (raw or [])
    if not isinstance(rows, list):
        return False, "fresh broker positions returned invalid payload", ""

    wanted_symbol = str(tradingsymbol or "").strip().upper()
    wanted_exchange = str(exchange or "").strip().upper()
    by_product: dict[str, int] = {}

    for row in rows:
        if isinstance(row, dict):
            symbol = str(
                row.get("tradingsymbol") or row.get("symbol") or ""
            ).strip().upper()
            row_exchange = str(row.get("exchange") or "").strip().upper()
            row_product = str(
                row.get("product") or row.get("position_type") or ""
            ).strip().upper()
            raw_qty = row.get("quantity")
            if raw_qty is None:
                raw_qty = row.get("net_quantity")
            if raw_qty is None:
                raw_qty = row.get("size")
        else:
            symbol = str(getattr(row, "symbol", "") or "").strip().upper()
            row_exchange = str(getattr(row, "exchange", "") or "").strip().upper()
            if not row_exchange:
                underlying = str(getattr(row, "underlying", "") or "")
                row_exchange = (
                    underlying.split(":", 1)[0].strip().upper()
                    if ":" in underlying else ""
                )
            row_product = str(
                getattr(row, "product", "")
                or getattr(row, "position_type", "")
                or ""
            ).strip().upper()
            raw_qty = getattr(row, "quantity", None)
            if raw_qty is None:
                raw_qty = getattr(row, "size", 0)

        if symbol != wanted_symbol:
            continue
        if wanted_exchange and row_exchange != wanted_exchange:
            continue
        if not row_product:
            continue
        if requested_product and row_product != requested_product:
            continue
        try:
            net_qty = int(float(raw_qty or 0))
        except (TypeError, ValueError):
            return False, "fresh broker position quantity is invalid", ""
        by_product[row_product] = by_product.get(row_product, 0) + net_qty

    qualifying = []
    for row_product, net_qty in by_product.items():
        if direction == "long" and net_qty >= wanted_qty:
            qualifying.append((row_product, net_qty))
        elif direction == "short" and net_qty <= -wanted_qty:
            qualifying.append((row_product, net_qty))

    if not qualifying:
        detail = ", ".join(
            f"{key}={value}" for key, value in sorted(by_product.items())
        ) or "none"
        return (
            False,
            f"broker inventory does not prove {direction} {wanted_qty} "
            f"{wanted_exchange}:{wanted_symbol} protection ({detail})",
            "",
        )
    if len(qualifying) != 1:
        return False, "ambiguous broker product bucket for protection", ""

    resolved_product, net_qty = qualifying[0]
    if resolved_product not in {"CNC", "NRML", "MTF"}:
        return False, f"broker product {resolved_product} does not support GTT protection", ""
    return (
        True,
        f"verified {direction} protection inventory {resolved_product}={net_qty}",
        resolved_product,
    )


def build_gtt_request(
    *,
    tradingsymbol: str,
    exchange: str,
    qty: int,
    trigger_premium: float,
    last_price: float,
    direction: str = "long",
    target_premium: float = 0.0,
    product: str = K.PRODUCT_NRML,
    tick_size: float = 0.05,
) -> dict:
    """Build the Kite GTT request after inventory and product verification."""
    if not isfinite(tick_size) or tick_size <= 0:
        raise ValueError("invalid GTT tick size")
    def snap(value):
        return float((Decimal(str(value)) / Decimal(str(tick_size))).quantize(Decimal("1")) * Decimal(str(tick_size)))

    oco = (
        _oco_legs(trigger_premium, target_premium, direction, tick_size)
        if target_premium else None
    )
    if oco:
        trigger_values, _stop_first = oco
        trigger_type = K.GTT_TYPE_OCO
    else:
        trigger_values = [
            snap(trigger_premium)
        ]
        trigger_type = K.GTT_TYPE_SINGLE

    # The Connect GTT API documents priced LIMIT legs. Each price belongs to its
    # matching trigger, including the reversed stop/target order for shorts.
    # A small marketable limit offset does not guarantee an exit fill.
    step = Decimal(str(tick_size))
    orders = []
    for trigger in trigger_values:
        buffer = _gtt_limit_buffer()
        raw = Decimal(str(trigger)) * (Decimal(1) - buffer if direction == "long" else Decimal(1) + buffer)
        rounded = (raw / step).to_integral_value(
            rounding=ROUND_FLOOR if direction == "long" else ROUND_CEILING
        ) * step
        orders.append(_exit_order(tradingsymbol, exchange, qty, direction, product, max(float(step), float(rounded))))

    ref_last_price = snap(last_price)

    return {
        "trigger_type": trigger_type,
        "tradingsymbol": tradingsymbol,
        "exchange": exchange,
        "last_price": ref_last_price,
        "trigger_values": trigger_values,
        "orders": orders,
    }

def _oco_legs(trigger_premium: float, target_premium: float, direction: str, tick_size: float = 0.05) -> Optional[tuple]:
    """``(trigger_values, ordered_pair_is_stop_first)`` for a two-leg GTT, or None if
    the two levels cannot form a valid OCO.

    Kite requires the two trigger values in ASCENDING order, with each order leg in
    the matching position. For a long option the stop is below and the target above;
    for a short it is the other way round. If the "target" is not actually on the
    profitable side of the stop the pair is nonsense — return None and let the caller
    place a plain stop, rather than arming a target that would fire instantly.
    """
    stop = float((Decimal(str(trigger_premium)) / Decimal(str(tick_size))).quantize(Decimal("1")) * Decimal(str(tick_size)))
    target = float((Decimal(str(target_premium)) / Decimal(str(tick_size))).quantize(Decimal("1")) * Decimal(str(tick_size)))
    if stop <= 0 or target <= 0:
        return None
    if direction == "long" and target <= stop:
        return None
    if direction == "short" and target >= stop:
        return None
    lower, upper = (stop, target) if direction == "long" else (target, stop)
    return [lower, upper], direction == "long"


async def _reference_price(client, symbol: str, exchange: str, fallback: float) -> float:
    """Live GTTs use a fresh broker quote, never a fabricated entry/stop price."""
    value = fallback
    if getattr(client, "_is_paper", None) is False:
        reader = getattr(client, "get_ltp", None)
        if not callable(reader):
            return 0.0
        try:
            key = f"{exchange}:{symbol}"
            response = await reader([key])
            value = (response.get(key) or {}).get("last_price", 0)
        except Exception as exc:  # noqa: BLE001
            log.warning("kite GTT reference quote unavailable for %s: %s", symbol, exc)
            return 0.0
    try:
        value = float(value)
        return value if isfinite(value) and value > 0 else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def stop_is_breached(trigger: float, reference: float, direction: str) -> bool:
    return (direction == "long" and reference <= trigger) or (direction == "short" and reference >= trigger)


def protection_is_breached(request: dict, reference: float, direction: str) -> bool:
    """Check the actual tick-rounded stop and target before broker submission."""
    triggers = request["trigger_values"]
    stop = triggers[0] if direction == "long" else triggers[-1]
    if stop_is_breached(stop, reference, direction):
        return True
    if len(triggers) == 2:
        target = triggers[-1] if direction == "long" else triggers[0]
        return reference >= target if direction == "long" else reference <= target
    return False


def is_definitive_refusal(exc: BaseException) -> bool:
    """True when ``exc`` proves a GTT placement created NOTHING at the broker.

    Three shapes prove it: the broker-write guard refused the call before it left
    this process, the request never reached the wire (connect / pool failures), or
    Kite answered with a 4xx - it looked at the request and said no. A read timeout,
    a 5xx or any other transport error proves nothing: the trigger may exist, so the
    outcome stays UNKNOWN and the position stays fenced against a blind second send.
    """
    if isinstance(exc, PermissionError):
        return True
    try:
        import httpx

        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
            return True
    except Exception:  # noqa: BLE001
        pass
    from app.services.exchanges.kite.errors import KiteError

    if isinstance(exc, KiteError):
        try:
            code = int(exc.status_code or 0)
        except (TypeError, ValueError):
            return False
        return 400 <= code < 500
    return False


async def place_stop(client, *, tradingsymbol: str, exchange: str, qty: int,
                     trigger_premium: float, last_price: float,
                     direction: str = "long",
                     target_premium: float = 0.0,
                     product: str = "", tick_size: float = 0.05,
                     before_submit: Optional[Callable[[], None]] = None,
                     on_refused: Optional[Callable[[], None]] = None) -> Optional[int]:
    """Place a broker-side exit only after fresh inventory proof.

    ``before_submit`` runs just before the broker write so a caller can fence the
    position durably. ``on_refused`` runs when the write provably created nothing
    (see :func:`is_definitive_refusal`), so that fence can be lifted: only a genuinely
    unknown outcome may stay latched.
    """
    if qty <= 0 or trigger_premium <= 0:
        return None
    allowed, reason, resolved_product = await verify_protection_inventory(
        client, tradingsymbol=tradingsymbol, exchange=exchange, qty=qty,
        direction=direction, product=product,
    )
    if not allowed:
        log.warning("kite protective GTT refused for %s: %s", tradingsymbol, reason)
        return None
    reference = await _reference_price(client, tradingsymbol, exchange, last_price)
    if not reference:
        log.warning("kite protective GTT refused for %s: fresh reference quote unavailable", tradingsymbol)
        return None
    if getattr(client, "_is_paper", None) is False and stop_is_breached(trigger_premium, reference, direction):
        log.warning("kite protective GTT refused for %s: stop already breached; exit reconciliation required", tradingsymbol)
        return None
    request = build_gtt_request(
        tradingsymbol=tradingsymbol, exchange=exchange, qty=qty,
        trigger_premium=trigger_premium, last_price=reference,
        direction=direction, target_premium=target_premium,
        product=resolved_product, tick_size=tick_size,
    )
    if getattr(client, "_is_paper", None) is False and protection_is_breached(request, reference, direction):
        log.warning("kite protective GTT refused for %s: rounded stop or target already breached; exit reconciliation required", tradingsymbol)
        return None
    try:
        if before_submit is not None:
            before_submit()
        res = await client.place_gtt(**request)
        tid = int((res or {}).get("trigger_id") or 0)
        return tid or None
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "kite protective GTT place failed for %s @ %.2f (target %.2f): %s",
            tradingsymbol, trigger_premium, target_premium or 0.0, exc,
        )
        if on_refused is not None and is_definitive_refusal(exc):
            try:
                on_refused()
            except Exception as hook_exc:  # noqa: BLE001
                log.warning("kite protective GTT refusal hook failed for %s: %s",
                            tradingsymbol, hook_exc)
        return None

async def move_stop(client, *, trigger_id: int, tradingsymbol: str, exchange: str,
                    qty: int, trigger_premium: float, last_price: float,
                    direction: str = "long", target_premium: float = 0.0,
                    product: str = "", tick_size: float = 0.05) -> bool:
    """Trail a GTT only while its full exit quantity still exists at the broker."""
    if trigger_id <= 0 or trigger_premium <= 0:
        return False
    allowed, reason, resolved_product = await verify_protection_inventory(
        client, tradingsymbol=tradingsymbol, exchange=exchange, qty=qty,
        direction=direction, product=product,
    )
    if not allowed:
        log.warning(
            "kite protective GTT modify refused for %s (#%s): %s",
            tradingsymbol, trigger_id, reason,
        )
        return False
    reference = await _reference_price(client, tradingsymbol, exchange, last_price)
    if not reference:
        log.warning("kite protective GTT modify refused for %s: fresh reference quote unavailable", tradingsymbol)
        return False
    if getattr(client, "_is_paper", None) is False and stop_is_breached(trigger_premium, reference, direction):
        log.warning("kite protective GTT modify refused for %s: stop already breached; exit reconciliation required", tradingsymbol)
        return False
    request = build_gtt_request(
        tradingsymbol=tradingsymbol, exchange=exchange, qty=qty,
        trigger_premium=trigger_premium, last_price=reference,
        direction=direction, target_premium=target_premium,
        product=resolved_product, tick_size=tick_size,
    )
    if getattr(client, "_is_paper", None) is False and protection_is_breached(request, reference, direction):
        log.warning("kite protective GTT modify refused for %s: rounded stop or target already breached; exit reconciliation required", tradingsymbol)
        return False
    try:
        await client.modify_gtt(trigger_id, **request)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "kite protective GTT modify failed for %s (#%s): %s",
            tradingsymbol, trigger_id, exc,
        )
        return False

#: What happened to a GTT we tried to cancel. The distinction is load-bearing:
#: "cancelled" means the broker stop is provably out of the way and it is safe to
#: place our own exit; anything else means a broker SELL may already be live for
#: this position, and placing a second one would sell twice and go net short.
CANCELLED = "cancelled"
GONE = "gone"        # already triggered, already deleted, or unknown to the broker
UNKNOWN = "unknown"  # the call failed and we cannot tell — treat as NOT cancelled

_GONE_MARKERS = ("not found", "does not exist", "no such", "already", "triggered",
                 "invalid trigger", "404")


async def cancel_stop_result(client, trigger_id: int) -> str:
    """Cancel a GTT and report which of the three outcomes above happened.

    The classification of a failure into GONE vs UNKNOWN is a heuristic over the
    broker's error text, because Kite's exact message for "this trigger already
    fired" is not something this repo can pin. Both mean the same thing to a caller
    deciding whether to sell: NOT cancelled.

    What this function cannot tell you is the thing that actually matters next —
    whether a stop is still going to exit the position for us. "Our cancel failed"
    covers both "it already fired and is selling" (stand down) and "it is not there
    any more and nothing will exit this position" (we must sell ourselves). Only the
    broker knows which. Ask it with ``stop_status`` before standing down.
    """
    if trigger_id <= 0:
        return GONE  # nothing armed — nothing can double-fire
    try:
        await client.delete_gtt(trigger_id)
        return CANCELLED
    except Exception as exc:  # noqa: BLE001
        text = str(exc).lower()
        if any(marker in text for marker in _GONE_MARKERS):
            log.info("kite protective GTT #%s was already gone: %s", trigger_id, exc)
            return GONE
        log.warning("kite protective GTT cancel failed (#%s): %s", trigger_id, exc)
        return UNKNOWN


async def cancel_stop(client, trigger_id: int) -> bool:
    """True only when the GTT was provably cancelled by this call."""
    return (await cancel_stop_result(client, trigger_id)) == CANCELLED


#: What the BROKER says about a trigger, when a cancel did not come back CANCELLED.
STOP_ACTIVE = "active"          # still resting at the exchange — it will fire on its own
STOP_TRIGGERED = "triggered"    # it fired; a broker SELL exists for this position
STOP_ABSENT = "absent"          # cancelled / deleted / expired / disabled / unknown to the
                                # broker → NOTHING there will ever exit this position
STOP_UNVERIFIED = "unverified"  # we could not ask — must be treated as "may still be armed"

#: The only two GTT statuses under which the broker still acts for us.
_ACTING_STATUSES = {"active": STOP_ACTIVE, "triggered": STOP_TRIGGERED}
#: The statuses Kite documents for a trigger that will never act again.
_INERT_STATUSES = frozenset({"cancelled", "canceled", "deleted", "expired", "rejected",
                             "disabled"})
#: Order-book statuses that mean the exit order will never fill. Everything else —
#: COMPLETE, OPEN, TRIGGER PENDING, VALIDATION PENDING, PUT ORDER REQ RECEIVED — is a
#: sell that has filled or is still working, and we must not place a second one.
_DEAD_ORDER_STATUSES = frozenset({"REJECTED", "CANCELLED", "CANCELED"})

#: Internal: the broker's GTT book provably does not hold this trigger. Not a verdict on
#: its own — a trigger vanishes both when it is deleted (nothing will exit us) and,
#: possibly, once it has fired (a SELL exists). The order book separates the two.
_NOT_ON_BOOK = "__not_on_book__"


def _classify(status: str) -> str:
    """A single GTT status → verdict. An unrecognised status is UNVERIFIED, never
    ABSENT: the trigger demonstrably exists at the broker, so we cannot rule out that
    it is going to fire, and guessing ABSENT here is the one mistake that sells twice."""
    s = (status or "").strip().lower()
    if s in _ACTING_STATUSES:
        return _ACTING_STATUSES[s]
    if s in _INERT_STATUSES:
        return STOP_ABSENT
    return STOP_UNVERIFIED


async def _status_from_trigger(client, trigger_id: int) -> Optional[str]:
    """``GET /gtt/triggers/{id}``. A verdict, ``_NOT_ON_BOOK``, or None when the answer
    was inconclusive (call failed for any reason other than 404, empty envelope)."""
    try:
        data = await client.get_gtt(int(trigger_id))
    except Exception as exc:  # noqa: BLE001
        if int(getattr(exc, "status_code", 0) or 0) == 404:
            return _NOT_ON_BOOK
        log.warning("kite protective GTT #%s status check failed: %s", trigger_id, exc)
        return None
    if not isinstance(data, dict):
        return None
    status = str(data.get("status") or "").strip()
    return _classify(status) if status else None


async def _status_from_list(client, trigger_id: int) -> Optional[str]:
    """``GET /gtt/triggers`` as the second opinion, because Kite does not promise a 404
    for a trigger it no longer holds — a 400/``InputException`` is just as likely, and
    reading ABSENT out of an error *message* is the guess this module refuses to make.
    Absence from a list we successfully read is hard evidence; an error is not.

    Returns a verdict, ``_NOT_ON_BOOK``, or None when the list could not be read."""
    try:
        rows = await client.get_gtts()
    except Exception as exc:  # noqa: BLE001
        log.warning("kite protective GTT #%s list check failed: %s", trigger_id, exc)
        return None
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if int(row.get("id") or row.get("trigger_id") or 0) != int(trigger_id):
            continue
        status = str(row.get("status") or "").strip()
        return _classify(status) if status else STOP_UNVERIFIED
    return _NOT_ON_BOOK


async def _exit_order_is_working(
    client,
    tradingsymbol: str,
    direction: str,
    *,
    exchange: str = "",
    product: str = "",
    qty: int = 0,
) -> Optional[bool]:
    """Is an exit order working for this exact broker position bucket.

    A symbol-only match is not evidence. The same contract can coexist across
    exchange/product buckets, and another round-trip can have the same exit side.
    The fallback that decides whether Sterling may place its own SELL therefore
    requires the complete position identity: exchange + symbol + product +
    direction + quantity. Missing or malformed identity is UNVERIFIED upstream.
    """
    want_sym = str(tradingsymbol or "").strip().upper()
    want_exchange = str(exchange or "").strip().upper()
    want_product = str(product or "").strip().upper()
    try:
        want_qty = int(qty)
    except (TypeError, ValueError):
        want_qty = 0
    if not want_sym or not want_exchange or not want_product or want_qty <= 0:
        return None

    try:
        orders = await client.get_orders()
    except Exception as exc:  # noqa: BLE001
        log.warning("kite order-book check for %s failed: %s", tradingsymbol, exc)
        return None
    if not isinstance(orders, list):
        return None

    want_txn = K.TXN_SELL if direction == "long" else K.TXN_BUY
    for o in orders:
        if not isinstance(o, dict):
            continue
        if str(o.get("tradingsymbol") or "").strip().upper() != want_sym:
            continue
        if str(o.get("transaction_type") or "").strip().upper() != want_txn:
            continue

        if str(o.get("status") or "").strip().upper() in _DEAD_ORDER_STATUSES:
            # A terminal rejected/cancelled order cannot race our exit, so no
            # further identity proof is needed for this row.
            continue

        row_exchange = str(o.get("exchange") or "").strip().upper()
        row_product = str(o.get("product") or "").strip().upper()
        raw_qty = o.get("quantity")
        try:
            row_qty = int(float(raw_qty))
        except (TypeError, ValueError):
            # This could be our exit, but its identity is incomplete. Do not
            # manufacture ABSENT from an unreadable candidate.
            return None
        if not row_exchange or not row_product:
            return None
        if (
            row_exchange != want_exchange
            or row_product != want_product
            or row_qty != want_qty
        ):
            continue
        return True
    return False


async def resting_sell_triggers(client, *, tradingsymbol: str, exchange: str,
                                product: str, direction: str = "long") -> Optional[list]:
    """Every GTT in the broker's book that would exit this exact position bucket.

    For a placement whose outcome is unknown we hold no trigger id, so the only way to
    find what may be resting is to read the book. Each row is
    ``{"id", "verdict", "qty"}`` where ``verdict`` is ``STOP_ACTIVE`` / ``STOP_TRIGGERED``
    / ``STOP_ABSENT`` / ``STOP_UNVERIFIED``; only rows whose exit leg matches exchange,
    symbol, product and closing side are returned. ``None`` means the book could not be
    read, or a row for this symbol cannot be attributed (incomplete identity): the
    caller must treat that as "may still be armed", never as "nothing there".
    """
    want_sym = str(tradingsymbol or "").strip().upper()
    want_exchange = str(exchange or "").strip().upper()
    want_product = str(product or "").strip().upper()
    if not want_sym or not want_exchange or not want_product:
        return None
    try:
        rows = await client.get_gtts()
    except Exception as exc:  # noqa: BLE001
        log.warning("kite GTT book unreadable while looking for %s: %s", tradingsymbol, exc)
        return None
    if not isinstance(rows, list):
        return None
    want_txn = K.TXN_SELL if direction == "long" else K.TXN_BUY
    found: list = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cond = row.get("condition") if isinstance(row.get("condition"), dict) else {}
        legs = [o for o in (row.get("orders") or []) if isinstance(o, dict)]
        symbols = {str(cond.get("tradingsymbol") or "").strip().upper()}
        symbols |= {str(o.get("tradingsymbol") or "").strip().upper() for o in legs}
        symbols.discard("")
        if want_sym not in symbols:
            continue
        verdict = _classify(str(row.get("status") or ""))
        try:
            tid = int(row.get("id") or row.get("trigger_id") or 0)
        except (TypeError, ValueError):
            tid = 0
        matched_qty = 0
        complete = True
        for leg in legs:
            leg_exchange = str(leg.get("exchange") or cond.get("exchange") or "").strip().upper()
            leg_product = str(leg.get("product") or "").strip().upper()
            leg_txn = str(leg.get("transaction_type") or "").strip().upper()
            if not leg_exchange or not leg_product or not leg_txn:
                complete = False
                continue
            if leg_exchange != want_exchange or leg_product != want_product or leg_txn != want_txn:
                continue
            try:
                matched_qty = max(matched_qty, int(float(leg.get("quantity") or 0)))
            except (TypeError, ValueError):
                complete = False
        if matched_qty <= 0:
            if verdict in (STOP_ACTIVE, STOP_TRIGGERED, STOP_UNVERIFIED) and (
                    not complete or not legs):
                return None  # same symbol, cannot be attributed: do not guess
            continue
        if tid <= 0:
            return None
        found.append({"id": tid, "verdict": verdict, "qty": matched_qty})
    return found


async def stop_status(client, trigger_id: int, *, tradingsymbol: str = "",
                      direction: str = "long", exchange: str = "",
                      product: str = "", qty: int = 0) -> str:
    """Ask the broker what actually became of GTT ``trigger_id``.

    Two ordinary events leave a trigger absent WITHOUT the position having exited: the
    user deletes or edits it in the Kite app, or it fired and its market SELL was
    rejected so the trigger is consumed with no fill. In both, a caller that "leaves the
    exit to the broker" waits forever.

    Three sources, cheapest first, and each one only ever narrows the answer:

    1. the trigger itself — an acting or documented-inert status ends it;
    2. the trigger list, when (1) errors or comes back empty — Kite's error code for an
       unknown trigger is not something this repo can pin, so absence from a list we did
       read replaces trusting a 404;
    3. the order book, once (1) or (2) prove the trigger is not there — the only source
       that distinguishes "deleted, nothing will exit us" from "fired, a SELL exists".

    ABSENT — the verdict that lets a caller place its own exit — therefore requires
    positive evidence from two independent reads, because a wrong ABSENT sells on top of
    a live broker SELL and goes NAKED SHORT, whereas a wrong UNVERIFIED costs at worst a
    delayed exit the caller is told about. Pass the full broker bucket
    (exchange, tradingsymbol, product, direction, quantity): without exact identity
    step (3) cannot safely attribute an order and a missing trigger remains UNVERIFIED.
    """
    if trigger_id <= 0:
        return STOP_ABSENT
    verdict = await _status_from_trigger(client, trigger_id)
    if verdict is None:
        verdict = await _status_from_list(client, trigger_id)
    if verdict is None:
        return STOP_UNVERIFIED
    if verdict != _NOT_ON_BOOK:
        return verdict
    working = await _exit_order_is_working(
        client,
        tradingsymbol,
        direction,
        exchange=exchange,
        product=product,
        qty=qty,
    )
    if working is None:
        return STOP_UNVERIFIED
    return STOP_TRIGGERED if working else STOP_ABSENT
