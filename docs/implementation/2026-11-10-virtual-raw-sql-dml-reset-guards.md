# Raw SQLite virtual-book DML reset guards — offline review

**Parent:** Draft PR #54, immutable source `f4b56c6bb6f59577c6124acbf256739bef0a78fd`.
**Posture:** DRAFT / UNMERGED / UNDEPLOYED. Live broker execution unauthorized, and 0/12 independent release controls approved.

## Confirmed red-first defect

The parent had fail-closed `SimLedger` API admission, but no SQLite table-level defense against ordinary raw DML through `app.services.db._conn()` or another connection. An `ACTIVE` or `FAILED` durable reset marker could coexist with new virtual account, order or GTT writes. Thirteen targeted cases failed against the parent: twelve raw INSERT/UPDATE/DELETE mutations committed under ACTIVE/FAILED markers and there were zero tenant table triggers. An isolated unfenced owner remained writable, and the reset coordinator's existing exact-token cleanup worked.

## Implementation

1. `sim_reset_sql_fences.py` declares 9 tenant-owned virtual tables and installs 27 idempotent `BEFORE INSERT/UPDATE/DELETE` triggers, scoped to the `owner` row key. The triggers check the durable `system_config` marker `simulation_reset_fence_v1:sim:<owner>` and refuse raw DML unless a connection-local reset permit was granted. A malformed marker is treated as fenced, not clear.
2. `db._conn()` registers the default SQL function `sterling_sim_reset_write_allowed` returning false for all owners. Only the existing `SimLedger._check_writer_fence` path can override it *on that connection* after verifying the matching `ACTIVE` token while holding the same `BEGIN IMMEDIATE` SQLite writer lock. FAILED or incorrect tokens are refused. No global switch or public unlock path was introduced.
3. `db.init()` installs guards for existing virtual tables at startup. `SimLedger._ensure_schema()` installs them on first virtual-table creation, so a second Python process inherits the constraints through the database schema.
4. Unrelated owners can still use managed database connections while another owner is fenced. Direct, unmanaged SQLite connections which have not registered the SQL function cannot write even in the absence of a fence; this is an explicit, tested fail-closed compatibility restriction.
5. The scoped offline Gamma Canonical Recovery workflow tracks both `db.py` and the new trigger module, in addition to the existing simulation test suite.

## Verification

- New tests: raw account/order/GTT inserts, updates and deletes across ACTIVE and FAILED states; 27 required trigger definitions; unaffected owner writes; reset coordinator exact-token cleanup; invalid marker; independent Python process; and unmanaged-connection refusal.
- Red-first on immutable parent: 13 cases failed as expected; unaffected-owner and coordinator cases already passed.
- Current branch complete simulation test directory: **610 passed, 5 non-failing warnings**.
- Current branch selected Gamma/canonical/journal regression: **511 passed**.
- Source compilation, strict source inventory, governance schema, inventory/governance unit checks and `git diff --check` must pass before publication; exact GitHub CI SHA evidence is a separate check.

## Hard limits — not an ACID or security certification

- SQLite triggers protect ordinary writes to existing owner-bearing `sim_*` tables. The ownerless `sim_sequences` global counter is guarded by `SimLedger` methods but is not fenced at the database schema level. Malicious or privileged code with raw database access can alter `system_config`, drop triggers, register a forged SQL function, or bypass these protections entirely. Treat these as application consistency defenses, **not** adversarial database access control.
- This does not guarantee crash-atomicity across independent state stores, live broker/broker-side GTT reconciliation, restart reconstruction of externally observed fills, or stale ACTIVE fence safe release. No automation unlocks ACTIVE or FAILED markers.
- The parent PR #15 remains draft with full CI failures, including multiple backend and browser failures. Passing synthetic suites does not resolve the full product gate or certify production SELL/UNKNOWN no-resend.
- Independent broker proof, operational backup/restore, role/workspace completeness and 12 independently approved release controls remain outstanding. Production release remains **NO_GO**.
