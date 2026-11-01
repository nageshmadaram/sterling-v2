# Sterling OpenAlgo upgrade finish implementation spec

Date: 2026-11-01

## 1. Objective

This document captures the current state of the Sterling V2 upgrade work as of 2026-11-01, compares what is already implemented against what still needs to be finished, and defines the implementation plan required to close the remaining gap.

The repository already contains substantial work: a hardened role-and-policy model, a read-only OpenAlgo compatibility layer, stronger canonical execution guards, and production-staged operational gates that explicitly keep live brokerage routing in a pre-production NO-GO state. The remaining work is not cosmetic. It is the engineering closure work required to make the upgrade reviewable, operationally defensible, and safe to hand off without claiming live production authorization.

This is a finish-spec, not a launch-spec. The target is a governed, audited, pre-production completion that remains blocked from real-money live execution until independent sign-off is obtained.

## 2. Executive summary of current status

### Status: mostly complete in product architecture, incomplete in final operational closure

The upgrade is materially advanced in the following areas:

- account/workspace and admin policy model is redesigned and partially hardened
- OpenAlgo-style compatibility surfaces are intentionally read-only and feature-gated
- execution boundaries and canonical reconciliation guardrails are being enforced
- migration and database guardrails have explicit disposable-only protections
- the repo now documents and enforces a pre-production review posture with explicit NO-GO guidance

The remaining gaps are concentrated in three buckets:

1. route and contract completeness across all legacy execution paths
2. proof that policy and execution state is consistent across all surfaces
3. final evidence pack, observability, auditability, and independent sign-off workflow

## 3. What is completed today

### 3.1 Platform and compatibility hardening

Completed work already present in the repo includes:

- a platform-surface registry and read-only catalog for OpenAlgo-style discovery
- a compatibility endpoint for read-only order status lookup using canonical journal evidence only
- strict route classification to separate public, configuration, admin, and read-only surfaces
- JWT + feature gate enforcement at the router edge instead of trusting the app-wide shell alone

Relevant code and workflow evidence:

- `backend/app/api/v1/endpoints/platform_surfaces.py`
- `backend/app/api/v1/endpoints/openalgo_compat.py`
- `backend/app/services/platform_surface_registry.py`
- `.github/workflows/openalgo-platform-contracts.yml`

This work establishes the correct boundary principle: Sterling can expose a compatibility/read-only surface without granting live execution authority.

### 3.2 Policy and access control hardening

Completed work includes:

- role-based write enforcement for broker and config operations
- reason_code support in admin/audit flows
- account-policy and configuration-policy reads that include explicit role semantics
- route policy changes to block mutable operations from viewer/unknown role identities
- safer handling of account activation and broker testing operations

Evidence:

- `backend/app/core/feature_access.py`
- `backend/app/services/auth/account_policy.py`
- `backend/app/services/auth/configuration_policy.py`
- `backend/app/api/v1/endpoints/admin_connections.py`
- `backend/app/api/v1/endpoints/settings_capabilities.py`
- `backend/app/api/v1/endpoints/sterling_users.py`

This is strong progress toward a canonical access model, but it still needs global consistency validation.

### 3.3 Canonical execution and reconciliation boundary enforcement

Completed work includes:

- protection against unverified fill quantities and identity mismatches in broker reconciliation
- lower-level checks preventing overfill, zero-price fill acceptance, and unknown status upgrades
- canonical reduction resolution helpers and explicit intent-key tracking
- safer exit settlement flow that refuses to rearm or re-sell on uncertain states
- stronger order lifecycle semantics for broker confirmations that are still unresolved

Evidence:

- `backend/app/services/canonical_fill_reconciliation.py`
- `backend/app/engines/intraday/position.py`
- `backend/app/services/adaptive_edge_runner.py`
- `backend/app/services/adaptive_edge_positions.py`
- `backend/app/agents/broker_agent.py`

This is one of the most important areas of progress. It materially reduces false-positive execution certainty and protects against replay/reconciliation drift.

### 3.4 Migration, recovery, and database safety controls

Completed work includes:

- disposable-only Alembic guardrails
- asyncpg requirement for PostgreSQL migration flows
- SQLite backup/restore preflight guard checks
- stronger binding manifest validation and atomic writes for account binding records
- migration-driven workflow checks for disposable Postgres and restore drills

Evidence:

