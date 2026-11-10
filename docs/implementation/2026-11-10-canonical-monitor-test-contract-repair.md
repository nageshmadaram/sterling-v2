# Canonical monitor production-audit test-contract repair (offline)

**Parent:** draft PR #57, SHA `7f0656d8cf5b2f7bd2aad26b863f569163b8305e`.
**Status:** DRAFT / UNMERGED / UNDEPLOYED. Live broker route unauthorized, independent production approvals 0/12, production NO-GO.

## Root cause and evidence

The older production-audit tests were written for an old `place_order_option` paper broker mock and no canonical inventory. Current `monitor._exit_position()` now correctly refuses reduction before GTT disarm if canonical `db.get_inventory_strict` evidence is missing, and real transport is only reachable via `canonical_strategy_reduction.submit_reduction`. The old test fixtures therefore returned refusal too early, masking their intended exit monitoring, ACK-vs-fill, wrong-direction and lost-reply assertions.

Eight cases in `test_production_audit.py` failed against the latest stacked PR #57 tree; this was not evidence that the canonical sender re-sent an UNKNOWN order. Canonical integrated journal/identity/transport and restart UNKNOWN tests in `test_canonical_strategy_reduction.py` passed separately.

## Fix

- Add a *test-local* paper-only fixture with disposable SQLite `db.init()`, an explicit canonical inventory row for the exact synthetic account + UID + contract + exchange and the required net side/quantity.
- Keep the real `reduction_inventory_refusal` check enabled before the old monitor path reaches downstream order transport. The controlled fake `submit_reduction` checks this same real gate and uses only the synthetic `place_order_option` fake, returning a canonical-shaped `ExecutionResult` on success and an ambiguous UNKNOWN on simulated lost ACK. It is a monitor unit test seam, **not** a replacement for the actual canonical broker submit path.
- Keep real canonical submission tests separately executed in the same CI job for idempotent reservation, no-resend after lost reply, and SQLite backup/restart recovery.
- A new negative test requires missing inventory to leave the position OPEN, with no exit claim or synthetic broker order, rather than making entry/stop protection permissive.
- Include the updated monitor test module in the scoped offline Gamma CI path filter and Python test step.

## Acceptance and limits

- Before fixture repair: **8 failed**, 316 passed across the five focused synthetic broker test modules.
- After repair: **325 passed** across those same five modules (includes the added missing-inventory refusal). Target module itself: 52 passed.
- All tests are offline/synthetic. No authenticated broker connection, no actual stop GTT modifications, no live positions. Tests do not establish production SELL handoff or independent production authorization.
- The historical PR #15 head remains behind and its CI remains failed. Current review-only changes are stacked; they are not merged into that historical PR, main or production.

**NO-GO until independently approved.**
