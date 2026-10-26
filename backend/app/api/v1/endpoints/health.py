import time
from fastapi import APIRouter, Depends, Request
from app.core.auth import UserContext, require_admin
from pydantic import BaseModel
from typing import Literal, Optional
from app.core.config import settings

router = APIRouter()

_start_ms = int(time.time() * 1000)


class PositionsSummaryHealth(BaseModel):
    status: Literal["not_checked"] = "not_checked"
    open: Optional[int] = None
    partially_closed: Optional[int] = None
    closed: Optional[int] = None


class AlertsSummaryHealth(BaseModel):
    status: Literal["not_checked"] = "not_checked"
    active: Optional[int] = None
    triggered: Optional[int] = None


class HealthResponse(BaseModel):
    scope: Literal["liveness"] = "liveness"
    status: str
    version: str
    environment: str
    live_execution: bool  # False only when the STERLING_LIVE_EXECUTION kill switch is engaged
    real_public_data: bool
    default_underlying: str
    exchange_adapter: str
    exchange_reachable: Optional[bool] = None
    positions: PositionsSummaryHealth
    alerts: AlertsSummaryHealth
    cache_keys: Optional[int] = None
    uptime_seconds: int
    background_checker: Literal["not_checked"]
    timestamp_ms: int


def _live_execution_enabled() -> bool:
    """Whether new-entry origination is permitted (the operator kill switch is off).

    Read from the lane registry, which owns the master floor, so this endpoint can
    never disagree with what the execution path will actually do. Exits,
    protection and reconciliation keep working regardless of this value.
    """
    from app.core import lane_registry

    return bool(lane_registry.LIVE_EXECUTION_ENABLED)


@router.get("/health")
async def health(request: Request) -> HealthResponse:
    """Shallow process health, never evidence of broker connectivity or exposure.

    Account/alert inventory is deliberately not read on this public probe. Unknown
    counts stay null rather than implying a flat account or zero active alerts.
    Use authenticated broker inventory and deep health for their respective facts.
    """
    now_ms = int(time.time() * 1000)

    return HealthResponse(
        status="ok",
        version="0.4.0",
        environment=settings.environment,
        live_execution=_live_execution_enabled(),
        real_public_data=settings.real_public_data,
        default_underlying=settings.default_underlying,
        exchange_adapter="zerodha",
        exchange_reachable=None,
        positions=PositionsSummaryHealth(),
        alerts=AlertsSummaryHealth(),
        cache_keys=None,
        uptime_seconds=int((now_ms - _start_ms) / 1000),
        background_checker="not_checked",
        timestamp_ms=now_ms,
    )


@router.get("/health/deep")
def deep_health(request: Request, user: UserContext = Depends(require_admin)):
    """Comprehensive health check that verifies every critical subsystem (RT-10).

    Unlike /health which only proves the HTTP server is alive, this endpoint
    checks database, broker, telegram, stream and disk — the components whose
    failure leaves the trading engine blind or unable to protect positions.
    """
    import shutil
    from fastapi.responses import JSONResponse

    checks: dict = {}
    healthy = True

    workers = getattr(request.app.state, 'critical_workers', {})
    worker_status = {name: ('running' if task is not None and not task.done() else 'stopped')
                     for name, task in workers.items()}
    checks['workers'] = {'status': 'ok' if worker_status and all(
        value == 'running' for value in worker_status.values()) else 'unavailable',
        'tasks': worker_status}

    # Database
    try:
        from app.services import db
        db_ok = db.is_available()
        checks["database"] = {"status": "ok" if db_ok else "degraded"}
        if not db_ok:
            healthy = False
    except Exception:
        checks["database"] = {"status": "error", "detail": "Health check unavailable"}
        healthy = False

    # Disk space (CR-11)
    try:
        usage = shutil.disk_usage("/")
        pct_used = ((usage.total - usage.free) / usage.total) * 100
        free_gb = usage.free / (1024**3)
        disk_ok = pct_used < 95
        checks["disk"] = {
            "status": "ok" if disk_ok else "critical",
            "percent_used": round(pct_used, 1),
            "free_gb": round(free_gb, 2),
        }
        if not disk_ok:
            healthy = False
    except Exception:
        checks["disk"] = {"status": "error", "detail": "Health check unavailable"}

    # Broker connection
    try:
        from app.services.exchanges.kite import ticker_manager
        ticker_status = ticker_manager.status(user.user_id)
        broker_connected = ticker_status.get("connected", False)
        checks["broker"] = {
            "status": "ok" if broker_connected else "disconnected",
            **{k: v for k, v in ticker_status.items() if k in ("connected", "started", "last_tick_ms")},
        }
    except Exception:
        checks["broker"] = {"status": "unavailable", "detail": "Health check unavailable"}

    # Stream manager
    try:
        from app.api.v1.endpoints.stream import stream_manager
        n_clients = len(stream_manager.all_connections)
        n_channels = len(stream_manager.active_connections)
        checks["stream"] = {"status": "ok", "clients": n_clients, "channels": n_channels}
    except Exception:
        checks["stream"] = {"status": "error", "detail": "Health check unavailable"}

    # Telegram
    try:
        from app.services.notifications import telegram_bot
        poll = telegram_bot.poll_status()
        tg_ok = poll["state"] == "polling"
        checks["telegram"] = {"status": "ok" if tg_ok else poll["state"]}
    except Exception:
        checks["telegram"] = {"status": "not_configured"}

    healthy = healthy and all(check["status"] == "ok" for check in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "healthy" if healthy else "degraded", "checks": checks},
    )
