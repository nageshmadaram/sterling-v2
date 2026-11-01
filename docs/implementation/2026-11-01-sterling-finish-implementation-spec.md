# Sterling V2 finish implementation spec

Date: 2026-11-01
Status: Pre-production NO-GO; implementation finish plan only

## 1. Objective

Sterling V2 has substantial product and platform work already in place: workspace/account policy, admin controls, execution-authority groundwork, app-shell modernization, service lifecycle protections, safe-mode logic, and a structured evidence/story pipeline. The remaining work is not a rewrite of the engine: it is closure of the remaining safety, governance, reconciliation, test, and independent-review gaps so the codebase can be treated as a controlled pre-production implementation rather than a live-trading system.

This document defines the implementation work needed to finish the upgrade and make the repo review-ready for the next stage.

## 2. Current state: completed vs. incomplete

### 2.1 Completed work already represented in the repo

The project has clear evidence for the following items:

1. Account and workspace modernization
   - Shared account navigation and app-shell patterns
   - Workspace-aware routing and selection flows
   - Broker ownership / reassignment and role-bound access controls
   - Masked credential handling and audit events
   - Session provenance and device/browser identity capture

2. Product UI and admin shell hardening
   - Neutral shell and shared controls
   - Management/admin configuration surfaces
   - Mobile and desktop consistency
   - Modal/focus consistency and route protection

3. Execution architecture groundwork
   - Canonical execution concepts separated from legacy flows
   - Execution authority / audit trail concepts documented
   - Durable journal and reconciliation patterns
   - Migration work for legacy execution modernization

4. Safety and lifecycle controls
   - Startup/stop/status orchestration
   - Safe-mode guardrails and status gating
   - Pre-production operator controls and refusal logic
   - Explicit lifecycle expectations for service and broker state

5. Governance and evidence readiness work
   - Control matrix and source-inventory documentation
   - Pre-production review documentation and route ownership tracking
   - Exact-SHA evidence manifests and explicit no-go status when required
   - Read-only compatibility surface and “fail closed” policy enforcement

6. Fail-closed compatibility hardening already landed in engineering follow-ups
   - OpenAlgo compatibility routes are explicitly denied unless reviewed and authorized
   - Read-only order-status endpoints reject contradictory fill/value evidence
   - Evidence manifests preserve the 0/12 accepted-control posture and explicitly refuse live authorization from CI success alone

### 2.2 Remaining blockers

The repository is still not production-certifiable. The remaining work is concentrated in a few domains:

1. Independent verification and sign-off are incomplete
   - No independent production sign-off
   - No live operation acceptance record
   - No external evidence review for deployment readiness

2. Broker routing remains unauthorized
   - Live order routing is explicitly not approved
   - Broker execution and reconciliation are not accepted in a production-like environment
   - Restore/rollback and security drills remain incomplete

3. Execution closure is still incomplete
   - Unknown / uncertain broker outcomes remain a recovery problem
   - Reconciliation must prove journal match, fill truth, and durable projection across strategy state
   - Cross-strategy and cross-process exit/durable projection rules need completion

4. Route governance and authorization need completion
   - Compatibility routes must remain read-only and non-authorizing
   - Public/private route exposure must be proven against JWT/feature grants
   - Planned surfaces must not be mounted or exposed as live capabilities

5. Browser / UI validation still fails in isolated acceptance workflows
   - Workspace switching, admin directory, and account menu states still show contract drift
   - UI acceptance is not yet enough to certify the release decision

6. Broad CI and regression gate are unresolved
   - Product integrity and regression gate workflows remain incomplete or failing
   - CI success is not equivalent to release approval

## 3. Non-goals and release posture

This implementation plan assumes the following posture:

- No live broker order routing is authorized at any point in this phase
- No production deployment authorization is granted by code or CI alone
- All changes are intentionally designed for reviewable pre-production closure only
- The objective is to close the gap to a review-ready branch, not to establish production trading

## 4. Implementation objective

The finish phase should produce a repo state where all of the following are true:

