# Durable simulation-reset fence for offline Gamma review evidence

**Parent:** Sterling V2 draft PR #50 at immutable SHA `e85d3b86d5fc16ea7f65a84736583399a5019765`.
**Scope:** simulation tenant only, review-only Gamma evidence and canonical SQLite journal. No live trading authorization, broker API, GTT modification, deployment or main merge. Independent controls: **0/12 accepted; production NO_GO**.

## Race reproduced

Prior to this change, simulation reset verified immutable Gamma children and review holds in one standalone SQLite preflight transaction, then disarmed strategies and reset virtual cash/trade history before the canonical journal purge began a second transaction. A separate process could insert an irreversible `REVIEW_HELD` checkpoint between those transactions. The journal purge would then fail closed, but earlier independently committed reset steps could have erased other proof or virtual account state. A source-level test demonstrated the missing atomic reset admission API; no broker account, live order or production database was used.

## Implementation and invariants

1. `order_journal.begin_simulation_reset(sim_uid)` validates the virtual UID; under SQLite `BEGIN IMMEDIATE` with FULL synchronous durability it checks all frozen child intents and any existing `REVIEW_HELD` checkpoint and writes the `simulation_reset_fence_v1:<sim_uid>` state in **the same transaction**. The state is a versioned key namespace with strict JSON `{state:ACTIVE|FAILED, token:<32 lowercase hex>}`. Missing means idle; malformed means fail closed.
2. Both offline Gamma review hold creation and non-submittable child reservation check this fence **inside their own SQLite BEGIN IMMEDIATE transactions**, after any read-only preview, before any durable insert or replay. SQLite serializes the competing fence and review writers, including across independent Python processes. A review hold committed first causes reset admission to refuse; a reset fenced first causes the review writer to refuse.
3. The canonical journal now refuses regular reserve, submit claim, transitions, acknowledgement, timeout, projection acknowledgement, broker observation and journal-fill recording for a fenced tenant. Journal cleanup requires the exact ACTIVE reset token and repeats the immutable-intent/hold check in the same write transaction; direct/unmatched-token cleanup is refused.
4. A successful reset releases the fence **only after** all requested cleanup and control-plane steps succeed. Any reported partial failure persists a durable `FAILED` fence and leaves the virtual control plane HALTED / RECOVERY_REQUIRED instead of asserting a clean reset. A later explicit confirmed reset may retry `FAILED` after revalidating absence of immutable evidence. An unexpected interruption similarly marks FAILED if possible. If process death or finalization failure leaves `ACTIVE`, it is **never automatically expired or silently cleared**: manual recovery and independent investigation are required.
5. `set_mode(..., SIMULATION)` refuses re-arming when the durable marker exists. Fencing is per-tenant, not global; unrelated tenants and live broker configuration are unchanged.

## Negative and recovery evidence

- Four initial new acceptance cases were red against the exact parent, lacking any durable begin/recovery fence. Their corrected forms pass, including an independent subprocess killed with `os._exit(79)` and a persistent ACTIVE marker observed after restart.
- Independent Python subprocess attempting `hold_terminal_child_review` while another process's reset marker was ACTIVE was refused before inserting a hold. A reset inserted between read-only Gamma preview and its durable writer transaction also caused refusal.
- Negative journal cases cover ordinary reservation, submission, cancellation/UNKNOWN transition, ACK, timeout, observation, projection and recorded fills; unauthorized/wrong reset token is refused. Corrupt marker data is fail-closed.
- HTTP tests verify known ACTIVE conflict returns sanitized 409 and corrupt marker returns sanitized 503 before virtual cash mutation. Simulation mode cannot re-arm after crash or incomplete reset. Failed cleanup and unexpected/finalization interruption retain FAILED/ACTIVE marker and recovery state until explicit intervention or allowed retry.
- On the implementation worktree, local Gamma/canonical/journal suite: **511 passed** in the final local rerun. Simulation mode/API/boot suite: **111 passed**, 5 non-failing warnings, in the final local rerun. Exact-SHA GitHub CI is separately required.
- Python syntax, strict source-only preproduction inventory, governance schema and 0/12 independent control checks have been run locally. These are scoped checks, not production authorizations.

## Explicitly unproved / remaining failure modes

- The durable fence applies to the **participating journal and offline Gamma review writers in the canonical SQLite store**. It does NOT serialize other databases, uncontrolled raw SQLite writes, background virtual-exchange writes, arbitrary in-memory caches, or an external broker.
- A process crash between non-atomic writes to different simulation stores may still leave those stores inconsistent. No distributed transaction, compensating rollback, full host restart/restore proof or completed recovery UX is claimed. A stale ACTIVE marker intentionally blocks new reset/review actions pending operator investigation.
- A FAILED fence can be retried after explicit reset confirmation, but this is not certified full cross-store recovery. Independent replay/restoration and reconciliation acceptance remain required.
- Full parent draft PR #15 backend/frontend/browser CI, executable production partial-remainder SELL, fresh broker/Kite/GTT truth, atomic stop handoff, UNKNOWN no-resend, host recovery and all twelve independent release gates remain open.

**DRAFT / UNMERGED / UNDEPLOYED / NO-GO live broker execution.**
