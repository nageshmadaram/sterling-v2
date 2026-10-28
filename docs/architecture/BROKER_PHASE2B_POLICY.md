# Phase 2B — Broker access boundary and provider contract audit

**Scope:** Sterling V2, dependent on foundation PRs #1, #2, and read normalization PR #3. OpenAlgo remains an architecture reference only. This is not a release or live broker integration.

## Rules and enforcement

- The approved read facade is backend/app/services/exchanges/normalized_read.py, reusing the existing Kite transport (backend/app/services/exchanges/kite/) and adapter factory. It has no method to place, modify, cancel, flatten, or submit an order.
- The existing CanonicalExecutionService, execution router, strategy logic and fail-closed risk controls are not altered. Read-only market observations do not authorize live trading.
- The Python AST inventory scans backend/app/, backend/study/, backend/scripts/ and kitelake/ (not tests). Each recognized method call includes file, function, line, method, classification and risk. Raw vendor URLs and Kite SDK imports/constructors are flagged.
- The guard compares per-call expression fingerprints against the pinned validated Phase 2A commit 89eda42aff0f687ac7cadcdead3cc3d9f598c797. New direct broker reads/writes outside an approved adapter/service boundary fail CI. New raw SDK imports, provider calls or direct registry SQL writes also fail. It includes synthetic negative control tests.
- Existing direct-access sites are explicitly tracked as legacy exceptions requiring review, not automatically approved. A pinned baseline does not waive their risk. They require separate narrow migration PRs with risk and strategy lifecycle tests.

## Initially reviewed examples

These are grounded example locations, not a complete inventory. The CI artifact broker-access-inventory contains the full detected inventory.

| File | Function | Pattern | Assessment |
|---|---|---|---|
| backend/app/services/exchanges/kite/client.py | KiteClient.get_quote | Kite provider request | approved provider transport |
| backend/app/services/exchanges/normalized_read.py | NormalizedKiteReader.quotes | get_quote | approved normalized read boundary |
| backend/app/services/snapback.py | _quote_for | client.get_quote | legacy read exception; freshness review |
| backend/app/services/snapback.py | process_prospective_pending_entries | client.get_quote | legacy execution-adjacent read exception |
| backend/app/services/kite_engine/service.py | _resolve_premium_stop | client.get_ltp | execution-adjacent read exception; high priority |
| backend/app/services/kite_engine/service.py | _reconcile_pending_positions | client.get_orders | legacy reconciliation read exception |
| backend/app/api/v1/endpoints/kite.py | quote | raw c.get_quote forwarded | legacy route exception |
| backend/app/api/v1/endpoints/kite.py | orders | raw c.get_orders forwarded | legacy route exception |
| backend/app/services/providers/kite/diagnostics.py | verify_kite_api_reachability | direct provider URL / httpx | unknown, requires review |
| backend/app/agents/broker_agent.py | place_order | adapter order submit | legacy broker write; high risk |
| backend/app/services/execution/order_router.py | _submit_live | adapter order submit | existing execution authority; risk-gate review |
| backend/app/services/simulation/sim_client.py | place_order | virtual exchange order | simulation exception, review isolation |
| kitelake/pipeline.py | kiteconnect | SDK import | offline ingestion, review credential/network isolation |

Other direct Kite reads/writes exist across EMA Trend, Gamma, Snapback, intraday, strategy, API and execution modules. They are not rewritten in Phase 2B. The generated report is the exhaustive inventory **of AST-detectable patterns under the scanner's declared scope**, not proof of complete runtime isolation.

## Contract and negative cases

- Account, funds, positions, holdings, order book, trade book, quote, depth, candle, option chain and health records retain the Phase 2A observation contract.
- Exchange-prefix validation and an explicit exchange field on quotes and depth.
- Zero, negative, NaN and infinite last prices and malformed depth levels are rejected.
- Unsupported option rights are rejected. Unknown broker order status becomes UNKNOWN, never filled.
- Missing quote rows refuse the batch. Unknown or stale timestamps never become fresh merely by fetch.
- Unauthorized/invalid provider responses yield sanitized error codes, not raw token-bearing messages.
- The existing KiteClient read methods are exercised through a synthetic async transport without external network calls.

## Remaining exceptions, policy limits, and gates

The AST gate cannot prove absence of dynamic getattr, aliasing, runtime monkey-patching, all TS/JS or SQL execution paths, or indirect SDK imports. Source expression fingerprint matching is a regression guard, not a runtime authorization boundary. Legacy execution paths are still order-capable; this slice does not migrate them through risk gates. Baseline exceptions need owner review with risk-flow evidence before release.

The targeted SQLite and PostgreSQL registry foundation checks passed earlier, but full repository CI is failing independently and privileged DB INSERT/DDL bypass, operator provisioning, production restore, tenant permissions and real Kite/TrueData acceptance remain unverified. No DB migration, API, UI, order authority, live toggle or deployment change is introduced here.

CI workflow .github/workflows/broker-phase2b-contract.yml runs the audited access inventory, uploads JSON/Markdown findings, checks for newly introduced violations and reruns provider/registry tests. Do not merge/deploy or proceed to Phase 3 until all release gates pass.
