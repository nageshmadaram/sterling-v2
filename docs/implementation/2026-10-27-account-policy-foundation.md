# Account overhaul: implementation slice 1

Current status: [workspace flow and central administration](2026-10-27-workspace-flow.md). The notes below describe the earlier slice.

Implements the first part of specification §19.1. This is development work in the checkout, not a deployed or release-ready overhaul.

## Implemented

- Additive account schema version 1 runs in the user-store schema transaction. A singleton designation, partial unique role index and SQL triggers prevent additional administrators, promotion, removal, demotion, suspension and identity transfer. A valid existing sole administrator keeps their identity. Multiple or inactive administrators stop migration without changing those records. Empty installations retain explicit credential bootstrap.
- Independent durable Kite/TV/Radio grants, default denied for every identity including the admin. Inactive and missing identities receive no effective features. `require_feature` checks durable storage and fails closed on storage errors.
- Admin-only `GET/PUT /api/v1/auth/users/{user_id}/features`. PUT requires all three strict booleans and `expected_revision`; stale writes return 409. Unknown fields are rejected. Responses are private/no-store. This is an additive adapter under the existing users API, not yet the final `/admin/users` API.
- Feature changes, revision, redacted audit and invalidation-outbox insertion commit together. Credential-management changes also insert redacted audit in their existing transaction. No outbox consumer is implemented yet.
- Identity persistence now propagates failures before replacing cached identities. Upsert updates preserve the designated identity rather than using SQLite replace/delete semantics.
- The existing user editor no longer offers administrator promotion.
- Read-only preflight: `backend/.venv/bin/python backend/scripts/account_policy_preflight.py /absolute/path/to/database`. Use a staging copy for rollout preparation. This command does not migrate, seed, contact brokers, or print hashes.

## Verification

Disposable SQLite tests cover all eight grant combinations; admin independence; missing/inactive identities; service and raw SQL administrator bypass attempts; competing revisions; audit failure rollback; no memory-only success; idempotent migration; ambiguous legacy administrator rollback; API conflicts and strict request validation. Validation result: 74 backend tests passed across the policy, user CRUD and authentication regression suites; frontend `tsc --noEmit` and `git diff --check` passed. Test fixtures that previously seeded an administrator and then cleared only its memory cache now prepare an empty schema instead.

## Boundary and next work

Feature policy is not wired into existing trading/media routes in this slice. Existing feature navigation is unchanged. Do not advertise these grants as enforcing feature access until the route, stream, callback, worker-admission and Node/Nginx direct-document inventory is complete and negative tests pass. The protected broker-removal guard remains in place; no broker ownership or risk rules changed.

Next: complete route-policy inventory and configuration-delegation contracts, safe broker-registry/assignment and encrypted-secret references, finite device sessions/MFA, then protected media and launcher/admin UI. No existing-user grants have been guessed. No live database, broker, order, notification transport or deployment was operated during development. Production release still requires explicit legacy mapping and all relevant acceptance gates in the source specification.
