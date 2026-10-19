# EMA Trend — Broker Fill Projection Milestone (19 October 2026)

**Status: OFFLINE CONTRACT IMPLEMENTED; PRODUCTION LIVE ENTRY BLOCKED.**

Scope: EMA Trend only. Other live paths (SuperTrend, Gamma-Unwind, Snapback)
are unchanged. Source is tested against synthetic Kite order-history facts; no
broker order write is performed by this module.

## What was implemented

- `backend/app/services/ema_trend/live_fill_projection.py`: one pure
  `rehearse_verified_fill` transition from an existing strict `LiveCampaign`
  and one `LiveOrderFact`, keyed by a known canonical `BrokerIntent`.
- Fails closed on wrong account, exchange, symbol, product, side, quantity,
  broker order ID, unknown canonical intent, and crash-window submissions whose
  broker order ID is not durably bound.
- Uses **cumulative broker-confirmed fills**, not requested quantity or ACKs.
  Returns incremental fill quantity/value and is idempotent on repeated facts.
- Marks partial/live holdings `PROTECTION_REQUIRED` until a separately
  verified broker stop covers the **exact confirmed quantity**.
- An additional fill with insufficient GTT stop coverage exposes the uncovered
  quantity; it does not silently claim protection.
- A reduction that makes an old broker stop oversized marks an **unverified
  orphan stop** and returns `BROKER_STOP_CLEANUP`; it never cancels a stop by
  pretending it no longer exists.
- Zero arithmetic holding after SELL remains `RECOVERY_REQUIRED` until a
  real broker inventory/order/GTT reconciliation proves closure.
- Contradictory/noncanonical broker expiry or unknown status remains UNKNOWN.
- Unit drills in `backend/tests/services/test_ema_trend_live_fill_projection.py`
  cover these transitions, including partial fills and replay.

## Offline test

Run in a local checkout on the target commit:

```bash
cd ~/Sterling/backend
.venv/bin/python -m pytest -q \
  tests/engines/ema_trend/test_live_contracts.py \
  tests/services/test_ema_trend_live_order_facts.py \
  tests/services/test_ema_trend_live_fill_projection.py \
  tests/services/test_ema_trend_readiness.py \
  tests/services/test_ema_trend_runtime.py
```

**Tests have been committed, not yet run on the operator's computer.**

## Next work / P0 production acceptance

1. **Canonical authoritative write transaction:** persist each verified
   cumulative fill into the signed Kite journal and strategy-owned live
   campaign atomically, surviving process loss between each step. This pure
   rehearsal must never replace the authoritative journal.
2. **Live owned campaign storage:** bind owner UID, selected real Kite account,
   exchange-qualified instrument, exact product, order IDs, trade IDs and
   revision to an immutable persisted campaign; separate virtual and real
   accounts.
3. **Broker-side protection:** for CNC long shares, implement a verified GTT
   stop with correct child-order reconciliation, acknowledging that a triggered
   LIMIT may remain unfilled; for MIS, implement an actual compatible broker
   exit order, not GTT-based fictitious coverage.
4. **Safe first-entry admission:** check fresh Kite session, static-IP identity,
   broker inventory/holdings/orders/GTTs, account binding, positive available
   cash, approved allocation and all strategy risk gates before any write.
   No test-only flag may bypass these in production.
5. **Auto-entry adapter:** wire EMA Trend's signal, plan, revision, sizing and
   canonical entry through the real live execution service only after (1–4)
   pass fake-broker crash/restart and fault-injection tests. Never interpret a
   successful call as a fill.
6. **Trailing stop/target/exit manager:** never weaken stops; use broker
   acknowledgements/fills and revisioned state, prevent over-selling and
   races between GTT child fills, manual exits, and automated closures.
7. **Operator/Telegram/UI status:** expose raw reason codes, order ID,
   protection `UNKNOWN/PENDING/ACTIVE` (only when broker verified),
   confirmed owned vs requested quantity, target/TSL, STOP/EXIT events,
   deduplication, alert delivery and replay/restart facts.
8. **Certification:** verify disconnect/reconnect drills, stale quotes,
   partial order fill, ambiguous POST timeout, missed websocket updates,
   orphan GTT, blocked protection/stop child, mid-session restart, symbol
   expiry, holdings authorization, same-symbol other-strategy ownership,
   kill switch, and report on exact release SHA.

**Unchanged kill switches:** `app.services.ema_trend.execution.arm()`
still accepts `PAPER` only; `release_scope.may_originate` permits EMA only
for simulated tenants; `ema_trend.live_preflight.inspect()` returns
`LIVE_BLOCKED`. Do not change or override these until the P0 evidence passes.

No broker write should be attempted while the market is closed or the registered
egress IP is unverified; the offline fake Kite transport provides the first
acceptance gate without putting real capital at risk.