- All live-order and broker-write pathways remain fail-closed and unmounted without explicit authorization
- Recovery / UNKNOWN outcomes are traceable and controlled
- OpenAlgo and compatibility surfaces are read-only and not able to claim broker authority
- Account policy and configuration permission gates are policy-backed and auditable
- Browser E2E stability is restored for workspace/admin flows
- Independent evidence and control sign-off is captured in a documented evidence pack
- The release decision remains explicit: no-go until signed off by independent reviewers

## 5. Detailed workstreams

### Workstream A: Safety closure and governance freeze

Goal: Ensure the system remains in a review-safe state even when future changes are introduced.

Required tasks:

1. Lock the default posture for all broker-write surfaces
   - Route-level gate checks must default to deny
   - Live execution toggles remain off unless an independent review explicitly approves them
   - Any manual override requires structured reason codes and durable audit records

2. Complete structured reason-code enforcement
   - All mutating policy and configuration operations must record a reason code
   - Require uppercase machine codes with validation: e.g. ACCESS_REVIEW, CONFIG_UPDATE, RECONCILE_UNKNOWN
   - Free-form strings must fail validation

3. Finalize evidence manifest generation
   - Exact SHA manifest must capture source hashes and the authoritative control matrix state
   - Must remain independent of CI green status
   - Must include current status: 0 accepted / 12 blocked controls until independent sign-off

Acceptance criteria:

- No mutating configuration action can proceed without a valid machine reason code
- Audit records preserve the actor, target, action, and reason code
- Evidence manifests are generated from the committed state and cannot be repurposed as approval

Primary files/areas:

- backend/app/services/auth/account_policy.py
- backend/app/services/auth/configuration_policy.py
- backend/app/api/v1/endpoints/settings_capabilities.py
- backend/app/api/v1/endpoints/admin_connections.py
- docs/ops/PREPRODUCTION_CONTROL_MATRIX.md
- scripts/validate_preproduction_controls.py

---

### Workstream B: Route authorization and compatibility closure

Goal: Eliminate unintended execution authority through compatibility, legacy, or public routes.

Required tasks:

1. Complete route-by-route authorization matrix
   - Every route must map to a policy class: public / read-only / configuration / kite / administration
   - All legacy or compatibility routes must be examined for implicit authorization inheritance

2. Finish OpenAlgo compatibility boundaries
   - Maintain a strict read-only adapter, never a broker write capability
   - Reject unknown or unexpected HTTP methods and unsupported route payloads
   - Validate response payloads against canonical journal evidence, not raw broker surface assumptions

3. Ensure platform surface registry remains non-authorizing
   - Planned UI routes must not be mounted
   - The registry is metadata-only and cannot create broker clients or writes
   - Route owners must be recorded and the inventory must remain explicit

Acceptance criteria:

- Unknown OpenAlgo routes reject before handler execution
- Compatibility POSTs do not allow write semantics or raw API-key impersonation
- Route inventory is consistent with actual mounted routes and no planned surfaces are exposed

Primary files/areas:

- backend/app/core/feature_access.py
- backend/app/api/v1/endpoints/openalgo_compat.py
- backend/app/services/platform_surface_registry.py
- backend/main.py
- backend/tests/unit/test_openalgo_compat.py
- backend/tests/unit/test_platform_surface_registry.py

---

### Workstream C: Canonical execution and reconciliation closure

Goal: Prove that execution outcomes are derived from authoritative canonical journal evidence before the system treats them as settled.

Required tasks:

1. Close remaining gaps in unresolved-order handling
   - UNKNOWN, SUBMITTING, and ambiguous broker outcomes must remain durable and not become auto-retries or silent success
   - No second sell or second order should be sent while a canonical exit is unresolved

2. Finish canonical reduction / exit-intent integrity
   - Reduction intent must be bound to the exact broker order and canonical journal intent key
   - A missing broker order id or mismatched identity must remain unresolved and trigger recovery
   - Quantities and fill prices must be verified before marking a reduction settled

3. Harden journal projection logic
   - Projection must only be marked once durable strategy ledger updates succeed
   - A broker fill with missing strategy projection remains in projection_pending state
   - Cross-strategy fill observation must not be mistaken for ownership or settlement

