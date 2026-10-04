"""Broker-side protective stop for auto-exec option longs (workstreams C / D).

The headline real-money bug was that a market BUY carries no stop — the trail
lived only in the UI. This places a *broker-side* GTT SELL at the trail price the
moment we enter, so the position is protected even if our server, the WS, or the
user's laptop dies. The tick monitor (monitor.py) complements it for intrabar
exits and trails the GTT up as the stop tightens.

GTT (Good-Till-Triggered) is the right primitive: it lives at Zerodha, survives
disconnects, and needs no resting margin. A stop alone is a single-leg GTT that
fires a market SELL of the full quantity when the premium falls to the trigger.

When the signal also carries a TARGET, both go into one **two-leg (OCO)** GTT
instead. That is the whole reason to use OCO here: the exchange cancels the
other leg when one fills, so "stop and target both fire and we sell twice" is
impossible by construction rather than by careful coding on our side. A target
enforced by our own tick monitor would be a second server-side sell path; this
is none.

Thin I/O wrappers around the KiteClient GTT surface; all calls are defensive
(never raise into the caller — a failed stop is logged, not fatal, and the tick
monitor remains as the backstop).
"""
from __future__ import annotations

from typing import Optional

from app.core.logging import get_logger
from app.services.exchanges.kite import constants as K

log = get_logger(__name__)


def _exit_order(
    tradingsymbol: str,
    exchange: str,
    qty: int,
    direction: str = "long",
    product: str = K.PRODUCT_NRML,
) -> dict:
    """One GTT order leg for the exact broker position bucket."""
    txn = K.TXN_SELL if direction == "long" else K.TXN_BUY
    return {
        "exchange": exchange,
        "tradingsymbol": tradingsymbol,
        "transaction_type": txn,
        "quantity": int(qty),
        "order_type": K.ORDER_TYPE_MARKET,
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
) -> dict:
    """Build the Kite GTT request after inventory and product verification."""
    oco = (
        _oco_legs(trigger_premium, target_premium, direction)
        if target_premium else None
    )
    exit_leg = _exit_order(tradingsymbol, exchange, qty, direction, product)
    if oco:
        trigger_values, _stop_first = oco
        trigger_type = K.GTT_TYPE_OCO
        orders = [dict(exit_leg), dict(exit_leg)]
    else:
        trigger_values = [
            round(round(float(trigger_premium) / 0.05) * 0.05, 2)
        ]
        trigger_type = K.GTT_TYPE_SINGLE
        orders = [exit_leg]

    ref_last_price = round(
        round(float(last_price or trigger_premium) / 0.05) * 0.05, 2
    )
    if direction == "long" and trigger_values:
        ref_last_price = max(ref_last_price, trigger_values[0] + 0.05)
    elif direction == "short" and trigger_values:
        ref_last_price = (
            min(ref_last_price, max(0.05, trigger_values[0] - 0.05))
            if ref_last_price > 0
            else max(0.05, trigger_values[0] - 0.05)
        )

    return {
        "trigger_type": trigger_type,
        "tradingsymbol": tradingsymbol,
        "exchange": exchange,
        "last_price": ref_last_price,
        "trigger_values": trigger_values,
        "orders": orders,
    }

def _oco_legs(trigger_premium: float, target_premium: float, direction: str) -> Optional[tuple]:
    """``(trigger_values, ordered_pair_is_stop_first)`` for a two-leg GTT, or None if
    the two levels cannot form a valid OCO.

    Kite requires the two trigger values in ASCENDING order, with each order leg in
    the matching position. For a long option the stop is below and the target above;
    for a short it is the other way round. If the "target" is not actually on the
    profitable side of the stop the pair is nonsense — return None and let the caller
    place a plain stop, rather than arming a target that would fire instantly.
    """
    stop = round(round(float(trigger_premium) / 0.05) * 0.05, 2)
    target = round(round(float(target_premium) / 0.05) * 0.05, 2)
    if stop <= 0 or target <= 0:
        return None
    if direction == "long" and target <= stop:
        return None
    if direction == "short" and target >= stop:
        return None
    lower, upper = (stop, target) if direction == "long" else (target, stop)
    return [lower, upper], direction == "long"


async def place_stop(client, *, tradingsymbol: str, exchange: str, qty: int,
                     trigger_premium: float, last_price: float,
                     direction: str = "long",
                     target_premium: float = 0.0,
                     product: str = "") -> Optional[int]:
    """Place a broker-side exit only after fresh inventory proof."""
    if qty <= 0 or trigger_premium <= 0:
        return None
    allowed, reason, resolved_product = await verify_protection_inventory(
        client, tradingsymbol=tradingsymbol, exchange=exchange, qty=qty,
        direction=direction, product=product,
    )
    if not allowed:
        log.warning("kite protective GTT refused for %s: %s", tradingsymbol, reason)
        return None
    request = build_gtt_request(
        tradingsymbol=tradingsymbol, exchange=exchange, qty=qty,
        trigger_premium=trigger_premium, last_price=last_price,
        direction=direction, target_premium=target_premium,
        product=resolved_product,
    )
    try:
        res = await client.place_gtt(**request)
        tid = int((res or {}).get("trigger_id") or 0)
        return tid or None
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "kite protective GTT place failed for %s @ %.2f (target %.2f): %s",
            tradingsymbol, trigger_premium, target_premium or 0.0, exc,
        )
        return None

