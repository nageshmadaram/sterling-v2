# Sterling upgrade implementation spec — 2026-10-30

## Executive summary

Sterling has already moved well beyond a plain legacy broker dashboard into a structured product and platform effort. The repo now contains clear evidence of:

- shared account/workspace UX and admin flows
- a modernized shell and route model
- migration-oriented execution docs and risk-control frameworks
- a serious backend service architecture effort around durable execution and policy
- an offline market-data lake and operator-facing operational docs

However, the repository still clearly documents that the project is not yet production-certified. The current implementation is product-advanced but operationally incomplete. The next phase should not aim for live production launch. It should aim to convert the current implementation into a disciplined, auditable, evidence-backed upgrade slice that is ready for independent review and controlled rollout.

This document is the implementation specification for the next slice.

## 1. Comparison: completed vs. not completed

### 1.1 Completed work already visible in the repo

The following areas are materially complete or substantially implemented from a product and architecture perspective:

1. Account and workspace modernization
   - Shared account navigation and header behavior
   - Workspace-aware shell flow
   - Broker ownership and reassignment patterns
   - Admin-user catalog and broker assignment flow
   - Masked credential handling and audit surfaces
   - Session provenance and browser/device telemetry capture

   Evidence:
   - `docs/design/2026-10-27-sterling-account-and-experience-specification.md`
   - `backend/app/services/account_binding_service.py`

2. Product UI and shell redesign
   - Shared navigation and modal behavior
   - Updated administration/configuration surfaces
   - Mobile/desktop shell consistency
   - Runtime verification artifacts and front-end test coverage

   Evidence:
   - `frontend/src/components/journal/StrategyComparison.tsx`
   - `README.md`
   - `docs/implementation/`

3. Execution architecture migration groundwork
   - Canonical execution concepts are explicitly defined
   - Durable execution controls and migration roadmap are documented
   - Legacy engine config migration scripts exist
   - Comparison tools for observable parity are present

   Evidence:
   - `docs/architecture/EXECUTION_AUTHORITY.md`
   - `backend/scripts/migrate_scalping_to_sterling_engine.py`
   - `backend/app/engines/opening_volume_parity.py`

4. Data and operational maturity work
   - Offline Kite data lake and resume-safe storage model
   - Operational docs for risk, strategy, deployment, security and execution
   - Production hardening and operator notes are present in docs

   Evidence:
   - `kitelake/README.md`
   - `docs/operator/`
   - `docs/strategy/README.md`
   - `STRATEGIES.md`, `RISK_MANAGEMENT.md`, `EXECUTION.md`, `SECURITY.md`

### 1.2 What is still incomplete or blocked

The repo still states that production certification remains blocked. The main blockers are not UI features; they are evidence and operational controls.

Current blockers include:

- production evidence remains blocked
- independent security / compliance sign-off is not provided
- broker order routing is not authorized
- market data reliability and broker acceptance evidence are not complete
- database safety, backup/restore, audit evidence are not complete
- operator access controls are not proven in a real environment
- release governance is still separate from implementation work
- the execution migration remains partial across engines and strategy routes

The most important technical blocker is that the migration to a canonical execution pipeline is still partially in progress. The repo explicitly calls out that several engines are still routed via older manual flows instead of the canonical pipeline.

Evidence:
- `docs/architecture/EXECUTION_AUTHORITY.md`
- `docs/implementation/2026-10-29-next-upgrade-spec.md`
- `docs/design/2026-10-27-sterling-account-and-experience-specification.md`

### 1.3 Summary comparison

| Area | Status | Assessment |
|---|---|---|
| Account/workspace/admin UX | Mostly complete | Strong product implementation already exists |
| Shell/UI consistency | Mostly complete | Good product state, but needs hard enforcement and edge-case QA |
| Execution engine modernization | Partially complete | Canonical path exists, several routes still legacy |
| Production evidence | Not complete | No independent sign-off or production approval |
| Security / secrets / access validation | Not complete | Must be proven with evidence |
| Database / backup / recovery | Not complete | Needs formal operational verification |
| Broker acceptance / live routing | Not complete | Explicitly blocked |
| Release governance | Not complete | Needs a single reviewable evidence package |

## 2. Architectural comparison against the target state

The target architecture is not simply “more UI polish.” The repo strongly implies a target state with:

- one explicit admin/operator model
- one workspace/account policy model
- one canonical execution pipeline
- one durable broker operation and audit chain
- one production evidence package with sign-off gates

The current implementation has reached the product stage of that architecture, but not the operational governance stage.

### 2.1 Product layer

Already present:
- account shell
- workspace selection
- config and admin flows
- account-binding and policy-aware access patterns

### 2.2 Service layer

Partially present:
- execution authority design
- migration scaffolding
- configurable execution service boundaries
- risk/execution contracts are separated in docs and patterns

