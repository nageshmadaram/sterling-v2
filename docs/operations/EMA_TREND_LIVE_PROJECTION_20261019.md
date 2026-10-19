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


## 19 October follow-on: durable live-campaign staging (not a release approval)

The offline integration now includes:

- `live_campaign_store.py` stores strictly validated, separate real-account
  `LiveCampaign` records with revision CAS and a unique open ownership claim
  for the exact account, exchange, symbol and product.
- Only an already-existing canonical `SUBMITTED` order ACK can bind a reserved
  campaign intent; it never creates or sends an order.
- `project_verified_order` requires **three matching persisted facts**: exact
  Kite journal order identity and pending projection version; independently
  verified cumulative broker order history; and the signed fill ledger's exact
  cumulative order quantity/value and reconciled net position.
- Campaign revision and `kite_order_intents.projection_pending` acknowledgement
  use one SQLite FULL-sync transaction. A failed proof rolls back both. Replays
  cannot increment confirmed quantity twice.
- `live_lifecycle.py` validates a broker-observed **active CNC GTT** and proposes
  wait, protective-stop verification/replacement, or broker-exit action for
  breached SL, target, time cutoff and operator flatten. These are **proposals
  only**, not unverified live SELL orders.
- `confirm_existing_cnc_stop` reads Kite GTT and orders with a real-client
  identity, then independently checks signed inventory and unresolved journal
  projections before recording active broker coverage. A triggered GTT, a
  wrong-volume SELL child or outstanding external SELL refuses promotion.
- Offline test modules: `test_ema_trend_live_campaign_store.py`,
  `test_ema_trend_live_lifecycle.py`. They include synthetic Kite HTTP ACK,
  signed ledger, partial/repeated fill, stale CAS, broker ambiguity, active and
  triggered stop observations and **no real order-write methods**.
- The separate existing virtual runtime tests failed when pytest was launched
  with global `STERLING_LIVE_EXECUTION=0`, since the canonical entry gate
  blocked virtual BUYs. The `virtual` pytest fixture now patches the gate only
  while running its concrete `SimulatedKiteClient`; production switch is
  unchanged. These failures must be rerun.

**Still P0, not implemented:** automatic live EMA BUY/SELL submission,
broker-side stop placement and replacement, MIS broker-native stop handling,
GTT child-fill reconciliation, protective-stop cleanup after partial/manual
exits, CNC holdings-sale authorization, authentic static-egress certification,
and real-account replay/restart/timeout acceptance. Do not use an authenticated
Kite login, a fake-Kite success, or the `PROTECTED` model phase alone as
release signoff. A GTT limit leg may remain unfilled after a price gap.

Run the local tests with the operator's global live-entry switch remaining off:
```bash
cd ~/Sterling/backend
STERLING_LIVE_EXECUTION=0 .venv/bin/python -m pytest -q \
  tests/engines/ema_trend/test_live_contracts.py \
  tests/services/test_ema_trend_live_order_facts.py \
  tests/services/test_ema_trend_live_fill_projection.py \
  tests/services/test_ema_trend_live_campaign_store.py \
  tests/services/test_ema_trend_live_lifecycle.py \
  tests/services/test_ema_trend_readiness.py \
  tests/services/test_ema_trend_signal_admission.py \
  tests/services/test_ema_trend_runtime.py
```

No automated test execution has been reported against this follow-on commit.


## Offline verification and exit-preflight continuation

At commit `5e7ad334`, the operator verified **145/145 EMA offline tests
passed** on Ubuntu with `STERLING_LIVE_EXECUTION=0`. No broker orders were sent
by these tests.

The subsequent fail-closed changes (not yet verified locally) are:

- `LiveCampaign.orphan_stop_ids` persists each still-possibly-live GTT ID
  after an exit makes the broker stop oversized. The ID survives restart,
  is never silently dropped on repeated order-history replay, and prevents
  claiming `PROTECTED` or `CLOSED` without explicit cleanup proof.
- `live_lifecycle.decide_exit` now refuses a second automatic reduction if
  an exit is still reserved/acknowledged/partial/unknown, or if an additional
  entry might still fill. It preserves manual reconciliation requirements.