Acceptance criteria:

- A broker update that lacks verified identity or quantity is treated as UNKNOWN, not COMPLETE
- Repeated exit attempts while unresolved are blocked
- Projection_pending persists until durable strategy state is confirmed

Primary files/areas:

- backend/app/services/canonical_fill_reconciliation.py
- backend/app/services/canonical_strategy_reduction.py
- backend/app/services/execution_service.py
- backend/app/services/kite_engine/execution_lifecycle.py
- backend/app/services/gamma_move_runner.py
- backend/app/services/intraday_runner.py
- backend/tests/services/test_canonical_fill_reconciliation.py
- backend/tests/engines/sterling_kite_engine/test_execution_lifecycle.py

---

### Workstream D: Recovery, restore, and UNKNOWN-state drills

Goal: Ensure the system can recover from uncertain states without inventing fills or silently rearming protections.

Required tasks:

1. Complete restart and journal recovery tests
   - Restart should preserve canonical order state and not lose journal identity
   - Unknown broker outcomes must be recoverable by operator reconciliation instead of re-submission

2. Finalize disposable PostgreSQL / SQLite migration guardrails
   - Alembic must be limited to disposable targets only
   - Production migration is explicitly forbidden
   - Restore/rollback drill must prove ability to restore an isolated schema without live authority

3. Exercise operator and broker reconciliation flows
   - A missing or ambiguous order must leave the system in recovery state
   - Recovery must require explicit reconciliation and approval

Acceptance criteria:

- No migration or restore step touches live operational DBs
- Recovery state remains visible and operator-actionable
- Uncertain orders never become successful fills without evidence

Primary files/areas:

- backend/app/persistence/alembic_guard.py
- backend/alembic/env.py
- backend/app/services/account_binding_service.py
- scripts/tests/test_sqlite_recovery_preflight.py
- scripts/tests/test_alembic_rollback_preflight.py
- .github/workflows/postgres-migration-drill.yml
- .github/workflows/product-integrity-smoke.yml

---

### Workstream E: Browser and workspace acceptance completion

Goal: Restore stable UI path acceptance and verify that admin/account/workspace behavior matches the contract.

Required tasks:

1. Fix workspace switching and admin directory drift
   - Account menu, workspace selection, and directory behavior must align across desktop/mobile flows
   - Authenticated profile fixtures must be isolated and deterministic

2. Re-enable and stabilize isolated Chromium / E2E checks
   - Ensure workspace switching, account menu, admin directory and session selection pass under browser automation

3. Verify route and state contracts across UI
   - No UI route should imply executable live broker capability without backend gate proof

Acceptance criteria:

- Isolated workspace browser tests pass consistently
- Account menu and workspace flows are stable under real browser execution
- Admin and broker assignment flows remain read-only or explicitly controlled

Primary files/areas:

- frontend/src/pages/__tests__/WorkspaceRouter.test.tsx
- frontend/src/components/admin/AccessEditor.test.tsx
- frontend/src/components/
- docs/implementation/2026-10-27-workspace-flow.md
- docs/implementation/2026-10-28-account-navigation.md

---

### Workstream F: Independent evidence and release-pack completion

Goal: Build a review package that proves the system is safe for a controlled pre-production review, while explicitly refusing production authorization.

Required tasks:

1. Assemble exact engineering evidence pack
   - Source SHA manifest
   - Route inventory and owners
   - Control matrix and status of all 12 controls
   - CI/test evidence and unresolved items
   - Migration and restore drill logs

2. Add an independent reviewer checklist
   - Explicit sign-offs for safety, policy, testing, and release decision
   - Document what remains unresolved and why production authorization remains disabled

3. Ensure release documentation remains explicit about no-go status
   - README, DEPLOYMENT.md and operator docs must remain aligned with the review posture

Acceptance criteria:

- The review pack clearly states current status: pre-production only, no-go
- No document claims deployment readiness without a valid signed review outcome
- Evidence pack is tied to exact commit SHA and available for review

Primary files/areas:

