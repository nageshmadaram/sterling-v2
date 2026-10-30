# Sterling V2 product integrity: implementation slice (2026-10-30)

## Purpose and release boundary

This is a **source-only, reviewable product-integrity increment**, not an end-to-end
completion certificate. The branch starts at `main`, does not merge any frozen
Phase 11 or governance branches, and neither authorizes broker writes nor
changes deployment runtime configuration. Review and verify before integration.

## Verified source findings

1. `backend/app/services/account_binding_service.py` wrote broker-binding
   manifests in place using `Path.write_text`, so a write interruption could
   leave partial JSON. The file name was not checked against its internal binding
   identity. A generated ID incorporating unsafe components was not rejected at
   the filesystem boundary.
2. `backend/app/services/canonical_strategy_entry.py` accepted broker capital
   values of positive infinity, did not reject NaN/Infinity entry prices before
   constructing an execution request, and coerced fractional sizes via `int`.
3. `backend/app/core/broker_account_binding.py` inferred live-ready permission from any scope containing `live`, including `not_live` and `live_disabled`. It now requires exactly `live` or `live_minimum` (case-insensitive after trimming); unknown historical scopes remain readable but NOT live-ready.
4. `backend/tests/unit/test_canonical_execution_convergence.py` declares eight
   grandfathered direct entry calls in seven (file,method) entries. This is a
   static legacy-allowance baseline, **not proof the canonical migration is
   complete**. The existing noncanonical entry floor must remain enforced.
5. `backend/app/services/auth/user_store.py` and
   `backend/app/services/auth/operator_credentials.py` still use the
   `admin|user` role vocabulary. Complete independent `operator|viewer` policy,
   MFA lifecycle and principal/session migration are not established by these
   source files.
6. The independent production acceptance register associated with draft PR #14
   recorded **0/12 controls accepted and 12/12 blocked**. Offline CI and
   source tests do not replace host, device, database recovery, broker, market
   or independent approval evidence.

## Implemented on this branch

| Scope | Change | Evidence to execute |
| --- | --- | --- |
| Binding persistence | Stage a private 0600 file beside the destination, fsync it, publish via same-filesystem `os.replace`, fsync containing directory, remove staging after error | `backend/tests/unit/test_account_binding_manifest_integrity.py` |
| Binding identity | Reject unsafe filenames and mismatched filename/manifest IDs instead of loading an identity into the wrong account | Same test module |
| Binding permission scope | Refuse substring-spoofed `live` account scopes; only exact `live`/`live_minimum` are potentially live-ready, with all other gates still required | Same test module |
| Entry validation | Require integer quantity, finite positive capital price, finite required capital | `backend/tests/services/test_canonical_entry_input_integrity.py` |
| Broker capital | Reject non-finite broker-reported and paper-balance amounts | Same test module |
| Frontend CI | Generate local media CSS and icon assets before direct Vitest command | `.github/workflows/ci.yml`; PR check results |
| WebSocket acceptance | In isolated CI environment, explicitly grant the synthetic admin Kite permission via audited policy API, then authenticate WebSocket at handshake and frame | `backend/study/check_frontend_stream.py`; CI E2E job |
| Browser CI | Use Python 3 in CI when developer-only backend venv is absent | `frontend/e2e/tma-login-boundary.spec.ts`; CI E2E job |

No broker API is called by the new negative-path tests; the positive-path
test uses a synthetic mocked canonical service.

## Validation plan / truth table

| Layer | Required evidence | State at source authoring |
| --- | --- | --- |
| Code review | Inspect diff and confirm no broker authority or config changes | Implemented, independent review pending |
| Focused backend | `cd backend && python -m pytest tests/unit/test_account_binding_manifest_integrity.py tests/services/test_canonical_entry_input_integrity.py -q` | NOT YET EXECUTED |
| Existing backend regressions | `cd backend && python -m pytest tests/unit/test_broker_binding_verification.py tests/unit/test_canonical_execution_convergence.py -q` | NOT YET EXECUTED against this branch |
| All backend/frontend/E2E | PR CI `.github/workflows/ci.yml` including Python 3.12/3.13, frontend build/Vitest, browser matrix | NOT YET VERIFIED |
| Real broker and production | Independent external receipts, authority and sign-off | BLOCKED; NOT REQUESTED |

Prior PR #15 CI revisions showed frontend Vitest succeeding after media generation, frontend typechecking succeeding, and a separate mobile Expo dependency version failure. The current revision must be checked independently. See [issue #16](https://github.com/nageshmadaram/sterling-v2/issues/16) for the mobile mismatch.

After a CI run, replace **NOT YET EXECUTED** only with a linked job result. A
green isolated test does not certify a whole application or any real broker path.

## Pending / due / not addressed

| Priority | Remaining work | Status | Due |
| --- | --- | --- | --- |
| P0 | Convert remaining grandfathered direct order calls with route-by-route lifecycle/audit/recovery evidence | PENDING | Unscheduled |
| P0 | Comprehensive route/auth test matrix (including negative authorization and direct HTTP calls) | PENDING | Unscheduled |
| P0 | Integrate principal roles, broker assignment, workspace grants, revocation, token/session invalidation and migration | PENDING | Unscheduled |
| P1 | Independent database upgrade/recovery/downgrade drill in a production-like environment | EXTERNALLY BLOCKED | After approved environment |
| P1 | Resolve mobile Expo dependency/lockfile version alignment and pass Expo export checks (issue #16) | PENDING — CI FAIL | Unscheduled |
| P1 | Verify all frontend journeys on desktop/mobile and real device/error recovery | NOT WORKED in this slice | Unscheduled |
| P1 | Inventory all endpoints, provider/lake paths and migration scripts against running services | NOT WORKED in this slice | Unscheduled |
| P1 | Full local compose integration and synthetic end-to-end audit run with persisted state | NOT WORKED in this slice | Unscheduled |
| P2 | Clean up remaining documentation/config drift and broad architectural legacy | NOT WORKED in this slice | Unscheduled |
| External | Host/TLS/secrets/production DB/broker/market/monitoring/independent approver acceptance | BLOCKED | Unknown |

## Migration, rollback and recovery

There is no schema migration in this increment. Existing JSON binding
manifests continue to load unchanged if the internal identity matches the
filename. Files with inconsistent identity or unsafe generated identifiers
are deliberately rejected; preserve the original bytes, investigate
out-of-band, and never silently reassign an account.

Backup mutable state before deployment. If application compatibility fails,
roll back the code commit only **with the broker and execution boundaries
disabled**; do not rewrite or delete existing binding histories. The staged
files use a `.tmp` suffix and do not count as manifests. Interrupted writes
before rename preserve the previous committed file; after rename the
replacement file is complete. Cross-process last-writer-wins races are **not
solved** by this patch and still require transactional/locking treatment.

## Decision

`PRODUCT_INTEGRITY_PATCH: IMPLEMENTED, VERIFICATION PENDING`

`END_TO_END_PRODUCT_COMPLETION: NOT ESTABLISHED`

`PRODUCTION_CERTIFICATION: NOT ACHIEVED`

`PRODUCTION_DEPLOYMENT: NO-GO`

`LIVE_TRADING: NOT AUTHORIZED`

`BROKER_ORDER_ROUTING: NOT AUTHORIZED`
