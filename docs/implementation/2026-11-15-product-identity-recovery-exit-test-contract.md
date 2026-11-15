# Synthetic product identity after journal recovery — exit transport test contract

**Parent:** draft Sterling V2 PR #65 at immutable SHA `30cbf0b8e5fe3b4ef77b200cb0af23e34023bf72`.
**Classification:** review-only tests, offline CI coverage and this memo; **no production source changed**.

## Historical/reproduced failure

Original PR #15 full backend CI reported three failures for `test_journal_product_survives_recovery_reload_protection_and_exit` parametrized across `MIS`, `NRML`, and `CNC`. On the current draft stack all three FAILED while the remaining 17 product-persistence cases passed. Actual recovery projected the proper `product`, quantity, durable registry, and **signed entry inventory (+50) with no reconciliation flag** for each product. In the old tests, the fake Kite client implemented `place_order_option`, but the canonical exit sender now uses a more restrictive authenticated `CanonicalExecutionService` pathway. These tests concern the product field surviving recovery and stop/exit sequencing, not authorization of an uncredentialed synthetic broker transport.

## Test-only remediation

- Use a fixture **only in the positive recovery/product tests** that replaces the final `canonical_strategy_reduction.submit_reduction` transport with a controlled synthetic recorder, leaving actual `monitor._exit_position`, GTT handling, holdings matching and `reduction_inventory_refusal` in place.
- Before sending, the recorder checks the exact owner/account's canonical inventory quantity/exchange, an in-flight exit claim with immutable tag and `INTENT_REDUCE` capability; its only effect is recording an order with the test fake's `place_order_option`. The real order journal and live broker adapter are **not** exercised through this mock and remain covered by other dedicated canonical tests.
- Positive cases explicitly assert signed opening inventory +50 and no reconciliation flag, plus unchanged product on registry reload, GTT operations (none for MIS), and outbound fake transport product. Acknowledgement must not be treated as a confirmed exit fill.
- All pre-existing unknown, cross-product and invalid broker-mode negative cases retain their **real, unmocked** authorization code. New negative case proves the wrong broker product, even for the correct symbol and account, cannot cancel GTT or send a reduction after recovery.
- Include the complete product-persistence module in the existing scoped Gamma/canonical offline CI path filter and required test step.

## Evidence

- Parent: 3 failed, 17 passed in `test_product_persistence.py`.
- Repaired module including one added negative case: **21/21 PASS**.
- Product-provenance and broker-identity failure gates retained; no live execution path is enabled or exercised.
- Integrated Gamma/canonical/regression, strict source inventory, governance and GitHub exact-SHA checks must be recorded independently before asserting their status.

## Release boundary

Passing these synthetic product tests does NOT authorize `CNC` for NFO or prove any particular broker product supported on a real Kite account; it only verifies the persistence and propagation of the product identity through the local application. Exact real broker contract/product validity, live position evidence, protective GTT state, partial-remainder SELL, UNKNOWN resends, cross-store recovery and 12 independent production approvals remain outstanding.

**DRAFT / UNMERGED / UNDEPLOYED / SHADOW/SAFE_MODE / LIVE BROKER ROUTING BLOCKED / PRODUCTION NO-GO.**
