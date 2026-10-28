# Sterling V2 — Phase 3 market-data reliability validation register

**Date:** 2026-10-29  
**Disposition:** targeted CI **PASS**, full production release **BLOCKED**.  
**Phase 3 PR:** https://github.com/nageshmadaram/sterling-v2/pull/5  
**Validated source commit:** `9520b3570cf1a465ec8e42aecd1e034c60023777`  
**Validation run:** https://github.com/nageshmadaram/sterling-v2/actions/runs/40789670146

## Verified result

The dedicated GitHub Actions job ran in the real repository checkout with
Python 3.12 and Sterling's backend dependency lock:

| Gate | Observation |
|---|---|
| New Phase 3 quote/depth/option/snapshot/quality models | PASS (included in targeted suite) |
| Broker → alternate → lake → previous deterministic fallback | PASS |
| Stale/unknown/future timestamp, partial quote, missing sources | PASS |
| Quality scoring, structured events, read-only monitoring and provider-health checks | PASS |
| Historical UTC normalization, gap detection, OHLCV aggregation | PASS |
| Opt-in atomic isolated local history cache | PASS |
| Existing TrueData historical and last-tick method adapters (synthetic responses) | PASS |
| Market regime classification | PASS |
| Phase 2A / 2B normalized broker, provider contract, direct-access tests | PASS |
| Registry signed access token, audit/immutability safety regression | PASS |
| **Combined targeted tests** | **67 passed, 7 warnings, 0 failed** |
| Phase 2B source access audit | PASS, **0 new unreviewed**; 272 existing findings |
| Static no-new-order-method and no-FastAPI-route check | PASS |
| Full application regression / live deployment certification | **NOT PASSING / NOT PERFORMED** |
| Live broker / TrueData authenticated source feeds | **NOT VERIFIED** |
| Database privilege hardening and production restore | **NOT VERIFIED** |

The seven warnings are pre-existing Python SyntaxWarnings for escaped Markdown
backticks in the Phase 2B audit script
(`backend/scripts/audit_broker_access.py` lines 273, 278 and 279).
They are not hidden or marked as passed security checks; they require an
independently reviewed cleanup if needed.

## Implementation and isolation

- New `backend/app/schemas/market_reliability.py` defines immutable, typed,
  timezone-aware observational records. Price, quantity and candle consistency
  are enforced at the model boundary.
- New `backend/app/services/market_data/reliability.py` performs deterministic
  quality evaluation, explicit provenance and fallback, observational regime
  tagging, and structured quality health/events. Explicit `provider_health()`
  reports unreachable as unavailable, and missing probes as unknown.
- New `backend/app/services/market_data/historical_reliability.py` adds
  opt-in local historical cache, bounded UTC date-range queries, conservative
  gap detection, complete-candle aggregation, and read-only adapters for
  existing TrueData tick/bar methods. The alternate tick adapter requires
  an exact, reviewed exchange-symbol → TrueData symbol map.
- The Phase 2A `NormalizedKiteReader` remains the broker observation
  boundary. Existing Kite order implementations, strategy/risk code and
  exchange routing were **not modified**.
- The new snapshot/quality records have explicit
  `execution_authorized=False`, enforced by the Pydantic literal type.
  Quality `good` **cannot** authorize or bypass existing risk gates.
- No new broker write, route, UI, execution scheduler, database migration or
  production deployment was added. The existing immutable strategy version
  and transactional audit schema remains unchanged.

## Remaining assumptions and manual-review exceptions

1. **Real-time provider proof:** simulated tests do not prove an actual
   authenticated Kite quote/depth response, TrueData symbol eligibility or
   quotas, network latency, clock skew, real-time timestamps, or device receipt.
2. **Market calendar:** time-window gaps conservatively count missing calendar
   slots, including sessions with market closures; session-aware history
   coverage has not been integrated.
3. **Persistence:** the JSON lake is deliberately isolated, not a replacement
   for `kitelake` parquet. No real mounted removable drive, production
   rights, encrypted cache, retention, or restoration was certified.
4. **Regime:** trend/range/volatility/liquidity is a deterministic observation
   with small 8-candle window, not a statistically certified model, and not
   connected to the trading signal/admission lifecycle.
5. **Operator visibility:** health and events are internal read-only service
   outputs/log entries; no new authenticated frontend/health route was
   introduced. Production persistent audit sink and operator UI receipt need
   separate approval.
6. **Legacy broker access:** pinned Phase 2B audit still contains 272 findings,
   including 30 legacy write calls, 4 existing execution-authority calls,
   135 service reads, 25 API-route reads and 6 unknown/review items. A zero
   *new* count does not approve or eliminate any existing exceptions.
7. **DB/security:** privileged INSERT/DDL and some mutable metadata DB paths
   are not fully isolated; database roles and further security review remain
   required before exposing more registry writes.
8. **Full application:** historic unrelated CI/backend/browser/mobile/Snapback
   release failures remain unwaived; comprehensive regression and deployment
   certifications have not been completed.

## Release control

Keep PRs #1–#5 draft, dependent and unmerged. Keep `main` untouched.
**Live execution is OFF; no new broker execution or order submission authority.**
Do not deploy, begin Phase 4, wire strategy decisions to this provider resolver,
or claim market-data production readiness on the strength of targeted tests.
