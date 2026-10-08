"""A fresh Sterling machine, booted the way ``main.py``'s lifespan boots it.

``LiveMachine`` is the harness behind every test in this directory. It stands up:

* a throwaway state root and database (nothing under the repository is touched),
* a frozen clock inside the NSE continuous session on an ordinary 2026 weekday,
* an offline Kite exchange (``fake_kite.FakeKiteExchange``) wired in as the HTTP
  transport of the production ``KiteClient`` that ``accounts.build_client`` builds
  from a REAL stored live account,
* a network guard, so anything that tries to open a socket fails loudly,

and boots the application through ``main.lifespan`` itself, with only one change:
``asyncio.create_task`` is captured, so the long-running background loops
(dispatcher, scanners, session keeper, Telegram poller, ...) are *created* exactly as
in production but do not run on their own. The test then drives the single cycle it
cares about. That keeps the boot sequence the real one (a new line in ``main.py`` is
exercised here without anyone remembering to copy it) while keeping the run
deterministic.

"A process restart" is a real one as far as Python state goes: every ``app.*`` module
(and ``main``) is dropped from ``sys.modules`` and re-imported, the database file
stays. Test code therefore imports application modules lazily, inside functions, and
never holds a module across ``restart()``.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from tests.e2e_live.fake_kite import FakeKiteExchange

IST = timezone(timedelta(hours=5, minutes=30))

#: Monday, mid-session, not a holiday in the verified 2026 NSE calendar. 10:16 IST is the one
#: instant at which all three strategies may originate: the 09:15 1H bar closed a minute ago
#: (SuperTrend acts on a bar for ten minutes after it closes), the Snapback swing window runs
#: 09:20-10:30, and Gamma's cutoff is 15:20.
NOW = datetime(2026, 10, 12, 10, 16, 0, tzinfo=IST)
UID = "default"
ACCOUNT_API_KEY = "kfakeapikey001"
ACCOUNT_API_SECRET = "kfakeapisecret001"
ACCESS_TOKEN = "fake-access-token-0001"

#: Environment a clean machine does NOT have. The Snapback evidence-runner settings
#: are absent exactly as on the machine where the family gate once refused every
#: live entry.
_ABSENT_ENV = (
    "STERLING_EXPERIMENT_ID", "STERLING_DATASET_START", "STERLING_ALLOCATION_CAPITAL_INR",
    "STERLING_LIVE_EXECUTION", "STERLING_FAMILY_MODE", "STERLING_FAMILY_ACCOUNT_ID",
    "STERLING_FAMILY_USER_ID", "STERLING_PROSPECTIVE_EXPERIMENT", "STERLING_ROOT",
    "STERLING_EVIDENCE_DB", "STERLING_DEPLOYMENT_ID", "STERLING_RELEASE_TAG",
    "STERLING_EXPECTED_BUILD_SHA", "STERLING_SKIP_CLOCK_CHECK", "STERLING_ALLOW_DIRTY_WORKTREE",
    "KITE_API_KEY", "KITE_API_SECRET", "KITE_ACCESS_TOKEN", "KITE_USER_ID",
    "ZERODHA_API_KEY", "ZERODHA_API_SECRET", "ZERODHA_USER_ID",
    "STERLING_DAILY_LOSS_LIMIT_INR", "STERLING_LIVE_MAX_LOTS", "STERLING_LIVE_DAILY_LOSS_INR",
    "STERLING_LIVE_GROSS_EXPOSURE_INR",
)


class _CapturedTask:
    """What ``asyncio.create_task`` returns while the lifespan boots.

    The coroutine is held, never scheduled. ``await``-ing it (as the lifespan's
    shutdown does) finishes immediately.
    """

    def __init__(self, coro: Any, name: Optional[str]) -> None:
        self.coro = coro
        self.name = name or getattr(coro, "__qualname__", "task")
        self._cancelled = False

    def cancel(self) -> bool:
        self._cancelled = True
        return True

    def done(self) -> bool:
        return self._cancelled

    def cancelled(self) -> bool:
        return self._cancelled

    def get_name(self) -> str:
        return self.name

    def add_done_callback(self, *_a: Any, **_k: Any) -> None:
        return None

    def __await__(self):
        async def _noop() -> None:
            return None

        return _noop().__await__()


async def instant_sleep(_seconds: float = 0.0) -> None:
    """A sleep that yields to the loop and returns: loops under test never wait."""
    await asyncio.sleep(0)


class LiveMachine:
    def __init__(self, tmp_path: Path, monkeypatch: Any, now: datetime = NOW) -> None:
        self.now = now
        self.tmp = tmp_path
        self.monkeypatch = monkeypatch
        self.exchange = FakeKiteExchange(clock=lambda: datetime.now(IST))
        self.state_root = tmp_path / "state"
        self.db_path = tmp_path / "sterling.db"
        self.tasks: List[_CapturedTask] = []
        self.account_id: str = ""
        self.boots = 0
        self._saved_modules: Dict[str, Any] = {}
        self._lifespan = None
        self._freezer = None
        self._app = SimpleNamespace(state=SimpleNamespace())
        self.ticker_subscriptions: List[tuple] = []
        self.tickers_started = 0
        #: Order postbacks Kite pushed while no websocket was connected: lost, as in life.
        self.lost_postbacks: List[dict] = []
        self._network_attempts: List[Any] = []

    # ------------------------------------------------------------------ setup

    def install(self) -> None:
        """Environment, clock and network guard. Called once, by the fixture."""
        mp = self.monkeypatch
        for name in _ABSENT_ENV:
            mp.delenv(name, raising=False)
        self.state_root.mkdir(parents=True, exist_ok=True)
        data = self.state_root / "data"
        data.mkdir(parents=True, exist_ok=True)
        mp.chdir(self.tmp)
        mp.setenv("ENVIRONMENT", "development")
        mp.setenv("STERLING_STATE_ROOT", str(self.state_root))
        mp.setenv("STERLING_DB_PATH", str(self.db_path))
        mp.setenv("STERLING_SAFE_MODE_FILE", str(data / "safe_mode.json"))
        mp.setenv("STERLING_EXTERNAL_EXPOSURE_FILE", str(data / "external_exposure.json"))
        mp.setenv("STERLING_INTENT_DB", str(data / "intents.db"))
        mp.setenv("STERLING_PLAN_DB_PATH", str(data / "plans.db"))
        mp.setenv("STERLING_SNAPBACK_LIVE_DB", str(data / "live_positions.db"))
        mp.setenv("STERLING_NEW_TRADES_HALT_PATH", str(data / "new_trades_halt.json"))
        mp.setenv("STERLING_OBSERVATIONS_DB_PATH", str(data / "observations.db"))
        mp.setenv("STERLING_SHADOW_DIR", str(data / "shadow"))
        mp.setenv("STERLING_AUDIT_LOG", str(data / "operator_audit.jsonl"))
        mp.setenv("STERLING_BACKUP_DIR", str(self.tmp / "backups"))
        mp.setenv("STERLING_KITE_HIST_RPS", "0")
        mp.setenv("STERLING_KITE_API_RPS", "0")
        mp.setenv("STERLING_DEV_AUTH_ALLOW_ANONYMOUS_LIVE", "1")

        # The wire is the only thing that is fake. Any other attempt to reach out
        # (a stray requests/httpx call to a real host, a websocket) must fail loudly.
        attempts = self._network_attempts

        def _no_connect(sock, address, *args, **kwargs):  # noqa: ANN001
            import traceback

            frames = [f"{f.filename.split('/backend/')[-1]}:{f.lineno}:{f.name}"
                      for f in traceback.extract_stack() if "/backend/app/" in f.filename]
            attempts.append((address, frames[-6:]))
            raise OSError(f"network access is blocked in the offline admission dry run: {address!r}")

        mp.setattr(socket.socket, "connect", _no_connect)
        mp.setattr(socket, "create_connection",
                   lambda address, *a, **k: _no_connect(None, address))

        # Third-party libraries must be imported BEFORE the clock is frozen: freezegun
        # rewrites datetime references it finds in loaded modules, and importing pandas'
        # Cython time-series code under a frozen clock recurses until the interpreter
        # crashes. The whole application graph is pulled in first for the same reason
        # (its lazy imports then find everything already loaded).
        for heavy in ("numpy", "pandas", "scipy", "pyarrow", "httpx", "zoneinfo"):
            try:
                importlib.import_module(heavy)
            except Exception:  # noqa: BLE001 - optional in this environment
                pass
        importlib.import_module("main")

        from freezegun import freeze_time

        self._freezer = freeze_time(self.now, tick=True, real_asyncio=True)
        self._freezer.start()

    def teardown(self) -> None:
        if self._freezer is not None:
            self._freezer.stop()
            self._freezer = None
        self._restore_modules()
        for task in self.tasks:
            try:
                task.coro.close()
            except Exception:  # noqa: BLE001
                pass

    @property
    def network_attempts(self) -> list:
        return list(self._network_attempts)

    # --------------------------------------------------- fresh process / modules

    @staticmethod
    def _is_app_module(name: str) -> bool:
        return name == "main" or name == "app" or name.startswith("app.")

    def _purge_modules(self) -> None:
        for name in [n for n in sys.modules if self._is_app_module(n)]:
            self._saved_modules.setdefault(name, sys.modules[name])
            del sys.modules[name]
        importlib.invalidate_caches()

    def _restore_modules(self) -> None:
        for name in [n for n in sys.modules if self._is_app_module(n)]:
            del sys.modules[name]
        for name, module in self._saved_modules.items():
            sys.modules[name] = module
        self._saved_modules = {}

    def m(self, name: str) -> Any:
        """The CURRENT module object. Never cache the result across ``restart()``."""
        return importlib.import_module(name)

    def fresh_process(self) -> None:
        """Forget every in-memory byte the application learned; keep the files."""
        self._purge_modules()
        self._install_process_patches()

    def _install_process_patches(self) -> None:
        """Patches that belong to one process life (re-applied after every restart)."""
        machine = self
        accounts = self.m("app.services.exchanges.kite.accounts")
        real_build = accounts.build_client

        def build_client(account: Any) -> Any:
            client = real_build(account)
            from app.services.exchanges.kite import constants as K

            client._client = machine.exchange.async_client(K.BASE_URL)
            return client

        accounts.build_client = build_client

        # KiteClient.get_candles falls back to Yahoo when Kite has no bars for an underlying;
        # a unit test must not reach it (the repository's own suite pins the same stub).
        yahoo = self.m("app.services.yahoo_candles")

        async def _no_yahoo(name, resolution, days):
            return []

        yahoo.fetch_candles = _no_yahoo

        ticker_mod = self.m("app.services.exchanges.kite.ticker")
        ticker_manager = self.m("app.services.exchanges.kite.ticker_manager")

        class FakeKiteTicker(ticker_mod.KiteTicker):
            """The production ticker with its websocket removed.

            ``ticker_manager`` builds, starts, subscribes and supervises it exactly as it
            does a real one (credential binding, account matching, owner refcounts). It
            counts as connected from ``start()`` on and keeps its subscription set, but it
            never opens a socket: ticks and order postbacks are pushed by the test, and
            ONLY while a ticker exists, because Kite does not buffer postbacks for a socket
            that is not connected.
            """

            async def start(self) -> None:
                self._active = True
                self._connected = True
                self._task = asyncio.get_running_loop().create_future()   # a "running" task
                machine.tickers_started += 1

            async def stop(self) -> None:
                self._active = False
                self._connected = False
                task, self._task = self._task, None
                if task is not None and not task.done():
                    task.cancel()

            async def _send_quietly(self, *frames: str) -> None:
                return None

            async def subscribe(self, tokens, mode=ticker_mod.K.MODE_QUOTE) -> None:
                for token in tokens:
                    self._subscribed[int(token)] = mode
                    machine.ticker_subscriptions.append((int(token), mode))

            async def unsubscribe(self, tokens) -> None:
                for token in tokens:
                    self._subscribed.pop(int(token), None)

        ticker_manager.KiteTicker = FakeKiteTicker

    # ------------------------------------------------------------ provisioning

    def provision_account(self) -> str:
        """What the operator did the day the machine was set up: add the account in the
        UI and complete the Kite login. Everything goes through the production store."""
        db = self.m("app.services.db")
        accounts = self.m("app.services.exchanges.kite.accounts")
        models = self.m("app.services.exchanges.kite.models")
        assert db.init(), "the throwaway database must open"
        accounts.bootstrap()
        account = accounts.add(UID, models.KiteAccountCreate(
            label="Zerodha", api_key=ACCOUNT_API_KEY, api_secret=ACCOUNT_API_SECRET))
        accounts.save_session(
            UID, account.id, access_token=ACCESS_TOKEN, refresh_token="", public_token="pub",
            kite_user_id=self.exchange.kite_user_id, user_name=self.exchange.user_name)
        self.account_id = account.id
        return account.id

    def bind_family_account(self) -> None:
        """What ``.env.example`` tells the operator to do once the account exists: name the
        Sterling user and the Kite account Snapback reconciles against."""
        self.monkeypatch.setenv("STERLING_FAMILY_USER_ID", UID)
        self.monkeypatch.setenv("STERLING_FAMILY_ACCOUNT_ID", self.account_id)

    def operator_adds_account_and_logs_in(self) -> str:
        """The first-ever setup, through the same HTTP routes the UI uses: add the account's
        API credentials, ask for the login URL, and complete Kite's redirect callback."""
        from urllib.parse import parse_qs, urlparse

        from starlette.testclient import TestClient

        main = self.m("main")
        http = TestClient(main.app, raise_server_exceptions=False)
        added = http.post("/api/v1/kite/accounts", json={
            "label": "Zerodha", "api_key": ACCOUNT_API_KEY, "api_secret": ACCOUNT_API_SECRET})
        assert added.status_code == 200, added.text
        self.account_id = added.json()["id"]
        url = http.get("/api/v1/kite/login-url")
        assert url.status_code == 200, url.text
        state = url.json()["state"]
        done = http.get("/api/v1/kite/callback", params={
            "request_token": "fake-request-token", "action": "login", "status": "success",
            "state": state})
        assert done.status_code == 200 and "Connected" in done.text, done.text[:600]
        return self.account_id

    # --------------------------------------------------------------------- boot

    async def boot(self) -> None:
        """``main.lifespan`` startup, against the throwaway state.

        ``asyncio.create_task`` is captured for the duration of the startup only: every
        background loop the lifespan creates is recorded in ``self.tasks`` and not run.
        """
        main = self.m("main")
        self.tasks = []
        self.boots += 1
        machine = self
        real_create_task = asyncio.create_task

        def create_task(coro, *, name=None, **_kw):
            task = _CapturedTask(coro, name)
            machine.tasks.append(task)
            return task

        asyncio.create_task = create_task
        self._lifespan = main.lifespan(self._app)
        try:
            await self._lifespan.__aenter__()
        finally:
            asyncio.create_task = real_create_task

    async def shutdown(self) -> None:
        if self._lifespan is not None:
            lifespan, self._lifespan = self._lifespan, None
            try:
                await lifespan.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001 - teardown must finish
                pass

    async def restart(self, *, boot: bool = True) -> None:
        """Kill the process and start a new one on the same files."""
        await self.shutdown()
        self.fresh_process()
        if boot:
            await self.boot()

    # ---------------------------------------------------------------- runtime

    def task_names(self) -> List[str]:
        return [t.name for t in self.tasks]

    async def run_live_runtime_once(self) -> None:
        """The first pass of each loop that runs at boot, in the order production reaches them:
        the ticker watchdog (no initial delay), the exposure refresher (t+2s) and the startup
        recovery (t+3s) - through the production functions with the waits removed."""
        await self.m("app.services.exchanges.kite.ticker_manager").supervise_once()
        lr = self.m("app.services.live_runtime")
        await lr.exposure_refresher_loop(initial_delay=0, sleep=instant_sleep, max_cycles=1)
        await lr.recovery_loop(initial_delay=0, sleep=instant_sleep, max_cycles=1)

    async def refresh_exposure(self) -> None:
        """The 20-second exposure refresher firing once."""
        lr = self.m("app.services.live_runtime")
        await lr.exposure_refresher_loop(initial_delay=0, sleep=instant_sleep, max_cycles=1)

    def admission(self, *, strategy_id: str = "", uid: str = UID) -> dict:
        """What the single admission authority says for the live account right now."""
        supervisor = self.m("app.services.safety_supervisor")
        verdict = supervisor.SafetySupervisor().authorize(
            uid=uid, account_id=self.account_id, strategy_id=strategy_id)
        snap = verdict.snapshot
        return {"allowed": verdict.allowed, "code": verdict.code, "reason": verdict.reason,
                "blockers": list(snap.blockers) if snap else []}

    def ticker(self) -> Any:
        """The websocket ticker the application has opened for the user, if any."""
        ticker_manager = self.m("app.services.exchanges.kite.ticker_manager")
        return ticker_manager._tickers.get(UID)

    async def postbacks(self) -> int:
        """Push every queued order postback down the websocket the application opened.

        Delivered through the ticker's own order callback (``ticker_manager``'s order
        broadcaster -> ``monitor.on_order_update``), the path a live fill takes. With no
        ticker connected the update is dropped: nothing replays it.
        """
        delivered = 0
        while self.exchange.postbacks:
            order = self.exchange.postbacks.pop(0)
            ticker = self.ticker()
            if ticker is None or not ticker.is_active or not ticker._connected:
                self.lost_postbacks.append(order)
                continue
            await ticker._on_order_update(order)
            delivered += 1
        return delivered

    async def client(self) -> Any:
        accounts = self.m("app.services.exchanges.kite.accounts")
        return await accounts.acquire_client(accounts.get(UID, self.account_id))
