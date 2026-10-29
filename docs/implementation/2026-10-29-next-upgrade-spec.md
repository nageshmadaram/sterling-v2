# Sterling upgrade implementation spec — 2026-10-29

## Purpose

This document captures the current state of the Sterling V2 upgrade effort, distinguishes work that is already completed from the remaining work, and defines the next implementation plan for the next delivery slice.

This is a technical implementation specification for the next engineering phase. It is intentionally explicit about what is done, what is not yet proven, and what must be delivered before any production certification or operational rollout is considered.

## 1. Current status snapshot

### 1.1 What is completed

The repository already contains clear evidence of delivered work in the implementation archive and product documentation.

Key implemented areas observed from the repository include:

- Account navigation and administration redesign
  - Shared account/header behavior
  - Workspace selection and identity model
  - Admin user directory and broker assignment flows
  - Credential handling and masked access review surfaces
  - Session provenance and browser/device telemetry capture
- Product UI redesign and verification
  - Shared navigation and modal behavior
  - Unified header, settings and workspace interactions
  - Mobile adaptation and reduced-motion / layout consistency
  - Runtime verification artifacts and frontend test coverage
- Workspace flow and configuration access changes
  - Workspace selection and application shell flow
  - Account policy foundation and configuration access improvements
  - Management-mode design improvements
- Related evidence artifacts and supporting docs in `docs/implementation/`

These items are concrete implementation outcomes, not merely design notes.

### 1.2 What is not yet completed / still blocked

The repository contains completed UI and account-management work, but production certification remains blocked. The current observed gaps are not just product gaps; they are evidence and control gaps.

The current blocking status is:

- H01 production evidence: BLOCKED
- Total production controls accepted: 0 / 12
- Total production controls blocked: 12 / 12
- Independent sign-off: NOT PROVIDED
- Production deployment: NO-GO
- Live trading: NOT AUTHORIZED
- Broker order routing: NOT AUTHORIZED

This means the next implementation phase must continue to improve the product while also closing evidence and operational readiness gaps.

## 2. Completed implementation comparison

### 2.1 Completed product work already visible in the repo

From the implementation docs in `docs/implementation/`:

1. Account navigation and administration
   - Shared top-level account UX
   - Workspace-aware shell behavior
   - Broker ownership and reassignment flows
   - Credential management with masking and audit visibility
   - Session provenance / browser telemetry capture
   - Read-only broker verification and backup artifacts

2. Product UI redesign
   - Neutral shell navigation and shared control behavior
   - Unified admin and configuration surfaces
   - Mobile and desktop layout alignment
   - Modal and focus handling consistency
   - Runtime verification and UI audit artifacts

3. Workspace flow and configuration access
   - Better workspace selection and scope handling
   - More consistent settings and administration access model
   - Shared policy and management integration work

4. System/project hygiene and operational docs
   - Multiple operational docs and implementation notes under `docs/implementation/`
   - Supportive docs on risk, execution, strategies, security and deployment

### 2.2 What these completed items do not do

The completed items are substantial product improvements, but they do not by themselves imply:

- real production host hardening
- formal production certification
- independent security sign-off
- broker integration acceptance in production-like conditions
- monitoring or incident-readiness proof
- completed database and recovery evidence
- approved production deployment

The current state is therefore: product work is substantially advanced, but operational and certifiable evidence is still pending.

## 3. Remaining gaps and upgrade blockers

### 3.1 Product gaps

Some product-level work is still logically unfinished or not yet fully integrated into a single governed release track:

- Safety and evidence gates are still not synchronized into a single production package
- Operational readiness steps remain separate from product implementation work
- Production-like enforcement and review workflows are not yet fully codified as release evidence
- The repository still contains a split between product implementation work and production-certification evidence work

### 3.2 Non-product blockers

The blocking items are the real gating controls:

- H01 production evidence remains blocked
- TLS and edge exposure controls not independently accepted
- Secrets management evidence still outstanding
- Operator access controls not proven in a real environment
- Database safety, backup/restore and audit evidence still pending
- Broker acceptance and market-data reliability still pending
- Monitoring and alert acknowledgment still pending
- Legacy broker-access remediation still pending
- Independent governance sign-off still not provided

These are not optional enhancements; they are mandatory release gates.

## 4. Strategic objective for the next implementation phase

The next implementation phase should focus on a single integrated upgrade slice that closes the product gap between the current implementation maturity and the next production-ready release boundary.

This phase should not attempt to open live execution or production deployment. Instead, it should deliver a release-ready evidence package with clear operational controls, stable defaults, and full traceability.

The next phase should aim to achieve all of the following:

1. Harden the application shell and workspace behavior as a stable operating model.
2. Finalize the operator/admin workflow as the only supported governance path.
3. Lock the configuration and account model so all policy and access decisions are explicit and auditable.
4. Close the gap between implementation progress and production evidence requirements.
5. Prepare the repository for the next independent sign-off package without implying authority to deploy or trade.

## 5. Detailed implementation plan for the next phase

### Workstream A — Production-safe release architecture

#### Goal

Establish a release package that is operationally safe and reviewable without claiming live production readiness.

#### Scope

- Consolidate the current implementation state into a single release discipline.
- Standardize release artifact boundaries between:
  - product UI implementation
  - backend runtime changes
  - operational deployment artifacts
  - certification evidence files
- Separate product enhancement from certification evidence and approval.

#### Deliverables

- `docs/implementation/` package for the current phase
- release manifest summarizing:
  - implemented features
  - intentionally not-yet-approved features
  - production controls remaining blocked
  - evidence files required for sign-off
- release-checklist workflow for operational governance

#### Acceptance criteria

- Every delivered feature is mapped to a source artifact and validation artifact.
- Every blocked control is explicitly linked to evidence requirements and an accountable owner.
- No feature can be described as production-certified without an independent evidence package.

---

### Workstream B — Operational workspace and account model hardening

#### Goal

Stabilize the workspace, account, broker and admin flows as the canonical operating model.

#### Scope

- finalize workspace identity model and selection policy
- preserve account-level settings and broker assignment behavior
- ensure admin credential management remains masked and auditable
- consolidate user/workspace access logic so a single explicit state exists
- ensure all visible UI state maps cleanly to the backend policy model

#### Key tasks

- Review the current account-navigation and workspace-selection implementations.
- Validate that any broker re-assignment flow preserves auditability and does not silently create hidden authority.
- Confirm that admin credential writes are bound to the correct owner, profile and session.
- Ensure credential telemetry is stored with minimal exposure and explicit retention rules.
- Confirm that workspace switching and feature enablement calls check the same policy path.

#### Acceptance criteria

- One source of truth for workspace and account state.
- Admin and operator actions produce clear, reviewable audit artifacts.
- Hidden or stale access states are not produced by data races or stale refreshes.
- Broker ownership and access decisions remain explicit and reversible.

---

### Workstream C — Shell and UI consistency across product surfaces

#### Goal

Lock in the shared navigation and shell behavior across desktop/mobile and admin flows.

#### Scope

- finish shared shell and navigation standards across all product surfaces
- ensure admin, account, settings and operational panels share a consistent control model
- unify modal focus and accessibility behavior
- ensure routes and screens respect enabled-page policy

#### Key tasks

- review the existing product UI redesign work under `docs/implementation/2026-10-28-product-ui-audit.md`
- ensure all new features inherit the shared shell contract
- remove any duplicate or hidden navigation logic
- unify settings and admin page title/section behavior
- validate behavior at desktop and mobile widths with real runtime checks

#### Acceptance criteria

- One consistent shell model for all workspaces and views.
- Accounts, settings, and admin surfaces use the same routing and modal semantics.
- Hidden or disabled pages do not appear in authorized navigation paths.
- Responsive implementation remains stable across major screen widths.

---

### Workstream D — Security and evidence control integration

#### Goal

Bridge the gap between product implementation and real-world evidence requirements.

