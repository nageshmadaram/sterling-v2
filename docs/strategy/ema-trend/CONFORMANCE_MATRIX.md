# EMA Trend artifact chain and conformance

Updated 2026-10-17. Paths are relative to the repository root. “Implemented” describes paper functionality, not economic or live approval.

| From → to | Implementation artifacts | Verification / limits |
|---|---|---|
| Saved settings → frozen profile/rule identity | `backend/app/engines/ema_trend/config.py`, `manifest.py`; `services/ema_trend/config_store.py` | Strict fields, aliases, CAS, rule-source hashing, condition-based Swing horizon; repository-wide origin/config/profile/source metadata and parent lineage; deployment/dataset parity remains partial |
| Provider equity/history → immutable completed tape | `services/ema_trend/scanner.py`; `engines/ema_trend/calendar.py`, `dataset.py` | Real instrument resolution, completed bars, anchored indices, restatement refusal; content-addressed manifests link scans/signals/plans; authenticated read-only dataset inspection; gaps, availability, zero volume and calendar uncertainty reported; bounded calendars in CALENDAR_SOURCES.md |
| Tape → causal features/pivots | `engines/ema_trend/indicators.py`, `features.py`, `pivots.py` | Hand-computed goldens, readiness/prefix tests; completed higher-timeframe context only |
| Features → setup | `engines/ema_trend/setups.py` | Retest/box, pivot known at touch, bullish confirmation, expiration/invalidation |
| Setup → risk-sized plan | `engines/ema_trend/sizing.py`; `services/ema_trend/execution.py` | Server-owned quantity, cash/fee/risk/quote admission, fresh data and revision gates |
| Plan → durable intent → virtual broker | `services/ema_trend/repository.py`, `provenance.py`, `execution.py`; shared `execution_service`, `order_journal` | CAS ownership/outbox + concrete virtual-client guard; real broker writes prohibited |
| Broker fill → canonical ledger → campaign | `services/ema_trend/execution.py`; shared `fill_ledger`, `trade_journal` | Actual cumulative fills, fees and attribution; deduplication and UNKNOWN tests |
| Campaign + completed feature → quarter/add/exit | `engines/ema_trend/state_machine.py`; `services/ema_trend/runner.py` | Q0 quarters, vacancy FIFO/episodes, no averaging down, separate emergency close; known terminal failed reduction escalates to risk closure; no optional successor retry |
| Held campaign → continued supervision | `services/ema_trend/runner.py`; `main.py` | Scanner/auto-off exit tests, restart reconciliation; backend/feed dependent paper stop |
| Owner API → Connect draft/apply | `api/v1/endpoints/ema_trend.py`; `frontend/src/hooks/useEmaTrend.ts`, `components/EmaTrendSettings.tsx` | Auth scope, revision conflict, reset staging, blockers, export |
| Snapshot → shared row → detail/chart/actions | `frontend/src/components/kite/board/EmaTrendBoard.tsx`, `emaTrendAdapter.ts`, `EmaTrendTicket.tsx`; `components/charts/EmaTrendChart.tsx` | Shared SignalBoard tokens, equity shares, historical read-only guards; browser found/fixed click bubbling |
| Recorded bars → native replay → report | `engines/ema_trend/backtest.py`; `services/ema_trend/replay.py`; unified backtest endpoint/panel | Shared reducer, no broker mutations, ordered lifecycle fills, signal/context dataset audit, modelled/inconclusive labels; daily entry proxy and no queue model |
| Application Operations → EMA virtual inventory/actions | `api/v1/endpoints/family_operations.py`; `services/ema_trend/snapshot.py`; `frontend/src/components/kite/LiveOperationsPanel.tsx` | Owner-scoped separate PAPER section; dedicated EMA pause/close-all; no real broker inventory merge; unreadable state remains unknown |
| Simulation controls → mode/reset | `services/simulation/mode.py` | EMA PAPER adapter, AUTO/live denied, owner-scoped reset; legacy live-capable strategy tuple retained |
| Saved artifacts → export/backup → restored state | `services/ema_trend/snapshot.py`; settings export UI; existing `snapback_backup.py` | JSON cap explicitly disclosed; real full SQLite backup/restore exercised on temporary database |

## Acceptance mapping

T01–T05 (contracts/features/causality/setups/core state), T06–T08 (risk/execution/persistence), and product/replay checks have automated coverage under `backend/tests/engines/ema_trend`, `backend/tests/services/test_ema_trend_runtime.py`, `backend/tests/api/test_ema_trend_api.py`, and frontend EMA tests. This is not a claim that every planned adversarial permutation or T01–T16 criterion is accepted.

Remaining acceptance work: optional bounded ordinary reduction successor retry; deployment-wide provenance parity; corporate-action/settlement handling; realistic fast-mode execution and economic evidence; connected-provider global ops acceptance; broker-held protection; full operator restore UX and independent usability drill. These gaps prohibit unattended/live acceptance. See IMPLEMENTATION_STATUS.md and VALIDATION_REPORT.md.
