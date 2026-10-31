# Sterling upgrade finish implementation spec

Date: 2026-10-31

## 1. Objective

This document captures the current state of the Sterling V2 upgrade, compares what is already complete against what is still missing, and defines the implementation plan required to finish the upgrade without claiming production readiness.

The repo already demonstrates substantial product-level progress: a modern account/workspace model, governance-oriented operational docs, stronger service lifecycle controls, and a migration path toward canonical execution. However, the project is not finish-complete because live broker execution, production evidence, operational controls, and deployable recovery proof remain incomplete.

The goal of this phase is to complete the upgrade from a product-mature state into a governed, reviewable, operationally defensible implementation that is clearly marked as pre-production until external sign-off is obtained.

## 2. What is completed vs. what is still missing

### 2.1 Completed work already visible in the repo

The following items are materially implemented or strongly evidenced in the codebase and docs:

1. Account and workspace modernization
   - shared account navigation and header behavior
   - workspace-aware app shell flow
   - broker ownership and reassignment patterns
   - admin user directory and broker assignment flows
   - masked credential exposure and audit surfaces
   - session provenance / device/browser telemetry capture

   Evidence in repo:
   - `docs/implementation/2026-10-27-workspace-selection.md`
   - `docs/implementation/2026-10-27-workspace-flow.md`
   - `docs/implementation/2026-10-27-account-policy-foundation.md`
   - `docs/implementation/2026-10-28-account-navigation.md`
   - `backend/app/services/account_binding_service.py`

2. Product UI and shell redesign
   - neutral app shell and shared controls
   - management/admin configuration surfaces
   - mobile and desktop consistency updates
   - modal and focus handling consistency
   - runtime verification and frontend test coverage artifacts

   Evidence:
   - `frontend/src/components/SterlingLogo.tsx`
   - `docs/implementation/2026-10-28-product-ui-audit.md`
   - `docs/implementation/2026-10-27-management-redesign.md`
   - `docs/implementation/2026-10-27-configuration-access.md`

3. Execution architecture migration groundwork
   - canonical execution concepts documented and separated from legacy flows
   - durable execution governance concepts described
   - migration scripts and parity analysis for engine modernization
   - execution authority / audit trail concepts defined

   Evidence:
   - `docs/architecture/EXECUTION_AUTHORITY.md`
   - `backend/scripts/migrate_scalping_to_sterling_engine.py`
   - `backend/app/engines/opening_volume_parity.py`
   - `docs/implementation/2026-10-30-sterling-next-upgrade-spec.md`

4. Safety and lifecycle controls
   - startup and stop scripts for service sequencing
   - safe-mode gating and status orchestration
   - production-facing operator controls and refusal logic
   - explicit operational lifecycle expectations for service and broker-related state

   Evidence:
   - `scripts/sterling-start`
   - `scripts/sterling-stop`
   - `scripts/sterling-status`
   - `backend/app/services/service_lifecycle.py`
   - `backend/app/services/safe_mode.py`
   - `docs/ops/STERLING_OPERATOR_HANDOFF.md`

5. Data and operational maturity work
   - offline market-data lake / storage model discussions
   - operator handoff and production-run documents
   - deployment and strategy operational guidance
   - documentation and readiness artifacts for operators and engineers

   Evidence:
   - `docs/operator/`
   - `docs/strategy/`
   - `docs/ops/`
   - `docs/design/`
   - `kitelake/README.md`

### 2.2 Remaining gaps that block finishing the upgrade

The repo also clearly states that the upgrade is not production-certified. The main blockers are operational, not visual.

These are the key items that still must be completed before the upgrade can be finished as a review-ready implementation:

1. Production evidence and sign-off are incomplete
   - no independent production sign-off
   - no accepted evidence pack for live deployment
   - no final production controls acceptance record

2. Broker order routing remains unauthorized
   - live order routing is explicitly not approved
   - order execution and reconciliation are not validated in a production-like environment
   - broker stop/position reconciliation remains incomplete

3. Data integrity and recovery proof is incomplete
   - database migration and restore path still need formal verification
   - backup and rollback evidence are not complete
   - audit trail completeness is not proven across all execution flows

4. Execution migration is still only partial
   - several paths still rely on legacy behavior or migration scaffolding
   - canonical execution wrappers must be enforced across all route owners
   - parity checks are not complete for all strategy and engine branches

5. Policy and access model still needs hard consolidation
   - account, workspace, broker, session, and permissions must have a single explicit source of truth
   - stale or hidden authorization states must be removed
   - admin actions must be fully auditable and reviewable

6. Operational readiness package is not complete
   - no final control matrix with owners and evidence links
   - no final deploy review package
   - no independent approval gating for production changes

### 2.3 High-level status assessment

| Area | Status | Assessment |
|---|---|---|
| Account/workspace UX | Mostly complete | Product architecture is advanced and coherent |
| UI/shell consistency | Mostly complete | Features are implemented and documented |
| Execution architecture | Partially complete | Migration is underway but not completed across all routes |
| Safety / lifecycle controls | Mostly complete | Strong base layer exists |
| Broker acceptance / live routing | Not complete | Explicitly blocked |
| Production evidence | Not complete | Missing approval and proof artifacts |
| Database / restore / audit validation | Not complete | Needs operational verification |
| Policy and access hardening | In progress | Must be consolidated and enforced |
| Governance package | Not complete | Need a final sign-off package |

