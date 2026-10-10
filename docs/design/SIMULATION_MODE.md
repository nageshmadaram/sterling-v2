# Simulation mode and Trade Journal — design contract

Owner request: a **Simulation** mode that behaves exactly like AUTO — real-time signals, entries,
broker-style stops, trailing, exits — but with **virtual money and no real orders**, plus a
**Journal** tab with complete per-trade detail so the family can see what each strategy did,
which earns more, and why trades fail. Strategies: Supertrend, Snapback, Gamma Unwind only.

## Architecture: a virtual tenant

Every engine (Supertrend `kite_engine`, Gamma `gamma_move_runner`, Snapback dispatcher) already
iterates `accounts.all_accounts()` (active accounts) and runs per `acct.user_id` with
`client = await accounts.acquire_client(acct)`. State (positions, config, journal intents, daily
loss, signals, plans) is keyed by `uid` / `account_id`.

Simulation is therefore a **second tenant** that goes through the *same code paths*:

* `sim_uid = "sim:" + owner_uid` (e.g. `sim:default`) with ONE stored virtual account
  `SIM-<owner>` (`user_id = sim_uid`, `is_active`, always "connected", synthetic session).
* `accounts.build_client(account)` returns a `SimulatedKiteClient` for a sim account. It never
  holds real credentials.
* All three engines then scan the sim tenant on their own schedules with the sim tenant's own
  config (its own `auto_execute`, `stop_mode`, lots, risk…), place "orders" through the canonical
  execution service, get virtual fills, get virtual GTT stops, trail them, exit, book P&L — i.e.
  identical logic, isolated state, virtual money.
* Live and simulation run side by side. Per strategy the operator picks one mode:
  **MANUAL / SIMULATION / AUTO**. SIMULATION = sim tenant's strategy AUTO on and live AUTO off.

## Safety invariants (hard requirements, each needs a test)

1. `SimulatedKiteClient` can never reach a real broker write. It exposes an explicit **allowlist
   of read methods** delegated to the owner's real client (quotes, ltp, historical, instruments,
   depth/option chain, profile NOT needed). Every other attribute raises. Writes (place/modify/
   cancel order, GTT create/modify/delete, MF, alerts) act only on the in-memory/SQLite virtual
   ledger. If the owner's real session is unavailable, sim market-data reads fail closed (sim
   pauses; it does not invent prices).
2. A sim uid / sim account can never resolve to a live account (`accounts.get_active(sim_uid)`,
   `acquire_client`, `external_positions`, `live_runtime`, Square-Off/Kill switch paths). A live
   uid can never resolve to a sim account.
3. Live safety machinery is unchanged for live: `live_minimum`, external-exposure admission,
   live floor kill switch semantics stay live-only. A sim account is treated as non-live
   (`is_paper`-style seam = "no real money") so those do not apply to it, but the canonical
   service's journal, idempotency, risk approval, protective stop requirement, daily-loss halt
   (virtual) still apply to sim so behaviour matches AUTO.
4. Everything user-visible says **Simulation / virtual**, never "paper". Telegram alerts from the
   sim tenant are prefixed `🧪 SIMULATION` and routed to the owner chat; live alerts unchanged.
5. The global kill switch, safe-mode resume, broker login/logout, account management, live daily-
   loss limit routes are **not available in the Simulation view** (403) and sim actions never
   change global live state.

## Virtual exchange (SimulatedKiteClient / virtual ledger)

Persisted (SQLite, tables prefixed `sim_`): cash, orders, order history, trades/fills, positions
(net by symbol+product), GTTs, charges, equity snapshots. Default virtual capital ₹10,00,000
(configurable, reset action). Real-time, driven by live quotes from the owner's real client (cached
ticks when fresh, REST quote otherwise, shared rate limiter):

* MARKET/aggressive LIMIT fills at best ask (buy) / bid (sell) + configurable slippage ticks;
  resting LIMIT/SL/SL-M orders trigger on price crossing; partial fills optional (off by default).
* GTT single/OCO: evaluated on each quote pass; on trigger the child LIMIT order is placed and
  fills by the same rules (so a gap can leave it resting — same as Kite, and exercises the
  stale-child logic).
* Margin: option BUY = premium × qty; futures = 12% notional; reject on insufficient virtual cash.
* Charges (Zerodha-like) booked per trade so journal P&L is net and gross: brokerage ₹20/order cap,
  STT 0.1% on option sell premium (0.02% futures sell), exchange 0.03503% options, SEBI, stamp,
  18% GST on brokerage+exchange+SEBI.
