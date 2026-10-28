# Sterling V2 — Phase 3 observational market-data reliability

**Status: draft, not deployed.** Builds on unmerged PRs #1–#4. The code is
new, opt-in, and read-only; existing strategy, Kite routing, execution, risk,
frontend and production deployment are untouched.

## Canonical contract and execution boundary

- `app.schemas.market_reliability` contains frozen UTC-aware, finite-Decimal
  records: `InstrumentKey`, `Quote`, `Depth`, `Candle`, `HistoryQuery`,
  `HistoryWindow`, `OptionContract`, `OptionChain`, `Quality`,
  `QualityEvent`, and `MarketSnapshot`.
- Quote/depth/candle/option validation rejects mismatched instruments,
  implausible/non-finite prices, negative volumes and inconsistent OHLC.
- Snapshot and quality models hardcode `execution_authorized=False`;
  these are **observations, not risk approval, executable signals or a
  new order authority**. No code is wired into existing strategy admissions.
- No market-data response is a permission to place or cancel an order.
  Future execution-side gating must be independently enforced and certified.

## Provider resolution and deterministic quality

`MarketDataResolver` accepts explicitly supplied read-only sources in this
priority order:

1. `broker`: Phase 2A `NormalizedKiteReader` via `BrokerQuoteSource`.
2. `alternate`: `TrueDataAlternateQuoteSource`, using the already existing
   TrueData historical/tick client, with an explicit per-exchange symbol map.
3. `lake`: historical close from the opt-in `LocalHistoricalLake` store.
4. `previous`: cached `PreviousQuoteSource` in memory.

Each source is evaluated in order. The **first valid fresh, fully qualified
realtime source** is selected; a failed source or missing instrument is tried
against the next provider. If none qualifies as good, the highest-scoring
degraded quote may be returned *with a degraded status*, its source and issues.
If every source is missing or malformed, the result is explicitly `missing`
and contains no quote. The resolver does **not** silently reclassify a lake or
previous snapshot as live, and never forwards raw provider exceptions.

Quality starts at 100: unknown timestamps and future timestamp errors are
penalized 70; stale values 60; alternate provenance 10; historical provenance
40; previous snapshot 50; missing required depth 15; incomplete required
depth 20. Good realtime quality requires score >=85, a defensible timestamp,
and (when requested) complete depth. This quality is **not an execution gate**;
future strategy/risk gate integration requires its own review.

`MarketDataResolver.provider_health()` performs explicit read-only connectivity probes when supported; it reports `unknown` for unverified providers and `simulation` for paper mode, never authenticated real health. `QualityMonitor.health()` provides an internal read-only status summary;
`QualityEvent` contains UTC time, exchange/symbol, issue code, source and
score, bounded to 500 recent events and a structured logger. The service does
not create an unauthenticated HTTP endpoint or UI widget. Production monitoring
must add authenticated operator display and a durable event sink separately.

## Historical retrieval and regime

- `TrueDataHistoricalSource` uses the existing `get_bars` method; its
  explicitly documented interval set is checked. Naive TrueData bar times are
  interpreted as **IST** and normalized to UTC; other canonical inputs require
  explicit timezone.
- Queries are bounded, include exchange/symbol, timeframe and UTC range.
  `normalize_bars` rejects malformed/duplicate bars and
  `history_window` computes missing expected intervals (with coverage %).
- `aggregate_candles` groups complete UTC-aligned input bars only; incomplete
  periods are omitted, not fabricated.
- `LocalHistoricalLake` is an **opt-in isolated JSON cache**, atomically
  written under a caller-designated private directory, with hashed filenames.
  It does not write into or migrate Sterling's existing `kitelake` parquet
  store, nor automatically mount external drives. File access in its async
  lake quote wrapper is offloaded from the event loop.
- Gap detection is calendar-neutral, so a query containing market closures
  or overnight periods is conservatively marked incomplete. Market-calendar
  awareness and certified data licensing are future work.
- `classify_regime` uses deterministic last-eight-bar price span, directional
  movement, volume and optional spread to tag trending, ranging, volatile,
  low liquidity or unknown. It does **not** generate orders or automatically
  change existing strategy conditions.
- Option-chain normalization records expiry as a **UTC date marker**, not an
  actual exchange settlement timestamp; absent observation time yields
  degraded state. Greek/IV data are not invented.

## Unresolved assumptions and release blockers

- Real Kite/TrueData account sessions, broker quotas, TrueData exchange-symbol
  entitlement and provider freshness semantics are not certified by synthetic
  tests.
- TrueData symbol translation requires reviewed, explicit mapping, not a
  guessed exchange prefix. Invalid or unknown symbols must fail missing.
- Data-lake JSON cache is a **separate opt-in scaffold**; production historical
  lake manifest and authorized disk path integration remain pending.
- The existing legacy direct broker read/write paths from Phase 2B remain
  unchanged, including 30 high-risk legacy write findings and further raw
  provider exceptions. This slice does not eliminate them or certify their
  current risk gates.
- No Alembic migration is required; previously verified immutable
  registry/audit version gates must remain green. Privileged DB INSERT/DDL
  bypass and full app regressions are still release blockers.
- Full production backup/restore, tenant isolation, real-device latency,
  frontend operator UX and moving-market acceptance are **not verified**.

## Targeted validation

```bash
cd backend
python -m pytest -q tests/test_market_data_reliability.py \
  tests/test_market_data_history_reliability.py \
  tests/test_broker_normalized_read.py \
  tests/test_broker_read_contract_edges.py \
  tests/test_broker_access_policy.py \
  tests/test_registry_signed_auth_contract.py \
  tests/test_platform_strategy_registry.py \
  tests/test_platform_strategy_registry_safety.py
```

GitHub Actions also re-runs the pinned Phase 2B direct-access inventory with
`new_unreviewed=0`, and checks the new modules for order mutation methods.

**Stop here.** No Phase 4 risk-engine work, no live trading enable, no direct
broker write or order execution API, no PR merge or deployment activation.
