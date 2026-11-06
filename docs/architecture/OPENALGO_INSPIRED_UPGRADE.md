# Sterling platform modernization — additive implementation contract

**Reference:** [OpenAlgo](https://github.com/marketcalls/openalgo) is an architectural study only. Do not copy its UI, brand, pages, or strategy code. Preserve Sterling's Kite-focused Indian derivatives workflows, Snapback, Gamma-Unwind, SuperTrend, EMA Trend, Spectrum, TV and Radio.

## Reuse existing authorities

- `app.services.execution_service.CanonicalExecutionService` and `app.services.kite_engine.order_journal`: Sterling's existing broker execution/journaling authority.
- `docs/architecture/RISK_AUTHORITY.md` and `EXECUTION_AUTHORITY.md`: existing fail-closed risk and recovery invariants.
- `app.persistence`: separate SQLAlchemy mirror. Do not modify legacy raw SQLite (`sterling_paper.db`) as a side effect.
- `backend/alembic`: existing Alembic migration graph. Extend its baseline; do not create a competing migration root.

## Trading authority

```text
Versioned strategy event -> RiskAuthority (fails closed)
  -> authenticated human approval + durable execution-control check
  -> CanonicalExecutionService -> order journal -> Kite adapter
  -> broker reconciliation -> verified fills/positions -> analytics and alerts
```

No strategy, worker, frontend or AI may send broker orders. AI advice and notifications are not execution permissions. Default intended approval mode for *future* live flows is `semi_auto`; it cannot override global live-off, risk gates, or durable kill switches. Stale/absent market data cannot authorize additional live exposure. UNKNOWN broker outcomes must be reconciled, never blindly retried.

## Stage 1 — implemented metadata foundation

Alembic revision `4d2c62ab8b01` adds exactly three tables in Sterling's **separate SQLAlchemy database**:

| Table | Purpose | Guard |
|---|---|---|
| `platform_strategies` | Owner, name, lifecycle | Unique owner/slug, state constraints |
| `platform_strategy_versions` | Parameter snapshot, SHA-256, creator | Strategy FK, unique strategy/version |
| `platform_strategy_runs` | Run, version provenance, state | Version FK; DB permits only validation/simulation |

UTC timestamps use Unix epoch milliseconds (`*_at_ms`). Version snapshots canonicalize JSON and reject credential-field names. New runs *cannot* use LIVE mode in this schema. There are no new APIs, broker methods, execution workers, live flags, or frontend pages in Stage 1; existing strategy records are not automatically backfilled.

**Not yet enforced:** append-only version immutability against privileged SQL writes; transaction-bound audit outbox, actor/RBAC, PostgreSQL production migration, full legacy signal attribution, or existing runtime integration. These remain explicit prerequisites, not claims of completion.

## Validation and rollback

Use a disposable database only; do not target live database paths:

```bash
cd backend
DATABASE_URL=sqlite:////tmp/sterling_registry_validation.db alembic upgrade head
pytest -q tests/test_platform_strategy_registry.py
DATABASE_URL=sqlite:////tmp/sterling_registry_validation.db alembic downgrade efe2cc0f2c21
```

The test itself creates its own temporary SQLite database and performs upgrade/downgrade/re-upgrade with a refusal check for LIVE runs. Before any production schema change: record current migration head, back up, **restore-test**, acquire migration lock, stage, validate, and observe health. A downgrade drops all three new tables and their data, so never perform it on populated production tables without review and a recoverable backup.

## Next gated slices

1. Transaction-bound registry service, immutable versions, authorization, atomic audit/outbox and concurrent unique enforcement.
2. Strategy event/run linkage, scheduler, deterministic replay.
3. Broker abstraction *behind* existing execution authority; preserve Kite recovery/no-retry safeguards.
4. Typed market data quality, freshness, fallback for analytics; cached data must never authorize live orders.
5. Fill-verified P&L, fees, metrics, options Greeks and native Sterling dashboard with compact/expandable rows.
6. RBAC, critical alerts, restore drills, Telegram/device proof, broker identity, and controlled go-live certification.

Passing these stage-one tests does not certify a running server, real broker, device UX, live profitability or production readiness.