- docs/implementation/OPERATIONAL_EVIDENCE_PACK.md
- docs/implementation/CONTROL_MATRIX.md
- docs/implementation/EXECUTION_ROUTE_MIGRATION.md
- README.md
- DEPLOYMENT.md
- docs/ops/PREPRODUCTION_CONTROL_MATRIX.md

## 6. Detailed implementation sequence

### Phase 1: Freeze and correctness (Week 1)

- Finalize route policy and compatibility closure
- Confirm every broker-write path is explicitly disabled or blocked by policy
- Close reason-code enforcement and audit durability
- Fix remaining non-authorizing contract bugs in openalgo_compat and feature access

Exit gate:

- Route inventory and control matrix align with actual code
- No public or compatibility route can claim write authority
- Audit records are durable and machine-validated

### Phase 2: Execution integrity and reconciliation (Week 2)

- Finish canonical reduction and UNKNOWN-state handling
- Resolve identity mismatches, fill validation, and journal projection handling
- Verify no second SELL is issued while exit is unresolved
- Validate psychically/cross-strategy fill ownership boundaries

Exit gate:

- All unresolved-order states are durable and recoverable
- Projection_pending is not treated as confirmed fill
- Canonical exit tests pass for unknown, refused, and partially confirmed outcomes

### Phase 3: Recovery drills and operational closure (Week 3)

- Run isolated SQLite restore preflight
- Run Alembic disposable guard and schema restore drill
- Validate recovery state, kill-switch behavior, and operator evidence path
- Verify no live DB is touched by migration / restore tooling

Exit gate:

- Migration and restore drills pass in isolated environments with no live authority
- Operational evidence is stored and reviewed

### Phase 4: Browser/UI acceptance (Week 4)

- Fix workspace selection and admin flow drift
- Re-run isolated browser acceptance under deterministic conditions
- Confirm routing and authorization boundaries remain consistent on the client

Exit gate:

- Browser acceptance tests pass or failures are fully triaged and documented as release blockers

### Phase 5: Independent evidence and no-go release pack (Week 5)

- Produce exact SHA evidence pack
- Capture unresolved issues and explicit sign-off requirements
- Lock the release posture: pre-production only, no-go until independent review

Exit gate:

- Release docs and evidence pack match actual state of code and CI
- No claim of production authorization is made

## 7. Definition of done for finishing the upgrade

The branch is not considered finished until all of the following are true:

- Live broker routing remains disabled and is explicitly marked unauthorized
- All compatibility and legacy routes are audited and remain non-authorizing
- Execution journal outcomes are valid and durable under unknown / ambiguous states
- Recovery / restore drills pass in isolated and disposable environments only
- Browser workspace/admin automation is stable and documented
- Complete evidence package has exact SHAs and control matrix status
- Independent review sign-off is recorded or explicitly noted as missing
- Release documents clearly state: no production deployment authorization, pre-production only

## 8. Working rules for implementation

1. Do not claim production readiness from CI green status alone
2. Do not mount or expose planned routes as live features
3. Do not infer live broker execution from a broker adapter or a paper transport mode
4. Treat UNKNOWNs as a state requiring reconciliation, not success
5. Preserve durable audit evidence for every policy or execution mutation
6. Keep all outcomes reviewable and explainable to a separate reviewer

## 9. Release decision

Current repo posture: pre-production, no-go.

This document intentionally defines the work needed to finish the implementation without crossing into authorized live trading. The implementation finish is complete only when the repo can demonstrate a durable, governed, reviewable, and explicitly non-authorized pre-production state with independent evidence sign-off.

## 10. Recommended next actions

1. Use the current implementation branch and close the remaining route and reconciliation gaps
2. Fix the workspace/browser E2E failures and keep them in the CI gate
3. Re-run source inventory and control validation to keep the 0/12 accepted posture intact
4. Produce the evidence-packed review package and route/workflow ownership matrix
5. Freeze release posture: no-go until external sign-off is performed

This is the finishing implementation specification for Sterling V2: finish safety closure, prove reconciliation integrity, close compatibility/auth route gaps, validate the browser contract, and keep the release decision explicitly no-go until independent review approves otherwise.