Still missing or incomplete:
- complete route conversion from legacy `place_order` / direct broker calls to canonical service calls
- full runtime control over all engines and strategy flows
- explicit lifecycle and recovery checks across all execution routes

### 2.3 Operational layer

Still missing:
- production evidence pack
- independent governance checklist
- real environment validation
- no-go boundaries and rollback procedures

## 3. Next implementation objective

The next slice should not attempt to certify live execution or production deployment. It should aim to complete the upgrade from product-mature to operationally governed.

The priority objective is:

“Close the implementation gap between the current Sterling product UX and a governed, traceable, deployable upgrade slice without claiming production readiness.”

## 4. Detailed implementation plan for the next slice

### Workstream A — Canonical execution migration

#### Goal

Move all production-relevant order flows onto the canonical execution service and make the execution path explainable, recoverable, and auditable.

#### Scope

- identify every direct manual order route still bypassing the canonical service
- map each route to an execution authority / broker contract / audit contract
- convert routes in a controlled sequence without changing risk semantics
- preserve existing strategy behavior while moving execution ownership

#### In-scope engines / routes

The repo already identifies the target sequence:

1. Sterling canonical execution engine
2. manual Kite orders API
3. opening volume leaders
4. intraday runner
5. gamma move
6. adaptive edge

#### Required actions

1. Build a migration inventory
   - enumerate direct broker calls and order submission paths
   - tag them by strategy/engine/owner
   - record current risk and safety gates

2. Introduce canonical execution wrappers
   - `submit_order(...)`
   - `cancel_order(...)`
   - `reconcile_order(...)`
   - `record_execution_outcome(...)`

3. Add route-level migration guards
   - all old routes must fail closed unless explicitly approved
   - enforce auditing and idempotence on order lifecycle
   - require correlation IDs on every execution event

4. Verify parity
   - compare old and new behavior on deterministic test data
   - confirm no silent changes in lot sizing, exits, or risk checks
   - validate order journal replay and reconciliation behavior

#### Deliverables

- migration matrix of every execution route
- canonical execution API contract
- route-by-route approval list
- parity and audit test results

#### Acceptance criteria

- no strategy route submits real orders except through the canonical path
- every order has a durable audit record
- each engine has an owner and rollback path
- old manual routes are not silently used in production

---

### Workstream B — Policy, account, and admin model consolidation

#### Goal

Unify the app into one explicit ownership, policy, and access model.

#### Scope

- single canonical source of truth for account, workspace, session and admin state
- explicit broker assignment, credential access, and workspace visibility rules
- consistent account binding and route-level authorization

#### Required actions

1. Define a single policy object
   - account
   - workspace
   - broker
   - session
   - user rights
   - feature grants

2. Remove duplication and hidden authorization states
   - no route reads stale profile state
   - no hidden broker assignment states
   - no mismatched credential visibility between UI and backend

3. Add auditability to admin writes
   - record actor identity, timestamp, metadata
   - store before/after state for key profile changes
   - ensure admin actions are reversible and inspectable

4. Validate migration of existing accounts
   - one-time migration from legacy assignment data
   - records preserved and linked to old broker identities
   - no silent access escalation

#### Deliverables

- policy model spec
- data migration plan
- audit log/traceability spec
- migration validation checklist

#### Acceptance criteria

- one source of truth for policy and membership
- no hidden or stale authorization states
- all admin changes are logged and reviewable
- existing broker account assignments remain valid and explicit

---

### Workstream C — Shell and route consistency hardening

#### Goal

Lock down the universal app shell and ensure high-trust routes behave consistently.

#### Scope

- unify routes, visible nav, and modal semantics
- standardize route availability by policy
- ensure desktop/mobile layouts share a consistent contract
- keep admin/configuration screens on same model as core trading surfaces

#### Required actions

1. Enumerate all routes and route groups
   - admin
   - settings
   - strategy
   - broker/account
   - ops/monitoring

2. Enforce route visibility by policy
   - no hidden feature exposure through stale UI state
   - route access matches backend authorization

3. Normalize shell behavior
   - consistent titles, controls, toasts, focus management, and modal semantics
   - consistent mobile and desktop interactions

4. Add route-level runtime tests
   - login & workspace behavior
   - admin access restrictions
   - broker assignment flows
   - feature gating

#### Deliverables

- route policy contract
- access matrix
- UI consistency checklist
- automated route tests

#### Acceptance criteria

- no policy mismatch between UI and backend
- every key route is traceable to policy rules
- all user states render consistently across screens

---

### Workstream D — Data integrity, observability, and recovery

#### Goal

Prove that the system is operationally consistent under failure, refresh, and restart conditions.

#### Scope

- durable database safety and migration controls
- replayable execution events
- observability and alerting around failed actions
- backup/restore and rollback evidence

#### Required actions

