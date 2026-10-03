"""
Zerodha Kite Connect Diagnostics & Feed Verification Service.

Verifies end-to-end operational readiness of:
1. Internet & DNS connectivity
2. Kite API Endpoint Reachability (api.kite.trade)
3. Session & Profile Validity (/user/profile)
4. Margins & Cash Ledger (/user/margins)
5. Master Instruments Database
6. Historical OHLCV Candle Stream
7. Live Market Quotes & Order Depth (/quote)
8. GTT & Order Placement Subsystem
"""
from __future__ import annotations

import time
import socket
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

import httpx

from app.core.logging import get_logger
from app.services.exchanges.kite import accounts as kite_accounts
from app.services.exchanges.kite.models import KiteAccountResponse

log = get_logger(__name__)


@dataclass
class KiteDiagnosticFieldCheck:
    name: str
    status: str  # PASS | FAIL | WARNING | IDLE
    value: Any
    description: str


@dataclass
class KiteDiagnosticCategoryResult:
    id: str
    name: str
    icon: str
    status: str  # PASS | FAIL | WARNING | IDLE
    latency_ms: float
    source_origin: str
    symbol_tested: str
    summary: str
    metrics: Dict[str, Any] = field(default_factory=dict)
    field_checks: List[KiteDiagnosticFieldCheck] = field(default_factory=list)
    raw_sample: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None
    troubleshooting_tip: Optional[str] = None


@dataclass
class KiteDiagnosticSuiteResult:
    timestamp: str
    overall_status: str  # PASS | PARTIAL | FAIL | WARNING
    total_tests: int
    passed_count: int
    warning_count: int
    failed_count: int
    total_duration_ms: float
    authenticated: bool
    account_label: Optional[str]
    kite_user_id: Optional[str]
    is_paper: Optional[bool]
    categories: List[KiteDiagnosticCategoryResult] = field(default_factory=list)


def _check_socket_connectivity(host: str = "8.8.8.8", port: int = 53, timeout: float = 2.0) -> float:
    """Measures raw socket connection latency in ms."""
    t0 = time.perf_counter()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        sock.close()
        return round((time.perf_counter() - t0) * 1000, 2)
    except Exception:
        return -1.0


async def verify_network_connectivity() -> KiteDiagnosticCategoryResult:
    """Test Category: Internet & Network Health."""
    t0 = time.perf_counter()
    sock_latency = _check_socket_connectivity("8.8.8.8", 53)
    dns_latency = _check_socket_connectivity("1.1.1.1", 53)

    metrics = {
        "internet_status": "Online" if sock_latency >= 0 else "Offline",
        "dns_latency_ms": max(0.0, dns_latency),
        "primary_gateway": "Connected" if sock_latency >= 0 else "Unreachable",
    }
    field_checks = [
        KiteDiagnosticFieldCheck(
            name="DNS Socket Ping",
            status="PASS" if sock_latency >= 0 else "FAIL",
            value=f"{sock_latency:.1f} ms" if sock_latency >= 0 else "Timeout",
            description="DNS root server socket handshake",
        ),
        KiteDiagnosticFieldCheck(
            name="Cloudflare Edge Ping",
            status="PASS" if dns_latency >= 0 else "FAIL",
            value=f"{dns_latency:.1f} ms" if dns_latency >= 0 else "Timeout",
            description="1.1.1.1 low-latency edge resolution",
        ),
    ]

    total_lat = round((time.perf_counter() - t0) * 1000, 2)
    status = "PASS" if sock_latency >= 0 else "FAIL"
    return KiteDiagnosticCategoryResult(
        id="internet_network",
        name="Internet & Network Connectivity",
        icon="🌐",
        status=status,
        latency_ms=total_lat,
        source_origin="system_network",
        symbol_tested="Global Internet",
        summary=f"Internet status: {metrics['internet_status']} (DNS Latency: {metrics['dns_latency_ms']} ms)",
        metrics=metrics,
        field_checks=field_checks,
        troubleshooting_tip="Ensure network adapter is active and external DNS servers (8.8.8.8 / 1.1.1.1) are reachable.",
    )


