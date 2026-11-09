# Gamma-Unwind recovery regression reconciliation — review only

**Source baseline:** parent draft PR #15, exact head `725f7ebbfd0ee7e5b52174375525a759e5696e23`.

**Release decision:** **NO-GO** for production, broker execution and deployment.
This document reports an isolated test-fixture reconciliation; it does not
certify Gamma-Unwind profitability, the canonical SELL lifecycle, or the
independent twelve-control production gate.

## Root-cause ledger

| Prior assertion | Observed current canonical requirement | Review-only disposition |
| --- | --- | --- |
| Synthetic live-position registry alone authorizes SELL | `reduction_inventory_refusal` demands scoped durable inventory before disarming GTT | Seed synthetic inventory *only* in a disposable test DB; add a test that zero canonical holding retains the stop and refuses SEND |
| Synthetic broker exposes `get_positions` alone | `verify_broker_reduction` also requires a fresh `get_positions_raw()` book at canonical submission | Implement fresh read in the synthetic double; do not bypass production proof |
| Legacy `place_order(symbol, side, qty)` signature | Canonical executor supplies Kite-compatible named `quantity`, `exchange`, `product`, `tag`, etc. | Support both representations in test double, checking exact signed closing side, quantity, exchange and symbol; reject malformed and oversized requests |
| Older Gamma exit tracking tag `gx` | Canonical order journal uses deterministic `ke` + hash tag | Assert canonical tag and broker 20-character maximum |
| A cancelled/UNKNOWN canonical send can be released after one empty order-book poll | Current `gamma_move_runner` intentionally retains the durable UNKNOWN fence; absence is not proof of non-delivery | Assert no duplicate SELL, no automatic GTT rearm and unresolved alert after grace |
| Flatten should immediately send 30 after a 20/50 partial fill | Second order tries the same immutable journal `signal_id` with a different quantity; `immutable_intent_conflict` is correctly rejected | Assert one broker send only and no oversell. **Still a functional blocker** for remaining-quantity exits |
| Gamma can sell when a different strategy's ledger replay makes canonical holding zero | One shared canonical inventory identity contradicts the Gamma strategy registry and synthetic broker book | Assert refusal, untouched broker orders and no fabricated P&L. **Still a migration/reconciliation blocker** |

## Test evidence from isolated worktree

- Baseline targeted `test_gamma_fired_child.py` + `test_gamma_recovery.py`:
  **21 failures / 32 passes** on untouched parent head.
- With the compatible synthetic fixture and updated safety invariants:
  **62 passes** across those same modules, including eight new negative
  transport-shape cases and a missing-canonical-inventory refusal.
- Full focused composite on the current uncommitted review candidate:
  **295 passed** in Python 3.12 across seven Gamma/canonical suites
  (`test_gamma_recovery`, `test_gamma_fired_child`, `test_gamma_move_live_audit`,
  `test_gamma_move_production`, `test_canonical_fill_reconciliation`,
  `test_canonical_strategy_reduction`, `test_canonical_execution_convergence`).
  This does not replace exact-commit GitHub Actions acceptance.
- No secret, broker session, real position, account, or live order used.
- This is local engineering evidence, not independent Actions sign-off.

## Separate P0 work, NOT fixed by test reconciliation

1. Design and prove a **distinct immutable reduction identity per confirmed
   remaining-quantity tranche**, tied to the same stable Gamma signal, shared
   inventory, exact broker order and journal projection version. Never retry
   UNKNOWN or still-open partial intents using a fresh identity. Avoid creating
   an alternate broker sender or weakening `immutable_intent_conflict`.
2. Reconcile verified partial fills into the canonical account inventory
   before permitting a subsequent reduction. Prove one consistent product,
   exchange, signed quantity, account owner, and no pending resting stop.
3. Preserve real-money entry global OFF, canonical `get_positions_raw()` proof
   before and just before a send, no-resend UNKNOWN journal fencing and
   independent broker-truth restart tests.
4. Restore an independently checked positive daily-loss-breaker scenario after
   a distinct strategy has previously closed the same symbol, using verified
   canonical acquisition/basis evidence (not simply seeding strategy state).
5. Parent PR #15 full CI remains red; other browser, Python and native-device
   acceptance may still fail. Do not treat passing focused synthetic tests as
   production readiness.

**Integration disposition:** review branch only, against PR #15. No parent
merge, deployment or live execution authorization; independent controls
remain **0/12 accepted**.
