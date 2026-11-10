# Durable simulation-reset stage checkpoints — review-only recovery evidence

**Stacked parent:** draft PR #55 at immutable SHA `c62b794d4864a16c1579ae15df95593dbdd4b1b2`.
**Posture:** DRAFT / UNMERGED / UNDEPLOYED; all 12 independent production controls unapproved, live broker routing unauthorized and production NO_GO.

## Gap reproduced red-first

Before this patch the canonical journal durably stored an `ACTIVE` or `FAILED` simulation reset marker, but there was no evidence of which cross-store cleanup steps had *definitively completed before a process crash*. An operator could not tell whether virtual cash, canonical journal or an independent Snapback store had been cleared. A stale `ACTIVE` marker remained appropriately fail-closed but lacked granular diagnosis.

Six new red-first cases reproduced missing checkpoint APIs, missing health diagnostics, incorrect handling of corrupt state and process-crash visibility. A process-crash test now initializes its disposable canonical SQLite store before running, and teardown avoids leaking a monkeypatch into fixture cleanup.

## Implementation

- Introduce a *separate*, durable, per-tenant `simulation_reset_checkpoint_v1:sim:<owner>` row in the **canonical SQLite `system_config`**. Its internal JSON includes the same UUID token as the reset fence, an enumerated state and the *last contiguous confirmed* stage. The token is **never** returned from public diagnostics.
- Starting an explicit reset creates `ACTIVE/fenced` checkpoint atomically with the existing `ACTIVE` reset marker under canonical SQLite `BEGIN IMMEDIATE` and full-sync settings. A corrupted or inconsistent prior checkpoint refuses reset rather than silently clearing it.
- `checkpoint_simulation_reset(uid, token, stage)` only accepts a verified matching `ACTIVE` reset token and a known, non-regressing stage. It writes durable evidence **after** each cleanup action has returned. If an earlier step failed, later attempted cleanup does not advance the contiguous progress checkpoint.
- Finalization changes the checkpoint to `COMPLETE/complete` or `FAILED/<last_good_stage>` **inside the same SQLite transaction** that removes or fails the reset fence. A failed/abandoned in-progress reset is not silently considered successful.
- Owner-only `GET /api/v1/sim/reset-health` now includes `{checkpoint:{state,stage}}`. A corrupt checkpoint or disagreement between marker and checkpoint returns sanitized 503. A legacy ACTIVE marker with no checkpoint reports `{UNKNOWN,unknown}` and remains blocked; absence of both reports `{CLEAR,none}`.
- Existing simulation test fixtures explicitly delete their own disposable checkpoint rows. No broker order, execution-mode unlock, stale ACTIVE expiry, or cross-store rollback is added.

## Evidence

- Red-first six new cases reproduced missing implementation on parent.
- New tests now pass for success completion, a failed journal step, token/generation validation, monotonic stages, an independently terminated process (`os._exit(79)`), health token secrecy, corrupt checkpoint sanitization, late Snapback cleanup failure with subsequent explicit retry, and inconsistent missing-marker detection.
- Complete scoped simulation suite / Gamma suite, Python compilation, strict preproduction source inventory, and governance/inventory unit acceptance are required for this exact branch before publication. Record exact-commit GitHub workflow results only after they complete.

## Scope and limitations

1. This is **forensic checkpoint evidence**, not a distributed transaction. Checkpoints may be behind committed external work if the process dies between completion and checkpoint commit. They cannot certify the absence of outstanding broker-side orders or all in-memory background writes.
2. Neither `ACTIVE` nor `FAILED` is automatically unlocked. An ACTIVE marker after death always requires independent human diagnosis and a separately reviewed recovery procedure. A FAILED marker still permits a new explicit reset only after existing safety preflight, not an implicit resume of a previously crashed workflow.
3. The per-tenant marker/checkpoint resides in canonical SQLite. A privileged raw writer able to tamper with `system_config` or delete all evidence can bypass that protection. This change does not create adversarial database authentication.
4. This does not resolve full PR #15 CI, the production partial-remainder SELL adapter, authoritative fresh Kite/GTT reconciliation, UNKNOWN no-resend proof, OpenAlgo parity, full browser/mobile acceptance, certified backup/restore or the 12 independent release gates.

**RELEASE NO-GO** remains mandatory.
