"""Sterling adapter for the user-supplied GAMMA-UNWIND client strategy."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.services.simulation.identity import is_sim_uid
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
        # The legacy single-user row is inherited by real tenants only: the virtual Simulation
        # tenant must start from its own (copied or default) config, never from the owner's.
        if not raw and uid and not is_sim_uid(uid):
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

    before = get_config(uid).as_dict()
    current = dict(before)
    current.update(values)
    for name in ("scan_stocks", "mark_levels_times", "build_watchlists_times"):
        if isinstance(current.get(name), list):
            current[name] = tuple(current[name])
    cfg = GammaMoveConfig(**current).validate()

    from app.services import db
    key = f"{_CONFIG_KEY}:{uid}" if uid else _CONFIG_KEY
    db.set_config(
        key,
        json.dumps(cfg.as_dict(), separators=(",", ":"), allow_nan=False),
    )

    changed = {
        name for name in values
        if before.get(name) != cfg.as_dict().get(name)
    }
    if uid and changed:
        from app.services import gamma_move_scanner as scanner

        level_cache_fields = {
            "zone_pivot_len", "zone_atr_len", "zone_depth",
            "zone_guard_mult", "zone_memory", "zone_break_on_wick",
            "zone_daily_bars",
            # These fields change which underlyings feed the tenant/day level
            # cache, so reusing the old cache would preserve an obsolete
            # universe until the next scheduled level refresh.
            "scan_all_stocks", "scan_stocks", "stock_contracts",
        }
        if changed & level_cache_fields:
            scanner.clear_levels(uid)

        execution_only = {
            "auto_execute", "execution_lots", "stop_mode",
            "alert_group_rate_per_min", "alert_per_chat_rate_per_sec",
        }
        signal_changed = changed - execution_only
        if signal_changed:
            scanner.invalidate_runtime(uid)
            try:
                from app.services import gamma_move_runner
                gamma_move_runner.invalidate_signal_state(
                    uid,
                    ", ".join(sorted(signal_changed)),
                )
            except Exception as exc:
                log.warning(
                    "gamma_unwind: live signal invalidation failed for %s: %s",
                    uid, exc,
                )

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


_INDEX_UNDERLYINGS = frozenset({
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
    "NIFTYFPI", "SENSEX", "BANKEX",
})


def listed_stock_underlyings(rows: list[dict]) -> list[str]:
    """Date-current F&O single-stock underlyings from Kite's NFO master.

    The supplied Gamma-Unwind client derives roughly 200+ stocks from the
    derivatives master and explicitly excludes index/test contracts. Sterling's
    old curated high-liquidity registry was not part of that strategy and could
    remove the very names that produced a reference signal.
    """
    listed: set[str] = set()
    for row in rows or []:
        name = str(row.get("name") or "").strip().upper()
        segment = str(row.get("segment") or "").strip().upper()
        instrument_type = str(row.get("instrument_type") or "").strip().upper()
        if not name or name in _INDEX_UNDERLYINGS or "NSETEST" in name:
            continue
        if segment not in {"NFO-OPT", "NFO-FUT"}:
            continue
        if instrument_type not in {"CE", "PE", "FUT"}:
            continue
        listed.add(name)
    return sorted(listed)


def stock_underlyings(rows: list[dict], cfg: GammaMoveConfig) -> list[str]:
    """Reference F&O-stock universe, optionally narrowed by the user's selection."""
    if not cfg.stock_contracts:
        return []
    listed = set(listed_stock_underlyings(rows))
    if cfg.scan_all_stocks:
        return sorted(listed)
    wanted = {str(x).strip().upper() for x in cfg.scan_stocks if str(x).strip()}
    return sorted(wanted & listed)


def _without_emitted_watches(
    watches: list[dict],
    signals: list[dict],
) -> list[dict]:
    emitted = {
        (
            str((row.get("instrument") or {}).get("tradingsymbol") or ""),
            str(row.get("list_name") or ""),
        )
        for row in signals
        if isinstance(row, dict)
    }
    return [
        row for row in watches
        if (
            str((row.get("instrument") or {}).get("tradingsymbol") or ""),
            str(row.get("list_name") or ""),
        ) not in emitted
    ]


