import os

# Pin the environment BEFORE app.core.config is imported. security/auth code now
# honours ENVIRONMENT from backend/.env (app.core.runtime_env); without this pin an
# operator who sets ENVIRONMENT=production in .env for the live process would flip
# every test that relies on the development identity seam. Tests that need
# production set it explicitly with monkeypatch.setenv.
os.environ.setdefault("ENVIRONMENT", "development")
# Many fixtures create LIVE Kite accounts and call routes with the anonymous
# development identity. The runtime refuses that identity once a live account exists
# (app.core.auth); the suite opts out explicitly. Tests of the refusal delenv this.
os.environ.setdefault("STERLING_DEV_AUTH_ALLOW_ANONYMOUS_LIVE", "1")

import tempfile
import numpy as np
import pytest
from typing import List
from app.schemas.market import Candle

# Ensure pytest NEVER writes to or reads from the live sterling_paper.db.
# All tests execute against an isolated temporary test database.
if "STERLING_DB_PATH" not in os.environ:
    _TEST_DB = tempfile.NamedTemporaryFile(suffix="_sterling_test.db", delete=False)
    _TEST_DB.close()
    os.environ["STERLING_DB_PATH"] = _TEST_DB.name

# The operator audit trail (app.core.http_hardening) appends one line per mutating
# request. Without this, every TestClient POST in the suite would write into the real
# data/audit/operator_audit.jsonl and bury operator actions under test noise.
if "STERLING_AUDIT_LOG" not in os.environ:
    os.environ["STERLING_AUDIT_LOG"] = os.path.join(
        tempfile.mkdtemp(suffix="_sterling_test_audit"), "operator_audit.jsonl"
    )

# The shared Kite REST limiter (exchanges/kite/client.py) paces real traffic; the suite
# drives mock transports as fast as it can, so it opts out (tests of the limiter build
# their own ReservationRateLimiter / patch the module-level buckets).
os.environ.setdefault("STERLING_KITE_HIST_RPS", "0")
os.environ.setdefault("STERLING_KITE_API_RPS", "0")

# Shadow records are written by the live entry path now, so a test that exercises
# an entry would append to the repository's own data/shadow directory. Point the
# store at a temporary directory for the whole session: a test run must not leave
# rows in the evidence a release is judged on.
if "STERLING_SHADOW_DIR" not in os.environ:
    os.environ["STERLING_SHADOW_DIR"] = tempfile.mkdtemp(suffix="_sterling_test_shadow")

# Admission now refuses new exposure on a host that has a live broker account
# and no recorded answer about what that broker holds — the state the CDSL
# discovery made real. The suite stands up the reconciled equivalent: a fresh
# observation saying the broker holds nothing Sterling did not open. A test that
# wants the blocking behaviour writes its own record over this one.
if "STERLING_EXTERNAL_EXPOSURE_FILE" not in os.environ:
    import json as _json
    from datetime import datetime as _datetime, timezone as _timezone

    _EXTERNAL = tempfile.NamedTemporaryFile(
        suffix="_sterling_test_external_exposure.json", delete=False, mode="w"
    )
    _json.dump({"observed_at": _datetime.now(_timezone.utc).isoformat(),
                "readable": True, "count": 0, "instruments": [], "detail": "",
                "_pytest_default": True}, _EXTERNAL)
    _EXTERNAL.close()
    os.environ["STERLING_EXTERNAL_EXPOSURE_FILE"] = _EXTERNAL.name

