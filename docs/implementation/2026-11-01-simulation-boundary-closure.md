# Sterling V2 — simulation-boundary integrity checkpoint

Date: 2026-11-01
Review branch: `upgrade/product-integrity-hardening-20261030` (draft PR #15)
Decision: **PRE-PRODUCTION / NO-GO. No production deployment or broker-write authorization.**

This is an engineering implementation note, **not** a production acceptance document,
a declaration that every remaining route was migrated, or a release certificate.
The required finish-spec on `main` remains
`docs/implementation/2026-11-01-openalgo-upgrade-finish-spec.md`.

## Existing implementation retained

The review branch already contains typed canonical execution requests,
durable order-intent semantics, bounded fill reconciliation, guarded reductions,
the OpenAlgo-style read-only `/compat/openalgo/orderstatus` contract,
non-authorizing platform-surface metadata, role/configuration policies, and
disposable-only migration guardrails. These are design and source observations,
**not** claims of full route coverage or broker-truth acceptance.

The authoritative operational decision remains in
`docs/ops/PREPRODUCTION_CONTROL_MATRIX.md` and its machine-readable artifacts:
**0/12 controls accepted; 12/12 blocked**.

## Code changes in this checkpoint

1. `backend/app/services/execution/order_router.py`
   - A cached `paper:<id>` or `shadow:<id>` is returned exclusively as
     `paper_position_id`, not as an exchange `order_id`. Cache entries must
     match the current simulation mode and have a nonempty simulated identity.
     Unrecognized broker-looking IDs and cross-mode cache entries fail closed
     with `idempotency_identity_conflict`.
   - `PENDING` and `UNKNOWN` cache values remain unapproved unresolved
     submissions; they must not become accepted duplicate acknowledgements.
   - If a paper-position writer raises or returns an unusable identifier,
     the response is `accepted=False`, `status=unknown`,
     `code=paper_record_unknown`. The cache retains `UNKNOWN` to prevent
     immediate blind recreation.
   - If a shadow-position writer raises or returns an unusable identifier,
     the response is `accepted=False`, `status=unknown`,
     `code=shadow_record_failed`. The same short-lived in-process
     `UNKNOWN` guard prevents immediate retry.
   - Failure details are **not** reflected back to API consumers or logged
     verbatim; an exception may contain secrets. The caller gets a stable
     operator-facing explanation instead.
   - No new broker transport calls or live order permissions were introduced.

2. `backend/app/services/execution_service.py`
   - The canonical post-send boundary no longer applies `str(...)` to an
     absent broker identity and treats `"None"` as a real order ID.
   - `_validated_broker_order_id` rejects null, booleans, zero, blank,
     UNKNOWN/PENDING/SUBMITTING/RESERVED, and simulated/idempotency-prefixed
     identifiers before invoking `order_journal.acknowledge`.
   - The existing canonical exception handling retains the journal intent
     as UNKNOWN and fences the account for recovery rather than resubmitting
     a broker request. No production execution permission was introduced.

3. `backend/tests/test_order_router.py`
   - Regression coverage for simulated duplicate identity separation,
     unknown write outcomes, secret-free responses, missing simulated IDs,
     and rejection of a paper cache entry under shadow mode.
   - Existing simulated duplicate expectation is updated to the distinct
     `paper_position_id` response field.

4. `backend/tests/unit/test_execution_unknown_settlement.py`
   - Eight synthetic invalid broker ACK shapes must remain UNKNOWN with an
     account recovery fence and no second send on replay.

## Reviewed route boundary (not a complete repository-wide inventory)

| Route/surface | Observed source contract | This checkpoint |
|---|---|---|
| Legacy `OrderRouter.submit` LIVE | Legacy request cannot establish canonical account/generation authority | Existing refusal retained |
| Legacy `OrderRouter.submit` PAPER/SHADOW | Virtual positions; no exchange order submission | Incorrect ACK/UNKNOWN signaling hardened |
| `OrderRouter.submit_canonical` | Requires typed canonical execution request and delegates to canonical execution service | Canonical broker ACK identity check hardened; full live acceptance outstanding |
| `BrokerAgent.place_order` | Typed canonical request only; raw payload rejected | Existing guard retained |
| `BrokerAgent.cancel_order` | Refuses noncanonical broker write to live/unknown transport | Existing guard retained |
| `/api/v1/compat/openalgo/orderstatus` | JWT + feature grant; journal-scoped read only | Existing contract retained |
| `/api/v1/platform/surfaces` | Inventory metadata, not route mounts or execution grants | Existing contract retained |

## Continued checkpoint: quote integrity and browser contract migration

After the original broker-ACK patch, the existing paper/shadow router was
verified to accept `0.0` as a virtual execution price when the adapter was
absent or a quote read failed. Both virtual writer paths now refuse a simulated
fill before persistence unless the input quote is **finite and positive**.
A definitive pre-write refusal does not insert an idempotency marker; a new
request may be tried after valid data becomes available. The test suite now
covers zero, negative, NaN and infinite synthetic quote inputs, and the agent
reference-flow test supplies a labeled synthetic market quote.

The latest review also inspected exact-commit Playwright E2E job logs for
`062f47b59b22dcf805483a001425291a2a2c9b23`:
Chromium reported **69 failing / 5 passing** scenarios (with retries), and
the Firefox and WebKit jobs also failed. These are **not accepted** runs.

Identified independent browser blockers:

- Strict mocked workspaces did not recognize the current authenticated
  `/api/v1/auth/profile` read; fixtures now give identity-consistent reads
  and continue to refuse unspecified endpoints.
- Directory-based administration calls `/api/v1/admin/directory`, while
  the old browser fixture expected a removed manage-user dropdown. The
  administration-isolation contract now uses the current directory read model
  and preserves the zero-Kite-network assertion.
- The root workspace sends an administrator to administration; existing
  isolated risk/operations tests incorrectly expected the Kite Tools button
  at `/`. They now navigate explicitly to the granted `/kite` workspace.
- Mobile E2E scripts called Playwright `.tap()` without enabling browser
  touch. Those tests now set `hasTouch` without forcing Firefox-unsupported
  `isMobile` semantics.
- The `frontend/e2e/media/spotify.spec.ts` tests request
  `/radio/index.html` and `/tv/index.html`. These entry documents are absent
  in the current V2 `frontend/public/radio` and `frontend/public/tv`
  directories. Their browser failures are **not** resolved by an auth mock.
  Restore actual authorized media artifacts and test them, or explicitly
  separate an approved legacy-media suite; do not manufacture green results.
- Replay browser-to-backend scenarios received `403 Feature access is not
  enabled` for the default principal. This is a secure denial, not
  permission to weaken backend policy; controlled test grants or adjusted
  negative contracts are still needed.
- EMA Trend signal profile tests contain stale mode/selector expectations.
  These require fixture-to-current-UI reconciliation before acceptance.

Browser fixture edits **are not** claims of passing browser tests. The
latest branch additionally needs its own completed Playwright and Python
CI results. Do not mark the repo regression-clean until those are reviewed.

## Focused verification

Targeted local command (execute with the repository's pinned dependencies):

```bash
cd backend
python -m compileall -q app/services/execution/order_router.py app/services/execution_service.py
python -m pytest -q tests/test_order_router.py tests/test_agents.py tests/unit/test_execution_unknown_settlement.py --tb=short
```

The existing `.github/workflows/product-integrity-smoke.yml` includes
`test_order_router.py`, `test_agents.py`, and backend compile checks.
The repository's main CI also runs the broader backend suite, including the
new canonical ACK regressions. **Neither local pytest nor a complete exact-head CI pass
has been observed by this checkpoint.** A queued or running Actions job is
not acceptance. Follow the current PR head SHA, not a historical green run.

## Explicit limitations and unresolved NO-GO requirements

- The in-memory legacy idempotency cache has a TTL; it is not a durable
  restart-recovery guarantee. **Canonical journal persistence is still
  required for any broker-affecting submission.**
- The legacy paper/shadow router now refuses virtual fills without valid
  positive market quotes, but this does not certify paper P&L, execution
  economics, or equivalence to live broker fills.
- The current review branch's
  `tests/unit/test_canonical_execution_convergence.py` asserts the five
  formerly direct protective SELL call sites now invoke `submit_reduction`
  and finds no grandfathered direct ordinary broker sends. This static code
  inventory is not a substitute for runtime restart, protection/stop ownership,
  partial-fill and broker-truth recovery acceptance. The historical PR
  description still contains older seven-call inventory figures; use the
  current source and exact-head tests, not that stale count.
- Full source/HTTP route inventory, frontend role parity, broker/account/session
  lifecycle verification, operational PostgreSQL migration and off-host restore,
  physical device checks, and independent broker-truth reconciliation remain
  incomplete.
- Each H01–G12 evidence gate needs source-SHA-bound results, named owners and
  independent review. Nothing in this checkpoint supplies those approvals.

**Release decision: NO-GO.** Do not merge or deploy based on this checkpoint
alone. Do not enable live execution, connect a broker for acceptance, submit
real orders, treat CI as human sign-off, or edit acceptance counts. Keep PR #15
draft until the full documented closure and exact-head checks have independent
review.
