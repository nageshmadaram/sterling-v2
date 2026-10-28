# Sterling V2 — Phase 2A: Normalized Kite read service

**Status:** incremental, read-only contract; dependent on registry-foundation PR #1 and safety PR #2. No live or production certification.

This implementation extends Sterling's *existing* `BaseExchangeAdapter`, `AuthenticatedExchangeAdapter`, `TradingExchangeAdapter`, `KiteClient`, `adapter_factory`, and registry instead of replacing their architecture. OpenAlgo is an architectural reference for separation and normalization only. No OpenAlgo UI, route, trading logic or branding was copied.

## What is implemented

- `app.schemas.broker_read`: frozen, broker-independent response records for account, funds, positions, holdings, order/fill observations, quotes, depth, candles, option chain and connection health.
- `app.services.exchanges.normalized_read.NormalizedKiteReader`: *observation-only* wrapper that delegates to the current Kite adapter's existing read methods. The wrapper has no place/modify/cancel/flatten/submit functions and no approval or execution authority.
- `create_normalized_kite_reader(config)` uses Sterling's existing `adapter_factory.create_account_adapter`, not a parallel registry.
- Kite exchange-local timestamps without offsets are explicitly interpreted in IST, then normalized to UTC. A missing quote timestamp gives `quality=unknown` and `stale=true`; stale data never becomes fresh merely by being fetched.
- Generic data errors are sanitized to stable `BrokerReadFailure.code` values, with no raw broker exception message in the client-facing error.
- Existing candle sourcing can involve fallback inside the existing adapter. Such candles are attributed to `existing_market_adapter` rather than incorrectly being labelled Kite.
- Simulation health is marked `simulation, authenticated=false`, never a successful live broker session.
- Synthetic provider tests assert data mapping, UTC timestamps, missing/stale input, options, factory re-use, no order-writing surface and error redaction.

## Safety boundary

Nothing here is wired to a live order path, new API route, scheduler, strategy engine or existing frontend. The underlying KiteClient remains order-capable for **existing** Sterling execution code, but it is private inside this read facade. The facade is an interface boundary, not a Python security sandbox. All broker writes must remain exclusively under Sterling's existing `CanonicalExecutionService` and risk approval. Do **not** infer live authorization from market-data availability or the health endpoint.

## Deliberately pending

- Complete symbol/exchange reference-data mapping, full historical-data completeness, clock-sync probes and alternative feed failover belong in Phase 3.
- Per-source quote subscriptions, OI/Greeks freshness metadata and exchange-specific instrument migration are not included.
- Hard enforcement preventing legacy strategies importing raw KiteClient, as well as all order paths migrated through a single execution authority, needs a separate repository-wide call-graph and risk audit.
- Real Kite session / TrueData acceptance, PostgreSQL production migration, restored deployment and phone/operator UI checks are not certified here.

## Validation and rollout

```bash
cd backend
python -m pytest -q tests/test_broker_normalized_read.py
```

No database migration is required for this read-only slice. Release only after the separate registry PRs satisfy their CI and review gates; full CI and broker-device tests still block production rollout.