async def verify_kite_api_reachability() -> KiteDiagnosticCategoryResult:
    """Test Category: Kite Connect Gateway Reachability."""
    t0 = time.perf_counter()
    url = "https://api.kite.trade"
    error = None
    status_code = 0
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(url)
            status_code = resp.status_code
    except Exception as exc:
        error = str(exc)

    latency = round((time.perf_counter() - t0) * 1000, 2)
    # Kite root URL responds with 200 or 403 (for no-auth requests), both prove gateway is alive
    is_alive = status_code in (200, 403, 401, 302)
    status = "PASS" if is_alive else "FAIL"

    field_checks = [
        KiteDiagnosticFieldCheck(
            name="Kite Trade HTTPS Gateway",
            status="PASS" if is_alive else "FAIL",
            value=f"HTTP {status_code} ({latency} ms)" if is_alive else "Unreachable",
            description="Official Zerodha Kite Connect REST entrypoint",
        ),
    ]

    return KiteDiagnosticCategoryResult(
        id="kite_gateway",
        name="Kite Connect API Gateway",
        icon="🔌",
        status=status,
        latency_ms=latency,
        source_origin="kite_rest",
        symbol_tested="api.kite.trade",
        summary=f"Kite Gateway active with HTTP response {status_code} ({latency} ms)",
        metrics={"status_code": status_code, "latency_ms": latency},
        field_checks=field_checks,
        error_message=error,
        troubleshooting_tip="Check firewall rules if api.kite.trade is blocked.",
    )


async def verify_kite_session(acct: Optional[Any]) -> KiteDiagnosticCategoryResult:
    """Test Category: Kite Authentication & Session."""
    t0 = time.perf_counter()
    if not acct:
        return KiteDiagnosticCategoryResult(
            id="kite_session",
            name="Kite Session & User Profile",
            icon="🔑",
            status="WARNING",
            latency_ms=0.0,
            source_origin="local_cache",
            symbol_tested="Account Profile",
            summary="No active Kite account configured",
            metrics={},
            field_checks=[
                KiteDiagnosticFieldCheck(
                    name="Account Configured",
                    status="WARNING",
                    value="None Active",
                    description="Add Kite API key and secret in Account & Login",
                )
            ],
            troubleshooting_tip="Go to Settings > Account & Login to add Kite API credentials.",
        )

    client = await kite_accounts.acquire_client(acct)
    user_name = "N/A"
    user_id = acct.kite_user_id or "N/A"
    error = None
    connected = acct.connected

    if connected:
        try:
            profile = await client.get_profile()
            if profile and isinstance(profile, dict):
                user_name = profile.get("user_name") or getattr(acct, "label", "User")
                user_id = profile.get("user_id") or user_id
                connected = True
        except Exception as exc:
            error = str(exc)
            connected = False

    latency = round(max(0.1, (time.perf_counter() - t0) * 1000), 2)
    status = "PASS" if connected else ("WARNING" if acct.has_credentials else "FAIL")

    field_checks = [
        KiteDiagnosticFieldCheck(
            name="Kite User ID",
            status="PASS" if user_id != "N/A" else "WARNING",
            value=user_id,
            description="Zerodha client account identifier",
        ),
        KiteDiagnosticFieldCheck(
            name="Account Name",
            status="PASS" if user_name != "N/A" else "WARNING",
            value=user_name,
            description="Verified Zerodha account holder",
        ),
        KiteDiagnosticFieldCheck(
            name="Daily Session State",
            status="PASS" if connected else "WARNING",
            value="Active & Connected" if connected else "Login Required (Expires 6 AM IST)",
            description="Daily Zerodha Kite Connect access token validity",
        ),
    ]

    return KiteDiagnosticCategoryResult(
        id="kite_session",
        name="Kite Session & User Profile",
        icon="🔑",
        status=status,
        latency_ms=latency,
        source_origin="kite_rest",
        symbol_tested=user_id,
        summary=f"User {user_id} ({user_name}) - {'Connected' if connected else 'Not Connected'}",
        metrics={"user_id": user_id, "user_name": user_name, "connected": connected},
        field_checks=field_checks,
        error_message=error,
        troubleshooting_tip="Daily session resets at 6:00 AM IST. Click 'Open Kite Login' in Settings to generate a new token." if not connected else None,
    )


