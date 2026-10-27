# Snapback exit/stop reconciliation safety checkpoint — 27 October 2026

**Branch:** `dev` only. **Main:** not changed by this checkpoint.
**Decision:** new real-money entries OFF. NO certification of live execution or profitability.

## Source corrections committed

- Broker exit ACK binder rejects unknown/whitespace order IDs, fractional/Boolean
  persisted quantities, inconsistent working-order ID/requested-quantity pairs,
  wrong owner/account/position and over-remaining-unit exits.
- The exit manager validates quantity and working-order metadata before calling
  canonical broker submission; an unresolved prior exit prevents another send.
- After broker-terminal reconciliation and durable fill projection, the position's
  working order ID and requested quantity are both retired inside one SQLite
  mutation. A concurrently changed order ID triggers a reconciliation hold.
- Triggered broker stops no longer treat `COMPLETE` as authority to invent a
  missing `filled_quantity`, truncate fractional quantities, or accept impossible
  premium prices. Unknown/conflicting receipts halt new entries for reconciliation.
- Broker-stop booking will not erase an outstanding or orphaned canonical exit.
  A conflicting order detected inside the position transaction is also blocked.

## Evidence categories

**Verified in isolated Python execution:** the pure
`backend/app/services/snapback_exit_order_binding.py` source was reconstructed
byte-for-byte and its local `git hash-object` matched the GitHub blob
`593bbf7978058a7210000287c4fc655fc5db91f4`.
It compiled and 20 isolated behavioral checks passed. These checks did not
import the full Sterling application or contact Kite.

**Committed, not executed on this candidate:** expanded regression tests in
`backend/tests/unit/test_snapback_exit_order_binding.py` and
`backend/tests/unit/test_snapback_exit_resolution_boundary.py`, covering
malformed ACKs, partial-fill retirement, concurrent order drift, invalid
stop receipts, and stop/canonical exit overlap.

**Unknown:** full-app test result, live broker execution/fill/reconciliation,
restart recovery, current production host state, independent forward after-cost
returns and profitability. The last inspected GitHub Actions jobs on the prior
`db348c1f` head had `runner_id=0`, zero steps, and no executed test evidence.

## Exact-head offline acceptance (authorized host)

Inspect `git status` and preserve local changes before switching branches.
After a clean fast-forward to the intended `dev` SHA, with
`STERLING_LIVE_EXECUTION=false`, execute
`bash scripts/snapback_offline_acceptance.sh`.
Its source/test result applies **only to that exact SHA**. Independently run
the full relevant integration, restart, shortfall, stop-trigger and broker
fault-injection suites before any release promotion.

Do not turn on live entries, classify historical simulated P&L as real broker
profit, or merge `dev` to `main` solely because these patches exist.
