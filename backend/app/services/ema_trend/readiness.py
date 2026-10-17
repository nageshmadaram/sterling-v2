"""Connected-broker acceptance observations. Never an execution permission."""
import asyncio
from datetime import datetime

from app.engines.ema_trend.config import PROFILES, descriptor, stable_id
from app.engines.ema_trend.instruments import contract, entry_expiry_block
from app.services.kite_engine.settlement import settled_holding
from . import config_store, repository as repo, scanner


async def check(uid: str, symbol: str, mode: str, expected_revision: int) -> dict:
    from app.services.exchanges.kite import accounts
    from app.services.exchanges.kite.client import KiteClient
    from .execution import quote

    settings = config_store.get(uid)
    if settings["revision"] != expected_revision:
        raise repo.Conflict("STALE_REVISION")
    cfg = config_store.config(uid)
    if symbol not in cfg.symbols or mode not in cfg.modes:
        raise ValueError("CHECK_REQUIRES_SAVED_SYMBOL_AND_PROFILE")
    started = repo.now_ms()
    checks = []
    account_id = None

    def observation(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    try:
        account = accounts.get_active(repo.owner(uid))
        if not account or not account.connected or account.is_sim or account.is_paper:
            raise ValueError("AUTHENTICATED_LIVE_DATA_ACCOUNT_REQUIRED")
        account_id = account.id
        client = await asyncio.wait_for(accounts.acquire_client(account), 10)
        if not isinstance(client, KiteClient) or client._is_paper is not False or getattr(client, "_account_id", None) != account.id:
            raise ValueError("BROKER_CLIENT_IDENTITY_MISMATCH")
        profile = await asyncio.wait_for(client.get_profile(), 10)
        if not account.kite_user_id or profile.get("user_id") != account.kite_user_id:
            raise ValueError("BROKER_PROFILE_IDENTITY_MISMATCH")
        observation("broker_identity", "PASS", "Authenticated broker profile matches the selected account.")

        async def read(name, method, expected_type):
            try:
                value = await asyncio.wait_for(method(), 10)
                if not isinstance(value, expected_type):
                    raise ValueError("INVALID_PAYLOAD")
                observation(name, "PASS", "Fresh broker response; no write performed.")
                return value
            except Exception as exc:
                observation(name, "FAIL", type(exc).__name__)
                return None

        positions, holdings, orders, triggers = await asyncio.gather(
            read("positions", client.get_positions_raw, dict),
            read("holdings", client.get_holdings, list),
            read("order_book", client.get_orders, list),
            read("gtt_book", client.get_gtts, list))
        # Presence of a GTT is not proof of protection of an EMA-owned fill.
        observation("owned_broker_protection", "BLOCKED",
                    "EMA live ownership, protection placement/replacement and child-fill attribution are not integrated.")
        if positions is not None and not isinstance(positions.get("net"), list):
            observation("position_schema", "FAIL", "Broker net inventory is unavailable.")
        meta = await asyncio.wait_for(scanner.equity_metadata(uid, symbol, client), 10)
        info = contract({**meta.as_meta(), "instrument_token": meta.token}, mode)
        observation("exact_contract", "PASS", f"{symbol}; {info['product']}; lot {info['lot_size']}")
        day = datetime.now(scanner.IST).date()
        if entry_expiry_block(info, day):
            observation("expiry", "FAIL", "Expiry-day or expired instrument cannot originate exposure.")
        if info["product"] == "MIS":
            observation("protection_product", "BLOCKED", "MIS requires exchange stop lifecycle; GTT is not a substitute.")
        if info["product"] == "CNC" and holdings is not None:
            proof = settled_holding(holdings, info["exchange"], info["tradingsymbol"], day)
            observation("settled_inventory", "OBSERVED", proof)
            observation("delivery_authorisation", "PASS" if proof["sale_authorised"] else "UNVERIFIED",
                        "Existing holdings authorisation is not proof of future unattended sale authorisation.")
        elif info["expiry"]:
            observation("derivative_settlement", "BLOCKED", "Physical/cash settlement classification and pre-expiry closure acceptance required.")
        try:
            book = await asyncio.wait_for(quote(uid, symbol, mode, client=client), 10)
            observation("executable_quote", "PASS", {"quote_ms": int(book.quote_ts*1000), "bid": str(book.bid), "ask": str(book.ask)})
        except Exception as exc:
            observation("executable_quote", "FAIL", type(exc).__name__)
        try:
            tape = repo.get(uid, "tape", f"{symbol}:{mode}:signal")
            last = tape.get("bars", [])[-1]
            observation("recorded_signal_tape", "OBSERVED", {"last_close_ms": last["close_ms"], "provider": last.get("provider"),
                        "required_signal_minutes": PROFILES[mode].signal_minutes})
        except (repo.NotFound, IndexError, KeyError):
            observation("recorded_signal_tape", "UNVERIFIED", "Run the broker-data scan; no dataset inferred.")
    except Exception as exc:
        known = {"AUTHENTICATED_LIVE_DATA_ACCOUNT_REQUIRED", "BROKER_CLIENT_IDENTITY_MISMATCH",
                 "BROKER_PROFILE_IDENTITY_MISMATCH"}
        observation("connected_acceptance", "FAIL", str(exc) if str(exc) in known else type(exc).__name__)
    if config_store.get(uid)["revision"] != expected_revision:
        observation("config_revision", "FAIL", "Saved configuration changed during observation.")
    observation("live_execution_adapter", "BLOCKED", "Strategy remains restricted to canonical virtual execution.")
    observation("forward_acceptance", "UNVERIFIED", "One read-only observation cannot prove execution, restart, settlement or economic acceptance.")
    result = {"status": "BLOCKED", "live_eligible": False, "broker_writes": False,
              "symbol": symbol, "mode": mode, "account_id": account_id,
              "config_revision": expected_revision, "rule_hash": descriptor()["rule_hash"],
              "started_ms": started, "completed_ms": repo.now_ms(), "checks": checks,
              "coverage": "SELECTED_SYMBOL_AND_PROFILE_ONLY", "valid_for_execution": False}
    return repo.put(uid, "acceptance", stable_id(uid, symbol, mode, started), result, 0)