async def verify_kite_margins(acct: Optional[Any]) -> KiteDiagnosticCategoryResult:
    """Test Category: Kite Margins & Cash Balance."""
    t0 = time.perf_counter()
    if not acct or not acct.connected:
        return KiteDiagnosticCategoryResult(
            id="kite_margins",
            name="Kite Margins & Capital Ledger",
            icon="💰",
            status="WARNING",
            latency_ms=0.0,
            source_origin="unverified",
            symbol_tested="Trading Margins",
            summary="Kite margin ledger not verified — active broker session required",
            metrics={"cash": None, "collateral": None},
            field_checks=[
                KiteDiagnosticFieldCheck(
                    name="Broker Margin Ledger",
                    status="WARNING",
                    value="Not measured",
                    description="Requires a successful authenticated /margins read",
                )
            ],
            troubleshooting_tip="Authenticate the active Kite account and rerun this diagnostic.",
        )

    client = await kite_accounts.acquire_client(acct)
    cash: Optional[float] = None
    collateral: Optional[float] = None
    error = None
    try:
        margins = await client.get_margins()
        if margins and isinstance(margins, dict):
            eq = margins.get("equity", {})
            cash = float(eq.get("available", {}).get("cash", 0.0))
            collateral = float(eq.get("available", {}).get("collateral", 0.0))
    except Exception as exc:
        error = str(exc)

    latency = round(max(0.1, (time.perf_counter() - t0) * 1000), 2)
    status = "PASS" if (not error and acct.connected) else "WARNING"

    field_checks = [
        KiteDiagnosticFieldCheck(
            name="Available Cash Balance",
            status="PASS" if cash is not None else "WARNING",
            value=f"₹{cash:,.2f}" if cash is not None else "Unavailable",
            description="Unencumbered intraday trading capital",
        ),
        KiteDiagnosticFieldCheck(
            name="Collateral Margin",
            status="PASS" if collateral is not None else "WARNING",
            value=f"₹{collateral:,.2f}" if collateral is not None else "Unavailable",
            description="Pledged securities margin",
        ),
    ]

    summary = (
        f"Available Cash: ₹{cash:,.2f} | Collateral: ₹{collateral:,.2f}"
        if error is None and cash is not None and collateral is not None
        else "Kite margin ledger could not be verified"
    )

    return KiteDiagnosticCategoryResult(
        id="kite_margins",
        name="Kite Margins & Capital Ledger",
        icon="💰",
        status=status,
        latency_ms=latency,
        source_origin="kite_rest" if not error else "unverified",
        symbol_tested="Equity & F&O Funds",
        summary=summary,
        metrics={"cash": cash, "collateral": collateral},
        field_checks=field_checks,
        error_message=error,
        troubleshooting_tip="Go to Settings > Account & Login and click 'Open Kite Login' to stream live funds balance.",
    )


