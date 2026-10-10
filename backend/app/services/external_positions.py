"""Positions the broker holds that Sterling did not open.

Sterling's own stores said the account was flat: no intent, no fill, no registry
entry. The broker said otherwise — 16625 of a CDSL call, bought overnight. Both
were telling the truth about different things, and the dangerous reading is the
local one, because "nothing in my journal" is not "the account is flat".

So a broker position with no matching Sterling record is recorded as what it is:
EXTERNAL_UNMANAGED. Not imported, not adopted, not given an invented intent or
fill. Importing it as though Sterling had opened it would corrupt the provenance
of every number downstream — the promotion gate would count a trade nobody's
strategy made, with an entry price nobody's rule chose.

An external position is exposure. It blocks the flatness gate, it appears in the
digest, and it prevents a start sequence from declaring the machine CLEAN. What
it does NOT do is become Sterling's to manage: no protection is inferred, no
stop is armed, nothing is sold. Whether to hold or exit it is a trading
decision, and this module deliberately cannot make one.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from app.services.simulation import identity as _sim_identity

log = logging.getLogger(__name__)


def _is_virtual(account: Any) -> bool:
    """The virtual Simulation account: it never holds, or reports, real broker exposure."""
    return bool(
        _sim_identity.is_sim_account_id(str(getattr(account, "id", "") or ""))
        or _sim_identity.is_sim_uid(getattr(account, "user_id", ""))
    )

__all__ = [
    "ExternalPosition", "ExternalExposure", "classify_broker_positions",
    "external_exposure", "owned_gtt_ids", "render_external", "SOURCE", "DISCOVERY_REASON",
]

SOURCE: str = "BROKER_EXTERNAL"
DISCOVERY_REASON: str = "RECONCILIATION_UNKNOWN_POSITION"


@dataclass(frozen=True)
class ExternalPosition:
    """A live broker position with no Sterling intent behind it."""

    account: str
    instrument: str
    quantity: int
    product: str = ""
    exchange: str = ""
    broker_avg_price: float | None = None
    last_price: float | None = None
    unrealised: float | None = None
    observed_at: str = ""

    #: Fixed provenance. These are not booleans a later caller may flip: they
    #: describe how the row entered the system, and it never entered any other way.
    source: str = SOURCE
    managed_by_sterling: bool = False
    discovery_reason: str = DISCOVERY_REASON
    protection_known: bool = False
    sterling_intent: str = "none"
    sterling_fill: str = "none"

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "managed_by_sterling": self.managed_by_sterling,
            "account": self.account,
            "instrument": self.instrument,
            "quantity": self.quantity,
            "product": self.product,
            "exchange": self.exchange,
            "broker_avg_price": self.broker_avg_price,
            "last_price": self.last_price,
            "unrealised": self.unrealised,
            "discovery_reason": self.discovery_reason,
            "protection_known": self.protection_known,
            "sterling_intent": self.sterling_intent,
            "sterling_fill": self.sterling_fill,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True)
class ExternalExposure:
    """What the broker holds that Sterling did not open. None means unreadable."""

    positions: tuple[ExternalPosition, ...] = ()
    #: None when the broker could not be asked. Never an empty list in that case.
    readable: bool = True
    detail: str = ""
    #: Internal Sterling account ids actually queried for this observation.
    #: Flat snapshots have no position rows from which identity can be inferred.
    #: Kept after the original fields so positional construction remains compatible.
    accounts: tuple[str, ...] = ()
    #: Non-position broker state that can still create/change exposure later and
    #: that Sterling cannot attribute to itself: foreign open orders, foreign
    #: active GTTs and executable ATO alerts. Sterling's own protective GTTs and
    #: working orders are NOT listed here (see ``owned_automation``): a stop the
    #: system armed on its own position is protection, not a hazard.
    automation: tuple[str, ...] = ()
    #: Broker orders/GTTs that Sterling attributed to its own positions or intents.
    #: Informational only; never counted as exposure and never blocks admission.
    owned_automation: tuple[str, ...] = ()
    #: One entry per live account that was asked, readable or not:
    #: ``{"account", "readable", "count", "instruments", "automation", "detail"}``.
    #: Admission for an account reads that account's entry, so a tenant whose
    #: broker session is missing cannot gate another tenant's entries.
    by_account: tuple[dict[str, Any], ...] = ()

    @property
    def count(self) -> int | None:
        return len(self.positions) if self.readable else None

    @property
    def flat(self) -> bool | None:
        """True only when the broker was asked and holds nothing unmanaged."""
        if not self.readable:
            return None
        return not self.positions and not self.automation

    def as_dict(self) -> dict[str, Any]:
        return {"readable": self.readable, "count": self.count, "flat": self.flat,
                "detail": self.detail, "accounts": list(self.accounts),
                "automation": list(self.automation),
                "owned_automation": list(self.owned_automation),
                "positions": [p.as_dict() for p in self.positions]}


@dataclass(frozen=True)
class _LocalOwnership:
    """Everything Sterling's own stores say belongs to one broker account."""

    #: Exact (exchange, product, tradingsymbol) identities of open positions and
    #: unresolved intents.
    identities: frozenset[tuple[str, str, str]] = frozenset()
    #: Broker GTT trigger ids Sterling armed (protective stops/targets).
    gtt_ids: frozenset[str] = frozenset()
    #: Broker order ids Sterling placed (entry, exit, protection orders).
    order_ids: frozenset[str] = frozenset()


