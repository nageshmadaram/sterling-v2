# Price Rules — live long index options

Price Rules is an independent fourth Sterling strategy, derived from the README in Live_PTrading-v2.14.zip. The supplied program is a paper practice pad; its executable is not executed, redistributed or linked. This integration uses Sterling's authenticated broker sessions and canonical order authority for real orders.

## Operator flow

1. Open Live → More → Price Rules. Broker identity, verified static egress, reconciliation, capital, daily loss and safety gates must permit entry.
2. Select an index, expiry and exact CE/PE contract from the chain, or enter the exact listed contract. Set 1–30 lots, positive stop points and optional target points (zero disables target).
3. Choose manual entry or a one-shot `>`, `>=`, `<` or `<=` condition on `EXCHANGE:INSTRUMENT`. Publish creates a durable DRAFT; it sends no order.
4. Review the bound account, exact contract, quantity and condition. Arm the displayed revision. A manual plan becomes executable on the next runtime check; a conditional plan waits for a fresh price. Plans expire after 60 minutes by default (API range 1–480 minutes).
5. The canonical service sends a BUY LIMIT at the fresh ask only after all entry gates pass. Confirmed fills enter the ledger; stops and optional targets are computed from the first confirmed fill. The row exposes broker protection and any unknown outcome.
6. Exit 25/50/75/100 percent of remaining owned quantity. Exits round to whole lots where possible, use at least one lot and never exceed confirmed holdings. Unfilled entry remainders require reconciliation first. An exact active owned stop must be confirmed cancelled before an ordinary SELL is sent.
7. Global square-off/halt cancels unsent plans and dispatches owned exits. Requests awaiting fills remain pending, not closed. Export history CSV shows confirmed quantities and gross realized P&L before fees.

Keys 1/2/3 select lots outside inputs. Escape requests full owned exits outside inputs. Keyboard actions ignore repeats, modifiers, pending commands and unreadable snapshots.

## Supported boundary

Long index CE/PE only, NFO/BFO, NRML, exact server instrument metadata. No naked short entry, averaging into an existing Price Rules contract, third-party activation, telemetry, custom paper funds, source-program executable parity, or profit-cap auto-flatten. Account daily-loss and exposure brakes remain canonical. CSV is gross; existing ledger/fee reconciliation remains the authority for account net performance. Price Rules is live-only and sends no live reads or commands from its Simulation panel.

## Durable artifacts and ownership

`system_config` namespace `price_rules:<uid>` stores plans, revisions, account/contract metadata, cumulative fills, exact entry/exit ids, GTT ids and durable mutation claims. Writes use SQLite BEGIN IMMEDIATE and synchronous FULL. Strict schema reads reject malformed/contradictory ownership. Entry and ordinary exits flow through canonical durable order intents; confirmed fills flow through the existing monotonic fill ledger and database inventory.

Protection uses typed protective broker capabilities with exact pinned account, contract, trigger levels, SELL product, quantity and order-price checks. OCO children are read by exact trigger/order ids. Shared protection admission, broker exposure accounting, ownership scans, account removal guards, attribution and recovery-clean checks include this strategy.

## Unknown outcome recovery

A lost entry reply never rearms or resends the plan. The runtime looks up the exact canonical intent and adopts its acknowledged order id when available. A lost exit reply similarly recovers only its exact durable intent. Durable preparation/protection claims fence concurrent mutations and survive crashes. Unknown GTT creation never adopts a similar trigger or creates a second stop. Triggered children must be terminal before residual protection is recreated. Broker deficits, foreign contracts, partial working children and unconfirmed stop cancellation block competing sells.

An interrupted exit preparation or unknown stop creation requires operator read-only reconciliation of exact broker ids and durable claims; this version does not offer a force-clear control. Use the broker's own interface for urgent intervention, preserve the evidence, and reconcile Sterling ownership before resuming. Do not remove claims or mark flat from a timeout or successful HTTP acknowledgement.

GTT stop children are LIMIT orders and can remain unfilled during gaps; an active trigger is not an executed exit. See [Kite GTT API](https://kite.trade/docs/connect/v3/gtt/) and [order API](https://kite.trade/docs/connect/v3/orders/).

## Authenticated API

- GET `/api/v1/price-rules`: own plans, revisions and entry authority.
- POST `/api/v1/price-rules/plans`: exact contract, lots, stop/target points, condition and optional valid_minutes.
- POST `/api/v1/price-rules/plans/{id}/command`: revision and arm/pause/cancel. Stale revisions refuse.
- POST `/api/v1/price-rules/positions/{id}/exit`: percent 25/50/75/100; server-owned account and quantity.

No endpoint accepts caller-selected tenant, account, raw order direction, stop id or claimed broker fill.

## Deployment

No executable or additional runtime dependency is required. Existing backend lifespan starts the strategy loop; the UI is lazy-loaded under More. Existing SQLite storage needs no schema migration. Deploy only after standard Sterling credential, broker binding, static egress and reconciliation checks. The currently running local backend has deliberately not been restarted and SAFE_MODE remains enabled; passing fake-broker tests does not certify a real broker session or execution.