- `backend/app/persistence/alembic_guard.py`
- `backend/alembic/env.py`
- `backend/app/persistence/base.py`
- `backend/app/services/account_binding_service.py`
- `.github/workflows/postgres-migration-drill.yml`
- `.github/workflows/preproduction-snapback-e2e.yml`
- `.github/workflows/product-integrity-smoke.yml`

These are the correct guardrails for a review-ready pre-production state and align with an explicit NO-GO posture.

### 3.5 Governance and docs posture

Completed work includes:

- repo-level README and DEPLOYMENT documents explicitly state pre-production review only
- there is a clear NO-GO position for live broker execution and product deployment
- CI workflows and inventory checks are structured to act as evidence gates rather than release approvals
- there are formal implementation notes describing the finish-target and closure work

Evidence:

- `README.md`
- `DEPLOYMENT.md`
- `docs/implementation/2026-10-31-sterling-finish-spec.md`
- `docs/ops/PREPRODUCTION_CONTROL_MATRIX.md`

This is critical because it removes the ambiguity that previously existed between “technical work is advanced” and “the project is approved for live operation.”

## 4. What remains to be finished

The following items still need to be completed before the upgrade can be treated as fully finish-ready.

### 4.1 Global route and contract completeness

Current status: partly complete, but not fully closed.

Remaining work:

- inventory every legacy direct broker route and compare it to the canonical execution boundary
- validate every order submission path is routed through the canonical service or explicitly denied
- ensure no hidden simulation-only or path-specific write bypass remains in any backend route
- validate that write operations cannot be triggered by a stale or partially authorized UI state

This is the highest-risk remaining area because it is easy to add new guardrails while missing one edge-case route.

### 4.2 Policy/state consistency across all surfaces

Current status: good foundation, but not yet fully consolidated.

Remaining work:

- confirm all role semantics match the same source of truth across backend policies, configuration rules, and UI projections
- ensure `viewer`, `operator`, `user`, `admin`, and unknown states produce identical behavior across backend/API/UI
- verify account binding, workspace selection, and broker assignment maintain one consistent state model
- confirm no stale hidden authorization state survives after reload, reconnect, or restart

### 4.3 Evidence pack and independent approval flow

Current status: there are partial docs and workflows, but not a final full closure package.

Remaining work:

- produce a final evidence registry with owners, dates, artifact paths, and status
- create a final sign-off matrix for each production control and release gate
- require explicit human approval records for broker-related production controls
- keep the system in a false-positive-proof “pre-production only” state until review is complete

### 4.4 Final operational proof under failure and restart conditions

Current status: improved, but not end-to-end complete.

Remaining work:

- exercise bounded reconnect/restart/reconciliation scenarios across multiple strategy paths
- validate that journaled entries are recoverable and replay-safe after failure
- prove that unknown reconciliation states remain unresolved rather than silently becoming accepted trade state
- validate audit trail completeness across the order lifecycle and admin actions

### 4.5 Final release gate and product integrity closure

Current status: there are valuable CI checks, but the repo still needs a final “finish gate” that interprets them as evidence rather than as an implicit approval.

Remaining work:

- define exact release gate semantics and required human sign-off
- keep the lock on real-money execution until sign-off artifacts are present
- make the difference between “technical milestone reached” and “operational approval granted” unmistakable

## 5. Implementation target end state

The upgrade is considered finished only when all of the following are true:

- every real broker-write path is mapped and governed by a canonical execution contract
- every route that can mutate trading state is explicitly authorized by policy and identity evidence
- every critical action produces a durable audit record with causal metadata and reason code
- database migration/restore paths are proven safe in disposable and recovery scenarios
- the app is clearly and consistently labeled as pre-production only until independent approval is obtained
- no live brokerage call is able to execute without a separate reviewable authorization chain

## 6. Detailed implementation plan

### Phase 1 — contract closure and route inventory

Goal: eliminate undocumented execution paths.

Tasks:

1. Build a full route inventory
   - list every request path that can touch broker state, order status, order submission, account activation, or policy mutation
   - map route -> account scope -> feature gate -> canonical service -> acceptance tests

2. Classify each route as one of:
   - public
   - read-only compatibility
   - configuration mutation
   - order mutation
   - admin policy mutation
   - internal service-only

3. Fail closed on any unclassified route
   - if a route cannot be explained by policy + service ownership, it must be blocked

