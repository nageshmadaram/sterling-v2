import os
import time
from contextlib import asynccontextmanager

# Resolve STERLING_* from backend/.env exactly as run_backend.sh does, BEFORE any module
# reads them: otherwise `python3 main.py` and the supervised launch pick different
# databases and state files from the same .env.
from app.core.runtime_env import load_sterling_env as _load_sterling_env

_load_sterling_env()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.core.observability import (
    configure_json_logging, new_correlation_id, set_correlation_id, reset_correlation_id,
)
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.backtest import router as backtest_router
from app.api.v1.endpoints.candles import router as candles_router
from app.api.v1.endpoints.config import router as config_router
import secrets
from app.core.csp import reset_csp_nonce, set_csp_nonce

log = get_logger(__name__)


def _kite_env_live_credentials_present() -> bool:
    """Would startup seed a real Kite account from the environment?"""
    api_key = (
        os.environ.get("KITE_API_KEY")
        or os.environ.get("ZERODHA_API_KEY")
        or ""
    ).strip()
    api_secret = (
        os.environ.get("KITE_API_SECRET")
        or os.environ.get("ZERODHA_API_SECRET")
        or ""
    ).strip()
    return bool(api_key and api_secret)


def _live_broker_authority_present() -> bool:
    """Virtual simulation accounts do not confer real broker authority."""
    from app.services.exchanges.kite import accounts

    return bool(accounts.live_accounts()) or _kite_env_live_credentials_present()


async def _background_kite_alerts(interval: int = 60) -> None:
    """Push new Kite engine signals to Telegram during Indian market hours."""
    import asyncio
    from app.services.notifications import telegram_kite as _kbot
    from app.services.kite_engine.market_hours import is_market_open
    await asyncio.sleep(12)
    while True:
        try:
            await _kbot.push_kite_alerts(include_signals=is_market_open())
        except Exception as exc:  # noqa: BLE001
            # Never copy exception text: some transport exceptions contain
            # bot-token-bearing API URLs. Individual failures already retry
            # from the durable outbox without acknowledging delivery.
            log.warning("kite alert push failed (%s)", type(exc).__name__)
        await asyncio.sleep(interval)


async def _supervise_telegram(name: str, runner, *, restart_delay: float = 5.0) -> None:
    """Restart a crashed Telegram I/O worker without touching the broker.

    A task that returns or raises unexpectedly would otherwise silently stop
    all bot commands or all signal/lifecycle alerts until the next backend
    restart. Await exactly one runner at a time: concurrent Telegram getUpdates
    calls on the same token cause 409 poll conflicts. On shutdown cancellation
    propagates immediately and never schedules a replacement.
    """
    import asyncio
    while True:
        try:
            await runner()
            log.error("Telegram %s worker exited unexpectedly; restarting", name)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - external transport is not a process killer
            # Never log exception text: Telegram request URLs contain bot tokens.
            log.error("Telegram %s worker failed (%s); restarting", name, type(exc).__name__)
        await asyncio.sleep(restart_delay)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services import db
    from app.core.runtime_lease import engine_lease
    from app.core.event_loop_watchdog import watch_event_loop
    async with watch_event_loop(threshold=5.0):
        with engine_lease(db._DB_PATH):
            async with _application_lifespan(app):
                yield