## 3. Target end state

The upgrade is considered finished when Sterling reaches the following target state:

- a single canonical execution path is used for all real order flows
- account/workspace/admin/policy state is explicit, consistent, and auditable
- the system can be reviewed without claiming live production status
- every production control has an owner, evidence requirement, and status
- all operational safety checks are proven and reversible
- live brokerage routing remains explicitly blocked until external sign-off is obtained

This is not a “launch” target. It is a “governed, reviewable, deployable pre-production upgrade” target.

## 4. Implementation scope to finish the upgrade

### Workstream A — Complete canonical execution migration

#### Goal

Make the execution path explainable, recoverable, auditable, and not bypassable by legacy routes.

#### Required implementation

1. Inventory all execution routes
   - enumerate every direct broker call and order-submission path
   - map each route to engine, strategy, owner, and risk gate
   - create a route matrix with current and target behavior

2. Introduce canonical execution service contract
   - `submit_order()`
   - `cancel_order()`
   - `reconcile_order()`
   - `record_execution_outcome()`
   - `require_execution_authority()`
   - include correlation ID, account ID, session ID, strategy ID, broker ID, and route metadata

3. Remove legacy bypasses
   - block direct route execution except for approved exceptions
   - make bypass attempts fail closed
   - emit alerts when an old route is called

4. Add idempotence and lifecycle integrity
   - every order lifecycle must be replayable
   - duplicate submissions should be detected
   - reconcile against broker state before marking order as final

5. Add parity testing
   - compare old and canonical behavior on deterministic test data
   - verify lot size, risk, stop logic, and exit semantics remain unchanged
   - validate order journals and reconciliation behavior after restart

#### Files and areas likely involved

- `backend/app/engines/`
- `backend/app/services/`
- `backend/app/core/`
- `backend/scripts/`
- `docs/architecture/EXECUTION_AUTHORITY.md`
- `docs/implementation/`

#### Exit criteria

- all real order routes go through canonical execution service
- every order has a durable audit record
- legacy execution paths are either gone or explicitly blocked
- route ownership and rollback paths are defined

---

### Workstream B — Consolidate account, workspace, broker, and policy model

#### Goal

Create a single explicit source of truth for account, workspace, session, broker, and authorization state.

#### Required implementation

1. Define canonical policy object
   - account
   - workspace
   - session
   - broker assignment
   - user roles / operator rights
   - feature grants / route visibility

2. Eliminate hidden state and stale reads
   - no route should discover user access from stale profile state
   - account/workspace selection and broker ownership must be consistent
   - admin screens and backend rules must use the same policy object

3. Add audit records for admin writes
   - actor identity
   - timestamp
   - before/after snapshot for critical changes
   - reason code / approval marker where appropriate

4. Validate migration
   - migrate legacy account binding data in a controlled way
   - preserve traceability to old broker identities
   - detect mismatches and refuse silent privilege escalation

#### Files and areas likely involved

- `backend/app/services/account_binding_service.py`
- `backend/app/core/`
- `backend/app/api/`
- `frontend/src/`
- `docs/implementation/2026-10-27-account-policy-foundation.md`

#### Exit criteria

- no stale hidden authorization states remain in the app
- account/workspace/broker policy is explicit and auditable
- admin changes are reviewable and reversible

---

### Workstream C — Harden route and shell permissions

#### Goal

Ensure the UI reflects the same access rules as the backend, in all states.

#### Required implementation

1. Enumerate all route groups
   - admin
   - settings
   - strategy
   - broker/account
   - ops/monitoring
   - shared shell areas

2. Attach policy to route visibility
   - route access must be computed from the canonical policy model
   - no route may appear available based on stale UI state

3. Standardize shell behavior
   - titles
   - modal semantics
   - focus behavior
   - toasts and status text
   - shared error patterns

4. Add route-level tests
   - workspace restriction tests
   - admin access tests
   - broker assignment tests
   - feature gating tests

#### Files and areas likely involved

- `frontend/src/`
- `frontend/src/routes/`
- `frontend/src/components/`
- `backend/app/api/`

#### Exit criteria

- route visibility matches backend authorization
- no feature appears enabled without backing permission
- app shell behavior is consistent across desktop/mobile situations

---

### Workstream D — Prove operational safety, recovery, and observability

#### Goal

Show that the system behaves correctly after failure, refresh, and interruption.

#### Required implementation

1. Database migration verification
   - check schema changes are backward-compatible
   - confirm idempotence for migration runs
   - verify restore paths from backup state

2. Event and audit traceability
   - every critical action includes correlation ID and causal metadata
   - record account, broker, session, strategy, route, and action outcome

3. Recovery tests
   - restart during order lifecycle
   - stale session handling
   - failed reconciliation
   - reconnect and recovery scenarios