- `live_exit_preflight.inspect_exit` performs **read-only Kite operations**:
  fresh positions, holdings, orders, and GTTs on an exact real-account client.
  It rejects ambiguous product ownership, overlapping settled/T1 holdings,
  pending orders/GTTs, orphan stops, unknown client identity, and stale
  campaign ownership. It returns `live_order_authorized=False` even when
  all the checks pass; it is an evidence report, not a trading capability.
- `test_ema_trend_live_exit_preflight.py` uses a fake Kite client with
  AsyncMock wire reads. All mutation methods fail the test if touched.
  Regression cases include disconnection, competing manual SELL, pending
  BUY, stop-trigger child, stale/inconsistent broker inventory and orphan GTT.

To rerun the selected EMA offline tests, add the new module to the earlier
145-case suite:

```bash
cd ~/Sterling/backend
STERLING_LIVE_EXECUTION=0 .venv/bin/python -m pytest -q \
    tests/engines/ema_trend/test_live_contracts.py \
    tests/services/test_ema_trend_live_order_facts.py \
    tests/services/test_ema_trend_live_fill_projection.py \
    tests/services/test_ema_trend_live_campaign_store.py \
    tests/services/test_ema_trend_live_lifecycle.py \
    tests/services/test_ema_trend_live_exit_preflight.py \
    tests/services/test_ema_trend_readiness.py \
    tests/services/test_ema_trend_signal_admission.py \
    tests/services/test_ema_trend_runtime.py
```

**Not implemented and NOT certified:** broker stop `place_gtt` /
`modify_gtt` / `delete_gtt` lifecycle for real EMA positions, safe
automatic SELL through the canonical execution service after stop-child
reconciliation, MIS exchange-native protective orders, account-wide broker
order ownership, production static-egress/Kite binding checks, and moving-market
acceptance. CI/CD remains out of scope until the end. Keep live entries OFF.


## 19 October: stop-command timeout/restart fence (offline-only)

The operator verified the prior selected EMA suite at `e28bdb5b`:
**162 passed, 0 failed, 5.64 seconds**, with live-entry switch OFF.

New **unverified** continuation:
- `live_protection_outbox.py` persists a unique CNC `PLACE` or
  `TIGHTEN` command per campaign, scoped to a real campaign's exact account,
  instrument and signed confirmed holdings. It enforces revisioned idempotency,
  strictly tighter TSL, and blocks when journal projection/unknown submissions
  are unresolved.
- `mark_outcome_unknown` durably marks a possibly-sent GTT mutation as
  `OUTCOME_UNKNOWN`. Restart/replay and a different request ID cannot cause
  a second attempt. In-process state alone is never relied upon.
- `readback_stop_command` can query Kite GTTs and orders using an exact
  real-account read client. A matching candidate GTT or an empty broker book
  is **not sufficient** to prove the original mutation's outcome. It never
  releases the fence or authorizes a retry.
- Existing `confirm_existing_cnc_stop` now checks the stop-command outbox
  inside its durable SQLite confirmation transaction. Any outstanding
  `RESERVED`/`OUTCOME_UNKNOWN` command prevents a false `PROTECTED` phase.
- `test_ema_trend_live_protection_outbox.py` uses isolated SQLite and
  fake Kite reads with all broker write methods forbidden. It covers persisted
  idempotency, crash-before-commit rollback, UNKNOWN recovery, a competing
  request key, changed holdings, pending journal projection, trailing stop
  non-weakening, missing GTT and stale broker order book.

**No live broker write path is enabled by this work.** Additional production
certification is needed to establish a broker-safe canonical GTT mutation
capability, reconcile ambiguous HTTP outcomes and fired LIMIT child orders,
verify delivery/CNC authorisation, and authorize automatic live SELLs without
overselling. No test assertion may itself unlock these gates.

Suggested next offline suite (including the new file):

```bash
cd ~/Sterling/backend
STERLING_LIVE_EXECUTION=0 .venv/bin/python -m pytest -q \
  tests/engines/ema_trend/test_live_contracts.py \
  tests/services/test_ema_trend_live_order_facts.py \
  tests/services/test_ema_trend_live_fill_projection.py \
  tests/services/test_ema_trend_live_campaign_store.py \
  tests/services/test_ema_trend_live_lifecycle.py \
  tests/services/test_ema_trend_live_exit_preflight.py \
  tests/services/test_ema_trend_live_protection_outbox.py \
  tests/services/test_ema_trend_readiness.py \
  tests/services/test_ema_trend_signal_admission.py \
  tests/services/test_ema_trend_runtime.py
```


