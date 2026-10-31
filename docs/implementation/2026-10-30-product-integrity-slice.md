# Sterling V2 product integrity: implementation slice (2026-10-30)

> **Historical implementation log, not the current acceptance register.**
> The intermediate counts of eight, seven and five direct broker-order
> calls and the then-pending migration notes below describe earlier commits.
> The current authoritative status is
> [2026-10-30-product-integrity-current-review.md](2026-10-30-product-integrity-current-review.md).
> The latest source baseline is **zero grandfathered direct order sends**,
> but runtime acceptance and canonical fill projection remain **unverified**.

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
4. `backend/tests/unit/test_canonical_execution_convergence.py` declares five
   grandfathered reduction calls across four (file,method) entries. This is a
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

The static legacy baseline now counts five method calls in four
(file,method) entries; this is **NOT** complete canonical execution convergence.

| Location | Calls | Classification | Current boundary |
| --- | ---: | --- | --- |
| BrokerAgent | 0 | Typed canonical facade | Legacy unbound order submissions refused; CanonicalExecutionService handles requests |
| Legacy OrderRouter | 0 | Typed canonical handoff | Generic LIVE payload refuses all broker sends, even on paper doubles |
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
| P0 | Canonicalize the five remaining direct SELL reductions with durable order intents and timeout/restart reconciliation | PENDING | Unscheduled |
| P0 | Complete full backend/frontend policy parity for all admin/operator/viewer mutation surfaces and browser controls | PARTIAL | Unscheduled |
| P0 | Execute and reconcile complete order lifecycle across restart with no duplicate broker sends | EXISTING TESTS, END-TO-END PASS NOT ESTABLISHED | Unscheduled |
| P1 | Full SQLite migration + rollback and Postgres restore drills on disposable real schema | PARTIAL (SQLite snapshot test only) | Unscheduled |
| P1 | Desktop/mobile Playwright, real-device routes, and error recovery | PENDING | Unscheduled |
| P1 | Expo SDK package/lockfile update (issue #16), clean npm ci and export | IMPLEMENTED IN SOURCE; final clean install/export acceptance pending | Unscheduled |
| External | Production host/TLS/MFA device checks, broker market acceptance, monitoring/notification and independent approver | BLOCKED | Unknown |

**Decision unchanged:** product-wide end-to-end certification NOT ESTABLISHED;
production controls accepted 0/12, blocked 12/12; production deployment NO-GO;
live trading and real broker order routing NOT AUTHORIZED.


## Continuing implementation — canonical BrokerAgent, broker grants and mobile parity

- `app/agents/broker_agent.py` no longer calls `adapter.place_order`.
  The facade requires a typed `ExecutionRequest` and delegates to
  `CanonicalExecutionService.submit_order`. Old ad hoc symbol/side/quantity
  payloads fail closed, even on paper adapters. A real broker adapter cannot
  gain an unreviewed send just because the wrapper is called. The static
  direct-call baseline eventually shrank from **eight to five**; its test is updated to
  forbid a newly reintroduced call.
- Kite broker account `POST /accounts/{id}/activate` now checks an explicit
  delegated broker update capability twice around the account lock; connection
  test `POST /accounts/{id}/test` requires the same grant before touching a
  broker client. Previously a Kite workspace grant alone was enough.
- Mobile Markets now queries `/api/v1/auth/me` before displaying order-entry
  affordances and closes an open order form on revoked permissions. The form
  independently fetches a fresh server policy before sending an order. Mobile
  global halt/daily-loss changes require designated administrator policy and
  recheck it immediately before mutation. Failure to read policy denies UI
  actions; backend remains the authoritative enforcement point.
- Expo SDK 58 constraints and `mobile/package-lock.json` were aligned using
  `npm install --package-lock-only` in an isolated GitHub Actions job, recording
  real registry metadata for six direct and transitive dependencies. The
  temporary resolver workflow was deleted. **Do not call Expo fixed until
  clean `npm ci`, `expo install --check`, mobile tests and export all pass.**
- The Snapback lifecycle alert test now grants the real simulation owner an
  explicit Kite feature in its disposable database. No production authorization
  bypass was introduced. Both Snapback and regression-gate CI use the pinned
  Python dependency lock instead of a potentially incompatible newer
  Pydantic/Freezegun pair.

### Updated verification (current branch head, not historical evidence)

```sh
cd backend
python -m pytest -q tests/test_agents.py tests/test_workspace_access.py \
  tests/unit/test_canonical_execution_convergence.py \
  tests/unit/simulation/test_r1_snapback_alerts.py
cd ../mobile
npm ci --ignore-scripts
npm run ts:check
npm test
npx expo install --check
npx expo export --platform all --max-workers 2 --output-dir dist
```

Run the repository's full backend, browser (Chromium/Firefox/WebKit),
regression, Expo, Docker and Snapback release gates on one immutable commit.
Success on an earlier SHA is not transferable to newer commits.

### Updated due/blocked register

**Completed in source:** BrokerAgent canonical request routing, broker
activation/connection grants, mobile role-aware order and global-risk controls,
Expo dependency/lockfile generation. **Validation of the latest SHA:** pending
independent CI results. **Still due (unscheduled):** five direct protected SELL
reductions, cross-engine UNKNOWN/restart
reconciliation, all database migration downgrade/restore drills, mobile real
device and desktop browser acceptance. **External blockers:** host TLS/secrets,
broker market acceptance, independent monitoring and release approval. Nothing
here grants live execution. **Production control acceptance: 0/12; blocked
12/12. Deployment: NO-GO. Live broker routing: NOT AUTHORIZED.**

### Offline journal restore drill (new test)

`backend/tests/unit/test_canonical_journal_snapshot_restore.py` uses the
application's actual `db.init()` schema, canonical `order_journal.reserve`,
`claim_submission`, `acknowledge`, `observe_order`, and
`mark_projected` primitives. The test invokes the isolated SQLite backup
verification CLI in another Python process, reopens the resulting copy, and
checks that a completed fill, pending position projection, immutable signal
identity, and UNKNOWN post-timeout state survive restoration. Replayed signal
reservations must not regain submission claim authority. This adds a
persistent offline integration check, **not** real broker-truth settlement.

Validation:
```sh
cd backend
python -m pytest tests/unit/test_canonical_journal_snapshot_restore.py -q
```
Its focused CI result must be reviewed against the final SHA before claiming
PASS. Production PostgreSQL and live-host backup/restore, complete broker
reconciliation, protection reacquisition and independent acceptance remain
unscheduled and blocked.

### OrderRouter generic LIVE path retired from direct broker I/O

`OrderRouter.submit(OrderRouterRequest)` now rejects unbound `LIVE`
requests after preflight safety checks: they contain no authenticated
account/generation/ownership fields and cannot authorize even a paper
adapter order. `OrderRouter.submit_canonical(request=ExecutionRequest,
risk_approval=RiskApproval)` hands typed requests to the existing
`CanonicalExecutionService`, which owns risk validation and journaling.
The former `adapter.place_order` and `adapter.place_order_option`
calls were removed. Explicit PAPER/SHADOW positions remain supported
through their existing simulation hooks. Safety and router tests were
updated to expect default-deny rather than a fake adapter fill. The
static baseline is now **five remaining direct SELL reductions**.

### Mobile safety policy direction parity

The earlier mobile global-risk UI was stricter than its backend:
the backend allows operators to engage a halt or tighten a loss limit,
but requires the designated administrator to lift a halt, disable or
loosen limits. The mobile actions now use fresh server identity grants
and current loss thresholds before each mutation, with negative INR
loss threshold comparison matching the backend. A lift request includes
a reason; it never sets `acknowledge_investigation`, so unresolved
broker conditions remain a blocker. The backend remains authoritative.

**Latest source status:** Implementation committed in review branch;
full latest-SHA CI outcomes pending. No automatic release/merge or
production order routing is permitted.

### Review continuation: legacy duplicate claims and mobile order acknowledgement

- `OrderRouter.submit` now refuses an already-recorded idempotency key
  when the request is a legacy unbound LIVE payload. Without that check,
  a different canonical sender's recorded ID could previously be
  misrepresented as an accepted LIVE duplicate by the legacy facade.
  A regression test constructs this case and asserts no adapter I/O.
- Mobile `OrderModal` no longer converts every HTTP 200 into "order
  placed successfully." It requires a nonblank string `order_id`
  and rejects error/blocked/rejected/unknown result statuses. The
  confirmation says **acknowledged, not filled**; an ambiguous response
  directs the user to inspect Orders before retrying. Tests cover empty,
  malformed, blocked and unknown results.
- Focused CI exercises `tests/test_order_router.py`,
  `tests/test_release_safety_62_65.py`, the real journal snapshot
  restoration test, and mobile policy/outcome tests via `npm test`.
  Latest-SHA CI is required before declaring these fixes validated.

**Production certification:** NOT ACHIEVED. Protected SELL migration
remains pending for all five remaining direct calls. No release, deployment,
broker session, or live-order routing is authorized.


### P0 continuation: broker ACK correctness and last-boundary reduction proof

**Committed in source; latest CI verification pending.**

- `POST /api/v1/kite/orders` now returns HTTP **409**
  `submission_unknown` if the idempotency cache holds the marker
  `PENDING` or `UNKNOWN`. Such values are not broker order identifiers.
  The handler never calls the broker on these duplicate requests, and
  clients must inspect/reconcile orders rather than retry. Two parametrized
  router tests exercise the persisted-marker response with a paper
  transport; both assert no additional broker I/O.
- `CanonicalExecutionService._submit_order` repeats the fresh,
  account/product/symbol/side/quantity broker-position reduction proof
  after the journal's submission claim, at the last async boundary before
  the transport send. A contradictory fresh position book is a definitive
  local refusal: it transitions the intent to `REJECTED`, sends no order
  and does not manufacture a recovery-required broker uncertainty.
  The regression test simulates inventory disappearing between the first
  and second proof. This is defense against manual or concurrent
  exposure changes, not a replacement for broker-side reduce-only
  semantics or cross-process controls.

Focused test commands:

```sh
cd backend
python -m pytest tests/test_kite_router.py::test_order_idempotency_marker_never_masquerades_as_broker_ack -q
python -m pytest tests/unit/test_execution_unknown_settlement.py -q
```

**Unresolved P0:** Five protected SELL direct sends still require
engine-specific durable canonical journal conversion and reconciliation.
Do not replace those emergency exit calls with untested generic
`submit_order`: the canonical inventory ledger and engine-specific
position stores must agree, GTT cancellations and UNKNOWN outcomes
must survive process death, and emergency protection must not be
lost. No live execution or broker-routing permission granted.

**Release and external acceptance:** 0/12 controls accepted, 12/12
blocked; independent sign-off NOT PROVIDED; production NO-GO.


### P0 continuation: legacy direct SELL UNKNOWN safety (review-only)

Two of the five grandfathered engine call sites have *not* been
canonicalized, but their dangerous timeout handling has been corrected:

- **Adaptive Edge (one direct SELL):** before the call, persist the
  exact requested size, price and reason alongside the `EXIT_INTENT`
  claim. Re-arm the cancelled GTT only after a *definitive* local/broker
  refusal. A timeout, unknown exception or successful response lacking an
  order ID leaves `exiting=True` persisted, prevents a second SELL,
  and engages the global halt for live broker clients.
- **Intraday (two direct SELLs):** full exits and partial scale-outs
  follow the same rule. Both persist requested quantity, limit price,
  type and reason before submission. Ambiguous replies preserve the
  pending-exit claim and never recreate a protective SELL; only a
  provably definitive refusal clears the claim and can re-arm the stop.
  Live ambiguous replies engage the halt. Test fixtures reset their
  isolated halt state between cases.

Focused test commands:

```sh
cd backend
pytest -q tests/services/test_adaptive_edge_execution.py \
  tests/services/test_intraday_runner.py \
  tests/unit/test_execution_unknown_settlement.py
```

**Critical remaining gap:** The affected legacy exits still do not
have full canonical order-intent journaling or deterministic automatic
order-book lookup after a lost ACK. An UNKNOWN exit may require
operator-led broker reconciliation before any re-arm or resumed
trading. Treat that as a **blocked recovery state**, not a completed
exit. The remaining Gamma-Unwind, Opening-Volume emergency close and
the other legacy SELL sites still require careful canonical
conversion. Five direct SELL calls remain in the AST baseline.
These changes do **not** establish live-trading readiness.

**Validation:** Latest-commit GitHub Actions, full end-to-end,
browser/mobile and PostgreSQL recovery still require completed
positive evidence; no assumption of PASS is made.