async def verify_kite_historical(acct: Optional[Any]) -> KiteDiagnosticCategoryResult:
    """Test Category: Kite Historical Candle Stream."""
    t0 = time.perf_counter()
    symbol = "NIFTY 50"
    # Instrument token for NIFTY 50: 256265
    token = 256265
    error = None
    candles_count = 0
    last_close: Optional[float] = None
    source = "kite_historical" if (acct and acct.connected) else "unverified"

    if acct and acct.connected:
        client = await kite_accounts.acquire_client(acct)
        try:
            today = datetime.now()
            from_dt = (today - timedelta(days=2)).strftime("%Y-%m-%d")
            to_dt = today.strftime("%Y-%m-%d")
            res = await client.get_historical(token, "5minute", from_dt, to_dt)
            candles = res.get("candles", []) if isinstance(res, dict) else (res if isinstance(res, list) else [])
            if candles and len(candles) > 0:
                candles_count = len(candles)
                last_c = candles[-1]
                if isinstance(last_c, (list, tuple)) and len(last_c) >= 5:
                    last_close = float(last_c[4])
                elif isinstance(last_c, dict) and last_c.get("close") is not None:
                    last_close = float(last_c["close"])
        except Exception as exc:
            error = str(exc)
            source = "unverified"

    latency = round(max(0.1, (time.perf_counter() - t0) * 1000), 2)
    status = (
        "PASS"
        if acct and acct.connected and not error and candles_count > 0 and last_close is not None
        else "WARNING"
    )

    field_checks = [
        KiteDiagnosticFieldCheck(
            name="Kite Historical API Stream",
            status="PASS" if (acct and acct.connected and not error) else "WARNING",
            value="Active & Streaming" if (acct and acct.connected and not error) else (f"Fallback: {error}" if error else "Login Required"),
            description="Zerodha Kite Historical 5m candle API",
        ),
        KiteDiagnosticFieldCheck(
            name="NIFTY 50 5m Candle History",
            status="PASS" if candles_count > 0 and last_close is not None else "WARNING",
            value=(
                f"{candles_count} candles loaded (Last Close: ₹{last_close:,.2f})"
                if candles_count > 0 and last_close is not None
                else "Not measured"
            ),
            description="5-minute historical OHLCV candles",
        ),
    ]

    summary = (
        f"Historical Candle Feed: {candles_count} broker bars verified"
        if status == "PASS"
        else "Kite historical feed not verified" + (f": {error}" if error else "")
    )

    return KiteDiagnosticCategoryResult(
        id="kite_historical",
        name="Kite Historical Candle Feed",
        icon="📈",
        status=status,
        latency_ms=latency,
        source_origin=source,
        symbol_tested=symbol,
        summary=summary,
        metrics={"candles_count": candles_count, "last_close": last_close},
        field_checks=field_checks,
        error_message=error,
        troubleshooting_tip="Kite historical API requires Kite Connect subscription with Historical API add-on." if error else None,
    )


async def verify_kite_quotes(acct: Optional[Any]) -> KiteDiagnosticCategoryResult:
    """Test Category: Kite Live Quotes & Depth."""
    t0 = time.perf_counter()
    instrument = "NSE:NIFTY 50"
    error = None
    ltp: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    source = "kite_quote" if (acct and acct.connected) else "unverified"

    if acct and acct.connected:
        client = await kite_accounts.acquire_client(acct)
        try:
            q = await client.get_quote([instrument])
            if q and isinstance(q, dict) and instrument in q:
                d = q[instrument]
                if d.get("last_price") is not None:
                    ltp = float(d["last_price"])
                depth = d.get("depth") or {}
                buys = depth.get("buy") or []
                sells = depth.get("sell") or []
                if buys and buys[0].get("price") is not None:
                    bid = float(buys[0]["price"])
                if sells and sells[0].get("price") is not None:
                    ask = float(sells[0]["price"])
        except Exception as exc:
            error = str(exc)
            source = "unverified"

    latency = round(max(0.1, (time.perf_counter() - t0) * 1000), 2)
    quote_ok = bool(ltp is not None and ltp > 0)
    depth_ok = bool(bid is not None and ask is not None and 0 < bid <= ask)
    status = "PASS" if acct and acct.connected and not error and quote_ok and depth_ok else "WARNING"

    field_checks = [
        KiteDiagnosticFieldCheck(
            name="Spot Quote LTP",
            status="PASS" if quote_ok else "WARNING",
            value=f"₹{ltp:,.2f}" if ltp is not None else "Not measured",
            description="Broker-returned last traded price",
        ),
        KiteDiagnosticFieldCheck(
            name="Two-Sided L1 Depth",
            status="PASS" if depth_ok else "WARNING",
            value=(
                f"Bid ₹{bid:,.2f} / Ask ₹{ask:,.2f}"
                if depth_ok else "Not verified"
            ),
            description="Executable top-of-book evidence",
        ),
    ]

    return KiteDiagnosticCategoryResult(
        id="kite_quotes",
        name="Kite Live Quotes & L1 Depth",
        icon="⚡",
        status=status,
        latency_ms=latency,
        source_origin=source,
        symbol_tested=instrument,
        summary=(
            f"Broker quote verified for {instrument}: ₹{ltp:,.2f}"
            if status == "PASS" and ltp is not None
            else f"Kite quote/depth not verified for {instrument}"
        ),
        metrics={"ltp": ltp, "bid": bid, "ask": ask, "instrument": instrument},
        field_checks=field_checks,
        error_message=error,
        troubleshooting_tip="Quotes API provides fast L1 market data for strategy triggers.",
    )