4. Add tests for each route class
   - unauthorized user cannot access route
   - viewer cannot write broker state
   - operator/user/admin roles behave exactly as expected
   - readonly compatibility surfaces do not expose mutation paths

Expected deliverables:

- route inventory matrix
- route-to-policy mapping doc
- route-level test suite pass

Acceptance criteria:

- no unclassified broker-write path remains
- no route silently falls through to a legacy execution branch

### Phase 2 — canonical execution completion

Goal: make the execution path explainable and auditable.

Tasks:

1. Complete canonical service contract for all live execution operations.
   - submit_order
   - cancel_order
   - reconcile_order
   - record_outcome
   - require_authority

2. Validate replacement of legacy broker paths in the major engines.
   - adaptive edge execution
   - intraday position lifecycle
   - order submission / cancellation routines
   - entry and exit fill reconciliation

3. Enforce intent-key durability for all order state transitions.
   - every order lifecycle needs a unique durable intent key
   - no ambiguous outcome is permitted to become final without reconciliation evidence

4. Add restart recovery tests.
   - process crash during order entry
   - process crash during fill reconciliation
   - stale order state after reconnect
   - duplicate order attempt after a timeout

Expected deliverables:

- canonical execution service implementation completion
- route migration log
- recovery/reconciliation test suite

Acceptance criteria:

- no final order lifecycle decision is made without durable evidence
- ambiguous reduces and unknown fills remain unresolved

### Phase 3 — authorization and policy consolidation

Goal: single explicit access model.

Tasks:

1. Define and enforce a canonical role semantics contract.
   - default role behavior
   - viewer restrictions
   - operator permissions
   - admin-only actions
   - config and broker mutation conditions

2. Align backend and frontend policy semantics.
   - UI route visibility must mirror server-side policy
   - no stale feature visibility in shell components
   - no “appears enabled” state without actual permission

3. Standardize admin change reasons and auditability.
   - every sensitive change should include structured reason code
   - reason codes must be bounded and machine-validated
   - before/after snapshots should be stored for admin changes

4. Remove stale implicit privileges.
   - any logic that infers authority from substring matching, indirect role names, or hidden state must be removed

Expected deliverables:

- explicit policy contract document
- route-visibility parity tests
- admin audit review pack

Acceptance criteria:

- policy decisions are consistent across all surfaces
- no stale implicit privilege can grant write access

### Phase 4 — migration, backup, and recovery proof

Goal: prove the system can survive failure without silently corrupting state.

Tasks:

1. Validate SQLite and PostgreSQL migration guards under test conditions.
   - isolated scratch database only
   - no writes to live operational database
   - all migration checks must pass under disposable-only configuration

2. Validate backup and restore behavior.
   - export/import data
   - restore to separate database
   - check schema and data continuity
   - verify rollback semantics on synthetic rows

3. Validate journaling and reconciliation after restart.
   - process restart after accepted order
   - restart while reconciliation is pending
   - restart after a failed or ambiguous broker response

4. Validate observability.
   - no secrets leak into logs
   - no unstructured raw auth state in debug output
   - failure reasons are machine-readable and actionable

Expected deliverables:

- migration/restore evidence report
- recovery drill report
- auditability checklist

Acceptance criteria:

- state restoration is deterministic and reviewable
- recovery outcomes are explainable and provable

### Phase 5 — final evidence package and sign-off gate

Goal: turn engineering progress into an approval package without claiming production permission.

Tasks:

1. Build the control matrix.
   - owner
   - evidence requirement
   - current status
   - next action
   - approval gate
   - review date

2. Build the evidence registry.
   - each control mapped to files, CI run, and human owner
   - no “approved” state without artifact linkage

3. Create the independent sign-off checklist.
   - product owner
   - engineering owner
   - security/reliability reviewer
   - operations reviewer
   - final approval authority

4. Keep the pre-production barrier explicit.
   - all docs must say “review only; no live execution authorization”
   - no shipping language that implies approval

Expected deliverables:

- `docs/implementation/CONTROL_MATRIX.md`
- `docs/implementation/OPERATIONAL_EVIDENCE_PACK.md`
- `docs/implementation/EXECUTION_ROUTE_MIGRATION.md`
- sign-off checklist

Acceptance criteria:

- all critical controls have evidence and owner
- all docs clearly separate capability from approval
- live broker execution remains explicitly blocked until sign-off

## 7. File-level implementation map

The work is concentrated across the following files and areas.

