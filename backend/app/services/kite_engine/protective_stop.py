"""Broker GTT protection for a verified position and product bucket.

Connect GTTs submit priced LIMIT orders when triggered. A registered trigger
survives an application disconnect, but registration and triggering do not prove
an exit fill. Gaps or illiquidity can leave a triggered order unfilled. Callers
must reconcile broker fills and treat a failed protection request as unprotected
exposure. The tick monitor coordinates trails and exits with this broker state.

A GTT with status ``triggered`` has only fired: the LIMIT order it submitted may
be filled, still resting (a gap can leave it unfillable), or rejected by the
exchange/RMS while the GTT stays ``triggered``. ``stop_status`` therefore reads
the child order and reports one of:

``STOP_TRIGGERED``          the child order is COMPLETE. The exit is done; stand
                            down and attribute the fill.
``STOP_TRIGGERED_RESTING``  the child is live (or not yet visible) inside the
                            grace window. Stand down for now and ask again.
``STOP_TRIGGERED_STALE``    the child is live but older than the grace window, or
                            its limit can no longer fill at the reference price.
                            The caller must cancel the child (``stop_state``
                            carries ``child_order_id``; ``cancel_fired_child``
                            does the cancel and confirms a terminal state), book
                            any part-fill, then send its own marketable exit for
                            the quantity the broker still holds, through its own
                            canonical exit path.
``STOP_ABSENT``             the child was REJECTED/CANCELLED, or no child ever
                            appeared after the grace window. Nothing at the
                            broker will exit the position: the caller sells.

``stop_state`` returns the same verdict plus the child's state, id, price and
filled quantity. ``FIRED_VERDICTS`` is the set of verdicts that mean "the trigger
fired" for a caller that only needs that question answered.

``place_stop`` reports whether a failed submission is ambiguous: see
``PlaceOutcome`` and ``place_stop_outcome``.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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


@dataclass(frozen=True)
class PlaceOutcome:
    """What a GTT placement attempt did, including the case nobody can answer.

    ``trigger_id`` is set only when the broker returned an id. ``ambiguous`` is
    True when the POST was sent and the answer was lost or unclassifiable (a
    timeout, a 5xx, a transport error, a reply with no trigger id): Kite may have
    created the trigger, so a blind second placement can arm two SELLs and leave
    the account net short. A definitive broker rejection (``KiteOrderError`` and
    friends, i.e. a 4xx) created nothing and is not ambiguous. A refusal before
    anything was sent (no inventory proof, no quote, stop already breached) is
    not ambiguous either.
    """

    trigger_id: Optional[int] = None
    ambiguous: bool = False
    reason: str = ""


async def place_stop_outcome(client, *, tradingsymbol: str, exchange: str, qty: int,
                             trigger_premium: float, last_price: float,
                             direction: str = "long",
                             target_premium: float = 0.0,
                             product: str = "", tick_size: float = 0.05,
                             before_submit: Optional[Callable[[], None]] = None,
                             on_refused: Optional[Callable[[], None]] = None) -> PlaceOutcome:
    """Place a broker-side exit only after fresh inventory proof, and say whether a
    failure leaves the broker's trigger book ambiguous.

    ``before_submit`` runs immediately before the POST: callers latch a durable
    "placement in flight" fence there so a crash or a timeout can be resolved
    against ``get_gtts`` instead of being retried blindly. ``on_refused`` runs when
    the write provably created nothing (see :func:`is_definitive_refusal`), so that
    fence can be lifted: only a genuinely unknown outcome may stay latched.
    """
    if qty <= 0 or trigger_premium <= 0:
        return PlaceOutcome(reason="invalid protection request")
    allowed, reason, resolved_product = await verify_protection_inventory(
        client, tradingsymbol=tradingsymbol, exchange=exchange, qty=qty,
        direction=direction, product=product,
    )
    if not allowed:
        log.warning("kite protective GTT refused for %s: %s", tradingsymbol, reason)
        return PlaceOutcome(reason=reason)
    reference = await _reference_price(client, tradingsymbol, exchange, last_price)
    if not reference:
        log.warning("kite protective GTT refused for %s: fresh reference quote unavailable", tradingsymbol)
        return PlaceOutcome(reason="fresh reference quote unavailable")
    if getattr(client, "_is_paper", None) is False and stop_is_breached(trigger_premium, reference, direction):
        log.warning("kite protective GTT refused for %s: stop already breached; exit reconciliation required", tradingsymbol)
        return PlaceOutcome(reason="stop already breached")
    request = build_gtt_request(
        tradingsymbol=tradingsymbol, exchange=exchange, qty=qty,
        trigger_premium=trigger_premium, last_price=reference,
        direction=direction, target_premium=target_premium,
        product=resolved_product, tick_size=tick_size,
    )
    if getattr(client, "_is_paper", None) is False and protection_is_breached(request, reference, direction):
        log.warning("kite protective GTT refused for %s: rounded stop or target already breached; exit reconciliation required", tradingsymbol)
        return PlaceOutcome(reason="rounded stop or target already breached")
    if before_submit is not None:
        try:
            before_submit()
        except Exception as exc:  # noqa: BLE001
            # The fence could not be latched, so nothing was sent.
            log.warning("kite protective GTT not sent for %s: pre-submit fence failed: %s",
                        tradingsymbol, exc)
            return PlaceOutcome(reason=f"pre-submit fence failed: {exc}")
    try:
        res = await client.place_gtt(**request)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "kite protective GTT place failed for %s @ %.2f (target %.2f): %s",
            tradingsymbol, trigger_premium, target_premium or 0.0, exc,
        )
        definitive = is_definitive_refusal(exc)
        if definitive and on_refused is not None:
            try:
                on_refused()
            except Exception as hook_exc:  # noqa: BLE001
                log.warning("kite protective GTT refusal hook failed for %s: %s",
                            tradingsymbol, hook_exc)
        return PlaceOutcome(ambiguous=not definitive, reason=str(exc) or type(exc).__name__)
    try:
        tid = int((res or {}).get("trigger_id") or 0)
    except (TypeError, ValueError):
        tid = 0
    if tid:
        return PlaceOutcome(trigger_id=tid)
    return PlaceOutcome(ambiguous=True, reason="broker reply carried no trigger id")


async def place_stop(client, *, tradingsymbol: str, exchange: str, qty: int,
                     trigger_premium: float, last_price: float,
                     direction: str = "long",
                     target_premium: float = 0.0,
                     product: str = "", tick_size: float = 0.05,
                     before_submit: Optional[Callable[[], None]] = None,
                     on_refused: Optional[Callable[[], None]] = None) -> Optional[int]:
    """Place a broker-side exit only after fresh inventory proof.

    Returns the trigger id, or None. A None does NOT always mean nothing was
    created: callers that retry placement must use ``place_stop_outcome`` and
    respect ``ambiguous``.
    """
    outcome = await place_stop_outcome(
        client, tradingsymbol=tradingsymbol, exchange=exchange, qty=qty,
        trigger_premium=trigger_premium, last_price=last_price,
        direction=direction, target_premium=target_premium, product=product,
        tick_size=tick_size, before_submit=before_submit, on_refused=on_refused,
    )
    return outcome.trigger_id


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
STOP_TRIGGERED = "triggered"    # it fired AND its child order is COMPLETE: the exit is done
STOP_TRIGGERED_RESTING = "triggered_resting"  # fired; child live (or not yet visible) inside
                                # the grace window — stand down, ask again
STOP_TRIGGERED_STALE = "triggered_stale"      # fired; child live but older than the grace
                                # window or unfillable at the reference price — the caller
                                # cancels the child, waits for a terminal state, then sells
STOP_ABSENT = "absent"          # cancelled / deleted / expired / disabled / unknown to the
                                # broker, or fired with a REJECTED/CANCELLED/never-placed
                                # child → NOTHING there will ever exit this position
STOP_UNVERIFIED = "unverified"  # we could not ask — must be treated as "may still be armed"

#: Every verdict that means "the trigger fired", whatever became of its child order.
FIRED_VERDICTS = frozenset({STOP_TRIGGERED, STOP_TRIGGERED_RESTING, STOP_TRIGGERED_STALE})

#: The state of the order a fired GTT submitted.
CHILD_FILLED = "filled"
CHILD_RESTING = "resting"
CHILD_REJECTED = "rejected"     # REJECTED/CANCELLED, or the GTT reports its placement failed
CHILD_MISSING = "missing"       # no such order in a readable book
CHILD_UNKNOWN = "unknown"       # could not be established

#: How long a fired trigger's child may rest before it is called stale. A limit two
#: percent through the trigger fills in seconds in a liquid option; in a gap it never
#: will, and every second spent waiting is a second unprotected.
TRIGGER_CHILD_GRACE_S = 30.0

#: The only two GTT statuses under which the broker still acts for us.
_FIRED = "__fired__"
_ACTING_STATUSES = {"active": STOP_ACTIVE, "triggered": _FIRED}
#: The statuses Kite documents for a trigger that will never act again.
_INERT_STATUSES = frozenset({"cancelled", "canceled", "deleted", "expired", "rejected",
                             "disabled"})
#: Order-book statuses that mean the exit order will never fill. Everything else —
#: COMPLETE, OPEN, TRIGGER PENDING, VALIDATION PENDING, PUT ORDER REQ RECEIVED — is a
#: sell that has filled or is still working, and we must not place a second one.
_DEAD_ORDER_STATUSES = frozenset({"REJECTED", "CANCELLED", "CANCELED"})
#: ``orders[].result.order_result.status`` values for a placement the broker refused.
_GTT_RESULT_FAILED = frozenset({"failed", "failure", "rejected", "error", "cancelled",
                                "canceled"})

#: Internal: the broker's GTT book provably does not hold this trigger. Not a verdict on
#: its own — a trigger vanishes both when it is deleted (nothing will exit us) and,
#: possibly, once it has fired (a SELL exists). The order book separates the two.
_NOT_ON_BOOK = "__not_on_book__"

_IST = timezone(timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class StopState:
    """The verdict of ``stop_status`` plus what is known about a fired trigger's child.

    ``child_*`` fields are populated only for the triggered-family verdicts (and for a
    trigger the book no longer holds whose exit order was found).
    """

    verdict: str
    child_state: str = ""
    child_order_id: str = ""
    child_price: float = 0.0
    child_quantity: int = 0
    child_filled_quantity: int = 0
    child_age_s: Optional[float] = None
    detail: str = ""

    @property
    def fired(self) -> bool:
        return self.verdict in FIRED_VERDICTS


def _classify(status: str) -> str:
    """A single GTT status → verdict (``_FIRED`` for a triggered one, resolved against
    its child order by the caller). An unrecognised status is UNVERIFIED, never
    ABSENT: the trigger demonstrably exists at the broker, so we cannot rule out that
    it is going to fire, and guessing ABSENT here is the one mistake that sells twice."""
    s = (status or "").strip().lower()
    if s in _ACTING_STATUSES:
        return _ACTING_STATUSES[s]
    if s in _INERT_STATUSES:
        return STOP_ABSENT
    return STOP_UNVERIFIED


def _json_field(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _parse_ts(raw) -> Optional[datetime]:
    """A Kite timestamp as an aware datetime. Naive stamps are IST wall-clock."""
    if raw in (None, ""):
        return None
    try:
        when = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).strip())
    except ValueError:
        return None
    return when if when.tzinfo is not None else when.replace(tzinfo=_IST)


def _status_from_trigger(data) -> tuple[Optional[str], Optional[dict]]:
    if not isinstance(data, dict):
        return None, None
    status = str(data.get("status") or "").strip()
    return (_classify(status), data) if status else (None, None)


async def _read_trigger(client, trigger_id: int) -> tuple[Optional[str], Optional[dict]]:
    """``GET /gtt/triggers/{id}``. ``(verdict, row)``; the verdict is ``_NOT_ON_BOOK`` on
    a 404 and None when the answer was inconclusive (call failed for any reason other
    than 404, empty envelope)."""
    try:
        data = await client.get_gtt(int(trigger_id))
    except Exception as exc:  # noqa: BLE001
        if int(getattr(exc, "status_code", 0) or 0) == 404:
            return _NOT_ON_BOOK, None
        log.warning("kite protective GTT #%s status check failed: %s", trigger_id, exc)
        return None, None
    return _status_from_trigger(data)


async def _read_listed(client, trigger_id: int) -> tuple[Optional[str], Optional[dict]]:
    """``GET /gtt/triggers`` as the second opinion, because Kite does not promise a 404
    for a trigger it no longer holds — a 400/``InputException`` is just as likely, and
    reading ABSENT out of an error *message* is the guess this module refuses to make.
    Absence from a list we successfully read is hard evidence; an error is not.

    Returns ``(verdict, row)``; the verdict is ``_NOT_ON_BOOK`` when the list was read
    and does not hold the trigger, None when the list could not be read."""
    try:
        rows = await client.get_gtts()
    except Exception as exc:  # noqa: BLE001
        log.warning("kite protective GTT #%s list check failed: %s", trigger_id, exc)
        return None, None
    if not isinstance(rows, list):
        return None, None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if int(row.get("id") or row.get("trigger_id") or 0) != int(trigger_id):
            continue
        status = str(row.get("status") or "").strip()
        return (_classify(status) if status else STOP_UNVERIFIED), row
    return _NOT_ON_BOOK, None


def _order_timestamp(order: dict) -> Optional[datetime]:
    for key in ("exchange_timestamp", "order_timestamp"):
        stamp = _parse_ts(order.get(key))
        if stamp is not None:
            return stamp
    return None


def _to_int(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _unfillable(price: float, reference: float, direction: str) -> bool:
    """A SELL limit above the market (a BUY limit below it) cannot fill."""
    if price <= 0 or reference <= 0:
        return False
    return price > reference if direction == "long" else price < reference


def _state_from_order(order: dict, *, direction: str, now: datetime,
                      fallback_at: Optional[datetime], reference_price: float,
                      grace_s: float) -> StopState:
    """Classify the order a fired trigger submitted."""
    status = str(order.get("status") or "").strip().upper()
    oid = str(order.get("order_id") or "").strip()
    price = 0.0
    try:
        price = float(order.get("price") or 0.0)
    except (TypeError, ValueError):
        price = 0.0
    qty = _to_int(order.get("quantity"))
    filled = _to_int(order.get("filled_quantity"))
    common = dict(child_order_id=oid, child_price=price, child_quantity=qty,
                  child_filled_quantity=filled)
    if status == "COMPLETE":
        return StopState(STOP_TRIGGERED, CHILD_FILLED, **common)
    if status in _DEAD_ORDER_STATUSES:
        return StopState(STOP_ABSENT, CHILD_REJECTED,
                         detail=str(order.get("status_message") or status), **common)
    started = _order_timestamp(order) or fallback_at
    age = max(0.0, (now - started).total_seconds()) if started is not None else None
    if (age is not None and age >= grace_s) or _unfillable(price, reference_price, direction):
        return StopState(STOP_TRIGGERED_STALE, CHILD_RESTING, child_age_s=age, **common)
    return StopState(STOP_TRIGGERED_RESTING, CHILD_RESTING, child_age_s=age, **common)


def _child_hint(row: Optional[dict]) -> dict:
    """What the GTT itself says about the order it placed when it fired
    (``orders[].result.order_result``): order id, status, rejection reason."""
    hint = {"found": False, "order_id": "", "rejected": False, "reason": "",
            "status": "", "at": None}
    if not isinstance(row, dict):
        return hint
    legs = _json_field(row.get("orders"))
    for leg in legs if isinstance(legs, list) else []:
        if not isinstance(leg, dict):
            continue
        result = _json_field(leg.get("result"))
        if not isinstance(result, dict) or not result:
            continue
        inner = _json_field(result.get("order_result"))
        inner = inner if isinstance(inner, dict) else {}
        status = str(inner.get("status") or "").strip().lower()
        reason = str(inner.get("rejection_reason") or "").strip()
        hint.update(
            found=True,
            order_id=str(inner.get("order_id") or "").strip(),
            reason=reason,
            status=status,
            at=_parse_ts(result.get("timestamp")),
        )
        hint["rejected"] = bool(reason) or status in _GTT_RESULT_FAILED
        return hint
    return hint


def _match_exit_order(
    orders: list, *, tradingsymbol: str, direction: str, exchange: str, product: str,
    qty: int, after: Optional[datetime] = None, ignore_order_ids: frozenset = frozenset(),
) -> tuple[str, Optional[dict]]:
    """Find the exit order for this exact broker position bucket in a readable book.

    ``("found", row)``, ``("none", None)`` or ``("unknown", None)``. A symbol-only match
    is not evidence: the same contract can coexist across exchange/product buckets and
    another round-trip can have the same exit side, so a candidate needs the complete
    identity. An incomplete candidate row is "unknown", never "none". When ``after``
    (the trigger time) is known, a row stamped at or after it also matches on a
    different quantity: the child is sized for the GTT, which can differ from what we
    hold now. COMPLETE wins over a working order.
    """
    want_sym = str(tradingsymbol or "").strip().upper()
    want_exchange = str(exchange or "").strip().upper()
    want_product = str(product or "").strip().upper()
    want_qty = _to_int(qty)
    if not want_sym or not want_exchange or not want_product or want_qty <= 0:
        return "unknown", None
    want_txn = K.TXN_SELL if direction == "long" else K.TXN_BUY
    live: Optional[dict] = None
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
        if str(o.get("order_id") or "").strip() in ignore_order_ids:
            continue
        row_exchange = str(o.get("exchange") or "").strip().upper()
        row_product = str(o.get("product") or "").strip().upper()
        try:
            row_qty = int(float(o.get("quantity")))
        except (TypeError, ValueError):
            # This could be our exit, but its identity is incomplete. Do not
            # manufacture ABSENT from an unreadable candidate.
            return "unknown", None
        if not row_exchange or not row_product:
            return "unknown", None
        if row_exchange != want_exchange or row_product != want_product:
            continue
        if row_qty != want_qty:
            stamp = _order_timestamp(o)
            if after is None or stamp is None or stamp < after - timedelta(seconds=10):
                continue
        if str(o.get("status") or "").strip().upper() == "COMPLETE":
            return "found", o
        live = live or o
    return ("found", live) if live is not None else ("none", None)


async def _exit_order_is_working(
    client,
    tradingsymbol: str,
    direction: str,
    *,
    exchange: str = "",
    product: str = "",
    qty: int = 0,
) -> Optional[bool]:
    """Is an exit order working (or filled) for this exact broker position bucket.

    The fallback that decides whether Sterling may place its own SELL requires the
    complete position identity: exchange + symbol + product + direction + quantity.
    Missing or malformed identity is UNVERIFIED upstream.
    """
    try:
        orders = await client.get_orders()
    except Exception as exc:  # noqa: BLE001
        log.warning("kite order-book check for %s failed: %s", tradingsymbol, exc)
        return None
    if not isinstance(orders, list):
        return None
    flag, _row = _match_exit_order(
        orders, tradingsymbol=tradingsymbol, direction=direction, exchange=exchange,
        product=product, qty=qty,
    )
    return None if flag == "unknown" else flag == "found"


async def _order_from_history(client, order_id: str) -> Optional[dict]:
    reader = getattr(client, "get_order_history", None)
    if not callable(reader):
        return None
    try:
        history = await reader(order_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("kite order history for %s failed: %s", order_id, exc)
        return None
    if not isinstance(history, list) or not history:
        return None
    last = history[-1]
    return last if isinstance(last, dict) else None


async def _resolve_fired(client, row: Optional[dict], *, tradingsymbol: str,
                         direction: str, exchange: str, product: str, qty: int,
                         reference_price: float, grace_s: float, now: datetime,
                         ignore_order_ids: frozenset) -> StopState:
    """A trigger with status ``triggered`` has only fired. Say what became of its child."""
    hint = _child_hint(row)
    trigger_at = hint["at"] or _parse_ts((row or {}).get("updated_at"))
    if hint["rejected"]:
        return StopState(
            STOP_ABSENT, CHILD_REJECTED, child_order_id=hint["order_id"],
            detail=hint["reason"] or f"GTT placement {hint['status']}",
        )
    try:
        book = await client.get_orders()
    except Exception as exc:  # noqa: BLE001
        log.warning("kite order-book check for %s failed: %s", tradingsymbol, exc)
        book = None
    if not isinstance(book, list):
        return StopState(STOP_UNVERIFIED, CHILD_UNKNOWN, child_order_id=hint["order_id"],
                         detail="order book unreadable")

    common = dict(direction=direction, now=now, fallback_at=trigger_at,
                  reference_price=reference_price, grace_s=grace_s)
    if hint["order_id"]:
        order = next(
            (o for o in book if isinstance(o, dict)
             and str(o.get("order_id") or "").strip() == hint["order_id"]),
            None,
        )
        if order is None:
            order = await _order_from_history(client, hint["order_id"])
        if order is None:
            return StopState(STOP_UNVERIFIED, CHILD_UNKNOWN, child_order_id=hint["order_id"],
                             detail="child order not found")
        return _state_from_order({"order_id": hint["order_id"], **order}, **common)

    # The trigger carries no child result: fall back to the order book.
    flag, order = _match_exit_order(
        book, tradingsymbol=tradingsymbol, direction=direction, exchange=exchange,
        product=product, qty=qty, after=trigger_at, ignore_order_ids=ignore_order_ids,
    )
    if flag == "unknown":
        # No identity to attribute an order with. The trigger fired; whether its child
        # filled is unproven. Stand down, but do not call the position protected.
        return StopState(STOP_TRIGGERED_RESTING, CHILD_UNKNOWN,
                         detail="child order cannot be attributed")
    if flag == "found" and order is not None:
        return _state_from_order(order, **common)
    age = max(0.0, (now - trigger_at).total_seconds()) if trigger_at is not None else None
    if age is not None and age >= grace_s:
        return StopState(STOP_ABSENT, CHILD_MISSING, child_age_s=age,
                         detail="no child order appeared after the trigger fired")
    return StopState(STOP_TRIGGERED_RESTING, CHILD_MISSING, child_age_s=age,
                     detail="child order not visible yet")


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
        if verdict == _FIRED:
            verdict = STOP_TRIGGERED  # fired; the child is not read for an unattributed row
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


async def stop_state(client, trigger_id: int, *, tradingsymbol: str = "",
                     direction: str = "long", exchange: str = "",
                     product: str = "", qty: int = 0, reference_price: float = 0.0,
                     grace_s: Optional[float] = None, now: Optional[datetime] = None,
                     ignore_order_ids: frozenset = frozenset()) -> StopState:
    """``stop_status`` with the child order's state attached. See ``stop_status``.

    ``reference_price`` (the live premium) lets a caller get a child whose limit can no
    longer fill classified STALE immediately instead of after the grace window.
    ``ignore_order_ids`` excludes the caller's own exit orders from child attribution.
    """
    if trigger_id <= 0:
        return StopState(STOP_ABSENT)
    grace = TRIGGER_CHILD_GRACE_S if grace_s is None else float(grace_s)
    moment = now or datetime.now(_IST)
    verdict, row = await _read_trigger(client, trigger_id)
    if verdict is None:
        verdict, row = await _read_listed(client, trigger_id)
    if verdict is None:
        return StopState(STOP_UNVERIFIED)
    if verdict == _FIRED:
        return await _resolve_fired(
            client, row, tradingsymbol=tradingsymbol, direction=direction,
            exchange=exchange, product=product, qty=qty,
            reference_price=reference_price, grace_s=grace, now=moment,
            ignore_order_ids=ignore_order_ids,
        )
    if verdict != _NOT_ON_BOOK:
        return StopState(verdict)
    try:
        orders = await client.get_orders()
    except Exception as exc:  # noqa: BLE001
        log.warning("kite order-book check for %s failed: %s", tradingsymbol, exc)
        return StopState(STOP_UNVERIFIED)
    if not isinstance(orders, list):
        return StopState(STOP_UNVERIFIED)
    flag, order = _match_exit_order(
        orders, tradingsymbol=tradingsymbol, direction=direction, exchange=exchange,
        product=product, qty=qty, ignore_order_ids=ignore_order_ids,
    )
    if flag == "unknown":
        return StopState(STOP_UNVERIFIED)
    if flag == "none" or order is None:
        return StopState(STOP_ABSENT)
    return _state_from_order(
        order, direction=direction, now=moment, fallback_at=None,
        reference_price=reference_price, grace_s=grace,
    )


async def stop_status(client, trigger_id: int, *, tradingsymbol: str = "",
                      direction: str = "long", exchange: str = "",
                      product: str = "", qty: int = 0, reference_price: float = 0.0) -> str:
    """Ask the broker what actually became of GTT ``trigger_id``.

    Two ordinary events leave a trigger absent WITHOUT the position having exited: the
    user deletes or edits it in the Kite app, or it fired and its LIMIT child was
    rejected so the trigger is consumed with no fill. A third leaves it ``triggered``
    with the child resting unfilled in a gap. In all three, a caller that "leaves the
    exit to the broker" waits forever.

    Three sources, cheapest first, and each one only ever narrows the answer:

    1. the trigger itself — an acting or documented-inert status ends it, except that
       ``triggered`` only means it fired: the child order is read next (the trigger's
       own ``orders[].result.order_result``, else the order book) and the verdict is
       ``STOP_TRIGGERED`` (child COMPLETE), ``STOP_TRIGGERED_RESTING`` /
       ``STOP_TRIGGERED_STALE`` (child live) or ``STOP_ABSENT`` (child REJECTED /
       CANCELLED / never appeared after the grace window);
    2. the trigger list, when (1) errors or comes back empty — Kite's error code for an
       unknown trigger is not something this repo can pin, so absence from a list we did
       read replaces trusting a 404;
    3. the order book, once (1) or (2) prove the trigger is not there — the only source
       that distinguishes "deleted, nothing will exit us" from "fired, a SELL exists".

    ABSENT — the verdict that lets a caller place its own exit — therefore requires
    positive evidence, because a wrong ABSENT sells on top of a live broker SELL and
    goes NAKED SHORT, whereas a wrong UNVERIFIED costs at worst a delayed exit the
    caller is told about. Pass the full broker bucket (exchange, tradingsymbol,
    product, direction, quantity): without exact identity the order book cannot safely
    attribute an order and a missing trigger remains UNVERIFIED. Use ``stop_state`` for
    the child order's id, price and filled quantity.
    """
    return (await stop_state(
        client, trigger_id, tradingsymbol=tradingsymbol, direction=direction,
        exchange=exchange, product=product, qty=qty, reference_price=reference_price,
    )).verdict


# ---------------------------------------------------------------------------
# A fired trigger whose LIMIT child rests unfilled
# ---------------------------------------------------------------------------
#: How a stale child ended once the cancel went out.
CANCEL_CLEARED = "cleared"          # REJECTED/CANCELLED: it will never fill any more
CANCEL_COMPLETED = "completed"      # COMPLETE: it filled before the cancel landed - the exit is done
CANCEL_UNCONFIRMED = "unconfirmed"  # not provably terminal: nothing may be sold over it

#: After the cancel request the child is re-read until it is terminal.
CHILD_CANCEL_POLLS = 4
CHILD_CANCEL_POLL_S = 0.5
_CHILD_TERMINAL = frozenset({"COMPLETE", "REJECTED", "CANCELLED", "CANCELED"})


@dataclass(frozen=True)
class ChildCancel:
    """What became of a fired trigger's child order after it was cancelled.

    ``filled_quantity`` / ``average_price`` are the child's final cumulative fill: a
    part-filled child that was cancelled really sold that much, and the caller must
    book it before it sells what the broker still holds.
    """

    outcome: str
    order_id: str = ""
    status: str = ""
    filled_quantity: int = 0
    average_price: float = 0.0
    order: Optional[dict] = None


async def cancel_fired_child(client, state: StopState, *, tradingsymbol: str) -> ChildCancel:
    """Cancel the resting LIMIT child of a fired trigger and confirm it is terminal.

    ``state`` is the ``stop_state`` of a ``STOP_TRIGGERED_STALE`` verdict (or a
    ``STOP_TRIGGERED_RESTING`` one whose caller does not wait out the grace window,
    e.g. an operator flatten). The cancel is the ONLY thing this does: it never
    sells. The final state is read back from the broker; only a terminal read is
    trusted, because a cancel that raised may still have landed - or the child may
    have filled first - and a SELL placed over a child that is still working sells
    the position twice.

    ``CANCEL_CLEARED``    the child is REJECTED/CANCELLED; book any part-fill, then sell
                          only what the broker still holds through the normal exit.
    ``CANCEL_COMPLETED``  the child filled first: the exit is done, stand down.
    ``CANCEL_UNCONFIRMED`` anything else (no child id, the book unreadable, still working
                          after the polls): alert and hold.
    """
    oid = str(state.child_order_id or "").strip()
    if not oid or state.child_state != CHILD_RESTING:
        return ChildCancel(CANCEL_UNCONFIRMED, order_id=oid)
    from app.services.snapback_family_mode import INTENT_CANCEL, canonical_broker_capability

    account = str(getattr(client, "_account_id", "") or "paper")
    try:
        with canonical_broker_capability(
            f"cancel-stop-child:{account}:{tradingsymbol}:{oid}", intent=INTENT_CANCEL,
        ):
            await client.cancel_order(oid)
    except Exception as exc:  # noqa: BLE001
        # It may have filled or been cancelled meanwhile; the order history decides.
        log.warning("kite stop child %s cancel raised for %s: %s", oid, tradingsymbol, exc)
    final: Optional[dict] = None
    for attempt in range(max(1, CHILD_CANCEL_POLLS)):
        order = await _order_from_history(client, oid)
        if (isinstance(order, dict) and str(order.get("order_id") or oid).strip() == oid
                and str(order.get("status") or "").strip().upper() in _CHILD_TERMINAL):
            final = order
            break
        if attempt + 1 < CHILD_CANCEL_POLLS:
            await asyncio.sleep(CHILD_CANCEL_POLL_S)
    if final is None:
        return ChildCancel(CANCEL_UNCONFIRMED, order_id=oid)
    status = str(final.get("status") or "").strip().upper()
    filled = _to_int(final.get("filled_quantity"))
    try:
        average = float(final.get("average_price") or 0.0)
    except (TypeError, ValueError):
        average = 0.0
    outcome = CANCEL_COMPLETED if status == "COMPLETE" else CANCEL_CLEARED
    return ChildCancel(outcome, order_id=oid, status=status, filled_quantity=filled,
                       average_price=average, order=dict(final))
