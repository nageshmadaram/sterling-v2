# Sterling policy and route behavior — pre-production contract

The durable authorities are `backend/app/services/auth/account_policy.py`, `configuration_policy.py`, `backend/app/core/feature_access.py` and authenticated session validation. This is a composition of existing authority stores, not a new interchangeable client-side permission source.

| Dimension | Source | Enforcement | UI behavior |
|---|---|---|---|
| Active identity, role and revision | Account policy, current user store | Default deny for inactive/unknown identity; exact role checks | Render /auth/me current policy role; never trust persisted role |
| Kite / TV / Radio grants | Account policy feature grants | Route-level feature access, including write restrictions | Mount only granted workspace |
| Workspace visibility | Account policy visibility | Presentation preference, not authorization grant or revocation | Hidden workspace must not mount, even by direct URL; no request-access button |
| Config sections and scoped actions | Configuration policy | Resource owner + section action + role | Disable unavailable configuration writers |
| Broker assignment and session | Kite account binding + user identity | Exact account/broker/session binding | Never use client-side assignment as proof |
| Live-risk permission | SafetySupervisor, execution control, release scope | Enforce in canonical service at send boundary | Expose disabled/NO-GO reasons, never show execution as approved |

An admin toggling a feature uses a revision CAS transaction, before/after audit and invalidation event outbox. The current audit schema records actor, action, target, state and timestamp; free-form reason/approval marker is not consistently mandatory, and must be added without falsifying historical records.

**Known fixed discrepancy:** `WorkspaceRouter` formerly checked grants when mounting but did not also apply visibility; `/auth/me` previously serialized the request-context role. Both are corrected on the PR branch, with focused tests added. Backend visibility remains intentionally independent of a grant.

**Unverified:** comprehensive endpoint-by-verb policy enforcement, broker assignment mismatch across every API route, stale-session race handling on all workers, frontend modal/focus/toast uniformity across mobile and desktop. Add route-level tests on a disposable database; do not declare this completed on the strength of role-dependent rendering alone.
