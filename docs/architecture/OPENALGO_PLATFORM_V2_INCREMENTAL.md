# Sterling V2 / OpenAlgo-style platform — incremental implementation register

This is **Sterling V2**, `nageshmadaram/sterling-v2`, not the independent
`nageshmadaram/sterling` repository. OpenAlgo is a reference for API and
product structure, not permission to replace Sterling execution/risk systems.

## Source-verified inventory

- `exchanges/kite/accounts.py` owns account-scoped broker sessions and the
  isolated virtual Simulation account. Do not create another broker client.
- `exchanges/kite/ticker_manager.py` owns the existing live ticker, token
  ownership registry and WebSocket fan-out. Do not subscribe per surface.
- `execution_service.py`, `canonical_fill_reconciliation.py`, and the
  durable `kite_engine/order_journal.py` remain the execution/fill source of
  truth. Incomplete exits and ambiguous broker replies remain blockers.
- `/api/v1/compat/openalgo/orderstatus` exists on PR #15 and consults only
  the authenticated principal's canonical journal state. It is not an OpenAlgo
  API-key substitute, broker poll, live order, or full API equivalence.
- `upgrade/api_inventory.py` reports static route deltas. It must not
  interpret a matching path name as equivalent semantics.
- `upgrade/sterling_openalgo.py` has read-only inventory and copy-only
  SQLite preflight. Full operational PostgreSQL migration is explicitly NO-GO.
- The six new UI paths are not global workspaces, even when functionality
  (charts, tools, strategies) exists within the Kite workspace.

## This slice

`GET /api/v1/platform/surfaces` is JWT-authenticated, Kite-feature-gated,
read-only discovery. The `existing_partial` state refers only to known
mounted API paths; `planned` paths are metadata, not routes.

The `canonical_services` mapping is a documentation/contract pointer to
already existing modules. It does **not** proxy, instantiate, cache, reset,
reconnect or enable them. These source paths must be audited and proven
with account/tenant/reconnect subscription tests before a dynamic UI uses them.

No new WebSocket, broker write, order intent, strategy worker, Python process,
Flow graph executor, keyboard trade action, risk rule or credential is created.
There is no hosted-code execution sandbox in this slice.

## Acceptance sequence

| Phase | Task | Necessary evidence |
|---|---|---|
| 0 | Restore exact-HEAD CI; negative broker-fill tests | Product Integrity, Snapback and full regression PASS |
| 1 | Inspect account/session and ticker ownership semantics | Cross-user/Simulation/account-rotation tests, reconnect without duplicate streams |
| 2 | Finalize read-only platform/API/tool registry contract | JWT, feature grant, viewer, route-isolation and static/dynamic metadata tests |
| 3 | Normalize broker read DTOs without changing execution | Journal status identity, freshness/error envelopes, pagination/tenant tests |
| 4 | Shared strategy/RMS adapter | Existing canonical risk/execution path, no bypass via adapter |
| 5 | Isolated Python host in offline mode | OS sandbox, network/filesystem denial, resource ceilings, durable scheduler audit |
| 6 | Flow graph offline validation/dry run | Strict node allowlist, bounded graphs, no broker order node execution |
| 7 | Tool/chart/scalping UI read surfaces | Real device/browser access and view parity, no new write button authority |
| 8 | Operational migration and release | Independent restore/rollback, security review, twelve controls accepted |

## Non-negotiable boundaries

The source registry is not a feature-authorization token. The backend must
revalidate operator identity, account and workspace scope for every read, and
must use canonical broker risk/idempotency for any later execution request.
The current kill switch and LIVE/SIMULATION split stay unchanged. Do not
merge or deploy this stacked draft on top of a failing execution parent.

**Current status:** catalog implementation submitted for review. Full
OpenAlgo-style product host remains partial and production NO-GO.

## Additive account + market-data read contract (follow-up review slice)

The authenticated `GET /api/v1/platform/runtime` projection consumes exactly
`accounts.get_active(scoped_uid)` and, when that account exists,
`ticker_manager.status(scoped_uid)`. It never calls `ensure()`,
`acquire_client()`, `build_client()`, `subscribe()` or a broker API.
The account store remains the only authority for active identity and session
token presence; the ticker manager remains the only authority for feed state.
No second maps, sessions, network clients, workers or order routes are added.

The payload distinguishes:
- `stored_session_present`: a stored token exists; **not** broker-validated login;
- `active_account_feed_bound`: the ticker belongs to the same active,
  non-paper account and there is a stored token;
- `feed_connected`: a currently connected existing feed bound to that account;
- `subscribed_count`: aggregate only, no symbols, tokens or owner tags;
- `shared_owner_feed`: Simulation is reading its authenticated owner's
  existing live feed. It has no independent broker socket.

A missing active account skips ticker lookup. A stale account-bound stream is
never represented as connected. A malformed account/user scope is a refusal,
not an auto-reconnect or session creation. Live and Simulation identities must
be resolved through `get_current_user` and the existing feature-access gate.

The discovery registry now names the actual `GET /api/v1/kite/accounts`
endpoint, not the non-mountable `/api/v1/` prefix; the existing restricted
`POST /api/v1/compat/openalgo/orderstatus` remains explicitly read-only.
Source-level tests verify those concrete HTTP methods and paths.
This is **not** full broker API normalization, strategy execution, a
`/tools` launcher or OpenAlgo API-key compatibility.

