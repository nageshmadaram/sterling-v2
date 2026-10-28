# Sterling V2 Phase 2B — evidence and remaining direct-access register

**Snapshot:** 2026-10-29. **Review status:** targeted Phase 2B validation PASS; full release still BLOCKED.

## Verified artifacts

- GitHub Actions dedicated Phase 2B [run 40786287009](https://github.com/nageshmadaram/sterling-v2/actions/runs/40786287009) for commit 39f840ff557fbb522f973bce2870949544882496: 49 targeted tests passed, source audit passed with 0 newly unreviewed access patterns. Python compile, normalized KiteClient mocked-transport integration, malformed/missing/unauthorized/partial/stale observations, policy negative tests, existing broker registry, signed JWT + revocation and registry immutability/audit safety were included.
- Complete line/function-level inventory (JSON and Markdown): [GitHub artifact 12859447724](https://github.com/nageshmadaram/sterling-v2/actions/runs/40786287009/artifacts/12859447724).
- Independent [SQLite/PostgreSQL registry foundation run 40784019259](https://github.com/nageshmadaram/sterling-v2/actions/runs/40784019259): 12 registry tests passed; disposable PostgreSQL migration, immutable trigger refusal, audit transaction rollback, one-way lock, backup/restore and downgrade/re-upgrade passed.
- The above are *targeted* repository checks. Prior full CI, Snapback release, regression, browser E2E and mobile checks have existing failures; none are waived. No real Kite/TrueData session, broker device acceptance or production DB/restore proof is claimed.

## Broker call inventory counts

The AST scanner recorded 272 detectable instances, with 0 newly introduced unreviewed patterns relative to the pinned Phase 2A SHA. Counts are non-overlapping detection categories, not numbers of runtime executions:

| Classification | Instances | Interpretation |
|---|---:|---|
| Approved Kite transport | 14 | Allowed existing provider interface |
| Approved normalized read boundary | 10 | No order-writing methods added |
| Legacy unnormalized reads | 135 | Require future migration, quality/staleness/tenant review |
| Legacy API raw-read routes | 25 | Require endpoint normalization/security review |
| Legacy broker-write methods | 30 | HIGH, review actual order authority/risk gates |
| Existing router execution methods | 4 | Existing authority, verify fail-closed controls |
| Legacy simulated-client operations | 12 | Verify no real broker escape |
| Legacy adapter/provider exceptions | 6 | Provider diagnostic boundary review |
| Legacy offline ingestion accesses | 7 | Verify isolation and credential handling |
| Legacy SDK / research accesses | 12 | HIGH, raw SDK outside adapter |
| Unknown / requires review | 6 | Explicit manual review queue |
| Existing TrueData provider URLs | 3 | Data-provider routing review |
| Static provider URL config | 3 | Not evidence of an outbound request |
| Disposable PostgreSQL negative-test SQL | 5 | Tests of append-only trigger refusal, not runtime writes |

## Urgent manual-review list

These are existing call sites, **not approved new execution permissions**. Preserve protective exits during a separate code-owner/risk review. Do not delete or redirect any live order path in this Phase 2B PR.

| Existing file | Function | Action | Review classification |
|---|---|---|---|
| backend/app/agents/broker_agent.py | BrokerAgent.place_order / cancel_order | place_order, cancel_order | legacy write; verify canonical authority |
| backend/app/api/v1/endpoints/kite.py | logout, convert_position._do | invalidate_session, convert_position | legacy write; auth/binding verification |
| backend/app/api/v1/endpoints/kite.py | modify_order._do, cancel_order._do | modify_order, cancel_order | legacy write; risk / reconciliation |
| backend/app/api/v1/endpoints/kite.py | place_gtt._do, modify_gtt._do, delete_gtt._do | GTT mutations | legacy write; stop protection |
| backend/app/api/v1/endpoints/kite_engine.py | emergency_square_off | emergency_square_off_all | legacy risk emergency exit |
| backend/app/services/adaptive_edge_runner.py | _exit_position | place_order | legacy strategy write |
| backend/app/services/ema_trend/fake_stop_driver.py | run_fake_stop | place_gtt, modify_gtt | legacy protective stop write |
| backend/app/services/ema_trend/runner.py | manage_campaign | cancel_order | legacy protective exit |
| backend/app/services/gamma_move_runner.py | _execute_exit_locked | place_order | legacy strategy exit |
| backend/app/services/intraday_runner.py | _exit_position, _scale_out | place_order | legacy strategy exits |
| backend/app/services/kite_engine/execution_lifecycle.py | _PlaceGttRecorder.place_gtt | place_gtt | legacy stop mutation |
| backend/app/services/kite_engine/monitor.py | _exit_position, resolve_resting_exit | cancel_order | legacy protective exit |
| backend/app/services/kite_engine/protective_stop.py | place_stop_outcome, move_stop, cancel_stop_result, cancel_fired_child | GTT / order cancellations | legacy stop protection |
| backend/app/services/opening_volume_execution.py | _cancel_and_reconcile, _sell_and_verify | cancel_order, place_order_option | legacy execution path |
| backend/app/services/price_rules.py | protect, exit_position | place_gtt, modify_gtt, delete_gtt | legacy price-rule protection |
| backend/app/services/snapback_live_exits.py | SnapbackExitManager._stop_fill_pending | cancel_order | legacy Snapback exit |
| backend/app/services/execution/order_router.py | OrderRouter._submit_live | place_order, place_order_option | existing execution authority; must retain approval |
| backend/app/services/execution/order_router.py | OrderRouter.execute_partial_market_close, amend_resting_stop | market_reduce_close, cancel_replace_stop | existing risk-reduction authority |

### Six unknown / direct construction cases requiring explicit owners

| File | Function / field | Issue |
|---|---|---|
| backend/app/services/providers/kite/diagnostics.py | provider URL at line 131 | direct diagnostic HTTP reachability; provider transport ownership |
| backend/app/services/providers/truedata/config.py | URL defaults at lines 19, 21, 24 | verify config-only use and credential separation |
| backend/app/services/providers/truedata/diagnostics.py | URL at line 190 | verify network request scope |
| backend/app/services/simulation.py | _hydrate_missing_candles at line 4880 | constructs KiteClient; verify simulation cannot gain real order access |

The legacy offline-SDK exception queue includes kitelake/cli.py, kitelake/pipeline.py, kitelake/ticks.py, kitelake/config.py, backend/scripts/hydrate_historical_ohlcv.py and backend/study/acceptance_transport.py / snapback_live_evidence_acceptance.py. These are not part of the normalized runtime read facade. The full JSON artifact contains each file, function, line and fingerprint.

## Code and safety boundary

Phase 2B uses the pre-existing KiteClient with mocked network transport, keeping current execution routes unchanged. The normalized facade now rejects impossible quote prices, negative depth, malformed order counts and unsupported option-right values, maps known broker status strings without guessing fills, preserves timezone/source/freshness metadata and returns sanitized error codes. No new broker write, HTTP order route, scheduler, strategy deployment or DB migration was introduced.

The AST guard fails CI for new direct broker reads/writes/SDK access beyond the pinned reference. It **does not remove** the existing 30 legacy write methods or prove indirect/dynamic/TypeScript access is absent. Pinned source exceptions are temporary review debt, not a safety waiver.

PostgreSQL database triggers protect immutable version/audit rows and one-way locks, but privileged direct INSERT, DDL and unlogged strategy metadata UPDATE are not fully eliminated. DB role privileges, transaction-bound DB enforcement and tenant isolation must be reviewed before exposing additional registry writes.

**Release decision:** BLOCKED; keep PRs #1–#4 as drafts; do not merge, deploy, enable live execution, activate broker order paths or proceed to Phase 3 market-data fallback.
