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

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

log = logging.getLogger(__name__)

__all__ = [
    "ExternalPosition", "ExternalExposure", "classify_broker_positions",
    "external_exposure", "render_external", "SOURCE", "DISCOVERY_REASON",
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

    @property
    def count(self) -> int | None:
        return len(self.positions) if self.readable else None

    @property
    def flat(self) -> bool | None:
        """True only when the broker was asked and holds nothing unmanaged."""
        if not self.readable:
            return None
        return not self.positions

    def as_dict(self) -> dict[str, Any]:
        return {"readable": self.readable, "count": self.count, "flat": self.flat,
                "detail": self.detail,
                "positions": [p.as_dict() for p in self.positions]}


def _known_symbols(account_id: str) -> set[tuple[str, str, str]] | None:
    """Exact broker identities Sterling can attribute to one account.

    Production attribution is never symbol-only. The same contract can exist in
    MIS and NRML buckets, and a process may know positions for several accounts.
    Returning an exact (exchange, product, symbol) set prevents either collision
    from hiding external exposure.
    """
    try:
        from app.services import db

        db.init()
        from app.services.kite_engine import order_journal, positions

        wanted_account = str(account_id or "").strip()
        if not wanted_account:
            return None

        known: set[tuple[str, str, str]] = set()
        for uid in positions.known_uids():
            for position in positions.open_positions(uid):
                if str(getattr(position, "account_id", "") or "").strip() != wanted_account:
                    continue
                exchange = str(getattr(position, "exchange", "") or "").strip().upper()
                product = str(getattr(position, "product", "") or "").strip().upper()
                symbol = str(getattr(position, "symbol", "") or "").strip().upper()
                if exchange and product and symbol:
                    known.add((exchange, product, symbol))

            for intent in order_journal.unresolved(uid, account_id=wanted_account):
                exchange = str(getattr(intent, "exchange", "") or "").strip().upper()
                symbol = str(getattr(intent, "symbol", "") or "").strip().upper()
                payload = getattr(intent, "payload", {}) or {}
                product = str(payload.get("product") or "NRML").strip().upper()
                if exchange and product and symbol:
                    known.add((exchange, product, symbol))
        return known
    except Exception as exc:  # noqa: BLE001
        log.warning("external positions: local state unreadable: %s", exc)
        return None


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


async def _external_for_client(
    resolved_client: Any, *, resolved_account: str
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

    return classify_broker_positions(
        rows,
        account=resolved_account or ownership_account,
        known_symbols=_known_symbols(ownership_account),
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
    details: list[str] = []
    for acct in live:
        display_account = str(
            getattr(acct, "kite_user_id", "") or getattr(acct, "id", "") or ""
        ).strip()
        ownership_account = str(getattr(acct, "id", "") or "").strip()

        try:
            resolved_client = await kite_accounts.acquire_client(acct)
        except Exception as exc:  # noqa: BLE001
            return ExternalExposure(
                readable=False,
                detail=(
                    f"broker client unavailable for "
                    f"{display_account or ownership_account or 'unknown account'}: "
                    f"{type(exc).__name__}: {exc}"
                ).rstrip(": "),
            )

        exposure = await _external_for_client(
            resolved_client,
            resolved_account=display_account or ownership_account,
        )
        if not exposure.readable:
            return ExternalExposure(
                readable=False,
                detail=(
                    f"{display_account or ownership_account or 'unknown account'}: "
                    f"{exposure.detail}"
                ),
            )

        combined.extend(exposure.positions)
        if exposure.detail:
            details.append(
                f"{display_account or ownership_account or 'unknown account'}: "
                f"{exposure.detail}"
            )

    return ExternalExposure(
        positions=tuple(combined),
        readable=True,
        detail="; ".join(details),
    )

def render_external(exposure: ExternalExposure) -> str:
    if not exposure.readable:
        return f"EXTERNAL BROKER EXPOSURE: UNKNOWN — {exposure.detail}"
    if not exposure.positions:
        return "EXTERNAL BROKER EXPOSURE: none (the broker holds nothing Sterling did not open)"

    lines = [f"EXTERNAL BROKER EXPOSURE: {len(exposure.positions)} position(s) "
             "Sterling did not open and does not manage", ""]
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
    configured = os.environ.get("STERLING_ROOT")
    base = Path(configured) if configured else Path(__file__).resolve().parents[3]
    return base / OBSERVATION_FILE


def record_observation(exposure: ExternalExposure) -> None:
    """Persist what the broker last said. Never raises into the caller."""
    import json

    path = _observation_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "readable": exposure.readable,
            "count": exposure.count,
            "instruments": [p.instrument for p in exposure.positions],
            # The whole position, not just its name: the operator screen has to
            # show quantity, price and account without making its own broker
            # call, and a request-time network read is the thing the admission
            # design exists to avoid.
            "positions": [p.as_dict() for p in exposure.positions],
            "detail": exposure.detail,
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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


def _live_account_configured() -> bool | None:
    """Whether this deployment could hold live broker exposure.

    None means the account registry could not be read. That is materially
    different from False: treating an unreadable registry as no live account
    would turn the admission safety check off exactly when ownership is unknown.
    """
    try:
        from app.services.exchanges.kite import accounts as kite_accounts

        kite_accounts.bootstrap()
        return any(
            not getattr(a, "is_paper", True)
            for a in kite_accounts.all_accounts()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("external positions: live-account registry unreadable: %s", exc)
        return None


def admission_blocker() -> tuple[str, str]:
    """Should new exposure be refused because of what the broker holds?

    Returns ``(code, reason)``, or ``("", "")`` to permit. Reads the recorded
    observation rather than the broker: admission is a hot path, and a check
    that needs the network is a check that fails when the network does.
    """
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

    if not record.get("readable", False) or record.get("count") is None:
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
    if age > MAX_OBSERVATION_AGE_SECONDS:
        return (
            "external_exposure_stale",
            f"the broker was last checked {int(age)}s ago; reconciliation evidence "
            f"must be no older than {MAX_OBSERVATION_AGE_SECONDS}s",
        )

    count = int(record.get("count") or 0)
    if count:
        listed = ", ".join(str(i) for i in (record.get("instruments") or [])[:3])
        return ("external_broker_exposure",
                f"the broker holds {count} position(s) Sterling did not open and does "
                f"not manage" + (f": {listed}" if listed else ""))
    return "", ""