1. Database migration and restoration verification
   - verify all schema changes are backward-compatible
   - prove migration is idempotent and reversible
   - test restore path from backups

2. Event and audit traceability
   - every order action has event ID and causality chain
   - include session, account, broker, strategy, and route metadata

3. Resilience tests
   - start/restart mid-operation behavior
   - stale session handling
   - failed order reconciliation
   - reconnect and recovery tests

4. Observability guardrails
   - no secrets in logs
   - structured error taxonomy
   - alerting for failed execution, policy failure, or reconciliation drift

#### Deliverables

- database safety checklist
- recovery runbook
- observability register
- evidence package for operations

#### Acceptance criteria

- the system explains what happened after a failure
- restored systems are traceable to valid backup state
- no production action can proceed without supporting evidence

---

### Workstream E — Production control mapping and governance evidence

#### Goal

Turn implementation progress into an approval-ready artifact set rather than leaving evidence as ad hoc product documentation.

#### Scope

Map each product change to concrete production controls.

#### Required controls

At minimum:

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

#### Required actions

1. Create a control matrix
   - issue
   - owner
   - evidence required
   - current status
   - next action

2. Add evidence artifacts
   - security validation logs
   - deployment configuration review
   - restore test results
   - broker integration acceptance evidence
   - QA runbook and sign-off records

3. Require independent approval for any production status change
   - move to approval only after evidence is attached
   - keep NO-GO status until sign-off is complete

#### Deliverables

- control matrix
- evidence registry
- sign-off checklist
- exception and risk register

#### Acceptance criteria

- every control has an owner and evidence requirement
- no control status is marked accepted without proof
- the repository clearly distinguishes product progress from approved production readiness

## 5. Implementation sequencing

### Phase 1 — Product stabilization and traceability

Duration: 1–2 weeks

Goals:
- finalize implementation inventory
- reconcile the current product and docs state
- map features to source files and evidence files

Tasks:
- inventory completed product features
- confirm each feature has a primary code owner
- align docs with actual implementation state
- identify incomplete code paths and their owners

Exit criteria:
- single traceability map from feature -> code -> validation artifact

### Phase 2 — Canonical execution migration

Duration: 2–4 weeks

Goals:
- convert all real order paths to canonical execution service
- prove parity and idempotence

Tasks:
- enumerate all order submission paths
- implement canonical wrappers and route gating
- add order-journal and reconciliation tests
- migrate engines in sequence with validation between each step

Exit criteria:
- no production execution path bypasses the canonical service

### Phase 3 — Policy and access hardening

Duration: 1–2 weeks

Goals:
- unify account/workspace/admin policy model
- remove stale access states

Tasks:
- consolidate account and session policy logic
- validate admin flows and credential handling
- add route-level authorization checks

Exit criteria:
- single policy model; no stale or hidden authorization states

### Phase 4 — Evidence and governance package

Duration: 1–2 weeks

Goals:
- prepare independent review package
- establish no-go / go gates with evidence

Tasks:
- map all controls to evidence artifacts
- complete backup/recovery and security checklists
- prepare governance package for review

Exit criteria:
- project is review-ready without claiming production status

## 6. Risks and explicit non-goals

### Risks

- route migration may accidentally change strategy semantics
- hidden legacy paths may still be live and untracked
- policy migration may create temporary access outages
- database migrations may not be fully reversible without evidence
- production controls may be claimed prematurely without proof

### Explicit non-goals

This next phase must not:

- claim production readiness
- authorize live trading
- authorize production broker routing
- bypass independent sign-off
- merge unreviewed production config or secrets changes
- treat product completion as evidence of operational readiness

## 7. Definition of done for the next slice

The next slice is done only when all of the following are true:

- every real execution route is mapped to the canonical execution service
- account, workspace, and policy state have a single source of truth
- admin and broker flows are auditable and reversible
- the repository contains a clear control matrix with owners and evidence requirements
- the system can be reviewed without claiming live production status
- no-go gates remain active until evidence is approved

## 8. Concrete recommendation

The correct next action is not “ship the upgrade.” The correct next action is:

1. finish the execution migration inventory and route conversion plan
2. lock the account/workspace policy model behind explicit access checks
3. build the production-control matrix and evidence registry
4. keep all live broker routing and production deployment explicitly blocked until external approval

This keeps the implementation moving while preserving the necessary operational discipline.

## 9. Final status summary

Sterling is meaningfully advanced in product design and platform structure. The project has crossed the line from prototyping into a serious product architecture effort. But the repository itself still makes clear that the system is not yet in a production-certified or broker-authorized state.

The correct next phase is therefore a controlled upgrade toward evidence-backed operational maturity, not a launch phase.

This document should be treated as the implementation plan for the next engineering slice.

---

Document path: `docs/implementation/2026-10-30-sterling-next-upgrade-spec.md`
