# Sterling V2 Phase 7 — Hypothetical Performance Analytics Validation

**Date:** 2026-10-29  
**Disposition:** targeted offline analytics validation **PASS**; real/prod profitability and live trading **BLOCKED**.  
**Draft PR #9:** https://github.com/nageshmadaram/sterling-v2/pull/9  
**Validated code SHA:** `3fb761704a2acd3365240e8485196b825aee50cf`  
**GitHub Actions:** https://github.com/nageshmadaram/sterling-v2/actions/runs/40806845331

## Executed evidence

| Targeted validation | Observed result |
|---|---|
| Explicit synthetic entry/exit pricing, fees and cash slippage | PASS |
| Two independent, completed and approved Phase 6 offline decisions required | PASS |
| Immutable strategy, version, signal, symbol and risk-policy linkage | PASS |
| No P&L inferred from standalone approved signals or empty decision journals | PASS |
| Duplicate exit evidence refused, missing decision records rejected | PASS |
| Long/short Decimal P&L calculation, net fee and slippage accounting | PASS |
| Profit factor, win rate, expectancy, price/gross/net P&L | PASS |
| Trade-order equity curve and absolute/percentage drawdown | PASS |
| UTC-day/month buckets, rolling profit factor and rolling expectancy | PASS |
| Sharpe and Sortino active UTC-day descriptive ratios with explicit starting capital | PASS |
| Grouped strategies, versions, symbols, regimes and approval modes | PASS |
| Assumed entry notional labeled as synthetic; live exposure always null | PASS |
| Informational low-PF, negative expectancy, low-win-rate, drawdown and loss warnings | PASS |
| NULL for unsupported zero-denominator or insufficient-sample ratios | PASS |
| Ledger SQL immutable UPDATE/DELETE and execution_authorized=true refusal | PASS |
| Journal insert outage -> no synthetic trade record committed | PASS |
| Tampered analytical evidence and unauthorized tenant access denied | PASS |
| Strict signed-JWT GET-only KPI and trade-evidence endpoints | PASS |
| Phase 1–6 registry, broker, market-data, risk, scheduler and journal regressions | PASS |
| **Combined targeted tests** | **120 passed, 9 warnings, 0 failed** |
| Phase 2B access audit | **0 new unreviewed; 272 inherited classified findings** |
| Static check: eight GET routes, no broker-writing/worker API | PASS |
| Disposable PostgreSQL16 additive migration | PASS |
| PostgreSQL append-only triggers, rollback and synthetic journal linkage | PASS |
| PG16 isolated pg_dump/pg_restore count verification | PASS |
| Disposable-only Alembic downgrade to d86e31a4f290 and re-upgrade | PASS |
| Full repository regression, real broker history and production certification | **NOT CERTIFIED** |

Nine reported warnings: seven inherited Python invalid-escape SyntaxWarnings in
the Phase 2B audit inventory script and two FastAPI/Starlette TestClient
deprecation warnings. No warnings were misrepresented as production approval.

## Incremental validation record

- Initial [run 40806508698](https://github.com/nageshmadaram/sterling-v2/actions/runs/40806508698):
  120 targeted tests PASS, new broker access 0, PostgreSQL ledger integrity
  and rollback PASS. The PostgreSQL restore verification job failed its last
  row-count assertion because the derived CI workflow was still counting
  Phase 5 `platform_orchestration_events` rather than the new
  `platform_hypothetical_trade_events` table. This was a CI check error,
  not a failed database restore or compromised journal.
- Corrected [run 40806683940](https://github.com/nageshmadaram/sterling-v2/actions/runs/40806683940):
  both jobs PASS after changing restore count to the Phase 7 trade table.
- Finalized sizing distinction (assumed entry notional is not broker exposure),
  then [run 40806845331](https://github.com/nageshmadaram/sterling-v2/actions/runs/40806845331):
  **120 PASS, zero failures; both PostgreSQL and regression jobs SUCCESS.**

## Delivered artifacts

- `app.domain.offline_performance`: frozen synthetic-only entry/exit price
  assumptions and immutable hypothetical trade records.
- `app.services.offline_performance_metrics`: pure finite-Decimal P&L,
  profit factor, expectancy, win/loss statistics, drawdown/equity and
  rolling/Sharpe/Sortino calculations; comparisons and informational warnings.
- `app.persistence.offline_performance_models` plus Alembic
  `e97c602ab458`: additive immutable hash-chained synthetic trade evidence
  with source journal foreign keys and a database `execution_authorized=false`
  CHECK constraint.
- `app.persistence.offline_performance_service`: authenticated offline-only
  hypothetical evidence recording with exact Phase 6 approved/completed decision
  checks, explicit assumptions, tenant isolation, duplicate prevention and
  source SHA256 revalidation. **No write API is exposed.**
- `app.api.v1.endpoints.offline_performance`: eight signed-authenticated,
  GET-only operator/admin KPI, timeline, breakdown, risk and trade journal
  routes; all responses remain non-executing and clearly label assumptions.
- Phase 7 tests, PostgreSQL16 proof script and CI workflow.
- `docs/architecture/PHASE7_OFFLINE_PERFORMANCE.md`: metric definitions,
  source limitations, API shape and release blockers.

## Hard limitations and remaining blockers

1. **No broker-confirmed trades or fills:** The Phase 6 journal records
   risk/approval/queue decisions, not actual entry/exit executions. Synthetic
   assumed fills are entirely operator supplied and are NOT proof of market
   attainability, execution quality, strategy edge or profitability.
2. **No authenticated historical trade ingestion:** Historical real trading
   performance remains unknown. Direct integration with legacy live trade
   database or Kite positions is deliberately absent. Nothing from this KPI
   layer can authorize live trading or broker writes.
3. **Metric caveats:** the Sharpe/Sortino series uses only active UTC days and
   assumed starting capital, with zero assumed risk-free rate; it is NOT
   calendar-complete exchange-session reporting. Session P&L is a UTC-day
   proxy, not a certified market calendar. There is no externally certified
   benchmark, slippage distribution, out-of-sample profitability evidence or
   live fill reconciliation.
4. **Audit limitations:** PostgreSQL privileged INSERT/DDL could forge records
   or remove triggers; least-privilege production grants, independent WORM
   storage and external notarization remain pending.
5. **Execution isolation:** Phase 4 offline risk receipts and Phase 5/6
   queue/journal are separate transactions. Existing legacy broker writes and
   order authority remain untouched and are not proven to require journal
   receipts. All 272 inherited broker-access findings remain in the inventory,
   including 30 legacy write paths and 4 existing execution-authority cases.
6. **Operator delivery:** The read-only KPI APIs exist in the draft branch;
   no merged/deployed dashboard, device acceptance or external Telegram/push
   alert delivery is claimed. Warnings are `delivery=not_sent`.
7. **Full acceptance:** Full frontend/backend E2E, performance under real
   load, production PostgreSQL backup/restore, real broker identity, IP
   whitelist, moving-market orders/stops and reconnect drills remain blocked.

**Decision: keep all PRs #1–#9 draft and unmerged. Keep `main` unchanged.
No deployment, live trading activation, broker order routing or Phase 8.**