# An absent safety state now reads as SAFE_MODE, which is the correct production
# answer and the wrong default for a test suite: every capacity and execution test
# would refuse before reaching the behaviour it is actually asserting. So the suite
# stands up an initialised NORMAL state, in a temporary file, exactly as a real
# machine does on first boot. Tests that care about safe mode engage it themselves.
if "STERLING_SAFE_MODE_FILE" not in os.environ:
    _TEST_SAFE_MODE = tempfile.NamedTemporaryFile(
        suffix="_sterling_test_safe_mode.json", delete=False
    )
    _TEST_SAFE_MODE.close()
    os.environ["STERLING_SAFE_MODE_FILE"] = _TEST_SAFE_MODE.name
    os.unlink(_TEST_SAFE_MODE.name)
    from app.services.safe_mode import SafeModeService

    SafeModeService(_TEST_SAFE_MODE.name).initialise(
        operator_ack=True, note="pytest session"
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """Seed the control plane for whichever database this test ended up pointed at.

    Dozens of test fixtures build a throwaway database of their own, and an
    execution_control table with no row now reads RECOVERY_REQUIRED — the right
    production answer for a database that has never spoken to the broker, and a
    blanket refusal for every order-placing test. This runs after fixture setup,
    so it sees the test's own database, and it only writes when no state exists.
    A test that wants the uninitialised control plane deletes the row in its own
    body, after this has run.
    """
    try:
        from app.services import db

        if db._available and not int(db.get_execution_control().get("initialized", 0)):
            reconciled_control_plane()
    except Exception:
        pass
    yield


@pytest.fixture(scope="session", autouse=True)
def _reconciled_control_plane():
    """Give the shared test database the state a completed startup recovery leaves.

    An execution_control table with no row now reads RECOVERY_REQUIRED, which is
    the right production answer — a fresh database has never spoken to the broker
    — and the wrong default for the suite: every order-placing test would refuse
    before reaching what it asserts. Tests about the uninitialised control plane
    build their own database and delete the row themselves.
    """
    try:
        from app.services import db

        db.init()
        db.set_execution_control(
            operator_state="RUNNING",
            recovery_state="CLEAN",
            reason_code="test_session",
            reason="pytest session startup recovery",
            actor="tests",
        )
    except Exception:
        pass
    yield


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_db():
    yield
    try:
        test_db_path = os.environ.get("STERLING_DB_PATH")
        if test_db_path and os.path.exists(test_db_path) and "test" in test_db_path:
            os.remove(test_db_path)
    except Exception:
        pass



@pytest.fixture
def allow_live_execution(monkeypatch):
    """Explicit opt-in for tests whose subject is the live execution machinery.

    Production defaults LIVE_EXECUTION_ENABLED to True and exposes the operator
    kill switch STERLING_LIVE_EXECUTION=0. Tests that need to reach sizing,
    journaling, protection or broker-rejection branches pin it explicitly here,
    so a kill switch left in the developer's environment cannot leak into them.
    """
    from app.core import lane_registry

    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)
    return True


def reconciled_control_plane():
    """Write the execution-control state a completed startup recovery leaves.

    A database with no execution_control row now reads RECOVERY_REQUIRED, because
    a fresh database has never spoken to the broker. Any test that stands up its
    own database and then places an order has to say that recovery ran.
    """
    from app.services import db

    db.set_execution_control(
        operator_state="RUNNING",
        recovery_state="CLEAN",
        reason_code="test_session",
        reason="pytest startup recovery",
        actor="tests",
    )


def normal_safe_mode_file(monkeypatch, path):
    """Point the safety state at ``path`` and stand it up as NORMAL.

    An absent file is SAFE_MODE, so a test that only wants an isolated safety
    state — rather than an engaged one — has to initialise it, exactly as a real
    machine does on first boot.
    """
    from app.services.safe_mode import SafeModeService

    monkeypatch.setenv("STERLING_SAFE_MODE_FILE", str(path))
    SafeModeService(path).initialise(operator_ack=True, note="test fixture")
    return path


def make_candles(n: int = 100, base: float = 30000.0, trend: float = 10.0) -> List[Candle]:
    np.random.seed(42)
    candles = []
    price = base
    for i in range(n):
        price += trend + np.random.normal(0, base * 0.002)
        o = price - abs(np.random.normal(0, base * 0.001))
        c = price + abs(np.random.normal(0, base * 0.001))
        h = max(o, c) + abs(np.random.normal(0, base * 0.0005))
        l = min(o, c) - abs(np.random.normal(0, base * 0.0005))
        candles.append(
            Candle(
                timestamp_ms=1_700_000_000_000 + i * 3_600_000,
                open=round(o, 2), high=round(h, 2),
                low=round(l, 2), close=round(c, 2),
                volume=float(np.random.uniform(100, 500)),
            )
        )
    return candles


