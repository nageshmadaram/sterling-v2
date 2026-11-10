# Tenant-scoped virtual-ledger writer fencing under the canonical SQLite reset lock

**Parent:** Draft Sterling V2 PR #52 at immutable commit `d83a6aace20ad346a78f86e4fbb8cbff2509f9bd`.
**Intended release:** independent review ONLY. Draft / unmerged / undeployed; no live broker orders, GTT writes or credentials. All 12 independent production release controls remain unapproved; production NO_GO.

## Defect independently reproduced (red-first)

The previously reviewed durable simulation reset fence coordinated the canonical order journal, Gamma review writers and selected runtime/API admissions. It did **not** serialize raw virtual-ledger writes. A separate virtual writer could update a tenant's `sim_orders`, `sim_gtts`, `sim_trades`, `sim_positions`, virtual cash, equity snapshots and settings after reset admission, thereby recreating rows after reset had purged the tenant.

On the exact parent source, 13 negative tests failed: direct account/cash/capital/settings/order/history/GTT/mark/fill/snapshot/reset writes succeeded despite an ACTIVE fence, while newly required owner-scoped transaction and coordinator-token APIs were absent. The unrelated-owner test already passed. No broker network, real account or production database was touched.

## Scoped implementation

- Owner-scoped `SimLedger.transaction(owner=...)` begins a SQLite `BEGIN IMMEDIATE` transaction and checks the durable `simulation_reset_fence_v1:sim:<owner>` marker **on the same SQLite connection** before allowing owner mutations. Missing marker permits ordinary writes, malformed/unreadable marker propagates failure, ACTIVE/FAILED markers refuse ordinary writes with `simulation_reset_fenced`. The check is repeated on nested calls using the same open transaction.
- `SimLedger._cx(write_owner=...)` wraps direct mutators with that owner-scoped transaction. Guarded methods now include account creation, virtual cash/capital, settings, orders/history, GTTs, price marks, trade/fill accounting, equity snapshots and owner reset.
- All eight outer `VirtualExchange` order, modification, cancellation, GTT, expiration and evaluation transactions now specify `owner=self.owner`, protecting the initial sequence allocation and subsequent nested writes as one owner-admitted transaction. Virtual postbacks are still not broker writes.
- A reset may call `SimLedger.reset(owner, reset_token=...)` only with the matching persisted **ACTIVE** token, retaining reset-owned bootstrap. `FAILED` and mismatched tokens always refuse. The token inherits only inside the reset's current ledger transaction, not through API state or a process-global variable.
- `sim_runtime.reset_owner()` and the simulation reset coordinator forward the token. The reset-owned tenant bootstrap passes the token to account initialization and uses the resulting virtual capital for the daily-loss override. Existing separate simulated tenants are not blocked by another owner's fence.
- The default SimLedger uses the same SQLite file as the canonical journal. The optional injected `connection_factory` is used by the pure fake-ledger tests and is **not represented as an independently proven production fence**. The legacy uninitialized-default-database test now initializes `db` explicitly, reflecting production boot. An additional negative test requires missing `system_config` schema to fail closed, rather than treating it as an empty fence.
- Scoped Gamma Canonical Recovery CI now reacts to `ledger.py`, `virtual_exchange.py`, their tests and executes both ledger and virtual-exchange regressions.

## Verified evidence on this source tree

- Initial red-first: **13 negative cases failed** on immutable parent PR #52, unrelated-owner isolation case passed.
- After remediation: **14/14** focused cases passed; **2/2** additional real-virtual-exchange and transaction-ordering race cases passed.
- Full local `backend/tests/unit/simulation`: **587 passed**, five non-failing warnings. The final test exception-type tightening did not change source logic and should be included in final review and exact-SHA CI verification.
- Full local Gamma/canonical/journal selected suite: **511 passed**.
- Python compilation, governance schema, strict preproduction source inventory, governance/inventory unit tests, and `git diff --check` passed. Remote exact-SHA GitHub workflows must be verified separately after creating a new stacked draft PR.

## Security and recovery limits (unverified)

1. The new transaction fence covers only participating **owner-scoped default SimLedger writers and VirtualExchange routes**. Arbitrary raw SQL via other services, a public `transaction()` without owner, the global sequence generator when called directly, an injected noncanonical ledger connection or separate databases are not certified by this patch.
2. SQLite serializes fenced writer admission in the **one canonical database**, not independent external stores. Host crash across state stores may still leave a partial reset; there is no implemented distributed rollback/restore protocol or certified automated unlock.
3. An ACTIVE fence after process death remains blocked for manual investigation. FAILED may be retried only through the already-reviewed explicit reset flow; this is not a certified full recovery or authorization for a live broker position.
4. Parent PR #15 full backend, frontend and browser regression still fails. Genuine partial-remainder SELL, broker-authenticated position/GTT truth, atomic protective stop handoff, UNKNOWN-order no-resend and 12 independent approvals remain out of scope.

**Release decision: DRAFT / UNMERGED / UNDEPLOYED / NO-GO.**