async def move_stop(client, *, trigger_id: int, tradingsymbol: str, exchange: str,
                    qty: int, trigger_premium: float, last_price: float,
                    direction: str = "long", target_premium: float = 0.0,
                    product: str = "") -> bool:
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
    request = build_gtt_request(
        tradingsymbol=tradingsymbol, exchange=exchange, qty=qty,
        trigger_premium=trigger_premium, last_price=last_price,
        direction=direction, target_premium=target_premium,
        product=resolved_product,
    )
    try:
        await client.modify_gtt(trigger_id, **request)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "kite protective GTT modify failed for %s (#%s): %s",
            tradingsymbol, trigger_id, exc,
        )
        return False

def _norm_gtt_values(values) -> tuple[float, ...]:
    out = []
    for value in values or []:
        try:
            out.append(round(float(value), 2))
        except (TypeError, ValueError):
            return ()
    return tuple(out)


def _active_gtt_matches_request(row: dict, request: dict) -> bool:
    """True only for one active trigger with the exact protection identity."""
    if not isinstance(row, dict):
        return False
    if str(row.get("status") or "").strip().lower() != "active":
        return False
    expected_type = str(request.get("trigger_type") or "").strip().lower()
    actual_type = str(row.get("type") or row.get("trigger_type") or "").strip().lower()
    if actual_type and expected_type and actual_type != expected_type:
        return False

    condition = row.get("condition") if isinstance(row.get("condition"), dict) else {}
    symbol = str(
        row.get("tradingsymbol") or condition.get("tradingsymbol") or ""
    ).strip().upper()
    exchange = str(
        row.get("exchange") or condition.get("exchange") or ""
    ).strip().upper()
    if symbol != str(request.get("tradingsymbol") or "").strip().upper():
        return False
    if exchange != str(request.get("exchange") or "").strip().upper():
        return False

    trigger_values = row.get("trigger_values")
    if trigger_values is None:
        trigger_values = condition.get("trigger_values")
    if _norm_gtt_values(trigger_values) != _norm_gtt_values(
        request.get("trigger_values")
    ):
        return False

    actual_orders = row.get("orders")
    expected_orders = request.get("orders")
    if not isinstance(actual_orders, list) or not isinstance(expected_orders, list):
        return False
    if len(actual_orders) != len(expected_orders):
        return False

    fields = (
        "tradingsymbol", "exchange", "transaction_type", "quantity",
        "order_type", "product",
    )
    for actual, expected in zip(actual_orders, expected_orders):
        if not isinstance(actual, dict) or not isinstance(expected, dict):
            return False
        for field in fields:
            if field == "quantity":
                try:
                    if int(float(actual.get(field) or 0)) != int(
                        float(expected.get(field) or 0)
                    ):
                        return False
                except (TypeError, ValueError):
                    return False
            elif (
                str(actual.get(field) or "").strip().upper()
                != str(expected.get(field) or "").strip().upper()
            ):
                return False
    return True


async def find_active_protection(
    client,
    *,
    tradingsymbol: str,
    exchange: str,
    qty: int,
    trigger_premium: float,
    last_price: float,
    direction: str = "long",
    target_premium: float = 0.0,
    product: str = "",
) -> tuple[str, int]:
    """Find one exact active GTT after a placement acknowledgement was lost.

    Return active only when fresh broker inventory is valid and one active trigger
    exactly matches the intended protection. An absent result does not authorize a
    replacement. An ambiguous result also requires operator reconciliation.
    """
    allowed, reason, resolved_product = await verify_protection_inventory(
        client,
        tradingsymbol=tradingsymbol,
        exchange=exchange,
        qty=qty,
        direction=direction,
        product=product,
    )
    if not allowed:
        log.warning(
            "kite pending protection inventory unverified for %s: %s",
            tradingsymbol,
            reason,
        )
        return "unverified", 0

    request = build_gtt_request(
        tradingsymbol=tradingsymbol,
        exchange=exchange,
        qty=qty,
        trigger_premium=trigger_premium,
        last_price=last_price,
        direction=direction,
        target_premium=target_premium,
        product=resolved_product,
    )
    try:
        rows = await client.get_gtts()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "kite pending protection list failed for %s: %s",
            tradingsymbol,
            exc,
        )
        return "unverified", 0
    if not isinstance(rows, list):
        return "unverified", 0

    matches: list[int] = []
    for row in rows:
        if not _active_gtt_matches_request(row, request):
            continue
        try:
            trigger_id = int(row.get("id") or row.get("trigger_id") or 0)
        except (TypeError, ValueError):
            trigger_id = 0
        if trigger_id > 0:
            matches.append(trigger_id)

    if len(matches) == 1:
        return "active", matches[0]
    if len(matches) > 1:
        return "ambiguous", 0
    return "absent", 0


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
