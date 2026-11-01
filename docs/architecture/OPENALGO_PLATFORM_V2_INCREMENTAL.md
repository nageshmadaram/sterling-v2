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

## Canonical read normalization and strategy inventory (stacked slice)

`GET /api/v1/platform/broker/summary` normalizes the **passive** existing
account/ticker snapshot into one explicit status:
`not_configured`, `no_stored_token`, `stored_token_unverified`,
`feed_disconnected` or `feed_connected`. Even a connected feed does not
establish a validated broker login or trading authorization: the read contract
sets both claims to `false` and never contacts the broker.

`GET /api/v1/platform/strategies` projects *declared* lanes directly from
`app.core.lane_registry.all_lanes()`. It deliberately does not invoke
`may_originate()` as a trade admission decision, perform risk preflight or
submit an order; its `order_submission_enabled` remains `false`.
The real execution entry path, kill switch, safety supervisor and durable
journal remain the sole authorities. These are not shadow execution/risk
engines and do not change any original route.

Both endpoints require the existing scoped JWT/identity and Kite feature grant,
are GET-only and non-cacheable. Ambiguous session/feed inputs are refused rather
than normalized into a false success. Tests check the invariant matrix,
strategy registry consistency and access-denial-before-service calls.