Acceptance: run `backend/tests/unit/test_platform_session_read.py`,
`backend/tests/unit/test_platform_surface_registry.py`, existing
`test_openalgo_compat.py`, ticker ownership, account rotation and Simulation
feed tests on the **exact** stacked PR head. It must remain unmerged until its
upstream parent and integration gates are separately accepted.

## Read-only tool registry over existing Kite endpoints

`GET /api/v1/platform/tools` lists a finite allowlist of the **existing**
PCR session/instrument reads, instrument-candle read, strategy-mode read and
Kite ticker-status read. It is authenticated, Kite-feature-gated, non-cacheable
and available to authorized read-only viewers. Each item records the actual
`GET` path, endpoint module and category. Source-AST tests check that
each route is declared in the original Sterling endpoint source.

This registry never invokes those endpoints, interpolates path variables,
calls broker adapters or bypasses existing policy. Actual data requests still
go to the canonical `/api/v1` routers with their ordinary tenant checks and
status/error semantics; the registry is **not** a proxy or permission grant.

The proposed global `/tools` React workspace **does not exist** in this
slice; existing Kite tools stay inside their current dock. A future UI
registry adapter can consume the authenticated metadata but must not create
separate data subscriptions or order actions. Python and Flow execution
remain disabled pending independent isolated-runtime design and tests.

## Non-authorizing broker API normalization (next stacked slice)

`GET /api/v1/platform/api-normalization` publishes source-verifiable OpenAlgo
operation-to-Sterling route mappings. Only the existing authenticated
`POST /api/v1/compat/openalgo/orderstatus` is an implemented **read-only**
compatibility adapter. `placeorder`, `cancelorder`, and `orderbook` are
documented as **no_wrapper**; their Sterling paths remain canonical and are
not called by this metadata router.

The mapping is a pure immutable registry. It does not execute a mapped
method, authenticate OpenAlgo API keys, poll brokers, create sessions,
subscribe, place/cancel orders, or alter Sterling risk gates. Unit tests
check actual route declarations, default-deny dispatch, authorized viewer
reads, no-store, denial on missing Kite grant, and production JWT refusal.
Full normalized response schemas and write-adapter parity remain outstanding,
and no OpenAlgo order execution API is authorized by this inventory.

This slice must remain draft/unmerged until its exact-head offline contract
workflow passes and its stacked parents and broad release gates have separate
acceptance. Production remains NO-GO.

## Strategy/RMS integration boundary: phase-1 lane read contract

`GET /api/v1/platform/strategies` is authenticated and Kite-feature-gated.
It projects *existing* `app.core.lane_registry.all_lanes` records into typed
strategy/mode metadata without copying the registry. It explicitly declares
`execution_authorized=false`, `strategy_dispatch_mounted=false`, and
`platform_execute_authorized=false` for every lane. A lane's declared
`live_minimum` state is NOT a trading permission.

The only existing entry-to-order path remains
`canonical_strategy_entry.submit_entry` →
`CanonicalExecutionService.submit_order`, with its original kill-switch,
account identity, risk approval, durable intent and evidence checks. These
are **source references** in the new discovery contract, not an alternative
execution adapter or a new endpoint for strategies to submit orders.

The next execution-adapter work must be preceded by exact-head tests of
account-scoped identity, accepted/rejected capital and risk decisions,
UNKNOWN fill behavior, protective stop retention, and all legacy strategy
entry/exit call sites. No code in this slice relaxes those gates.
An offline Flow validator and Python runner must each be implemented
without importing or contacting canonical broker clients or write paths.
Actual `/strategy`, `/flow`, `/python`, `/tools`, `/trading`, and
`/scalping` workspaces remain unmounted.

This is a phase-1 read contract only; full OpenAlgo runtime, normalized
broker responses, executable platform adapters, and production certification
remain outstanding. Parent PR #15 and all stacked drafts remain NO-GO until
their independent exact-SHA release checks are accepted.

## Offline-only Python draft and Flow topology foundations

`app.services.platform_offline_authoring` provides two pure review utilities,
with **no HTTP route, code execution, persistence, workers, external process,
broker imports, market subscriptions or strategy integration**:

1. `review_python_draft(source)`: bounded UTF-8 size, Python syntax parse and
   AST node budget. Returns only syntax validity, source SHA-256, and explicit
   `sandbox_available=false` / `execution_authorized=false`. Syntax-valid
   code is **not trusted or safe to execute**. The source is never executed,
   compiled to bytecode, logged by this utility, or saved.
2. `validate_flow_topology(graph)`: a strict, finite nodes/edges-only graph
   schema, deterministic topological ordering, rejection of duplicates,
   malformed references, cycles, unknown fields, oversized graphs and any
   embedded action or broker-call payload. Returns
   `graph_executor_available=false` and `execution_authorized=false`.
   This is a graph-shape validator, **not** a Flow node interpreter or dry run.

Neither helper is a runnable Python host or functioning no-code builder.
A future execution host requires an external OS-enforced sandbox (not
AST-based "security"), memory/CPU/time/network/filesystem confinement,
tenant isolation, durable audit, independent risk and broker authorization
review, and negative end-to-end validation. Existing canonical trading
paths must not be imported into untrusted execution environments.

Full charting/scalping UI routes, Python execution and Flow execution remain
unmounted. Production is NO-GO.