def zero_signal_funnel_message(
    cfg: GammaMoveConfig,
    scan: dict[str, Any] | None,
) -> str:
    """Operator-facing reason a completed live view has no watch/E1 rows."""
    scan = dict(scan or {})
    stage_a = dict(scan.get("stage_a") or {})
    stage_b = dict(scan.get("stage_b") or {})
    stage_c = dict(scan.get("stage_c") or {})
    scanned = int(stage_a.get("scanned") or 0)
    near = stage_a.get("near_level")
    candidates = int(stage_b.get("candidates") or 0)
    watched = int(stage_c.get("watched") or 0)

    if not scan:
        return (
            "Gamma-Unwind has not completed today's reference watchlist build. "
            "Use Scan now; E1 can only exist after a watch contract is selected."
        )
    if scanned <= 0:
        return (
            "Gamma-Unwind completed with zero eligible listed F&O stock "
            "underlyings. Check the active Kite instrument master and the "
            "Instruments settings."
        )
    if near == 0:
        return (
            f"Scanned {scanned} F&O stocks; none were within ±"
            f"{cfg.stock_band_pct:g}% of an active daily supply/demand zone. "
            "This is a valid zero-watch session, not a missing signal."
        )
    if candidates <= 0:
        return (
            "At least one stock was near a reference zone, but no HOI-1/HOI-2 OTM "
            "contract passed the expiry/strike-band/liquidity selection."
        )
    if watched <= 0:
        return (
            f"Selected {candidates} contract candidate(s), but none could be "
            "warmed into the 5-minute E2 watcher. Inspect Scan coverage for "
            "historical OI/bar availability."
        )
    if cfg.entry_trigger == "closed_5m":
        return f"Watching {watched} contract(s); no completed 5-minute candle has passed the experimental close-only trigger and final gates yet."
    return (
        f"Watching {watched} contract(s); no live E1 breakout has fired yet. "
        "E2 is internal and E1 requires a subsequent live premium breakout tick."
    )


def _global_live_execution_enabled() -> bool:
    from app.core.lane_registry import LIVE_EXECUTION_ENABLED
    return bool(LIVE_EXECUTION_ENABLED)


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
            "Builds daily supply/demand zones and selects HOI-1/HOI-2 OTM contracts "
            "near the zone edge. A completed 5-minute bar arms internal E2; "
            "E1 requires a live premium breakout with forming-bar volume above SMA20. "
            "The separate close-only mode is experimental, not reference parity. It applies "
            "ZONE, LIQUIDITY, DIRECTION and UNWIND gates to tier the signal."
        ),
        "provenance": (
            "Ported from the user-supplied "
            "GAMMA-UNWIND-client-0.8.2-linux package."
        ),
        "signal_parity": False,
        "historical_parity_verified": False,
        "operator_updates": ["HOI-1/HOI-2", "09:10 mark and additional 11:15/14:15 builds"],
        "client_top_n_oi": CLIENT_TOP_N_OI,
        "execution_note": (
            "The supplied client has no order function. Sterling execution is a "
            "separate safety adapter and only ENTRY tier may increase exposure."
        ),
    }


def _decorate_live_rows(uid: str, cfg: GammaMoveConfig, rows: list[dict], *,
                        paper: bool | None, held: set[str]) -> list[dict]:
    """Attach live price and entry-eligibility to today's board rows.

    ``levels.ltp`` is the latest tick of the watched contract, never the E1 print
    or the 09:20 chain price (those stay in ``levels.entry`` / ``watch.chain_ltp``).
    A row with no live runtime reports ``ltp: null`` rather than a frozen number
    that reads as current. ``entry_gate`` is the single answer to "can this ENTRY
    be taken now, and if not why not"; arm() enforces the same rules.
    """
    from app.services import gamma_move_runner as runner
    from app.services import gamma_move_scanner as scanner

    now = datetime.now(_IST)
    now_ms = int(now.timestamp() * 1000)
    by_symbol = {
        rt.contract.instrument.tradingsymbol: rt
        for rt in scanner.runtimes(uid).values()
    }
    live_enabled = _global_live_execution_enabled()
    out: list[dict] = []
    for row in rows:
        item = dict(row)
        levels = dict(item.get("levels") or {})
        inst = item.get("instrument") or {}
        symbol = str(inst.get("tradingsymbol") or "")
        rt = by_symbol.get(symbol)
        price = age = None
        if rt is not None:
            if rt.forming is not None and rt.last_tick_ms:
                price = rt.forming.close
                age = (now_ms - rt.last_tick_ms) / 1000.0
            elif rt.closed_bars:
                last = rt.closed_bars[-1]
                price = last.close
                age = (now_ms - (last.ts_ms + 5 * 60_000)) / 1000.0
        levels["ltp"] = price
        levels["ltp_age_s"] = None if age is None else round(max(0.0, age), 1)
        item["levels"] = levels
        if item.get("gates") is not None:
            watch = item.get("watch") or {}
            gate = runner.entry_gate(
                cfg,
                tier=str(item.get("tier") or ""),
                dte=watch.get("dte"),
                at_ms=int(item.get("at_ms") or 0),
                paper=paper,
                live_enabled=live_enabled,
                now=now,
            )
            if gate["eligible"] and symbol in held:
                gate = {
                    "eligible": False, "hold": gate["hold"],
                    "code": "already_holding",
                    "reason": f"Already holding {symbol}.",
                }
            item["entry_gate"] = gate
        out.append(item)
    return out


