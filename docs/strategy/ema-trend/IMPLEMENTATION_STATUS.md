# EMA Trend implementation status

Updated: 2026-10-17. Scope: `ema_trend` cash-equity strategy, paper/research only.
Live execution remains **disabled**. Code presence does not establish executed
test evidence, production coverage, broker safety, or economic viability.

This supersedes the initial 2026-10-15 skeleton table, which still listed
most packages NOT_STARTED after implementation code had been committed.

| Package | State | Source / remaining acceptance evidence |
|---|---|---|
| A00 Baseline | RECORDED | `BASELINE_MANIFEST.json` captures the earlier dirty workspace; not a verification of the current branch |
| A01 Contracts | CODE_PRESENT / TESTS_UNVERIFIED | `backend/app/engines/ema_trend/config.py`, `contracts.py`; contract tests need execution |
| A02 Data | CODE_PRESENT / TESTS_UNVERIFIED | `services/ema_trend/scanner.py`, `engines/ema_trend/calendar.py`; provider/session/adjustment gaps still require evidence |
| A03 Features | CODE_PRESENT / TESTS_UNVERIFIED | `indicators.py`, `features.py`, `pivots.py`; independently replay golden fixtures |
| A04 Setups | CODE_PRESENT / TESTS_UNVERIFIED | `setups.py`; this draft PR adds discontinuity refusal and tests |
| A05 Lifecycle | CODE_PRESENT / TESTS_UNVERIFIED | `state_machine.py`; this draft PR adds persisted operator flatten intent in campaign contracts |
| A06 Risk/economics | CODE_PRESENT / TESTS_UNVERIFIED | `sizing.py`, `services/ema_trend/execution.py`; validate dynamic cash, risks, fees |
| A07 Persistence/API | CODE_PRESENT / TESTS_UNVERIFIED | `repository.py`, `api/v1/endpoints/ema_trend.py`; CAS/restart/tenant drills pending |
| A08 Paper loop | CODE_PRESENT / TESTS_UNVERIFIED | `runner.py`, `execution.py`; this draft PR keeps flatten after delayed fills; browser-close/failure drills pending |
| A09 Research/replay | CODE_PRESENT / TESTS_UNVERIFIED | `backtest.py`, `replay.py`; evidence status stays INCONCLUSIVE |
| A10 Product | CODE_PRESENT / TESTS_UNVERIFIED | `EmaTrendSettings.tsx`, `EmaTrendBoard.tsx`, `EmaTrendChart.tsx`; interaction parity and actual E2E run pending |
| A11 Runtime fanout | PARTIAL / UNVERIFIED | Runner startup and stored campaigns present; global portfolio/ops/backup and disconnect drills not certified |
| A12 Integration closure | NOT_VERIFIED | Backend/frontend and Playwright EMA tests present but not executed in this GitHub-only edit session |
| A13 Family operation | PARTIAL / NOT_CERTIFIED | UI pause/flatten/export exist; restore drill and independent operational instructions pending |
| A14 Live eligibility | BLOCKED | No executable live transport for EMA; real broker protection, compliance, fees and economic validation not demonstrated |

## Current isolated branch and test requirements

Draft PR: https://github.com/nageshmadaram/sterling/pull/243

- No merge or release until tests execute successfully and regression failures
  are resolved or documented with reproducible baseline evidence.
- Minimum focused verification:
  `cd backend && python -m pytest tests/engines/ema_trend tests/services/test_ema_trend_runtime.py tests/api/test_ema_trend_api.py -q`
- Then run the unified backtest/API tests, relevant execution and simulation
  suites, EMA frontend component tests, and the Playwright EMA workflow.
- Prove that global live entry stays OFF, no real broker mutation occurs, a
  paused scan does not stop held-position risk management, and flatten
  requests survive ambiguous orders/restarts.
- Never promote results from modelled OHLC fills to verified real execution or
  claim that earnings are guaranteed.