async def verify_kite_orders_gtt(acct: Optional[Any]) -> KiteDiagnosticCategoryResult:
    """Test Category: Kite GTT & Order Subsystem."""
    t0 = time.perf_counter()
    gtt_count: Optional[int] = None
    error = None
    gtt_read_ok = False

    if acct and acct.connected:
        client = await kite_accounts.acquire_client(acct)
        try:
            triggers = await client.get_gtts()
            if isinstance(triggers, list):
                gtt_count = len(triggers)
                gtt_read_ok = True
        except Exception as exc:
            error = str(exc)

    raw_mode = getattr(acct, "is_paper", None) if acct else None
    mode = raw_mode if raw_mode is True or raw_mode is False else None
    latency = round((time.perf_counter() - t0) * 1000, 2)
    status = "PASS" if acct and acct.connected and gtt_read_ok and mode is not None else "WARNING"

    field_checks = [
        KiteDiagnosticFieldCheck(
            name="GTT Rule Subsystem",
            status="PASS" if gtt_read_ok else "WARNING",
            value=f"{gtt_count} Active GTT Rules" if gtt_count is not None else "Not verified",
            description="Broker-read Good-Till-Triggered protection inventory",
        ),
        KiteDiagnosticFieldCheck(
            name="Order Routing Execution Mode",
            status="PASS" if mode is not None else "WARNING",
            value=(
                "Paper Sandbox" if mode is True
                else "Live Broker Direct" if mode is False
                else "Unknown"
            ),
            description="Verified active-account execution mode",
        ),
    ]

    return KiteDiagnosticCategoryResult(
        id="kite_orders_gtt",
        name="Kite Orders & GTT Protection",
        icon="🛡️",
        status=status,
        latency_ms=latency,
        source_origin="kite_orders",
        symbol_tested="Order System",
        summary=(
            f"GTT inventory verified ({gtt_count} triggers) | "
            f"Mode: {'Paper' if mode is True else 'Live'}"
            if status == "PASS"
            else "GTT/order-routing state not fully verified"
        ),
        metrics={"gtt_count": gtt_count, "is_paper": mode},
        field_checks=field_checks,
        error_message=error,
        troubleshooting_tip="GTT orders protect positions without requiring continuous WebSocket connection.",
    )