async def snapshot(uid: str, day: date | str | None = None) -> dict:
    """Board snapshot for live today or one read-only historical day.

    Historical evidence is typed: live_recorded is exact runtime evidence;
    historical_replay describes its saved reconstruction resolution explicitly.
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
            "resolution": "1minute_ohlcv_proxy",
        })
    for row in indexed_days.values():
        row["is_trading_day"] = is_trading_day(date.fromisoformat(row["day"]))
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
        watches = _without_emitted_watches(
            list((archive or {}).get("watchlist") or []),
            signals,
        )
        issue = str((archive or {}).get("issue") or "")
        source = str((archive or {}).get("source") or "available_replay")
        replay = dict((archive or {}).get("replay") or {})
        coverage = list((archive or {}).get("coverage") or [])
        archive_complete = bool(
            archive
            and (
                source == "live_recorded"
                or (source == "historical_replay" and replay.get("complete") is True)
            )
        )
        blockers: list[str] = []
        if archive is None:
            blockers.append(
                f"No cached Gamma-Unwind state for {selected.isoformat()}. "
                "Historical signals will load automatically from available broker history."
            )
        elif not signals and not watches and issue:
            blockers.append(issue)
        elif not signals and not watches:
            blockers.append(
                f"Gamma-Unwind found no watch/E1 rows for {selected.isoformat()}."
            )
        warnings = [
            "Gamma-Unwind 0.8.6 selects HOI-1 and HOI-2 OTM contracts within the 2.5% zone radius.",
        ]
        if source == "historical_replay":
            resolution = replay.get("resolution")
            proxy = {
                "1minute_ohlcv_proxy": (
                    "minute highs and end-of-minute volume; intraminute ordering is unknown"
                ),
                "5minute_close_proxy": "five-minute closes",
            }.get(resolution, "an unverified archived resolution")
            warnings.insert(
                0,
                f"Historical signal reconstruction uses {proxy} as the E1 proxy "
                "because Kite has no historical tick endpoint. It is read-only "
                "and never execution evidence.",
            )
        elif source == "live_recorded":
            warnings.insert(
                0,
                "Historical view contains exact Gamma-Unwind runtime events recorded live.",
            )
        if not is_trading_day(selected):
            warnings.insert(
                0,
                "This archive is outside the Gamma reference trading calendar. "
                "Recorded rows are retained for audit; they do not establish a valid trading session.",
            )
        return {
            "day": selected.isoformat(),
            "today": today.isoformat(),
            "historical": True,
            "recorded": archive_complete,
            "source": source,
            "replay": replay,
            "coverage": coverage,
            "replay_available": not archive_complete,
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
                "live_execution_enabled": _global_live_execution_enabled(),
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
    watches = _without_emitted_watches(watches, signals)
    positions = (session or {}).get("positions") or []
    paper = runner.is_paper(uid)
    held = {str(p.get("symbol") or "") for p in positions}
    signals = _decorate_live_rows(uid, cfg, signals, paper=paper, held=held)
    watches = _decorate_live_rows(uid, cfg, watches, paper=paper, held=held)

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
        blockers.append(zero_signal_funnel_message(cfg, scan))

    warnings = [
        "Gamma-Unwind 0.8.6 selects HOI-1 and HOI-2 OTM contracts within the 2.5% zone radius.",
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
        # An archive row can exist solely because a failed scan persisted its
        # issue/coverage. That is not a completed observation and must not stop
        # the board's one-time bootstrap scan. Exact runtime signals/watches are
        # evidence even if the archive has not been flushed yet.
        "recorded": bool(
            signals
            or watches
            or (
                archive is not None
                and str((archive or {}).get("source") or "") == "live_recorded"
            )
        ),
        "source": str((archive or {}).get("source") or "live"),
        "replay": {},
        "coverage": list((archive or {}).get("coverage") or []),
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
            "is_paper": paper,
            "auto_execute": runner.auto_execute(uid),
            "live_execution_enabled": _global_live_execution_enabled(),
            "note": (
                "AUTO armed means both strategy switches are on. Real-money "
                "entry also requires the release-wide live execution gate."
            ),
        },
        "warnings": warnings,
        "blockers": blockers,
    }
