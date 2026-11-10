# Prevent unscoped virtual-ledger writes and global sequence mutation

**Parent:** Sterling V2 draft PR #53 at exact SHA `01eb9ad7c87d6944fcb2b0448b9911952f94f506`.
**Release posture:** synthetic/test-only review increment; draft, unmerged and undeployed; zero live broker authorization; all 12 independent production controls remain unapproved and production is **NO_GO**.

## Red-first defect

PR #53 fenced owner-scoped virtual-ledger methods in the default SQLite database but left three code paths that did not carry a tenant authorization context:

1. `SimLedger.transaction()` could open an unscoped `BEGIN IMMEDIATE` transaction and execute SQL against any tenant, bypassing the reset fence.
2. `SimLedger._cx()` could hand out a writable SQLite connection even when no authorized transaction existed, allowing a caller to modify `sim_accounts` directly.
3. `SimLedger.next_id()` advanced shared `sim_sequences` outside an owner scope. Nested owner transactions also allowed switching to a different owner, permitting unintended cross-owner mutation in one transaction.

All five new negative tests failed on the immutable parent SHA before implementation. Only synthetic virtual-ledger and disposable application test databases were used; no real Kite or other broker writes were attempted.

## Implemented

- In the default production `SimLedger`, `transaction()` refuses an ownerless call unless it inherits a currently authorized owner from an open transaction. A nested owner switch is refused with `simulation_owner_scope_mismatch`. Any explicit mismatched nested reset token is refused. The transaction thread-local owner and reset token are both cleared at exit.
- `SimLedger.next_id(name, *, owner=None)` requires explicit owner admission or an inherited owner transaction for the default application store. Global order/GTT/trade sequence increments are serialized under the same shared SQLite writer lock as reset admission. In the existing fake-ledger testing seam, standalone sequences remain usable without a simulated production fence.
- Standalone default `_cx()` read helpers enable SQLite `PRAGMA query_only=ON` after schema initialization so incidental direct writes fail at the SQLite connection. `_cx(write_owner=...)` still enters an admitted write transaction; an existing owned transaction retains its authenticated connection. This is a defense against accidental internal writes, **not** a sandbox against privileged code capable of switching PRAGMAs or using `db.connection()` directly.
- `sim_runtime.set_capital` explicitly opens `transaction(owner=o)` rather than relying on the previously unscoped outer transaction, while maintaining its additional in-transaction reset-fence recheck.

## Verified local evidence

- Five red-first regression tests reproduced the ownerless SQLite transaction, raw read-connection write, standalone sequence, nested cross-owner transaction and explicit nested sequence failures. All five now pass.
- The full `backend/tests/unit/simulation` suite passes: **592 passed**, with five non-failing warnings.
- The scoped Gamma/canonical/journal suite passes: **511 passed**.
- Python compilation, strict source inventory, and governance/inventory unit validation are required before publication; exact-head GitHub CI outcomes must be independently checked once the draft PR exists.

## Residual risks and hard limits

- The controls apply to callers using the default `SimLedger` API and participating `VirtualExchange` routes. Direct use of `app.services.db.connection`, explicit `PRAGMA query_only=OFF`, raw Python SQLite writes, separately configured ledger factories and independent data stores are **not** restricted by these helpers.
- An untrusted plugin with unrestricted database access could bypass the connection-level pragma. Do not represent these API protections as a database authorization policy.
- No cross-store atomic reset, independently certified restart/restore procedure, manual stale-ACTIVE release, authenticated broker/GTT proof, executable partial-remainder SELL or independent production approval is implemented here.
- Draft parent PR #15 still has unresolved backend/frontend/browser regression evidence. All 12 independent release gates remain NO-GO.

**DRAFT / UNMERGED / UNDEPLOYED / LIVE BROKER EXECUTION NO-GO.**
