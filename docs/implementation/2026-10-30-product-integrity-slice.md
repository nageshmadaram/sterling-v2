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


## Continuation increment: role authorization, CI and recovery (2026-10-30)

### Source changes committed

- `account_policy.may_mutate` now centralizes operator-capable roles.
  The legacy persisted `user` role retains operator semantics; `operator` and
  `viewer` may be created without a destructive schema rewrite. Viewer/unknown
  roles are default-denied on Kite API writes, strategy entry admission and
  delegated broker/integration configuration changes. Feature grants remain
  independent of mutation privileges. The session version is bumped by the
  existing audited user-role edit transaction, revoking old JWTs.
- The WorkspaceRouter shows an explicit read-only notice for authorized
  Kite viewers and still unmounts Kite immediately on grant revocation.
- Browser Playwright setup now supplies a complete policy response and
  intentionally visits `/kite`: root is the administration destination for
  the designated admin. The previous setup attempted to locate the Kite shell
  at root with incomplete authorization fixture data.
- The Snapback release gate now installs `backend/requirements.lock.txt`
  instead of the unbounded development dependency list. An earlier run
  installed Pydantic 2.14 and failed 9 Snapback tests against
  `freezegun.api.FakeDatetime`. This change must be verified in fresh CI;
  it does not weaken the suite.
- Added `scripts/verify_sqlite_recovery.py`, which uses SQLite's online
  backup API to copy a source opened read-only into an **exclusive new file**
  outside the source directory, verify `quick_check`, table row counts and
  user schema version, and delete a failed snapshot. The source is never
  overwritten or modified. The CI suite includes isolated WAL/restart,
  refusal and cleanup tests.

### Grandfathered direct-order call audit

The static legacy baseline still counts eight method calls in seven
(file,method) entries; this is **NOT** complete canonical execution convergence.

| Location | Calls | Classification | Current boundary |
| --- | ---: | --- | --- |
| BrokerAgent | 1 | Paper-only generic facade | Live/unknown transport refused |
| Legacy OrderRouter | 2 | Paper-only generic entry facade | Live/unknown transport refused |
| Adaptive Edge | 1 | SELL exit | Inventory + `INTENT_REDUCE` and bound capability |
| Gamma Unwind | 1 | SELL exit | Precommitted exit identity + bound reduction |
| Intraday | 2 | SELL exit/scale-out | Broker inventory + bound reduction |
| Opening Volume | 1 | Emergency SELL close | Broker quantity + bound reduction |

A new AST regression checks every engine direct call is a literal SELL
inside a canonical reduction capability and refuses extra or unbounded calls.
These existing exit implementations are **not yet** routed through one durable
`CanonicalExecutionService.submit_order` bridge. Changing exit paths without
preserving stop cancellation, broker-inventory proof, ambiguous transport
outcomes, recovery and protection rearming would introduce financial risk.

### Validation matrix and exact scope

| Test | Command | Current claim |
| --- | --- | --- |
| Backend auth and roles | `cd backend && pytest tests/test_role_based_write_access.py tests/test_account_policy.py tests/test_workspace_access.py -q` | Committed; final-branch CI confirmation required |
| Direct calls and execution | `cd backend && pytest tests/unit/test_canonical_execution_convergence.py tests/services/test_canonical_entry_input_integrity.py -q` | Committed; final-branch CI confirmation required |
| SQLite backup/recovery | `PYTHONPATH=. python -m pytest scripts/tests/test_sqlite_recovery_preflight.py -q` | Executed by CI workflow; final result pending |
| Frontend role and navigation | `cd frontend && npm ci && npm run media:styles && npx vitest run src/pages/__tests__/WorkspaceRouter.test.tsx` | Committed; final-branch CI confirmation required |
| Browser journey | `cd frontend && npx playwright test -c playwright.config.ts` | Full CI/browser acceptance pending |
| Mobile Expo | `cd mobile && npm ci --ignore-scripts && npm run ts:check && npm test && npx expo install --check && npx expo export --platform all` | BLOCKED by dependency lock drift, issue #16 |
| Full backend | `cd backend && pytest tests/ -q` | Pending full pass; prior interrupted attempts are not passes |

Only claim PASS for a particular immutable commit SHA with a completed
successful job. Prior revisions passed frontend typecheck, Vitest (after
media asset generation), and Docker smoke, but earlier E2E setup and
Snapback dependency errors caused release-gate failures.

### Recoverability boundaries

A restored SQLite copy can be tested under an isolated test instance only.
No script may overwrite a live database. Stop writers for a deterministic
snapshot-versus-source comparison, preserve WAL files, and keep backups in
restricted storage. `quick_check` + row counts is **not** a complete
cryptographic comparison or a production recovery drill. PostgreSQL
Alembic upgrade/downgrade, real host restore, broker reconciliation after
restore and independent sign-off are not covered. Roll back this code only
through a reviewed commit revert with order submission authority disabled;
preserve durable order journals and account-binding history.

### Remaining implementation register

| Priority | Item | Status | Due |
| --- | --- | --- | --- |
| P0 | Canonicalize all five remaining direct SELL reductions with durable order intents and timeout/restart reconciliation, and retire paper-only facades | PENDING | Unscheduled |
| P0 | Complete full backend/frontend policy parity for all admin/operator/viewer mutation surfaces and browser controls | PARTIAL | Unscheduled |
| P0 | Execute and reconcile complete order lifecycle across restart with no duplicate broker sends | EXISTING TESTS, END-TO-END PASS NOT ESTABLISHED | Unscheduled |
| P1 | Full SQLite migration + rollback and Postgres restore drills on disposable real schema | PARTIAL (SQLite snapshot test only) | Unscheduled |
| P1 | Desktop/mobile Playwright, real-device routes, and error recovery | PENDING | Unscheduled |
| P1 | Expo SDK package/lockfile update (issue #16), clean npm ci and export | BLOCKED / NOT WORKED in this increment | Unscheduled |
| External | Production host/TLS/MFA device checks, broker market acceptance, monitoring/notification and independent approver | BLOCKED | Unknown |

**Decision unchanged:** product-wide end-to-end certification NOT ESTABLISHED;
production controls accepted 0/12, blocked 12/12; production deployment NO-GO;
live trading and real broker order routing NOT AUTHORIZED.
