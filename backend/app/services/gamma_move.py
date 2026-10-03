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


async def snapshot(uid: str, day: date | str | None = None) -> dict:
    """Board snapshot for live today or one read-only historical day.

    Historical evidence is typed: live_recorded is exact runtime evidence;
    historical_replay is a conservative one-minute-close reconstruction.
    """
    from app.services import gamma_move_history
    from app.services import gamma_move_positions as positions_store
    from app.services import gamma_move_runner as runner
    from app.services import gamma_move_scanner as scanner
    from app.engines.gamma_move.selection import (
        is_trading_day, previous_trading_day, recent_trading_days,
    )

    cfg = get_config(uid)
    today = ist_today()
    selected = today if day is None else (
        day if isinstance(day, date) else date.fromisoformat(str(day))
    )
    historical = selected != today

    # Hydration also migrates every row from the pre-ledger signal store into
    # its original event day, so a direct historical request after restart can
    # recover old recorded E1s immediately.
    runner.session_for(uid, cfg)
    archive = gamma_move_history.read_day(uid, selected)
    indexed_days = {
        row["day"]: dict(row)
        for row in gamma_move_history.list_days(uid, limit=60)
    }
    for recent in recent_trading_days(today, 30):
        key = recent.isoformat()
        indexed_days.setdefault(key, {
            "day": key,
            "signals": 0,
            "watches": 0,
            "last_run_ms": 0,
            "updated_ms": 0,
            "issue": "",
            "source": "available_replay",
            "resolution": "1minute_close",
        })
    history_days = sorted(
        indexed_days.values(),
        key=lambda row: row["day"],
        reverse=True,
    )
    latest_trading_day = previous_trading_day(
        today, inclusive=is_trading_day(today)
    )

    if historical:
        signals = list((archive or {}).get("signals") or [])
        watches = list((archive or {}).get("watchlist") or [])
        issue = str((archive or {}).get("issue") or "")
        source = str((archive or {}).get("source") or "available_replay")
        replay = dict((archive or {}).get("replay") or {})
        coverage = list((archive or {}).get("coverage") or [])
        blockers: list[str] = []
        if archive is None:
            blockers.append(
                f"No cached Gamma-Unwind state for {selected.isoformat()}. "
                "Use Replay day to reconstruct it from available broker history."
            )
        elif not signals and not watches and issue:
            blockers.append(issue)
        elif not signals and not watches:
            blockers.append(
                f"Gamma-Unwind found no watch/E1 rows for {selected.isoformat()}."
            )
        if coverage and not issue:
            blockers.append(str(coverage[0]))
        warnings = [
            "Client edition keeps HOI-1 only even though strategy.yaml top_n_oi is 2.",
        ]
        if source == "historical_replay":
            warnings.insert(
                0,
                "Historical replay uses one-minute option closes as the E1 proxy "
                "because Kite has no historical tick endpoint. It is read-only "
                "and never execution evidence.",
            )
        elif source == "live_recorded":
            warnings.insert(
                0,
                "Historical view contains exact Gamma-Unwind runtime events recorded live.",
            )
        return {
            "day": selected.isoformat(),
            "today": today.isoformat(),
            "historical": True,
            "recorded": archive is not None,
            "source": source,
            "replay": replay,
            "coverage": coverage,
            "replay_available": archive is None or source == "historical_replay",
            "is_trading_day": is_trading_day(selected),
            "latest_trading_day": latest_trading_day.isoformat(),
            "history_days": history_days,
            "strategy": {**descriptor(), "enabled": cfg.enabled},
            "config": cfg.as_dict(),
            "scan": dict((archive or {}).get("scan") or {}),
            "session": None,
            "candidates": signals + watches,
            "positions": [],
            "record": positions_store.load_record(uid).as_dict(),
            "mode": {
                "is_paper": runner.is_paper(uid),
                "auto_execute": False,
                "note": "Historical Gamma-Unwind views are read-only.",
            },
            "warnings": warnings,
            "blockers": blockers,
        }
    session = runner.session_status(uid)
    live_watches = [scanner.watch_view(rt) for rt in scanner.runtimes(uid).values()]
    archived_watches = list((archive or {}).get("watchlist") or [])
    watches = live_watches or archived_watches

    live_signals = list((session or {}).get("signals") or [])
    archived_signals = list((archive or {}).get("signals") or [])
    signal_map = {
        str(row.get("id") or ""): row
        for row in archived_signals + live_signals
        if isinstance(row, dict) and row.get("id")
    }
    signals = list(signal_map.values())
    positions = (session or {}).get("positions") or []

    current_scan = scanner.scan_stats(uid)
    scan = current_scan or dict((archive or {}).get("scan") or {})
    issue = scanner.scan_issue(uid)
    if not issue and not current_scan:
        issue = str((archive or {}).get("issue") or "")

    blockers: list[str] = []
    if not cfg.enabled:
        blockers.append("Gamma Unwind is switched off")
    if issue:
        blockers.append(issue)
    if not signals and not watches and cfg.enabled and not issue:
        blockers.append(
            "No current or recorded watch/E1 rows for today. Use Scan now to "
            "rebuild the daily zones/watchlist; E1 itself still requires the "
            "reference live-tick breakout."
        )

    warnings = [
        "Client edition keeps HOI-1 only even though strategy.yaml top_n_oi is 2.",
        "The reference client is signal-only; Sterling execution never changes signal tiering.",
    ]
    if not live_watches and archived_watches:
        warnings.append(
            "Showing today's last recorded watch state because the in-memory "
            "Gamma runtime is not active."
        )

    return {
        "day": today.isoformat(),
        "today": today.isoformat(),
        "historical": False,
        "recorded": archive is not None or bool(signals or watches),
        "source": str((archive or {}).get("source") or "live"),
        "replay": {},
        "coverage": [],
        "replay_available": False,
        "is_trading_day": is_trading_day(today),
        "latest_trading_day": latest_trading_day.isoformat(),
        "history_days": history_days,
        "strategy": {**descriptor(), "enabled": cfg.enabled},
        "config": cfg.as_dict(),
        "scan": scan,
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
        "warnings": warnings,
        "blockers": blockers,
    }