def make_bearish_candles(n: int = 100, base: float = 30000.0) -> List[Candle]:
    return make_candles(n, base, trend=-50.0)


def _default_risk():
    from app.schemas.risk import RiskParams
    from app.core.config import settings
    return RiskParams(
        capital=settings.default_capital,
        max_position_pct=settings.max_position_pct,
        max_contracts=settings.max_contracts,
    )


@pytest.fixture(autouse=True)
def reset_global_stores():
    """Reset every module-level and persisted mutable test store."""
    # `paper_store`, `alert_store`, `exchange_account_store` and the directional
    # engine's caches went with the crypto surface. Importing them here made an
    # AUTOUSE fixture raise, which errored every one of the 3748 collected tests
    # — the suite could not run at all.
    from app.services import eval_history, arrow_store
    from app.services import pnl_history, webhook_store
    from app.services.exchanges.kite import accounts as kite_accounts
    import app.api.v1.endpoints.config as config_ep

    eval_history.clear()
    arrow_store.clear()
    arrow_store._bootstrapped = True
    pnl_history.clear()
    pnl_history._loaded = True
    webhook_store.clear()
    webhook_store._loaded = True
    kite_accounts.clear()
    config_ep._risk = _default_risk()

    yield

    eval_history.clear()
    arrow_store.clear()
    arrow_store._bootstrapped = False
    pnl_history.clear()
    webhook_store.clear()
    kite_accounts.clear()
    config_ep._risk = _default_risk()


@pytest.fixture(autouse=True)
def _no_live_yahoo_requests(request, monkeypatch):
    """Unit tests must not reach Yahoo: ``KiteClient.get_candles`` falls back to it
    when Kite fails, which would make a scan test depend on the network and on
    whatever Yahoo happens to return. Tests that exercise the Yahoo module itself
    carry ``@pytest.mark.yahoo_unit`` and stub the HTTP layer themselves."""
    if request.node.get_closest_marker("yahoo_unit"):
        return

    async def _nothing(name, resolution, days):
        return []

    from app.services import yahoo_candles
    monkeypatch.setattr(yahoo_candles, "fetch_candles", _nothing)


@pytest.fixture(autouse=True)
def _market_is_open_unless_the_test_says_otherwise(monkeypatch):
    """Entry admission refuses new exposure outside the NFO continuous session, so
    every entry-path test silently depended on the wall clock: green at 11:00 IST,
    `session_closed` (and 100+ failures) after hours and at weekends.

    Pin the *default* clock of the session calendar to a verified trading
    instant. A test that passes an explicit ``now`` to ``market_hours`` still
    gets exactly that time, so the calendar's own tests are unaffected."""
    from datetime import datetime, timedelta, timezone

    from app.services.kite_engine import market_hours

    ist = timezone(timedelta(hours=5, minutes=30))
    pinned = datetime(2026, 9, 30, 11, 0, tzinfo=ist)   # Wednesday, mid-session
    original = market_hours._local

    def _local(now=None):
        return original(now if now is not None else pinned)

    monkeypatch.setattr(market_hours, "_local", _local)


@pytest.fixture(autouse=True)
def _default_exposure_record_names_the_live_accounts(monkeypatch):
    """A recorded broker answer is only valid for the exact live-account set it
    queried, so the suite's default "broker holds nothing" record must name
    whichever live accounts the test configured. Only the default record is
    completed; a record a test writes itself is returned untouched."""
    from app.services import external_positions

    original = external_positions.last_observation

    def _last_observation():
        record = original()
        if isinstance(record, dict) and record.get("_pytest_default"):
            ids = external_positions.live_account_ids()
            # The record is read as a fresh answer only for a minute; one written
            # at import time would be "stale" for every test after the first minute
            # of a full run, so the default record is always as old as now.
            from datetime import datetime, timezone
            record = {**record, "accounts": list(ids or ()),
                      "observed_at": datetime.now(timezone.utc).isoformat()}
        return record

    monkeypatch.setattr(external_positions, "last_observation", _last_observation)