def owned_gtt_ids(account_id: str) -> frozenset[str] | None:
    """Every broker GTT id Sterling already owns on this account.

    Recovery code that lost the acknowledgement for a new protective stop may
    search the broker trigger book by symbol/quantity/level. Those fields are
    not ownership proof: two release strategies can legitimately hold the same
    contract with identical stops. A trigger already recorded by any Sterling
    position therefore belongs to that owner and must never be adopted, modified
    or cancelled by another strategy.

    None means local ownership state could not be read. Callers must keep their
    uncertainty fence engaged rather than guessing.
    """
    ownership = _scan_local_ownership(str(account_id or "").strip())
    return None if ownership is None else ownership.gtt_ids

def _clean_id(value: Any) -> str:
    """A broker/Sterling id as a comparable string; zero/blank mean "none"."""
    text = str(value if value is not None else "").strip()
    return "" if text in ("", "0", "None") else text


def _scan_local_ownership(account_id: str) -> _LocalOwnership | None:
    """Read every Sterling store once and report what it owns on this account.

    Production attribution is never symbol-only. The same contract can exist in
    MIS and NRML buckets, and a process may know positions for several accounts.
    Returning an exact (exchange, product, symbol) set prevents either collision
    from hiding external exposure. Broker ids recorded on Sterling's positions
    and intents (the protective GTT id, entry/exit/protection order ids) are
    collected in the same pass so a Sterling-armed stop is recognised as the
    system's own protection rather than as foreign automation.

    ``None`` means a store could not be read: nothing can then be attributed,
    which keeps every broker row a hazard (fail closed).
    """
    try:
        from app.services import (
            adaptive_edge_positions,
            db,
            gamma_move_positions,
            intraday_positions,
            snapback_live_positions,
            price_rules_store,
        )
        from app.services.kite_engine import order_journal, positions

        db.init()
        wanted_account = str(account_id or "").strip()
        if not wanted_account:
            return None

        # Discover users from every durable namespace. An unresolved canonical
        # intent can exist before position projection, while a legacy engine can
        # own a position without ever creating a Kite-engine registry row.
        uids = set(positions.known_uids())
        uids.update(order_journal.known_uids())
        uids.update(snapback_live_positions.known_uids())
        uids.update(price_rules_store.known_uids())
        for key in db.get_all_configs_strict():
            for prefix in (
                "intraday_positions_",
                "gamma_move_positions_",
                "adaptive_edge_positions:",
            ):
                if key.startswith(prefix):
                    uid = key[len(prefix):].strip()
                    if uid:
                        uids.add(uid)
                    break

        known: set[tuple[str, str, str]] = set()
        gtt_ids: set[str] = set()
        order_ids: set[str] = set()

        def _remember_ids(owner: Any) -> None:
            gtt = _clean_id(getattr(owner, "gtt_id", None))
            if gtt:
                gtt_ids.add(gtt)
            for attr in ("order_id", "exit_order_id", "protection_order_id"):
                oid = _clean_id(getattr(owner, attr, None))
                if oid:
                    order_ids.add(oid)
            by_order = getattr(owner, "qty_by_order", None)
            if isinstance(by_order, Mapping):
                for oid in by_order:
                    oid = _clean_id(oid)
                    if oid:
                        order_ids.add(oid)

        for uid in sorted(uids):
            for position in price_rules_store.open_positions(uid):
                if position.account_id == wanted_account:
                    known.add((position.exchange, position.product, position.symbol))
                    _remember_ids(position)
            for position in positions.open_positions(uid):
                if str(getattr(position, "account_id", "") or "").strip() != wanted_account:
                    continue
                exchange = str(getattr(position, "exchange", "") or "").strip().upper()
                product = str(getattr(position, "product", "") or "").strip().upper()
                symbol = str(getattr(position, "symbol", "") or "").strip().upper()
                if exchange and product and symbol:
                    known.add((exchange, product, symbol))
                _remember_ids(position)

            for intent in order_journal.unresolved(uid, account_id=wanted_account):
                exchange = str(getattr(intent, "exchange", "") or "").strip().upper()
                symbol = str(getattr(intent, "symbol", "") or "").strip().upper()
                payload = getattr(intent, "payload", {}) or {}
                product = str(payload.get("product") or "NRML").strip().upper()
                if exchange and product and symbol:
                    known.add((exchange, product, symbol))
                oid = _clean_id(getattr(intent, "order_id", None))
                if oid:
                    order_ids.add(oid)

            # The intraday and adaptive-edge engines are MIS-only; Gamma below
            # carries its own bucket. Blank legacy owner ids remain untrusted and
            # therefore are NOT attributed here.
            for position in intraday_positions.open_positions(uid):
                if str(getattr(position, "account_id", "") or "").strip() != wanted_account:
                    continue
                contract = getattr(position, "contract", None)
                exchange = str(getattr(contract, "exchange", "") or "").strip().upper()
                symbol = str(getattr(contract, "tradingsymbol", "") or "").strip().upper()
                if exchange and symbol:
                    known.add((exchange, "MIS", symbol))
                _remember_ids(position)

            for position in adaptive_edge_positions.open_positions(uid):
                if str(getattr(position, "account_id", "") or "").strip() != wanted_account:
                    continue
                exchange = str(getattr(position, "exchange", "") or "").strip().upper()
                symbol = str(getattr(position, "symbol", "") or "").strip().upper()
                if exchange and symbol:
                    known.add((exchange, "MIS", symbol))
                _remember_ids(position)

            for position in gamma_move_positions.open_positions(uid):
                if str(getattr(position, "account_id", "") or "").strip() != wanted_account:
                    continue
                instrument = getattr(position, "instrument", None)
                exchange = str(getattr(instrument, "exchange", "") or "").strip().upper()
                symbol = str(getattr(instrument, "tradingsymbol", "") or "").strip().upper()
                # Gamma trades NRML (so a broker GTT can be armed); a pre-upgrade
                # intraday row persists MIS. Attribute the bucket the position
                # actually holds, never a hard-coded one.
                product = str(getattr(position, "product", "") or "NRML").strip().upper()
                if exchange and symbol:
                    known.add((exchange, product, symbol))
                _remember_ids(position)

            # Snapback's own live book: the bought option AND its futures hedge are
            # both Sterling's, in the NRML bucket the strategy trades.
            for position in snapback_live_positions.open_positions(uid):
                if str(getattr(position, "account_id", "") or "").strip() != wanted_account:
                    continue
                product = str(getattr(position, "product", "") or "NRML").strip().upper()
                for exchange, symbol in (
                    (getattr(position, "option_exchange", ""), getattr(position, "option_symbol", "")),
                    (getattr(position, "hedge_exchange", ""), getattr(position, "hedge_symbol", "")),
                ):
                    exchange = str(exchange or "").strip().upper()
                    symbol = str(symbol or "").strip().upper()
                    if exchange and symbol:
                        known.add((exchange, product, symbol))
                _remember_ids(position)
        return _LocalOwnership(
            identities=frozenset(known),
            gtt_ids=frozenset(gtt_ids),
            order_ids=frozenset(order_ids),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("external positions: local state unreadable: %s", exc)
        return None


def _known_symbols(account_id: str) -> set[tuple[str, str, str]] | None:
    """Exact broker identities Sterling can attribute to one account."""
    owned = _scan_local_ownership(account_id)
    return None if owned is None else set(owned.identities)


def _known_broker_ids(account_id: str) -> tuple[frozenset[str], frozenset[str]] | None:
    """(GTT ids, order ids) Sterling recorded for one account, or None if unreadable."""
    owned = _scan_local_ownership(account_id)
    return None if owned is None else (owned.gtt_ids, owned.order_ids)


def classify_broker_positions(
    rows: Iterable[Mapping[str, Any]],
    *,
    account: str,
    known_symbols: Sequence[Any] | set[Any] | None,
    observed_at: str | None = None,
) -> ExternalExposure:
    """Split broker positions into ones Sterling knows and ones it does not."""
    if known_symbols is None:
        return ExternalExposure(
            readable=False,
            detail="Sterling's own position and intent stores could not be read, so "
                   "no broker position can be attributed or excluded")

    stamp = observed_at or datetime.now(timezone.utc).isoformat()
    # Tuple identities are the production path. Plain strings remain accepted
    # for the small public helper/test surface, but external_exposure never uses
    # them for live attribution.
    known_exact: set[tuple[str, str, str]] = set()
    known_legacy: set[str] = set()
    for item in known_symbols:
        if isinstance(item, (tuple, list)) and len(item) == 3:
            known_exact.add(tuple(str(v or "").strip().upper() for v in item))
        else:
            known_legacy.add(str(item or "").strip().upper())

    external: list[ExternalPosition] = []

    for row in rows:
        quantity = int(row.get("quantity") or 0)
        if quantity == 0:
            # A closed position is not exposure. It stays out of the count and
            # out of the report.
            continue
        symbol = str(row.get("tradingsymbol") or row.get("symbol") or "").strip().upper()
        exchange = str(row.get("exchange") or "").strip().upper()
        product = str(row.get("product") or row.get("position_type") or "").strip().upper()
        identity = (exchange, product, symbol)
        if identity in known_exact or symbol in known_legacy:
            continue
        external.append(ExternalPosition(
            account=account,
            instrument=symbol,
            quantity=quantity,
            product=product,
            exchange=exchange,
            broker_avg_price=_float(row.get("average_price")),
            last_price=_float(row.get("last_price")),
            unrealised=_float(row.get("unrealised") if row.get("unrealised") is not None
                              else row.get("pnl")),
            observed_at=stamp,
        ))

    return ExternalExposure(positions=tuple(external))


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


#: Order states in which an order can no longer change exposure.
_TERMINAL_ORDER_STATES = frozenset({"COMPLETE", "CANCELLED", "CANCELED", "REJECTED"})

#: GTT states in which a trigger can no longer place an order. A TRIGGERED GTT
#: has already done its work: the order it placed is either still working (and so
#: is in the order book, where it is judged on its own) or it has executed (and so
#: is a position). Leaving the finished trigger listed as a hazard would mean that
#: the first time Sterling's own protective stop fired, every strategy would be
#: blocked from entering until the broker purged the row.
_INERT_GTT_STATES = frozenset({
    "CANCELLED", "CANCELED", "DELETED", "EXPIRED", "REJECTED", "DISABLED", "TRIGGERED",
})


#: Alert states in which an alert can no longer fire an order (ATO) or basket.
#: Kite reports a fired alert as disabled with ``disabled_reason: triggered``.
_INERT_ALERT_STATES = frozenset({"DISABLED", "DELETED", "TRIGGERED"})


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _surface_rows(surface: str, payload: Any) -> list[dict]:
    """A broker list surface as dict rows. Anything else is UNREADABLE.

    A row that is not a mapping cannot be attributed to anyone, and skipping it
    would certify a flat account while one broker subsystem returned garbage.
    """
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise ValueError(f"malformed {surface} payload ({type(payload).__name__})")
    rows: list[dict] = []
    for row in payload:
        if not isinstance(row, dict):
            raise ValueError(f"malformed {surface} row ({type(row).__name__})")
        rows.append(row)
    return rows


def _json_value(value: Any) -> Any:
    """Kite returns nested GTT fields as objects; tolerate JSON-encoded strings."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _identity_owned(
    owned: _LocalOwnership, exchange: str, product: str, symbol: str
) -> bool:
    """Whether (exchange[, product], symbol) is a contract Sterling holds or is entering.

    The product is part of the identity when the broker row carries one; a row
    that omits it is matched on (exchange, symbol) alone.
    """
    if not exchange or not symbol:
        return False
    if product:
        return (exchange, product, symbol) in owned.identities
    return any(i[0] == exchange and i[2] == symbol for i in owned.identities)


def _gtt_legs(row: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """Every (exchange, product, symbol) a GTT can trade. Empty = unattributable."""
    condition = _json_value(row.get("condition"))
    condition = condition if isinstance(condition, Mapping) else {}
    cond_exchange = _norm(condition.get("exchange"))
    cond_symbol = _norm(condition.get("tradingsymbol"))
    legs: list[tuple[str, str, str]] = []
    orders = _json_value(row.get("orders"))
    for order in (orders if isinstance(orders, list) else []):
        if not isinstance(order, Mapping):
            return []
        exchange = _norm(order.get("exchange")) or cond_exchange
        symbol = _norm(order.get("tradingsymbol")) or cond_symbol
        if not exchange or not symbol:
            return []
        legs.append((exchange, _norm(order.get("product")), symbol))
    if not legs and cond_exchange and cond_symbol:
        legs.append((cond_exchange, "", cond_symbol))
    return legs


async def _broker_automation(
    resolved_client: Any, *, owned: _LocalOwnership | None = None
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Broker state that can create exposure after the positions read says flat.

    Returns ``(hazards, owned_automation)``.

    Sterling's own protective GTT stop and its own working orders are not
    hazards: they are the protection and the entries the system itself armed,
    attributed by the GTT/order id recorded on its positions and intents, or by
    the exact contract identity it holds. Counting them would make the first
    protective stop Sterling ever placed block every later entry for every
    strategy. Only automation Sterling cannot claim remains a hazard: an
    unowned open order, an unowned active GTT, and an executable ATO alert.

    Mutual-fund orders and SIPs are deliberately not read: they cannot create
    derivative exposure, and requiring that surface to answer would let an
    unrelated MF outage halt trading.

    Any unreadable surface raises to the caller. A partial answer is dangerous:
    it would certify flatness precisely when one broker subsystem was unavailable.
    """
    owned = owned or _LocalOwnership()
    hazards: list[str] = []
    own: list[str] = []

    for row in _surface_rows("orders", await resolved_client.get_orders()):
        status = _norm(row.get("status"))
        if status in _TERMINAL_ORDER_STATES:
            continue
        order_id = str(row.get("order_id") or "").strip()
        label = f"order:{order_id or '?'}:{status or 'UNKNOWN'}"
        if (order_id and order_id in owned.order_ids) or _identity_owned(
            owned,
            _norm(row.get("exchange")),
            _norm(row.get("product")),
            _norm(row.get("tradingsymbol") or row.get("symbol")),
        ):
            own.append(label)
        else:
            hazards.append(label)

    for row in _surface_rows("GTTs", await resolved_client.get_gtts()):
        status = _norm(row.get("status"))
        if status in _INERT_GTT_STATES:
            continue
        trigger_id = str(row.get("id") or row.get("trigger_id") or "").strip()
        label = f"gtt:{trigger_id or '?'}:{status or 'UNKNOWN'}"
        legs = _gtt_legs(row)
        if (trigger_id and trigger_id in owned.gtt_ids) or (
            legs and all(_identity_owned(owned, *leg) for leg in legs)
        ):
            own.append(label)
        else:
            hazards.append(label)

    for row in _surface_rows("alerts", await resolved_client.get_alerts()):
        alert = row
        alert_id = str(alert.get("uuid") or alert.get("id") or "").strip()
        if _norm(alert.get("status")) in _INERT_ALERT_STATES:
            # A disabled, deleted or already-triggered alert cannot place an
            # order. A missing or unrecognised status still counts (fail closed).
            continue
        alert_type = str(
            alert.get("type") or alert.get("alert_type") or ""
        ).strip().lower()
        if not alert_type and alert_id:
            detail = await resolved_client.get_alert(alert_id)
            alert = detail if isinstance(detail, dict) and detail else alert
            alert_type = str(
                alert.get("type") or alert.get("alert_type") or ""
            ).strip().lower()
            if _norm(alert.get("status")) in _INERT_ALERT_STATES:
                continue
        if not alert_type or alert_type == "ato" or bool(alert.get("basket")):
            hazards.append(
                "alert:" + (alert_id or "?") + ":" + (alert_type.upper() or "UNKNOWN")
            )

    return tuple(sorted(set(hazards))), tuple(sorted(set(own)))


async def _external_for_client(
    resolved_client: Any, *, resolved_account: str, scan_automation: bool = False
) -> ExternalExposure:
    """Read and classify exactly one broker account."""
    try:
        payload = await resolved_client.get_positions_raw()
        rows = list((payload or {}).get("net") or [])
    except Exception as exc:  # noqa: BLE001
        return ExternalExposure(
            readable=False,
            detail=(
                f"broker positions could not be read: "
                f"{type(exc).__name__}: {exc}"
            ).rstrip(": "),
        )

    ownership_account = str(
        getattr(resolved_client, "_account_id", "") or resolved_account or ""
    ).strip()
    if not ownership_account:
        return ExternalExposure(
            readable=False,
            detail="broker account identity is unavailable; positions cannot be attributed",
        )

    known_symbols = _known_symbols(ownership_account)
    classified = classify_broker_positions(
        rows,
        account=resolved_account or ownership_account,
        known_symbols=known_symbols,
    )
    if not classified.readable:
        return ExternalExposure(
            positions=classified.positions,
            accounts=(ownership_account,),
            readable=False,
            detail=classified.detail,
        )

    automation: tuple[str, ...] = ()
    owned_automation: tuple[str, ...] = ()
    if scan_automation:
        try:
            # Same identity set that classified the positions above, plus the
            # broker ids Sterling recorded. If the ids cannot be read the
            # identity set alone attributes; anything it cannot claim is a hazard.
            ids = _known_broker_ids(ownership_account)
            ownership = _LocalOwnership(
                identities=frozenset(known_symbols or ()),
                gtt_ids=ids[0] if ids else frozenset(),
                order_ids=ids[1] if ids else frozenset(),
            )
            automation, owned_automation = await _broker_automation(
                resolved_client, owned=ownership
            )
        except Exception as exc:  # noqa: BLE001
            return ExternalExposure(
                accounts=(ownership_account,),
                readable=False,
                detail=(
                    "broker automation state could not be read: "
                    f"{type(exc).__name__}: {exc}"
                ).rstrip(": "),
            )

    return ExternalExposure(
        positions=classified.positions,
        accounts=(ownership_account,),
        readable=True,
        detail=classified.detail,
        automation=automation,
        owned_automation=owned_automation,
    )


async def external_exposure(client: Any = None, *, account: str = "") -> ExternalExposure:
    """Ask every relevant live broker account what it holds, and classify it.

    A broker account that cannot be asked makes the whole answer UNREADABLE.
    Returning the readable subset would under-count exposure, which is the one
    direction this safety surface must never fail.

    Passing an explicit client keeps single-account behaviour for callers that
    have already resolved account ownership.
    """
    if client is not None:
        return await _external_for_client(client, resolved_account=account)

    try:
        from app.services.exchanges.kite import accounts as kite_accounts

        kite_accounts.bootstrap()
        live = [
            a for a in kite_accounts.all_accounts()
            if not getattr(a, "is_paper", True)
            and not _is_virtual(a)
            and getattr(a, "is_active", True)
        ]
    except Exception as exc:  # noqa: BLE001
        return ExternalExposure(
            readable=False,
            detail=f"broker account registry unavailable: {type(exc).__name__}: {exc}",
        )

    if account:
        wanted = str(account).strip()
        live = [
            a for a in live
            if wanted in {
                str(getattr(a, "id", "") or "").strip(),
                str(getattr(a, "kite_user_id", "") or "").strip(),
            }
        ]
        if not live:
            return ExternalExposure(
                readable=False,
                detail=f"live broker account {wanted!r} is not configured",
            )

    if not live:
        return ExternalExposure(
            readable=False, detail="no live broker account is configured"
        )

    combined: list[ExternalPosition] = []
    combined_automation: set[str] = set()
    combined_owned: set[str] = set()
    observed_accounts: set[str] = set()
    details: list[str] = []
    failures: list[str] = []
    entries: list[dict[str, Any]] = []
    for acct in live:
        display_account = str(
            getattr(acct, "kite_user_id", "") or getattr(acct, "id", "") or ""
        ).strip()
        ownership_account = str(getattr(acct, "id", "") or "").strip()
        label = display_account or ownership_account or "unknown account"

        def _unreadable(detail: str) -> None:
            failures.append(detail)
            entries.append({
                "account": ownership_account, "readable": False, "count": None,
                "instruments": [], "automation": [], "detail": detail,
            })

        try:
            resolved_client = await kite_accounts.acquire_client(acct)
        except Exception as exc:  # noqa: BLE001
            _unreadable(
                f"broker client unavailable for {label}: "
                f"{type(exc).__name__}: {exc}".rstrip(": ")
            )
            continue

        exposure = await _external_for_client(
            resolved_client,
            resolved_account=display_account or ownership_account,
            scan_automation=True,
        )
        if not exposure.readable:
            _unreadable(f"{label}: {exposure.detail}")
            continue

        combined.extend(exposure.positions)
        combined_automation.update(exposure.automation)
        combined_owned.update(exposure.owned_automation)
        observed_accounts.update(exposure.accounts)
        if exposure.detail:
            details.append(f"{label}: {exposure.detail}")
        entries.append({
            "account": ownership_account, "readable": True,
            "count": len(exposure.positions),
            "instruments": [p.instrument for p in exposure.positions],
            "automation": list(exposure.automation),
            "detail": exposure.detail,
        })

    if failures:
        # Returning the readable subset as the answer would under-count exposure,
        # so the combined answer stays UNREADABLE. The per-account entries let
        # admission judge each account on what the broker said about that account.
        return ExternalExposure(
            readable=False,
            detail="; ".join(failures),
            by_account=tuple(entries),
        )

    return ExternalExposure(
        positions=tuple(combined),
        accounts=tuple(sorted(observed_accounts)),
        readable=True,
        detail="; ".join(details),
        automation=tuple(sorted(combined_automation)),
        owned_automation=tuple(sorted(combined_owned)),
        by_account=tuple(entries),
    )


def render_external(exposure: ExternalExposure) -> str:
    if not exposure.readable:
        return f"EXTERNAL BROKER EXPOSURE: UNKNOWN — {exposure.detail}"
    if not exposure.positions and not exposure.automation:
        return "EXTERNAL BROKER EXPOSURE: none (no unmanaged position or exposure-capable automation)"

    lines = [f"EXTERNAL BROKER EXPOSURE: {len(exposure.positions)} position(s), "
             f"{len(exposure.automation)} automation hazard(s)", ""]
    for item in exposure.automation:
        lines.append(f"  AUTOMATION  {item}")
    for position in exposure.positions:
        lines.append(
            f"  {position.instrument}  qty {position.quantity}  {position.product}"
            f"  avg {position.broker_avg_price}  last {position.last_price}"
        )
        lines.append(f"      account {position.account} | {position.source}"
                     f" | {position.discovery_reason}")
        lines.append("      Sterling has no intent, no fill and no protection for this "
                     "position. It is not managed here.")
    return "\n".join(lines)


# -- the recorded observation ------------------------------------------------
#
# Admission must not make a network call. It is on the path of every order
# decision, and a safety check that depends on a broker answering in time is a
# safety check that fails in exactly the conditions it exists for. So the
# broker answer is recorded when something already asks — the doctor, the
# exposure view, reconciliation — and admission reads the record.

OBSERVATION_FILE: str = "data/manifests/external_exposure.json"

#: Match the reconciliation freshness contract. A manual broker position can
#: be opened between any two Sterling decisions, so a day-old "flat" snapshot
#: is not evidence about current exposure.
MAX_OBSERVATION_AGE_SECONDS: int = 60


def _observation_path():
    import os
    from pathlib import Path

    root = os.environ.get("STERLING_EXTERNAL_EXPOSURE_FILE")
    if root:
        return Path(root)
    from app.core.state_paths import state_root
    return state_root() / OBSERVATION_FILE


def record_observation(exposure: ExternalExposure) -> None:
    """Persist what the broker last said. Never raises into the caller."""
    import json

    path = _observation_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written from a worker thread while admission reads it on the event loop:
        # replace atomically so a reader never sees a truncated or empty file.
        import os as _os
        staging = path.with_name(path.name + ".tmp")
        staging.write_text(json.dumps({
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "readable": exposure.readable,
            "count": exposure.count,
            "accounts": list(exposure.accounts),
            "automation": list(exposure.automation),
            "owned_automation": list(exposure.owned_automation),
            "instruments": [p.instrument for p in exposure.positions],
            "by_account": {
                str(e.get("account") or ""): dict(e) for e in exposure.by_account
                if str(e.get("account") or "")
            },
            # The whole position, not just its name: the operator screen has to
            # show quantity, price and account without making its own broker
            # call, and a request-time network read is the thing the admission
            # design exists to avoid.
            "positions": [p.as_dict() for p in exposure.positions],
            "detail": exposure.detail,
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _os.replace(staging, path)
    except Exception as exc:  # noqa: BLE001
        log.warning("external positions: could not record observation: %s", exc)


def last_observation() -> dict[str, Any] | None:
    """The last recorded broker answer, or None if there is none to read."""
    import json

    path = _observation_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a corrupt record is no record
        return None


def live_account_ids() -> tuple[str, ...] | None:
    """Exact configured live-account set, or None when it cannot be proven."""
    try:
        from app.services.exchanges.kite import accounts as kite_accounts

        kite_accounts.bootstrap()
        ids: list[str] = []
        for account in kite_accounts.all_accounts():
            if getattr(account, "is_paper", True) or _is_virtual(account):
                continue
            if not getattr(account, "is_active", True):
                # Sterling only trades through each user's ACTIVE account. A
                # stored-but-inactive login (a second family member's, or a
                # converted account that never logged in) cannot receive a
                # Sterling order, and demanding a broker read from it would
                # halt every entry on the account that can.
                continue
            account_id = str(getattr(account, "id", "") or "").strip()
            if not account_id:
                return None
            ids.append(account_id)
        return tuple(sorted(set(ids)))
    except Exception as exc:  # noqa: BLE001
        log.warning("external positions: live-account registry unreadable: %s", exc)
        return None


def _live_account_configured() -> bool | None:
    """Whether this deployment could hold live broker exposure."""
    ids = live_account_ids()
    return None if ids is None else bool(ids)


def recorded_account_set_matches(record: Mapping[str, Any]) -> tuple[bool, str]:
    """A cached broker answer is valid only for the live accounts it queried."""
    current = live_account_ids()
    if current is None:
        return False, "current live-account identity is unreadable"
    recorded = tuple(sorted({
        str(value or "").strip()
        for value in (record.get("accounts") or [])
        if str(value or "").strip()
    }))
    if not recorded:
        return False, "recorded broker exposure has no account identity"
    if recorded != current:
        return (
            False,
            "recorded broker exposure belongs to a different live-account set "
            f"(recorded={list(recorded)}, current={list(current)})",
        )
    return True, ""


def _scoped_entry(record: Mapping[str, Any], account_id: str) -> tuple[bool, dict | None]:
    """``(has_per_account_data, this account's entry)`` from a recorded observation."""
    by_account = record.get("by_account")
    wanted = str(account_id or "").strip()
    if not wanted or wanted == "default" or not isinstance(by_account, Mapping) or not by_account:
        return False, None
    entry = by_account.get(wanted)
    return True, entry if isinstance(entry, Mapping) else None


def admission_blocker(account_id: str = "") -> tuple[str, str]:
    """Should new exposure be refused because of what the broker holds?

    Returns ``(code, reason)``, or ``("", "")`` to permit. Reads the recorded
    observation rather than the broker: admission is a hot path, and a check
    that needs the network is a check that fails when the network does.

    With ``account_id`` the decision rests on what the broker said about THAT
    account, so a tenant whose session is missing (or who holds a position of
    their own) cannot gate another tenant's entries. A record with no per-account
    data, or no account given, keeps the combined rules: every account must have
    been readable and the answer must match the configured account set.
    """
    # The Simulation tenant holds no broker position: there is nothing for the broker to
    # contradict, and its absence from a live observation must never refuse a virtual order.
    if _sim_identity.is_sim_account_id(str(account_id or "").strip()):
        return "", ""
    live_configured = _live_account_configured()
    if live_configured is False:
        return "", ""
    if live_configured is None:
        return (
            "external_exposure_unreadable",
            "cannot determine whether a live broker account is configured",
        )

    record = last_observation()
    if record is None:
        return ("external_exposure_never_observed",
                "no reconciliation has recorded what the broker holds; run "
                "`sterlingctl exposure` or `sterlingctl doctor`")

    scoped, entry = _scoped_entry(record, account_id)
    if scoped:
        if entry is None:
            return ("external_exposure_unreadable",
                    f"the recorded broker observation does not include account {account_id!r}")
        if not entry.get("readable", False) or entry.get("count") is None:
            return ("external_exposure_unreadable",
                    str(entry.get("detail") or "the broker could not be asked what it holds"))
    elif not record.get("readable", False) or record.get("count") is None:
        return ("external_exposure_unreadable",
                str(record.get("detail") or "the broker could not be asked what it holds"))

    observed_at = str(record.get("observed_at") or "")
    try:
        seen = datetime.fromisoformat(observed_at)
    except ValueError:
        return ("external_exposure_unreadable",
                f"the recorded observation has no usable timestamp: {observed_at!r}")
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - seen).total_seconds()
    if age < 0:
        return (
            "external_exposure_unreadable",
            "the recorded broker exposure timestamp is in the future",
        )
    if age > MAX_OBSERVATION_AGE_SECONDS:
        return (
            "external_exposure_stale",
            f"the broker was last checked {int(age)}s ago; reconciliation evidence "
            f"must be no older than {MAX_OBSERVATION_AGE_SECONDS}s",
        )

    if not scoped:
        matches, mismatch = recorded_account_set_matches(record)
        if not matches:
            return ("external_exposure_account_identity", mismatch)

    source = entry if scoped else record
    automation = tuple(str(v) for v in (source.get("automation") or []) if str(v))
    if automation:
        return (
            "external_broker_automation",
            "the broker still has exposure-capable automation: "
            + ", ".join(automation[:3]),
        )

    count = int(source.get("count") or 0)
    if count:
        listed = ", ".join(str(i) for i in (source.get("instruments") or [])[:3])
        return ("external_broker_exposure",
                f"the broker holds {count} position(s) Sterling did not open and does "
                f"not manage" + (f": {listed}" if listed else ""))
    return "", ""