## Latest offline gate: stop request staging after 179 green tests

The operator reran the earlier selected suite on commit `d8b47c84` and
reported **179 passed, 0 failed in 6.16 seconds**, with
`STERLING_LIVE_EXECUTION=0`. Main and dev were clean and synchronized.

Additional changes (not yet locally verified):
- `live_protection_outbox.reserve_stop_command` refuses a stop mutation if
  **any** entry or exit remains nonterminal, and also refuses canonical
  `SUBMITTED` and `PARTIAL` orders. A pending fill can change the number of
  shares requiring broker protection even when the last journal projection
  was acknowledged.
- `live_stop_draft.draft_stop_command` builds a deterministic, **non-executable**
  single CNC SELL GTT request using the existing Kite request builder. It
  requires a durable command reservation, unchanged owner/account/instrument,
  strict tightening for TSL, a positive tick-aligned bid and stop, and a stop
  strictly below the quote. It checks the resulting SELL leg, exact quantity,
  order type and limit price before returning a fingerprint.
- `StopDraft.broker_write_authorized` is permanently `False`. The preview
  contains **no** Kite client or order-write call. A caller-supplied bid
  is NOT broker-certified market evidence; production must revalidate
  authenticated current quotes, exposure and the canonical cross-process
  write lease immediately before a permitted order mutation.
- New offline test cases in `test_ema_trend_live_protection_outbox.py`
  exercise GTT placement/TSL drafts, unknown outcomes, quote/tick failures,
  pending order vetoes and draft determinism.

**Still blocked:** transmitting real stop placements/modifications, GTT
cancellation/child-fill reconciliation and automatic live SELL execution,
including CNC settlement authorization and MIS-native stops. The offline
request preview is preparation for fake-Kite write-path drills; it is not a
release certification or an order-send authorization.


## 20 October: durable broker GTT ACK and verified-stop settlement

The operator verified the earlier EMA offline suite at `c5a96be2`:
**194 passed, 0 failed in 5.69 seconds**, with the global live-entry
switch OFF and clean synchronized main/dev branches.

New continuation (not locally verified yet):
- `ema_live_stop_commands.broker_trigger_id` is added through a backward
  compatible SQLite schema migration. Existing unresolved commands and their
  idempotency keys survive.
- `record_unverified_broker_ack` can attach a numeric GTT ID only to a
  persistently `OUTCOME_UNKNOWN` command, transitioning it to
  `ACK_PENDING_VERIFICATION`. Same-ID replay is idempotent; conflicting IDs,
  second keys, forged IDs, and a TSL response referring to the wrong original
  GTT are rejected. The command **remains fenced**, not protected.
- `readback_stop_command` distinguishes ACKed GTT present/missing/multiple,
  but never authorizes a duplicate write just from that book inspection.
- `verify_acknowledged_stop` independently reads GTT detail, listed GTTs,
  order history, broker net positions, and holdings from a real-account
  `KiteClient`. It checks the active single CNC SELL LIMIT GTT's instrument,
  quantity, trigger, broker account, existing order conflicts and exact
  ownership. The signed fill ledger and canonical journal are checked again
  inside one FULL-sync transaction.
- Only when all read-only checks pass can the command move to `VERIFIED`
  and the EMA live campaign move to `PROTECTED` with the exact stop price
  in the **same SQLite commit**. A crash rolls back both transitions.
  A verified TSL advances the campaign's floor and cannot weaken it.
- New fake-Kite/SQLite tests cover placement and trailing ACKs, duplicate
  trigger IDs, stale broker quantities, disappeared GTT, broker disconnect,
  competing open orders, unsupported/stale stop price, and crash rollback.

**Important limits:** These APIs do not perform GTT `place`, `modify`,
`cancel` or SELL execution. They only accept **already observed broker
ACKs** from a later separately certified canonical adapter. The existence
of an active GTT does NOT guarantee the resulting LIMIT child will fill
after a gap. The account-wide live switch and EMA strategy release scope
remain unchanged and live entries must stay OFF.

Run `tests/services/test_ema_trend_live_protection_outbox.py` plus the
previous ten-file EMA offline suite to validate this continuation; test
completion has not yet been recorded for the latest commit.