@asynccontextmanager
async def _application_lifespan(app: FastAPI):
    setup_logging()
    configure_json_logging()  # no-op unless settings.log_json (Phase 2 observability)
    # The SQLite store must be open BEFORE anything reads it (including production security credential inspection).
    from app.services import db as _db
    if not _db.init():
        raise RuntimeError("Durable trading database unavailable; execution startup refused.")
    log.info("Database: %s", getattr(_db, "_DB_PATH", "?"))

    # >>> live-posture: a stable deployment id (generated once, exported to this process) so the
    # account binding needs no ceremony, and ONE plain line when live trading lacks secure keys.
    from app.services import live_posture as _live_posture
    try:
        _live_posture.boot_deployment_id()
    except Exception as exc:  # noqa: BLE001 - the binding gate then refuses with its own reason
        log.error("Deployment id could not be established: %s", exc)
    # <<< live-posture

    from app.core.security import (
        validate_live_security,
        validate_production_security,
    )
    try:
        validate_production_security()
    except RuntimeError as exc:  # noqa: PERF203 - one plain line, then the original refusal
        _live_posture.announce_secure_keys_needed(exc)
        raise

    # Load broker metadata before identity seeding. Reading stored rows does not
    # decrypt secrets, so it is safe to use solely to answer whether this process
    # already carries (or is about to seed) real broker authority.
    from app.services.exchanges.kite import accounts as _kite_accounts
    _kite_accounts.bootstrap()
    live_broker_posture = _live_broker_authority_present()

    # ENVIRONMENT is not a money boundary. If this process has, or is configured
    # to create, any real Kite account, require production-grade crypto/browser
    # posture before a credential can be decrypted, encrypted or sent anywhere.
    live_security_problem = None
    try:
        validate_live_security()
    except Exception as exc:  # noqa: BLE001 - only fatal once real broker authority exists
        live_security_problem = exc
        _live_posture.announce_secure_keys_needed(exc)   # live-posture: also before any account exists
    if live_broker_posture and live_security_problem is not None:
        raise live_security_problem

    # First-party operator identity is part of live safety, not an optional UI
    # feature. With no broker authority, local loopback development can remain
    # anonymous. Once real broker authority exists, an active Argon2id admin is
    # mandatory regardless of ENVIRONMENT.
    from app.services.auth import user_store as _user_store
    _user_store.bootstrap()
    from app.core import runtime_env as _runtime_env
    if _runtime_env.is_production() or live_broker_posture:
        if not _user_store.has_active_admin_credential():
            scope = "Live broker" if live_broker_posture else "Production"
            raise RuntimeError(
                f"{scope} authentication error: no active Argon2id-backed admin "
                "account exists. Configure STERLING_ADMIN_PASSWORD or repair/reset "
                "the administrator before startup."
            )

    # Sterling is live-only. An older build may have stored an account as
    # non-live; convert it (idempotent, audited, refused if it holds any local
    # exposure) before anything builds a broker client from it.
    try:
        _kite_accounts.migrate_legacy_paper_accounts()
    except Exception as exc:  # noqa: BLE001
        if live_broker_posture:
            raise RuntimeError(
                f"Live broker account conversion/preflight failed: {exc}"
            ) from exc
        log.error("Legacy Kite account conversion failed: %s", exc)

    # Adopt KITE_API_KEY / KITE_API_SECRET (and optionally KITE_ACCESS_TOKEN) from
    # the environment so a fresh DB or a new machine boots already provisioned.
    # Explicit live credentials are a startup contract: do not log-and-continue
    # with a half-provisioned unattended trading process.
    try:
        from app.services.exchanges.kite import auth as _kite_auth
        _kite_auth.seed_from_env()
    except Exception as exc:  # noqa: BLE001
        if _kite_env_live_credentials_present():
            raise RuntimeError(f"Kite environment provisioning failed: {exc}") from exc
        log.warning("Kite env seeding skipped: %s", exc)
    # >>> simulation: the owner's virtual tenant (account, ledger, control plane, virtual-capital limits).
    # It can never stop live trading: a failure here is logged and the process carries on without it.
    try:
        from app.services.simulation import tenant as _sim_tenant
        _sim_tenant.boot_tenant(_sim_tenant.configured_owner_uid())
    except Exception as exc:  # noqa: BLE001
        log.error("Simulation tenant boot failed (live trading is unaffected): %s", exc)
    # <<< simulation
    # Init OHLCV table and kick off first fetch in background (non-blocking)
    from app.services.ohlcv_store import init_ohlcv_table
    init_ohlcv_table()
    
    # Restore the backwards-compatible single-bot transport. Environment
    # credentials win; encrypted DB values are only a fallback.
    try:
        import app.services.notifications.telegram as _tg_mod
        _tg_mod.restore_legacy_config_from_store()
        log.info("Telegram config restored: token=%s chat=%s",
                 "set" if _tg_mod.TELEGRAM_TOKEN else "—",
                 "set" if _tg_mod.TELEGRAM_CHAT_ID else "—")
    except Exception as _e:
        log.warning("Telegram config restore skipped: %s", _e)

    log.info("Startup complete")

    # >>> trade-journal-backfill: first boot only, import trades that closed before the journal existed
    try:
        from app.services import trade_journal_backfill as _tj_backfill
        _tj_backfill.run_once()
    except Exception as _exc:  # noqa: BLE001 - the journal never blocks startup
        log.warning("trade journal backfill skipped: %s", _exc)
    # <<< trade-journal-backfill

    import asyncio

    # Kite tick stream: restart it if its task dies.
    #
    # The stream's own reconnect loop handles a dropped socket. This covers the
    # case that loop cannot: the task itself finishing. `ticker_manager.ensure()`
    # repairs that, but only when something calls it, and its only caller is a
    # subscribe — which the frontend issues when its token set changes. An
    # operator watching a board of live prices changes nothing, so nothing
    # triggered the repair and every price sat on the 30-second REST heartbeat
    # looking alive.
    from app.services.exchanges.kite import ticker_manager as _kite_ticker_manager
    ticker_watchdog_task = asyncio.create_task(_kite_ticker_manager.supervise(interval=30))
    log.info("Kite ticker watchdog started (every 30s)")

    # Live runtime boot (safe-mode initialisation, startup recovery, broker
    # exposure observation). Every individual safety control fails closed, so a
    # machine nothing initialises can never open a position. This step supplies
    # the evidence those controls ask for: SAFE_MODE becomes NORMAL only when the
    # system is provably clean, recovery runs per live account as soon as it has
    # a broker session (retrying with backoff, observe-only, never resubmitting),
    # and the broker's exposure is re-observed every 20s. It starts BEFORE the
    # strategy loops so the first scan sees the real safety state.
    from app.services import live_runtime as _live_runtime
    live_runtime_handles = _live_runtime.start_live_runtime()

    # >>> simulation: virtual-exchange loop (resting orders, GTTs, marks) and the tenant's own recovery loop.
    # Both are best-effort and isolated from live trading; a failure to start is logged, never raised.
    sim_runtime_task = sim_recovery_task = None
    try:
        from app.services.simulation import sim_runtime as _sim_runtime, tenant as _sim_tenant_rt
        sim_runtime_task = _sim_runtime.start_sim_runtime()
        sim_recovery_task = asyncio.create_task(
            _sim_tenant_rt.recovery_loop(_sim_tenant_rt.configured_owner_uid()), name="sim-recovery")
        log.info("Simulation runtime started (virtual money, no real orders)")
    except Exception as exc:  # noqa: BLE001
        log.error("Simulation runtime failed to start (live trading is unaffected): %s", exc)
    # <<< simulation

    # ── Snapback live dispatcher ─────────────────────────────────────────────
    # The one loop that manages every Snapback position: exits (stop, trail, target,
    # time, hedge), protection retry, plan recovery, book reconciliation and AUTO
    # origination. Without it a manually armed entry is placed and then nothing
    # manages it. It runs regardless of AUTO, the kill switch or safe mode: those
    # only gate new entries, never the management of an open position.
    from app.services import snapback_dispatcher as _snapback_dispatcher
    snapback_dispatcher_task = _snapback_dispatcher.start()
    log.info("Snapback live dispatcher started (exits, protection, recovery, AUTO origination)")
    # ── end Snapback live dispatcher ─────────────────────────────────────────

    # Release scope: Supertrend, Snapback and Gamma Unwind all run. Family Mode
    # (STERLING_FAMILY_MODE) is retired as a strategy gate: if it is still set in
    # the environment it is reported at startup by the live runtime and ignored
    # here. Day-one prudence comes from the risk caps and exposure limits, not
    # from silencing a strategy.
    #
    # Gamma Unwind: scheduled zones/watchlists + closed-5m E2 + live-tick E1.
    # The loop reconciles against the broker before its first scan, so a restart
    # cannot open a second position in a contract it already holds.
    from app.services.gamma_move_runner import auto_scan_loop as _gamma_move_scan
    gamma_move_task = asyncio.create_task(_gamma_move_scan(interval=300))
    from app.services.price_rules import run_loop as _price_rules_loop
    price_rules_task = asyncio.create_task(_price_rules_loop())
    # Independent virtual-money EMA campaign supervision. Disabled scanners
    # still reconcile fills and manage any already-owned virtual exposure.
    from app.services.ema_trend import runner as _ema_trend_runner
    ema_trend_task = _ema_trend_runner.start()

    # Snapback board refresh is display-only: it does not write prospective
    # evidence. The post-close prospective scanner is the sole authoritative
    # evidence writer.
    from app.services.snapback_board_loop import auto_scan_loop as _snapback_board_scan
    snapback_board_task = asyncio.create_task(_snapback_board_scan(interval=900))

    # Archived runners: maintain existing positions (exits/stops) but do NOT scan
    # for new entries. This reduces CPU, memory and polling overhead (SC-01).
    from app.services.adaptive_edge_runner import auto_scan_loop as _adaptive_edge_scan
    adaptive_edge_task = asyncio.create_task(
        _adaptive_edge_scan(interval=60, manage_only=True)
    )
    from app.services.intraday_runner import auto_scan_loop as _intraday_scan
    intraday_task = asyncio.create_task(
        _intraday_scan(interval=300, manage_only=True)
    )
    # Startup invariant: the frozen prospective runner starts only when the evidence
    # database is writable, the manifest verifies, and the Snapback config loaded from
    # a reachable store with the frozen hash. A failure must be loud, never a silent
    # "defaults OFF" runtime that looks alive but can never produce a signal.
    from app.services.snapback_preflight import run_preflight
    from app.services.snapback_health import record_error, clear_error

    # The composed check, not the narrow config-only one. Splitting them made a
    # contradiction possible: /health/ready could answer 503 on a bad clock, a
    # full disk, a schema mismatch or the wrong experiment's database while the
    # runner started anyway and wrote evidence under exactly those conditions.
    snapback_preflight = run_preflight()
    snapback_runner_task = None
    if snapback_preflight.may_start_runner:
        clear_error("startup_preflight_failed")
        clear_error("recovery_required")
        clear_error("prospective_runner_unavailable")
        from app.services.snapback_runner import start as start_snapback_runner
        snapback_runner_task = start_snapback_runner()
        log.info("Snapback prospective unattended runner started (every 30s)")
    else:
        # The prospective evidence runner is optional for the live release: its
        # failure is recorded under its own key, which the live gate ignores.
        record_error("prospective_runner_unavailable", ", ".join(snapback_preflight.errors))
        log.warning(
            "SNAPBACK PREFLIGHT %s — evidence runner NOT started (live Snapback is unaffected). "
            "Failed checks: %s. Details: %s",
            snapback_preflight.status,
            snapback_preflight.errors,
            {c.code: c.details for c in snapback_preflight.required_failures},
        )

    from app.services.snapback_ops_scheduler import start as start_snapback_ops
    snapback_ops_task = start_snapback_ops()
    log.info("Snapback operations scheduler started (health every 60s, post-market chain after 15:40 IST)")

    log.info("Archived-strategy loops started; new scans and entries require per-user opt-in")

    # Kite Sterling Kite Engine — background auto-scan of connected Kite
    # accounts (advisory by default; gated auto-exec when the user enables it).
    # First reconcile each account's auto-open guard against the broker's real
    # positions: the guard is DB-persisted across restarts, but a position may
    # have closed/expired while we were down — reconciling prevents both a stale
    # guard (forever-blocked re-entry) and a dropped guard (double-entry).
    from app.services.kite_engine.service import (
        auto_scan_loop as _kite_auto_scan,
        reconcile_all_auto_open as _kite_reconcile_auto_open,
    )
    try:
        await _kite_reconcile_auto_open()
    except Exception as exc:  # noqa: BLE001
        log.warning("Kite auto-open startup reconcile failed: %s", exc)
    kite_engine_task = asyncio.create_task(_kite_auto_scan())
    log.info("Kite Sterling Kite Engine auto-scan loop started (every 5 min)")

    # Kite session keeper — renews access tokens shortly before the 06:00 IST reset
    # for accounts Zerodha issued a refresh_token to. The strategy engines run
    # headless, so without this a token that lapses overnight leaves the 09:15
    # scans sessionless until someone opens the UI.
    from app.services.exchanges.kite.auth import session_keeper_loop as _kite_session_keeper
    kite_session_task = asyncio.create_task(_kite_session_keeper())
    log.info("Kite session keeper started (every 10 min)")

    # Archived scanners start idle; each connected user must explicitly restore
    # the engine in Settings before it scans. Execution gates remain unchanged.
    from app.services.navigator.runtime import auto_scan_loop as _navigator_scan
    navigator_task = asyncio.create_task(_navigator_scan())
    from app.services.opening_volume_runner import start as _opening_start
    opening_volume_task = _opening_start()

    # ── Telegram bot + Kite signal alerts ────────────────────────────────────
    from app.services.notifications import telegram_bot as _tg_bot
    tg_bot_task = asyncio.create_task(_supervise_telegram("control", _tg_bot.poll_loop))
    tg_kite_alert_task = asyncio.create_task(_supervise_telegram(
        "alerts", lambda: _background_kite_alerts(interval=15)))
    log.info("Telegram control + lifecycle/signal alert workers supervised")

    # ── Live event bus + agents (Phase 3) — only when enable_event_bus is set ──
    from app.core.config import settings as _settings
    if getattr(_settings, "enable_event_bus", False):
        try:
            from app.bus.event_bus import EventBus
            from app.agents import PNLAgent, ReconciliationAgent, Orchestrator
            from app.services import event_emit
            _bus = EventBus()
            _pnl_agent = PNLAgent(bus=_bus)
            _recon_agent = ReconciliationAgent(bus=_bus)
            _orchestrator = Orchestrator(bus=_bus, heartbeat_interval=60.0)
            event_emit.configure(_bus, {
                "pnl": _pnl_agent, "reconciliation": _recon_agent,
                "orchestrator": _orchestrator,
            })
            app.state.event_bus = _bus
            app.state.pnl_agent = _pnl_agent
            app.state.orchestrator = _orchestrator
            await _orchestrator.start()
            log.info("Live event bus + agents started (PNL/Reconciliation, heartbeat 60s)")
        except Exception as exc:
            log.warning("event bus startup failed (non-fatal): %s", exc)

    from app.services.notifications.web_push import run as run_web_push
    web_push_task = asyncio.create_task(run_web_push())

    app.state.critical_workers = {
        'supertrend': kite_engine_task,
        'gamma_unwind': gamma_move_task,
        'snapback_board': snapback_board_task,
        'snapback_execution': snapback_dispatcher_task,
        'ema_trend': ema_trend_task,
        'ticker_watchdog': ticker_watchdog_task,
        'broker_session_keeper': kite_session_task,
        'telegram_control': tg_bot_task,
        'telegram_alerts': tg_kite_alert_task,
    }
    app.state.critical_workers.update({f'live_safety_{i}': task for i, task in enumerate(live_runtime_handles.tasks)})
    yield

    web_push_task.cancel()
    await asyncio.gather(web_push_task, return_exceptions=True)

    try:
        _orch = getattr(app.state, "orchestrator", None)
        if _orch is not None:
            await _orch.stop()
        from app.services import event_emit as _ee
        _ee.reset()
    except Exception as _exc:
        log.debug("suppressed: %s", _exc)

    # Snapback live dispatcher: every exit step is durable, so a cancel mid-cycle
    # resumes from the stored step on the next start.
    _snapback_dispatcher.stop()
    if snapback_dispatcher_task is not None:
        try:
            await snapback_dispatcher_task
        except (Exception, BaseException):
            pass

    # >>> simulation: stop first, it reads prices through the owner's live session
    try:
        if sim_recovery_task is not None:
            sim_recovery_task.cancel()
            try:
                await sim_recovery_task
            except (Exception, BaseException):
                pass
        from app.services.simulation import sim_runtime as _sim_runtime_stop
        await _sim_runtime_stop.stop_sim_runtime()
    except Exception as exc:  # noqa: BLE001 - shutdown must never raise
        log.warning("Simulation runtime stop failed: %s", exc)
    # <<< simulation

    await _live_runtime.stop_live_runtime(live_runtime_handles)

    for _t in (tg_bot_task, tg_kite_alert_task):
        _t.cancel()
        try:
            await _t
        except (Exception, BaseException):
            pass
    from app.services.notifications.telegram_kite import close_scan_tasks
    await close_scan_tasks()

    if kite_engine_task is not None:
        kite_engine_task.cancel()
        try:
            await kite_engine_task
        except (Exception, BaseException):
            pass
    kite_session_task.cancel()
    try:
        await kite_session_task
    except (Exception, BaseException):
        pass
    if navigator_task is not None:
        navigator_task.cancel()
        try:
            await navigator_task
        except (Exception, BaseException):
            pass
    if opening_volume_task is not None:
        from app.services.opening_volume_runner import stop as _opening_volume_stop
        _opening_volume_stop()
        if not opening_volume_task.done():
            opening_volume_task.cancel()
        try:
            await opening_volume_task
        except (Exception, BaseException):
            pass
    if snapback_board_task is not None:
        snapback_board_task.cancel()
        try:
            await snapback_board_task
        except (Exception, BaseException):
            pass
    await _ema_trend_runner.stop()
    price_rules_task.cancel()
    try:
        await price_rules_task
    except asyncio.CancelledError:
        pass
    if gamma_move_task is not None:
        gamma_move_task.cancel()
        try:
            await gamma_move_task
        except (Exception, BaseException):
            pass

    if adaptive_edge_task is not None:
        adaptive_edge_task.cancel()
        try:
            await adaptive_edge_task
        except (Exception, BaseException):
            pass

    if intraday_task is not None:
        intraday_task.cancel()
        try:
            await intraday_task
        except (Exception, BaseException):
            pass

    from app.services.snapback_runner import stop as stop_snapback_runner
    stop_snapback_runner()
    if snapback_runner_task and not snapback_runner_task.done():
        snapback_runner_task.cancel()
        try:
            await snapback_runner_task
        except (Exception, BaseException):
            pass

    from app.services.snapback_ops_scheduler import stop as stop_snapback_ops
    stop_snapback_ops()
    if snapback_ops_task and not snapback_ops_task.done():
        snapback_ops_task.cancel()
        try:
            await snapback_ops_task
        except (Exception, BaseException):
            pass

    ticker_watchdog_task.cancel()
    try:
        await ticker_watchdog_task
    except asyncio.CancelledError:
        pass

    # A crashed process and a cleanly stopped one leave identical evidence behind
    # unless one of them says so.
    try:
        from app.services.snapback_deployment import record_shutdown

        record_shutdown(reason="lifespan_shutdown")
    except Exception as exc:  # noqa: BLE001 - shutdown must never raise
        log.warning("Snapback shutdown record failed: %s", exc)

    log.info("Sterling shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Sterling",
        description="Indian markets options platform — engine-driven",
        version="0.4.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=r"^https?://([a-zA-Z0-9-]+\.)*trycloudflare\.com(:[0-9]+)?$|^https?://localhost(:[0-9]+)?$|^https?://127\.0\.0\.1(:[0-9]+)?$|^https?://(192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)[0-9.]*(:[0-9]+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request-size limit, no-store API responses and the operator audit trail
    # (app/core/http_hardening.py). Pure ASGI, so it never buffers a stream.
    from app.core.http_hardening import install_http_hardening
    install_http_hardening(app)

    @app.middleware("http")
    async def _security_headers(request, call_next):
        # Correlation id: honor an inbound one or mint a fresh id, bind it for
        # the request's logging context, and echo it back. (Phase 2 observability)
        cid = request.headers.get("X-Correlation-ID") or new_correlation_id()
        _cid_token = set_correlation_id(cid)
        # Minted BEFORE the handler runs so the handler can stamp it on the tags
        # it emits. A nonce the page never sees is a nonce that blocks the page.
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        _nonce_token = set_csp_nonce(nonce)
        try:
            response = await call_next(request)
        finally:
            reset_correlation_id(_cid_token)
            reset_csp_nonce(_nonce_token)
        response.headers["X-Correlation-ID"] = cid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # CSP — allow Telegram Mini App framing (web.telegram.org, t.me, *.telegram.org).
        if request.url.path in {"/tma", "/api/v1/tma", "/tma/operator"}:
            response.headers["Content-Security-Policy"] = (
                f"default-src 'none'; script-src 'nonce-{nonce}'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src https://fonts.gstatic.com; connect-src 'self'; img-src 'self' data:; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'self' https://*.telegram.org https://telegram.org https://t.me"
            )
        elif response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Content-Security-Policy"] = (
                f"default-src 'none'; style-src 'nonce-{nonce}'; "
                f"script-src 'nonce-{nonce}'; img-src data:; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
            )
            response.headers["X-Frame-Options"] = "DENY"
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; frame-ancestors 'none'"
            )
            response.headers["X-Frame-Options"] = "DENY"
        return response

    app.include_router(health_router)

    # Authentication routes are mounted before the trading surface. Every route
    # that Depends(get_current_user) therefore has the same production identity
    # provider available at the process boundary.
    from app.api.v1.endpoints.auth import router as auth_router
    app.include_router(auth_router, prefix="/api/v1")

    from app.api.v1.endpoints import stream
    app.include_router(stream.router, prefix="/api/v1/stream", tags=["stream"])
    # Generic OHLC candles for any underlying. Removed with the crypto sweep,
    # but 13 MOUNTED Kite components read it through `useCandles` —
    # KiteTicker, InstrumentPane, KiteDashboard, AstroPane and every chart —
    # so every chart in the Kite tab was 404ing.
    app.include_router(candles_router, prefix="/api/v1")
    app.include_router(config_router, prefix="/api/v1")
    from app.api.v1.endpoints.price_rules import router as price_rules_router
    app.include_router(price_rules_router, prefix="/api/v1")
    from app.api.v1.endpoints.ema_trend import router as ema_trend_router
    app.include_router(ema_trend_router, prefix="/api/v1")
    # Public YouTube library for the sandboxed TV page (fixed channel list, no input).
    from app.api.v1.endpoints.media_youtube import router as media_youtube_router
    app.include_router(media_youtube_router, prefix="/api/v1")
    app.include_router(backtest_router, prefix="/api/v1")

    # Zerodha Kite (Indian markets) — multi-tenant manual console
    from app.api.v1.endpoints.kite import router as kite_router
    app.include_router(kite_router, prefix="/api/v1")

    # TrueData market data provider endpoints
    from app.api.v1.endpoints.truedata import router as truedata_router
    app.include_router(truedata_router, prefix="/api/v1")

    # Kite-exclusive Sterling Kite Engine options engine (scanner + advisory/auto-exec)
    from app.api.v1.endpoints.kite_engine import router as kite_engine_router
    app.include_router(kite_engine_router, prefix="/api/v1")

    # Kite-specific Telegram alert targets (per-user)
    from app.api.v1.endpoints.kite_telegram import router as kite_telegram_router
    app.include_router(kite_telegram_router, prefix="/api/v1")

    # Sterling Value-Flow Navigator (Kite-only, off by default)
    from app.api.v1.endpoints.navigator import router as navigator_router
    app.include_router(navigator_router, prefix="/api/v1")

    # Advisory 09:15 same-minute relative-volume leader scanner.
    from app.api.v1.endpoints.opening_volume_leaders import router as opening_volume_leaders_router
    app.include_router(opening_volume_leaders_router, prefix="/api/v1")

    from app.api.v1.endpoints.adaptive_edge import router as adaptive_edge_router
    app.include_router(adaptive_edge_router, prefix="/api/v1")

    # Offline market-data lake (kitelake). Storage is relocatable — typically a removable
    # drive — so these endpoints report an absent volume as data, never as an error.
    from app.api.v1.endpoints.datalake import router as datalake_router
    app.include_router(datalake_router, prefix="/api/v1")

    from app.api.v1.endpoints.pcr import router as pcr_router
    app.include_router(pcr_router, prefix="/api/v1")

    from app.api.v1.endpoints.simulation import router as simulation_router
    app.include_router(simulation_router, prefix="/api/v1")

    # Snapback prospective operations (health only; no strategy or execution surface).
    from app.api.v1.endpoints.snapback_ops import router as snapback_ops_router
    app.include_router(snapback_ops_router, prefix="/api/v1")

    from app.api.v1.endpoints.family_operations import router as family_operations_router
    app.include_router(family_operations_router, prefix="/api/v1")

    from app.api.v1.endpoints.snapback_plans import router as snapback_plans_router
    app.include_router(snapback_plans_router, prefix="/api/v1")

    # Two-strategy, five-mode reporting surface. Read-only: promotion is an
    # evidence decision, never an HTTP call.
    from app.api.v1.endpoints.lanes import router as lanes_router
    app.include_router(lanes_router, prefix="/api/v1")

    # Account continuity, static egress and release certification. Also
    # read-only, for the same reason: activating a broker account binding is an
    # operator action taken at the command line, not an HTTP call.
    from app.api.v1.endpoints.continuity import router as continuity_router
    app.include_router(continuity_router, prefix="/api/v1")

    # Live runtime status and the authenticated SAFE_MODE resume action.
    from app.api.v1.endpoints.ops_live import router as ops_live_router
    app.include_router(ops_live_router, prefix="/api/v1")

    # >>> live-posture: secure keys, static IP and account binding facts, and accepting a new IP.
    from app.api.v1.endpoints.live_posture import router as live_posture_router
    app.include_router(live_posture_router, prefix="/api/v1")
    # <<< live-posture

    # >>> simulation-mode: the virtual tenant's status, per-strategy mode, reset and capital (/api/v1/sim/*).
    # Not the market-replay router above (/api/v1/simulation/*).
    from app.api.v1.endpoints.sim import router as sim_router
    app.include_router(sim_router, prefix="/api/v1")
    # <<< simulation-mode

    # >>> trade-journal: read-only per-trade detail for live and simulated trades (/api/v1/journal/*)
    from app.api.v1.endpoints.journal import router as journal_router
    app.include_router(journal_router, prefix="/api/v1")
    # <<< trade-journal

    # Telegram Mini App (TMA) frontend & endpoints
    from app.api.v1.endpoints.tma import router as tma_router
    app.include_router(tma_router)

    return app


app = create_app()

if __name__ == "__main__":
    import os
    import uvicorn

    from app.core.runtime_env import is_production as _runtime_env_is_production
    from app.services.snapback_deployment import bind_host

    port = int(os.getenv("PORT", "8000"))
    # Loopback unless STERLING_BIND_HOST says otherwise: binding every interface
    # on a home network exposes an authenticated broker session to whatever else
    # is on the Wi-Fi.
    uvicorn.run(
        "main:app",
        host=bind_host(),
        port=port,
        # Auto-reload restarts the process on every file edit: never in a trading session.
        # Opt in for development with STERLING_DEV_RELOAD=1.
        reload=os.getenv("STERLING_DEV_RELOAD", "").strip() == "1",
    )
