# Gamma terminal-partial tranche readiness: source-only review

**Date:** 2026-11-10 local; **base:** draft PR #33 at SHA
`c88777a7168fabb7265ab162a7a9c3a094fc8f09`.

**Status: review-only. NO-GO for broker routing and production.**

## Problem and prior safety disposition

When an initial Gamma exit requests 50 contracts but a confirmed terminal
`CANCELLED` order fills 20, the canonical intent for the original 50 must
remain immutable. Reusing its signal identity for 30 triggers
`immutable_intent_conflict`. The existing PR #33 pre-disarm refusal now preserves
protection instead of cancelling a GTT for an inadmissible repeated order.

Separately, the Gamma position projection can show 30 while shared canonical
inventory still shows 50 (or another strategy's ledger makes it zero). This
contradiction cannot authorize a fresh SELL, even if broker position polling
also reports 30. Broker and ledger truth may be from different instants.

## Implemented now: passive proof classifier

`app.services.gamma_tranche_readiness.inspect_terminal_partial_exit` reads
only existing durable journal, inventory and signed-fill evidence. It never
reserves an intent, removes a GTT, connects to a broker, executes a strategy,
or returns a trading authorization. Its result always has
`execution_authorized=False`, including `ready_for_review=True`.

For a diagnostic `ready_for_review` state, the following must all agree:

1. Exact user/account/strategy/signal/exchange/symbol and `SELL` identity.
2. Parent exit intent is `CANCELLED` with a valid broker order ID, canonical
   `ke` tag, positive confirmed value, and `0 < filled < requested`.
3. Parent journal reconciliation is clear; strategy projection has explicitly
   acknowledged the exact journal version, with no pending projection.
4. No other unresolved journal intent exists for the same broker symbol and
   exchange under that user and account.
5. Strict shared account inventory exists, has no reconciliation flag, has a
   positive replay version, and its net quantity is **exactly** the remainder.
6. A signed execution increment for the *same broker order ID* and account,
   exchange, symbol, side and cumulative quantity/value is present in the
   existing fill ledger; broker SELL evidence cannot be synthesized from the
   strategy-local Gamma position.

A diagnostic positive result is still insufficient for a new order. It does
**not** check fresh authenticated broker inventory, resting GTT/child order
status, active account generation, concurrent process races, exact cash/fees,
or verified atomic intent reservation across account, journal and stop state.

## Negative cases covered

- No prior canonical intent, unclaimed/UNKNOWN state, or pending projection.
- Strategy-only manually seeded inventory with no replay provenance.
- Wrong owner, account, strategy, signal, symbol, exchange or product.
- Mismatched inventory net quantity and reconciliation-required inventory.
- Another unresolved account/symbol journal intent.
- Unavailable fill ledger, with no internals leaked into diagnostic output.
- A consistent synthetic terminal partial from signed ledger replay yields
  only `ready_for_review=True`, **never** order authority.

## Next safe implementation gate

Before any separate tranche can be submitted, require independently reviewed
new immutable parent/child lineage schema, unique tranche identity (not based
on current wall-clock), cumulative broker-trade projection and inventory
reconciliation, protection state proof, and an atomic/serializable reservation
protocol. Keep no-resend UNKNOWN and fail-closed broker reduction checks.

All operations here were offline/synthetic in a disposable SQLite database.
No production code path imports or invokes this diagnostic; no new HTTP or
order submission route was installed. All twelve independent production
controls remain **unapproved**.
