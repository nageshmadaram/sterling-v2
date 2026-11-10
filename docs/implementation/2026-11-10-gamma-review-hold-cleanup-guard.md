# Gamma REVIEW_HELD checkpoint cleanup protection

**Review parent:** draft PR #49, `c40852767e53ea3eff8d2546aa2a121ec32124b6`.
**Scope:** immutable offline review evidence, not a production order protocol.
**Release:** DRAFT / UNMERGED / UNDEPLOYED / NO-GO for all live broker execution; independently approved controls **0/12**.

## Defect confirmed

After the parent terminal partial SELL was proven, the offline Gamma review helper can commit an irreversible `gamma_terminal_review_holds` checkpoint before creating its canonical non-submittable child. This is deliberate crash recovery behavior. The journal cleanup safeguard from PRs #47-#49 inspected child `kite_order_intents` only; with no child yet, it allowed `clear_for_tests(uid)` to erase the parent intent and broker observation evidence while leaving the review hold stranded. The simulation reset preflight also reported success for this case.

Two red-first cases reproduced the missing refusal on PR #49: preflight and direct journal cleanup both admitted a tenant holding only the checkpoint. One unrelated-tenant cleanup regression already passed.

## Minimal fail-closed correction

- `order_journal._assert_tenant_cleanup_safe` now checks both immutable marked child intents and existing `gamma_terminal_review_holds` rows scoped to that tenant.
- The optional offline-review table is inspected only when present. If its schema is present but unreadable, propagate the database error; do not assume the evidence is absent.
- Both the advisory simulation preflight and durable journal purge already call the same helper under SQLite `BEGIN IMMEDIATE`, closing the omission at their respective transaction boundaries.
- `mode.reset_simulation` maps the new `gamma_offline_review_hold_immutable` refusal to the same sanitized 409 `immutable_review_evidence` response before cash/strategy/control-plane writes.
- Ordinary unrelated-tenant journal cleanup remains permitted; no new broker order path, production schema expansion or live execution flag is added.

## Verified behavior and limitations

- Two new negative cases initially failed on the exact PR #49 source and now pass.
- Additional regressions cover tenant isolation, a review hold appearing after advisory preflight but before journal purge, and refusal on an unreadable existing review-hold schema.
- HTTP simulation reset returns 409 on the hold-only refusal without changing virtual cash or the armed strategy mode.
- On the branch, final local offline Gamma/canonical/journal suite **499 tests passed**, including the final two boundary cases; simulation mode/API/boot **104 tests passed**, 5 non-failing warnings. Exact-SHA GitHub workflow checks are separately required for remote evidence.

**Explicit residual defect:** The preflight-to-purge interval still permits a separate process to install a new hold after other simulation subsystems begin resetting. The journal purge then refuses under its own transaction, but earlier non-journal steps may have committed. This patch does NOT provide cross-process reset serialization, cross-store atomicity, crash-safe reset recovery or broker-state consistency. Those require a separate durable per-tenant reset fence and independently reviewed rollback/recovery protocol before production promotion.

All genuine partial-remainder SELL, authenticated fresh broker inventory, protective GTT truth, stop-handoff, UNKNOWN no-resend, parent PR #15 full CI and 12 independent approval requirements remain open. No real broker credentials, market orders or production databases are touched.