* Read surfaces the engines call must exist with Kite-shaped payloads: orders, order history,
  trades, positions (raw/net/day), holdings (empty), margins/funds, GTT list/get, plus what the
  canonical path, protective_stop, gamma/snapback/kite_engine call (derive by `rg` on
  `client.` usage in those modules).
* Background `sim_runtime` task: during market hours every ~2 s evaluate resting orders/GTTs/
  position marks, write equity snapshots each minute; started from `main.py` lifespan.

## View scoping (UI sees the sim tenant with the existing components)

`get_current_user` honours request header `X-Sterling-View: simulation` (also `?view=simulation`
for stream/WebSocket URLs): the returned `UserContext.user_id` becomes `sim_uid(owner)`. The same
boards, positions, orders, signals and config screens then show the sim tenant. Only the owner
(authenticated admin/owner) can switch. Deny-listed in this view: kill switch, safe-mode resume,
broker/account/session/login routes, live daily-loss limit.

## Mode API (`/api/v1/sim/*`)

`GET /sim/status` (capital, cash, equity, today P&L, per-strategy mode, sim running),
`PUT /sim/strategy/{supertrend|snapback|gamma}/mode` body `{mode: MANUAL|SIMULATION|AUTO}`
(switching to SIMULATION copies the owner's current strategy config to the sim tenant if the sim
tenant has none, sets sim AUTO on, live AUTO off; AUTO sets live AUTO on, sim off; MANUAL both
off), `POST /sim/reset` (confirm; clears virtual ledger, positions, journal rows of the sim tenant),
`PUT /sim/capital`.

As built (`app/api/v1/endpoints/sim.py`, `app/services/simulation/mode.py`). Owner only (admin whose id is
`STERLING_OWNER_UID`, default `default`); callable from either view, the owner comes from the request.

* `GET /sim/status` -> `{virtual, mode, owner, uid, account_id, capital, cash, equity, available,
  blocked_margin, unrealised, realised_today, charges_today, net_realised_today, today_pnl, pnl_total,
  return_pct, open_positions, open_orders, active_gtts, trades_today, settings, message,
  runtime{running,last_pass_ms,passes,last_error,session_open}, price_feed{available,detail},
  strategies{supertrend|snapback|gamma: {strategy, mode, conflict, live{master,strategy,armed,enabled},
  simulation{master,strategy,armed,enabled,configured}}}}`. `mode` is MANUAL, SIMULATION or AUTO;
  `conflict` is true only if both tenants are armed (label then reads AUTO).