#### Scope

- align product changes with the production control register
- ensure the current implementation can be mapped to control evidence requirements
- produce evidence files that are traceable to the implemented state
- make the evidence package review-ready for internal or external approval

#### Required control mapping

The production control register is explicit and must be treated as the compliance map.

- H01 — Infrastructure / Security / Platform / Network
- T02 — Network / Security
- S03 — Security / Secrets Management
- I04 — Identity / Security
- D05 — Database / Security
- R06 — Database / Operations
- B07 — Broker Integration / Compliance
- M08 — Market Data / Operations
- A09 — Operations / Monitoring
- L10 — Engineering / Security
- F11 — QA / Security / Operations
- G12 — Independent Governance / Release

#### Acceptance criteria

- Every control has at least one explicit evidence artifact requirement.
- Each control has a clear accountable owner and verification method.
- No control is marked accepted without recorded evidence and approval.

---

### Workstream E — Release evidence and governance package

#### Goal

Prepare for the next independent release gate without claiming production status.

#### Scope

- build a release evidence folder for all controls
- provide a decision matrix: accepted / blocked / no evidence
- capture open exceptions and unresolved risks
- prepare the project for independent review without implying deployment authority

#### Deliverables

- evidence registry
- control checklist with owners and status
- exception register
- sign-off checklist for governance reviewers
- status dashboard for release decision support

#### Acceptance criteria

- Every control has a named owner, evidence requirement and acceptance criterion.
- Reviewers can tell exactly what evidence is missing.
- The release package is independent from deployment execution rights.

---

## 6. Suggested execution plan

### Phase 1 — Stabilization and traceability

- finalize the implementation inventory from `docs/implementation/`
- map completed work to explicit system features
- identify what the next release must include
- record current evidence gaps in a single matrix

### Phase 2 — Control alignment

- align current product improvements to the 12 production controls
- create owner mapping for each control
- define evidence artifact drafts and review criteria

### Phase 3 — Operational evidence preparation

- prepare real-environment evidence collection procedures for H01 and related controls
- create safe, read-only verification steps where production actions are not permitted
- define independent sign-off requirements and documentation format

### Phase 4 — Governance-ready closeout

- compile control-by-control status to a release package
- prepare the next approval package without advertising production readiness
- maintain the NO-GO decision until all required evidence is accepted

## 7. Definition of done for the next implementation slice

The next implementation slice is complete when all of the following are true:

- Product shell and workspace behavior is stable and consistent across surfaces.
- Account, broker, admin and workspace flows are governed by one explicit policy model.
- The implementation can be mapped into the 12 production-control register.
- Every control has a named owner, evidence requirement and next action.
- The repository contains a single release evidence package and an operational readiness register.
- The project status clearly distinguishes between:
  - validated implementation
  - validated evidence handling
  - production-certified readiness

## 8. Explicit non-goals

The next implementation phase must not:

- claim production readiness
- authorize deployment
- authorize live trading
- authorize broker order routing
- merge to a production release path without independent evidence acceptance
- blur the distinction between offline validation and production approval

## 9. Final recommendation

The next engineering phase should be treated as:

- a product and governance integration phase
- not a production deployment phase
- not a live-trading enablement phase
- not a certification release phase

The correct near-term objective is to turn the completed implementation work into a fully traceable, evidence-backed, governance-ready next release package, while keeping the NO-GO status and the release freeze in place until all required controls are independently accepted.

## 10. Summary

What is already delivered:

- Account navigation and admin controls
- Workspace flow, config access and product UI refinements
- Multiple implementation artifacts and evidence artifacts

What remains required:

- real-world evidence for H01 and the rest of the 12 controls
- independent production review and explicit approval
- formal governance sign-off before any production decision can be made

This makes the next phase a controlled upgrade to operational readiness, not a path to production deployment.

---

Document path: `docs/implementation/2026-10-29-next-upgrade-spec.md`

This document intentionally emphasizes implementation continuity and evidence gate discipline without asserting production certification.

