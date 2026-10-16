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

## 2026-10-17 continued safety audit

The following pending-order and operator controls have code + regression cases
on draft PR #243; automated execution of those regression cases remains **NOT
VERIFIED** in this connector-only session:

- Reject a new reserved BUY when a campaign has a durable flatten latch.
- Cancel a RESERVED BUY rather than submitting it once flatten is requested.
- Request cancellation of submitted, known BUY orders after flatten, even when
  confirmed owned quantity is still zero.
- Record full flatten intent *before* broker-client acquisition and market-data
  reads; a disconnect returns `UNRESOLVED` without inventing a fill.
- Avoid requesting a quote once a never-submitted entry is cancelled and its
  zero-inventory campaign has reached CLOSED.
- Refuse to issue a duplicate SELL while `pending_close` is still unresolved.
- Keep live execution disabled, risk gates intact and strategy limited to paper.

Remaining critical proof: run the backend tests and real simulated-recovery
drills, check cross-process behavior, verify historical replay causality,
confirm frontend UI status accurately shows UNRESOLVED, and perform independent
broker/production certification before any real-money path is considered.

## 2026-10-17 operator UI closure

- The EMA Trend board now exposes a deliberate `Flatten all paper` command
  with an explicit confirmation. It invokes the authenticated API, which
  pauses new paper entries before attempting to close campaigns.
- Individual full-close requests remain available while an entry/reduction
  order is pending, including zero confirmed shares. Partial-reduction controls
  remain disabled in that unresolved state.
- API `UNRESOLVED` is an accepted request with outstanding reconciliation,
  not a successful flat fill, and must be displayed as such.
- Board status shows the persisted `flatten_requested` latch and
  `pending_close` ownership. A campaign with pending orders is not counted as
  safely flat merely because its projection is `CLOSED`.
- Frontend component/adapter tests and an API test cover these paths, but
  none have been run in this connector-only session.

**Do not present the EMA strategy as production-live, broker-protected or
income-generating.** Full operator acceptance requires executing tests and
simulated order/recovery drills. Its implementation remains paper/research only.

## Final emergency audit notes (2026-10-17)

- Resume now refuses new exposure while an operator flatten request has
  unsettled attributable lots, outstanding orders or nonterminal state.
- A campaign with state CLOSED and unsettled BUY remains visible as an error
  in the EMA board; the individual full-close request remains accessible.
- New API and frontend regression tests cover these cases; they are written,
  **not executed**. No result should be reported as passing.
- GitHub Actions returns failures with no job steps; logs are unavailable
  (`BlobNotFound`). The root cause is unknown and the checks are not evidence
  of executed test cases. See [OPERATOR_HANDOVER.md](OPERATOR_HANDOVER.md)
  for exact commands, transfer instructions, and remaining safety gates.

Status: **REVIEWABLE_PAPER_CANDIDATE; NOT CERTIFIED; NO REAL BROKER ORDERS**.