4. Observability guardrails
   - no secrets in logs
   - structured error taxonomy
   - alerts on failed execution, policy failure, reconciliation drift

#### Files and areas likely involved

- `backend/app/services/`
- `backend/app/core/`
- `scripts/`
- `docs/ops/`
- `docs/audits/`

#### Exit criteria

- the system can explain what happened after failure
- restoration is traceable to a valid backup state
- no production action proceeds without proof of supporting state

---

### Workstream E — Create production control matrix and review package

#### Goal

Replace ad hoc product documentation with an approval-ready evidence set.

#### Required implementation

Create a control matrix with at least the following controls:

- H01 — infrastructure / security / platform / network
- T02 — network / security
- S03 — secrets management
- I04 — identity / security
- D05 — database / security
- R06 — database / operations
- B07 — broker integration / compliance
- M08 — market data / operations
- A09 — operations / monitoring
- L10 — engineering / security
- F11 — QA / security / operations
- G12 — independent governance / release

For each control, maintain:

- issue description
- owner
- evidence required
- current status
- next action
- approval gate

#### Deliverables

- `docs/implementation/CONTROL_MATRIX.md` or equivalent
- evidence registry
- sign-off checklist with reviewer names and roles
- exception / risk register

#### Exit criteria

- every control has an owner and evidence requirement
- no status is marked accepted without proof
- the repo clearly separates product progress from approved production status

## 5. Required sequence of execution

### Phase 1 — Traceability and feature inventory

Duration: 1–2 weeks

Tasks:
- inventory all existing completed product features
- map each feature to source files and validation artifacts
- identify all legacy routes and direct broker call sites
- assign feature owners

Exit criteria:
- one traceability map from feature -> code -> evidence

### Phase 2 — Canonical execution migration

Duration: 2–4 weeks

Tasks:
- enumerate all order routes
- implement canonical wrappers and route gating
- validate idempotence and reconciliation logic
- migrate engines incrementally with parity checks between steps

Exit criteria:
- no real execution path bypasses canonical service

### Phase 3 — Authorization and policy hardening

Duration: 1–2 weeks

Tasks:
- unify account/workspace/broker policy
- remove stale authorization states
- add admin audit mechanisms
- add route-level tests and access enforcement

Exit criteria:
- no hidden or stale policy state remains

### Phase 4 — Operational proof and evidence package

Duration: 1–2 weeks

Tasks:
- complete database / restore validation
- complete recovery and observability proof
- create control matrix and evidence registry
- prepare review package for independent sign-off

Exit criteria:
- project is review-ready without claiming production status

## 6. Explicit non-goals

This phase must not:

- claim production readiness
- authorize live trading
- authorize live broker execution
- bypass independent approval
- merge unreviewed production config or secrets changes
- treat product completion as operational proof

## 7. Definition of done for the upgrade finish slice

The finish slice is complete only when all of the following are true:

- all real execution routes are mapped to the canonical execution service
- account/workspace/broker/policy data has a single explicit source of truth
- admin and broker flows are aligned with backend authorization and audit rules
- all critical database and recovery behaviors are validated
- a production control matrix exists with owners and evidence requirements
- deployment review artifacts are ready for independent sign-off
- live broker routing remains explicitly blocked until reviewed and approved

## 8. Files to create or update

Recommended final artifact set:

- `docs/implementation/2026-10-31-sterling-finish-spec.md` — main implementation plan
- `docs/implementation/CONTROL_MATRIX.md` — control registry and ownership
- `docs/implementation/EXECUTION_ROUTE_MIGRATION.md` — route inventory and migration checklist
- `docs/implementation/OPERATIONAL_EVIDENCE_PACK.md` — proof package checklist

## 9. Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Hidden legacy execution route | Silent behavioral drift | Map all routes and fail closed | 
| Stale auth state | Access mismatch | Use one canonical policy object |
| Database migration issue | Data loss or inconsistency | Validate restore and rollback tests |
| Premature production claim | Operational and compliance exposure | Keep NO-GO gates active until sign-off |
| Unreviewed broker route | Live execution risk | Keep broker routing blocked until approval |

## 10. Recommendation

The correct next step is not to “publish” the upgrade. The correct next step is to finish the governance and migration work in a controlled sequence:

1. complete route inventory and canonical execution migration plan
2. lock policy and route visibility under a single source of truth
3. prove operational recovery and observability
4. produce the control matrix and evidence pack
5. leave production status blocked until independent sign-off is satisfied

This keeps velocity high while preserving the discipline required to make Sterling operationally credible.

## 11. Final status summary

Sterling is already significantly advanced in product design and system architecture. It has crossed the threshold from prototyping into a serious platform effort, with clear workspace/account UX, lifecycle controls, and migration planning. However, the upgrade is not yet finished because the implementation still lacks the final proof package, live route authorization, and operational governance evidence required for a certified deployment.

The remaining work is therefore not cosmetic; it is the hard engineering and control work required to make Sterling reviewable, recoverable, and operationally defensible without claiming production readiness.

---

Document path: `docs/implementation/2026-10-31-sterling-finish-spec.md`