* `PUT /sim/strategy/{name}/mode` `{mode}` -> the strategy state above plus `changed`, `previous` and
  `strategies`. 400 `invalid_strategy|invalid_mode`; 403 `AUTO` from the Simulation view ("Not available in
  Simulation view": real money is armed from the live view only); 409 `{error:"auto_execute_blocked",
  reasons[]}` (the live AUTO readiness check, run against the live book for AUTO and the virtual book for
  SIMULATION); 503 `tenant_unavailable`; 500 `mode_change_failed` (the strategy is left OFF, never armed twice).
* `POST /sim/reset` `{confirm: true, capital?}` (only a JSON `true` confirms; else 422) -> `{ok, cleared[],
  failed{}, capital, cash, strategies, status}`.
* `PUT /sim/capital` `{capital}` (> 0) -> the status; the virtual daily-loss limits follow (2% hard, 1% soft).
* A strategy's mode is which tenant's AUTO is on: AUTO = live master+strategy flag on, SIMULATION = the
  virtual tenant's on, MANUAL = neither. Supertrend's flag is `supertrend_auto_execute`; Gamma's and
  Snapback's is `auto_execute` in their own configs; the master is the engine config's `auto_execute`.
  The virtual tenant is seeded with a copy of the owner's config the first time (nothing armed, stop mode
  `both` if it was `monitor`, a 2% daily-loss cap if the owner had none).

## Trade Journal (both LIVE and SIMULATION)

`app/services/trade_journal.py` — durable SQLite (`trade_journal_trades`, `trade_journal_events`,
`trade_journal_skips`). One row per trade (`trade_id`), keyed by mode (`LIVE`|`SIMULATION`),
strategy, symbol, underlying, option type, side, lot/qty, entry time/price, initial SL, target,
TSL history, exit time/price/reason (`SL`, `TSL`, `TARGET`, `EOD`, `TIME_STOP`, `SIGNAL_EXIT`,
`MANUAL`, `SQUARE_OFF`, `HEDGE`…), gross and net P&L, charges, R-multiple, hold minutes, MAE/MFE
if available, capital used, signal context at entry (the fields that made the strategy enter:
Supertrend alignment/ADX/regime/score; Gamma tier E1/E2 + gates + zone; Snapback lane + trigger +
filters), plain-English narrative generated from those fields, and an ordered event timeline
(signal, order submitted, filled, protection placed, TSL moved, target hit, exit submitted,
exit filled, failures). `trade_journal_skips` records signals the strategy declined or was blocked
on (gate/blocker code + plain-English reason) so "why didn't it trade" is answerable.

Hooks live at each strategy's existing entry-fill, protection, trail, exit-booking and refusal
points — identical code for live and sim; `mode` derives from `is_sim_uid(uid)`.

API `/api/v1/journal/*`: `trades` (filters: mode, strategy, date range, outcome, symbol; paging),
`trades/{id}` (detail + timeline), `summary` (per mode: trades, wins, win rate, gross/net P&L,
avg win/loss, profit factor, expectancy, best/worst, max drawdown, avg hold), `by-strategy`
(comparison table), `exit-reasons` (count + P&L per reason), `equity` (curve), `skips`,
`calendar` (daily P&L). All read-only; works for the sim and live tenants.

## Frontend

* Header **view switch**: `LIVE | SIMULATION`; Simulation shows a clear banner ("SIMULATION —
  virtual money ₹x, no real orders") and sends `X-Sterling-View: simulation` on every request.
* Per-strategy **mode selector** MANUAL / SIMULATION / AUTO on the trading controls.
* **Journal** tab: summary cards, strategy comparison, trades table (expandable timeline + plain-
  English story + entry context), exit-reason breakdown, equity curve, skipped-signals list, filters,
  LIVE/SIMULATION/ALL toggle. Mobile-friendly; shared formatters (`frontend/src/lib/format.ts`).

## Built: how the engines meet the virtual client (the lifecycle seam)

`client._is_paper` keeps meaning "no real money": `False` only for a real broker session, `True` for the
legacy instant-fill paper double AND for the virtual client. Real-money gates (live-minimum, external exposure,
family gate, the global kill switch, release scope) key on `is False` and never apply to Simulation.

The order lifecycle is a different question: a virtual order answers first and its fill arrives by postback a
moment later; a GTT fires by itself and its child fills later; protection is armed against the broker's own book.
That code asks `app.services.simulation.seam.is_live_like(client)` (real money OR virtual) instead, so the
virtual tenant runs the same durable-intent, postback-fill, protect-after-fill, GTT-fire attribution and
reconciliation code as live. `seam.is_instant_paper(client)` is the legacy double only.

* Supertrend: `kite_engine/{monitor,service,protection,protective_stop,execution_lifecycle}.py`.
* Gamma: `gamma_move_runner.py` (account match, broker-quantity reconcile, hold gate); `canonical_fill_reconciliation.py`.
* Snapback: `snapback_live_exits.py`, `snapback_live_wiring.py` (broker-book reconciliation), `snapback_readiness.py`
  (a virtual tenant has no bound broker account to reconcile).
* A virtual fill is reported by `VirtualExchange` after `postback_delay_s` through `default_postback_sink`
  (`ticker_manager.broadcast_order_update` for `sim:<owner>`), the path a live fill takes.
* **A simulation never engages the global kill switch.** Every engine site that can run for the virtual tenant
  engages it through `simulation.halt.engage_kill_switch(uid, reason)`: a live uid gets `live_safety.set_kill_switch`
  exactly as before, a simulation uid halts only its own control-plane row. A reset of the virtual books lifts it.
* The virtual tenant has no websocket: `ticker_manager._feed_simulation` hands the owner's ticks to the tenant's
  Supertrend monitor (trail breach, stop), Gamma's tick work and `sim_runtime.on_ticks` (a tick that crosses a
  trigger runs a pass at once).
* Prices: an ORDER reads a fresh book (`QuoteSource.books(fresh=True)`); evaluation passes share a 1 s cached read.
  No real price (the owner's session is gone) means nothing is placed and nothing fires.
* Offline proof: `backend/tests/e2e_live/test_sim_e2e.py` (entries, virtual GTT stops, stop-outs, a gap that leaves
  the child resting, trailing, the real Supertrend scan, restart, fail-closed, live/sim never mix) and
  `backend/tests/unit/simulation/`.