### Backend policy and access

- `backend/app/core/feature_access.py`
- `backend/app/services/auth/account_policy.py`
- `backend/app/services/auth/configuration_policy.py`
- `backend/app/api/v1/endpoints/sterling_users.py`
- `backend/app/api/v1/endpoints/admin_connections.py`
- `backend/app/api/v1/endpoints/settings_capabilities.py`

Implementation focus:

- ensure a single source of truth for authorization state
- ensure viewer/unknown identities cannot mutate broker or config state
- ensure a structured reason code accompanies critical state changes

### Backend compatibility and surface contracts

- `backend/app/api/v1/endpoints/openalgo_compat.py`
- `backend/app/api/v1/endpoints/platform_surfaces.py`
- `backend/app/services/platform_surface_registry.py`

Implementation focus:

- keep compatibility surfaces read-only
- ensure no OpenAlgo-compat endpoint provides execution authority
- validate metadata, JWT, and feature gating at the endpoint boundary

### Canonical execution and reconciliation

- `backend/app/services/canonical_fill_reconciliation.py`
- `backend/app/engines/intraday/position.py`
- `backend/app/services/adaptive_edge_runner.py`
- `backend/app/services/adaptive_edge_positions.py`
- `backend/app/agents/broker_agent.py`

Implementation focus:

- reject unknown fills and mismatched broker identities
- ensure no partial or overfilled order is treated as complete by inference
- preserve intent-key tracking and reconcile after restart

### Persistence and migration safety

- `backend/app/persistence/alembic_guard.py`
- `backend/alembic/env.py`
- `backend/app/persistence/base.py`
- `backend/app/services/account_binding_service.py`

Implementation focus:

- require disposable-only migration configuration
- prohibit unsafe DB targets and secret-bearing config
- maintain atomic store integrity for bindings and manifests

### CI / proof / governance

- `.github/workflows/openalgo-platform-contracts.yml`
- `.github/workflows/postgres-migration-drill.yml`
- `.github/workflows/preproduction-source-inventory.yml`
- `.github/workflows/preproduction-snapback-e2e.yml`
- `.github/workflows/product-integrity-smoke.yml`

Implementation focus:

- prove that the changes are valid under review conditions
- keep enforcement logic in CI as evidence, not approval
- ensure the repo’s default state stays non-authorizing

## 8. Implementation sequence

Recommended order of completion:

1. route inventory and route classification
2. canonical execution closure across remaining engines
3. policy consolidation and authorization parity
4. migration/recovery proof and observability validation
5. evidence pack and sign-off gate creation

This sequence is deliberate: if the route map and canonical execution closure are not complete, evidence generation is not trustworthy.

## 9. Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Hidden legacy broker route | Silent non-canonical execution | Inventory and fail closed |
| Viewer or stale role writes | Unauthorized mutations | Consolidate policy model |
| Ambiguous broker reconciliation | False-positive fills or duplicate exits | Refuse uncertain state |
| Migration drift | Data loss or schema mismatch | Disposable DB tests + restore proof |
| Premature launch claim | Operational and compliance exposure | Keep NO-GO status until sign-off |

## 10. Definition of done for the finish slice

The upgrade is done only when all of the following have been satisfied:

- every route that can mutate broker state is explicitly classified and governed
- every route decision is backed by a canonical service or explicit deny
- policy decisions are consistent across backend and UI
- critical admin and broker actions are auditable and reason-coded
- migration and restore operations are proven safe in isolated test conditions
- a final evidence package with owners and controls exists
- live broker execution remains blocked until human approval is recorded

## 11. Final recommendation

The correct finish path is not to “ship” the upgrade or to assume live execution is now approved. The correct finish path is to close the remaining route, policy, evidence, and recovery gaps in strict order and leave the project in a reviewed, reproducible, pre-production-only state until independent approval is obtained.

This keeps delivery velocity high while preserving the engineering discipline required for a trading platform with broker-write authority and persistent execution state.

## 12. Status summary

At the time of this doc:

- Product and architecture maturity: strong
- Execution guardrails: materially improved
- Policy hardening: substantially underway
- Migration and recovery protection: strong foundation and partial proof
- Final operational sign-off: not complete
- Live trading authorization: explicitly blocked and not approved

The remaining work is therefore closure work, not reinvention.

Document path: `docs/implementation/2026-11-01-openalgo-upgrade-finish-spec.md`