async def verify_kite_instruments(acct: Optional[Any] = None) -> KiteDiagnosticCategoryResult:
    """Measure the actual broker instrument master; never report a canned count."""
    t0 = time.perf_counter()
    symbol_count = 0
    segments: list[str] = []
    error = None
    if acct and acct.connected:
        try:
            client = await kite_accounts.acquire_client(acct)
            rows = await client.search_instruments("", "", limit=200_000)
            symbol_count = len(rows or [])
            segments = sorted({
                str((row or {}).get("segment") or (row or {}).get("exchange") or "").strip()
                for row in (rows or [])
                if str((row or {}).get("segment") or (row or {}).get("exchange") or "").strip()
            })
        except Exception as exc:
            error = str(exc)

    latency = round(max(0.1, (time.perf_counter() - t0) * 1000), 2)
    status = "PASS" if symbol_count > 0 and not error else "WARNING"
    field_checks = [
        KiteDiagnosticFieldCheck(
            name="Master Instrument Roster",
            status="PASS" if symbol_count > 0 else "WARNING",
            value=f"{symbol_count:,} broker rows" if symbol_count > 0 else "Not measured",
            description="Actual Kite instrument master returned for this session",
        ),
        KiteDiagnosticFieldCheck(
            name="Segment Coverage",
            status="PASS" if segments else "WARNING",
            value=" / ".join(segments[:12]) if segments else "Not measured",
            description="Segments observed in the returned instrument master",
        ),
    ]
    return KiteDiagnosticCategoryResult(
        id="kite_instruments",
        name="Master Instruments & Symbol Index",
        icon="📚",
        status=status,
        latency_ms=latency,
        source_origin="kite_instruments_db" if status == "PASS" else "unverified",
        symbol_tested="Kite instrument master",
        summary=(
            f"Verified {symbol_count:,} broker instrument rows"
            if status == "PASS"
            else "Kite instrument master not verified"
        ),
        metrics={"total_instruments": symbol_count if symbol_count > 0 else None, "segments": segments},
        field_checks=field_checks,
        error_message=error,
        troubleshooting_tip="Authenticate Kite and rerun to verify the current instrument master.",
    )


async def run_kite_diagnostics(
    user_id: str,
    category_id: Optional[str] = None,
) -> KiteDiagnosticSuiteResult:
    """Runs all or specific Kite diagnostic categories."""
    t_start = time.perf_counter()
    acct = kite_accounts.get_active(user_id)
    is_authenticated = bool(acct and acct.connected)
    account_label = acct.label if acct else None
    kite_user_id = acct.kite_user_id if acct else None
    raw_mode = getattr(acct, "is_paper", None) if acct else None
    is_paper = raw_mode if raw_mode is True or raw_mode is False else None

    categories: List[KiteDiagnosticCategoryResult] = []

    runners = {
        "internet_network": verify_network_connectivity,
        "kite_gateway": verify_kite_api_reachability,
        "kite_session": lambda: verify_kite_session(acct),
        "kite_margins": lambda: verify_kite_margins(acct),
        "kite_instruments": lambda: verify_kite_instruments(acct),
        "kite_historical": lambda: verify_kite_historical(acct),
        "kite_quotes": lambda: verify_kite_quotes(acct),
        "kite_orders_gtt": lambda: verify_kite_orders_gtt(acct),
    }

    if category_id and category_id in runners:
        res = await runners[category_id]()
        categories.append(res)
    else:
        for cat_id, runner in runners.items():
            res = await runner()
            categories.append(res)

    total_duration = round((time.perf_counter() - t_start) * 1000, 2)
    passed_count = sum(1 for c in categories if c.status == "PASS")
    warning_count = sum(1 for c in categories if c.status == "WARNING")
    failed_count = sum(1 for c in categories if c.status == "FAIL")

    if failed_count == 0 and warning_count == 0:
        overall = "PASS"
    elif failed_count == 0:
        overall = "WARNING"
    elif passed_count > 0:
        overall = "PARTIAL"
    else:
        overall = "FAIL"

    return KiteDiagnosticSuiteResult(
        timestamp=datetime.now(timezone.utc).isoformat(),
        overall_status=overall,
        total_tests=len(categories),
        passed_count=passed_count,
        warning_count=warning_count,
        failed_count=failed_count,
        total_duration_ms=total_duration,
        authenticated=is_authenticated,
        account_label=account_label,
        kite_user_id=kite_user_id,
        is_paper=is_paper,
        categories=categories,
    )
