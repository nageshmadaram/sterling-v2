# Remediation validation

> Follow-up merge/sync verification: [23 October report](../2026-10-23/SYNC_AND_FOLLOWUP.md) adds image/publisher identity, partial-fill risk, read recovery, chart route, test database safety and missing-target R fixes.

The final result table is completed from the captured test logs. These checks validate local source, not the production process already running on port 8000.

| Check | Result | Evidence |
|---|---|---|
| Backend engines, modes, protection, journal, Price Rules and Gamma | 1,073 passed | [backend-release.log](evidence/fixes/backend-release.log) |
| Final focused manual-entry and admission verification | 37 passed | [entry-final.log](evidence/fixes/entry-final.log) |
| Full frontend suite | 2248 passed across 240 files | [frontend-release.log](evidence/fixes/frontend-release.log) |
| Final calculator, copy and actual order mutation-hook regressions | 15 passed | [plan-final.log](evidence/fixes/plan-final.log) |
| TypeScript | Passed | [typescript.log](evidence/fixes/typescript.log) |
| Production frontend build | Passed, output `/tmp/sterling-qa-fixed-build` | [build.log](evidence/fixes/build.log) |
| Source whitespace (raw evidence excluded) | Passed | `git diff --cached --check -- . ':!docs/qa/**'` |

The focused tests overlap the broad suites; do not add their counts together. Backend coverage is a selected suite, not all backend tests in the repository. The frontend command runs the full Vitest suite. No skipped failures or relaxed production admission switches are used to obtain these results.

Commands, from `backend/`:

```sh
.venv/bin/python -m pytest \
  tests/engines/sterling_kite_engine \
  tests/unit/test_mode_execution.py \
  tests/services/test_price_rules.py \
  tests/unit/test_trade_journal_store.py \
  tests/unit/test_trade_journal_stats.py \
  tests/unit/test_trade_journal_hooks.py \
  tests/unit/test_trade_journal_api.py \
  tests/unit/test_trade_journal_backfill.py \
  tests/services/test_gamma_move_historical.py \
  tests/services/test_gamma_move_scanner.py \
  tests/services/test_gamma_move_scanner_audit.py \
  tests/test_trader_qa_20261022.py -q
```

Commands, from `frontend/`:

```sh
./node_modules/.bin/vitest run --maxWorkers=2
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/vite build --outDir /tmp/sterling-qa-fixed-build
```

Known non-failing output: upstream FastAPI/Starlette and multiprocessing deprecations; an existing duplicate Navigator OpenAPI operation ID warning; existing jsdom `window.confirm` output in an account-selection test; Vite glob deprecation and large-chunk advisory. These runs do not certify generated OpenAPI client generation, bundle performance or browser account-switch behavior from that test alone.

Broker safety: the entry fixture uses `AsyncMock`; Price Rules uses a fake broker and isolated database; the AMO serializer test uses `httpx.MockTransport` and rejects socket connections. Negative funds/IP mismatch were not used as reasons to weaken admission or manufacture a successful order. No real broker orders or GTTs were created or cancelled.

Deployment boundary: runtime identity diagnostics are implemented but are not available from the old running process until the controlled release. Go-live still requires matching immutable artifacts, reconciled broker state and authenticated browser/open-session checks described in [FIX_IMPLEMENTATION.md](FIX_IMPLEMENTATION.md).

Raw console logs, downloaded CSV files and unified patches preserve their original whitespace. They are excluded from the source-formatting check.
