# Synthetic broker-exit and fired-GTT child test-contract repair

**Parent:** Sterling V2 draft PR #64, exact SHA `b1b12c506f9ed3a070beba7a510e9db8b5169f95`.
**Scope:** Two test modules, the scoped *offline review* CI workflow and this risk memo. No production execution, risk gate, broker transport, stop protection, journal, or ledger code is changed.

## Exact baseline and failure analysis

The two formerly live-shaped synthetic modules `test_execution_identity.py` and `test_fired_child_exits.py` reproduced **13 failed, 38 passed** on the parent. Their mocks preceded the migration to canonical inventory admission and the canonical `submit_reduction` transport, so positive broker-stop scenarios never reached the operation they claimed to verify. Simply removing the real `reduction_inventory_refusal` check would cancel protective stops on unverified holdings and is prohibited.

The first test-stage fix explicitly populated disposable canonical inventory and supplied a fake *final downstream* sender. It reduced failed tests from 13 to 4. Three failures were caused by the fake sender lacking the normal `INTENT_REDUCE` capability, which the real canonical service supplies.

The remaining partial protective-fill test revealed an important **fixture-evidence mismatch**: a bare `db.set_inventory(+50)` row does not establish signed opening-fill history. `fill_ledger.record(SELL, 20)` reconstructs inventory from immutable execution increments and therefore correctly projected -20 from a journal that contained only the child SELL. The local `OpenPosition` correctly showed 30, but the signed ledger did not contain the preceding BUY. This does **not** establish a production bug where an authenticated, fully journaled opening fill exists; nor does it certify production reconciliation when evidence is missing.

## Test-only implementation

1. The fired-child `_position` helper now seeds an immutable **synthetic entry execution fill** via the real `fill_ledger.record` API with the exact test account, owner, exchange, contract, order ID, quantity, price and lot size. A child's 20-contract SELL therefore leaves +30 in the same signed projection, without manually overriding the inventory result after the SELL.
2. Both test modules use explicit, isolated synthetic inventory evidence. The `reduction_inventory_refusal` admission check remains real and is asserted at the downstream seam; no protection gets disarmed when canonical inventory is missing. Two new negative tests cover that refusal (one per module).
3. The only mocked step is `canonical_strategy_reduction.submit_reduction`'s final synthetic transport. It checks the pending exit claim and `INTENT_REDUCE` capability, calls the test's fake broker recorder and translates fake success, definitive rejection and ambiguous timeout to `ExecutionResult`. The real canonical journal, execution service, fresh broker proof, UNKNOWN replay and resend restrictions remain in the separate dedicated suites and are NOT bypassed in production.
4. The partial protective-fill test now additionally asserts the signed ledger is +30 and not flagged for reconciliation after the verified synthetic child fill; duplicate/unknown/uncancelled stop cases remain intact.
5. The scoped Gamma/canonical review CI workflow now runs both entire broker-exit modules whenever those files or the workflow change, alongside actual canonical execution/journal tests.

## Evidence

- Red-first parent: **13 failures, 38 passes** across the two modules.
- Corrected modules: **53/53 passed** with two additional missing-inventory negative cases.
- Important live-safety properties remain exercised: stop cancellation precedes SELL, a child filling 20 permits at most a 30-quantity reduction, cancelled-child uncertainty prevents SELL, mismatched broker holdings preserve GTT, definitive refusal re-arms a stop, ambiguous lost acknowledgement retains UNKNOWN, no blind resend, and bounded forced-exit behavior in the fake monitor.
- Final full scoped Gamma/canonical and static inventory/governance acceptance must be recorded for this exact source before promoting the draft.

## Explicit limitations

This repair is a **synthetic test-contract update**, not completion of canonical partial-remainder SELL execution for authenticated production Kite accounts. It does not prove real Kite position identity, product/lot ownership, duplicate order-book absence, post-cancel fresh broker truth, stop GTT state or crash-safe multi-store recovery. A missing signed BUY remains a reconciliation blocker, not an invitation to synthesize a holding during runtime. Real broker routing stays disabled, all independent release controls remain 0/12, and the reviewed PR stays draft/unmerged/undeployed.

**PRODUCTION NO-GO.**
