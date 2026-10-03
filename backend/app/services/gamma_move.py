"""Sterling adapter for the user-supplied GAMMA-UNWIND client strategy."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.engines.gamma_move import (
    CLIENT_TOP_N_OI, CONTRACT_VERSION, GammaMoveConfig, REFERENCE_VERSION,
    SPEC_VERSION, STRATEGY_ID, STRATEGY_NAME,
)

log = get_logger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))
_CONFIG_KEY = "gamma_move_config"
_DUMP_TTL_S = 900.0
_dump_cache: dict[str, tuple[float, list[dict]]] = {}


def ist_today() -> date:
    return datetime.now(_IST).date()


def ist_now_ms() -> int:
    return int(datetime.now(_IST).timestamp() * 1000)


def get_config(uid: str | None = None) -> GammaMoveConfig:
    key = f"{_CONFIG_KEY}:{uid}" if uid else _CONFIG_KEY
    try:
        from app.services import db
        raw = db.get_config(key)
        if not raw and uid:
            raw = db.get_config(_CONFIG_KEY)
    except Exception as exc:
        log.warning("gamma_unwind: config store unavailable (%s); engine OFF", exc)
        return GammaMoveConfig(enabled=False)
    if not raw:
        return GammaMoveConfig().validate()
    try:
        stored = json.loads(raw) if isinstance(raw, str) else dict(raw)
        merged = {
            **GammaMoveConfig().as_dict(),
            **{k: v for k, v in stored.items()
               if k in GammaMoveConfig.field_names()},
        }
        for name in ("scan_stocks", "mark_levels_times", "build_watchlists_times"):
            if isinstance(merged.get(name), list):
                merged[name] = tuple(merged[name])
        return GammaMoveConfig(**merged).validate()
    except Exception as exc:
        log.error("gamma_unwind: invalid persisted config (%s); engine OFF", exc)
        return GammaMoveConfig(enabled=False)


def set_config(values: dict[str, Any], uid: str | None = None) -> GammaMoveConfig:
    unknown = sorted(set(values) - GammaMoveConfig.field_names())
    if unknown:
        raise ValueError(f"Unknown gamma_unwind config fields: {', '.join(unknown)}")
    current = get_config(uid).as_dict()
    current.update(values)
    for name in ("scan_stocks", "mark_levels_times", "build_watchlists_times"):
        if isinstance(current.get(name), list):
            current[name] = tuple(current[name])
    cfg = GammaMoveConfig(**current).validate()
    from app.services import db
    key = f"{_CONFIG_KEY}:{uid}" if uid else _CONFIG_KEY
    db.set_config(key, json.dumps(cfg.as_dict(), separators=(",", ":"),
                                  allow_nan=False))
    return cfg


async def nfo_dump(uid: str) -> list[dict]:
    from app.services.exchanges.kite import accounts
    now = datetime.now(timezone.utc).timestamp()
    cached = _dump_cache.get(uid)
    if cached and now - cached[0] < _DUMP_TTL_S:
        return cached[1]
    acct = accounts.get_active(uid)
    if not acct:
        raise RuntimeError("No active Kite account")
    client = await accounts.acquire_client(acct)
    rows = list(await client.search_instruments("", "NFO", limit=1_000_000) or [])
    _dump_cache[uid] = (now, rows)
    return rows


def stock_underlyings(rows: list[dict], cfg: GammaMoveConfig) -> list[str]:
    """Sterling universe adapter; Gamma-Unwind rules start after selection."""
    from app.services.kite_engine.stock_registry import HIGH_LIQUIDITY_STOCK_NAMES
    if not cfg.stock_contracts:
        return []
    eligible = set(HIGH_LIQUIDITY_STOCK_NAMES)
    listed = {
        str(r.get("name") or "").upper()
        for r in rows if r.get("segment") == "NFO-OPT"
    }
    wanted = (
        eligible if cfg.scan_all_stocks
        else {str(x).upper() for x in cfg.scan_stocks}
    )
    return sorted(wanted & eligible & listed)


def descriptor() -> dict:
    return {
        "id": STRATEGY_ID,
        "name": STRATEGY_NAME,
        "contract_version": CONTRACT_VERSION,
        "reference_package": f"GAMMA-UNWIND client {REFERENCE_VERSION}",
        "spec_version": SPEC_VERSION,
        "edition": "client",
        "tagline": "OI-unwind breakout at daily supply/demand zones.",
        "how_it_works": (
            "Builds daily supply/demand zones; keeps the client edition's one "
            "highest-OI OTM contract (HOI-1) near the relevant edge; internally "
            "arms E2 when A+B+C+H hold on a closed 5-minute bar; emits E1 on a "
            "live premium breakout with forming volume above SMA20; then applies "
            "ZONE, LIQUIDITY, DIRECTION and UNWIND gates to tier the signal."
        ),
        "provenance": (
            "Ported from the user-supplied "
            "GAMMA-UNWIND-client-0.8.2-linux package."
        ),
        "signal_parity": True,
        "client_top_n_oi": CLIENT_TOP_N_OI,
        "execution_note": (
            "The supplied client has no order function. Sterling execution is a "
            "separate safety adapter and only ENTRY tier may increase exposure."
        ),
    }


async def snapshot(uid: str) -> dict:
    from app.services import gamma_move_positions as positions_store
    from app.services import gamma_move_runner as runner
    from app.services import gamma_move_scanner as scanner

    cfg = get_config(uid)
    session = runner.session_status(uid)
    watches = [scanner.watch_view(rt) for rt in scanner.runtimes(uid).values()]
    signals = list((session or {}).get("signals") or [])
    positions = (session or {}).get("positions") or []
    blockers: list[str] = []
    if not cfg.enabled:
        blockers.append("Gamma Unwind is switched off")
    issue = scanner.scan_issue(uid)
    if issue:
        blockers.append(issue)
    if not signals and not watches and cfg.enabled and not issue:
        blockers.append(
            "No current watchlist/E1 rows. The 09:20/13:15 build may not have "
            "run yet or no stock is inside the configured zone band."
        )
    return {
        "strategy": {**descriptor(), "enabled": cfg.enabled},
        "config": cfg.as_dict(),
        "scan": scanner.scan_stats(uid),
        "session": session,
        "candidates": signals + watches,
        "positions": positions,
        "record": (
            (session or {}).get("record")
            or positions_store.load_record(uid).as_dict()
        ),
        "mode": {
            "is_paper": runner.is_paper(uid),
            "auto_execute": runner.auto_execute(uid),
            "note": (
                "AUTO requires both the Kite master AUTO switch and "
                "Gamma Unwind auto-execute."
            ),
        },
        "warnings": [
            "Client edition keeps HOI-1 only even though strategy.yaml top_n_oi is 2.",
            "The reference client is signal-only; Sterling execution never changes signal tiering.",
        ],
        "blockers": blockers,
    }
