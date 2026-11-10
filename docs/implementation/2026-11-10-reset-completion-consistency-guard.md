# Reset completion proof and fail-closed checkpoint-consistency admission

Parent: draft PR #56 at `d23c577c6f0a73494a6a599c18a090a9d4e84982`. This change remains DRAFT, UNMERGED, UNDEPLOYED and NO-GO for live broker execution. Independent production release approvals: 0/12.

## Red-first defects

1. The matching reset generation token alone allowed `finish_simulation_reset(..., success=True)` to remove an ACTIVE fence even when external cleanup had confirmed only `journal_rows`, not `control_plane`. This bypassed the forensic checkpoint protocol's final stage.
2. An inconsistent checkpoint (`ACTIVE` while the durable reset fence marker was absent) could be ignored by `simulation_reset_fence_state` and tenant bootstrap, allowing ordinary virtual writer admission if a marker disappeared unexpectedly. A corrupt checkpoint was previously detected only by `/sim/reset-health`, not by all admission paths.

Both red-first tests failed on the exact draft PR #56 parent.

## Changes

- `finish_simulation_reset(success=True)` now refuses unless a validated, same-generation ACTIVE checkpoint exists at `control_plane`; the caller cannot infer successful reset from the token alone. The durable marker and checkpoint remain ACTIVE on refusal. The final success writes COMPLETE and removes the fence atomically within canonical SQLite.
- `simulation_reset_fence_state`, `assert_simulation_boot_permitted`, journal `_require_no_simulation_reset`, and `SimLedger._check_writer_fence` all read and validate marker/checkpoint consistency under their database access. Missing or malformed proof raises a fail-closed error.
- The test covering journal-only manual reset cleanup explicitly advances synthetic stages before exercising legitimate release; this keeps token-isolation coverage while obeying the new last-stage invariant. An existing corrupt-checkpoint test now asserts error and verifies the physical ACTIVE fence row remains, rather than expecting corrupted evidence to be reported as a readable ACTIVE state.
- Ordinary `SimLedger` and `order_journal` methods still have no public token recovery endpoint and must remain blocked when evidence is inconsistent. No stale ACTIVE unlock is added.

## Evidence and residual limitations

- Complete simulation suite: **620 passed**, 5 non-failing warnings.
- Gamma/canonical/journal scoped regression: **511 passed**.
- The patch does **not** provide distributed transactions, full host restore or independently certified cross-store consistency after crash; a checkpoint can lag committed work. It does not establish fresh broker/Kite GTT truth, authorize production SELL, fix parent PR #15 broad historical CI, or satisfy the 12 independent release controls.

**Production decision: NO-GO**.
