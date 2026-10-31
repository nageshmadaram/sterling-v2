# Pre-production execution convergence — review contract

**Authority:** NO-GO. This page defines requirements and known source locations; it does not certify their completion.

## Existing sources of authority

```text
Request (session/account/strategy/route)
        |
        v
account_policy + feature_access + configuration_policy (request authorization)
        |
        v
SafetySupervisor + lifecycle state + risk proof (no increased exposure by default)
        |
        v
CanonicalExecutionService.submit_order (durable intent + idempotency + UNKNOWN fence)
        |
        v
Kite transport (only when independently authorized in a future release)
        |
        v
Broker book observation -> canonical journal -> fill / position projection
```

The service implements `submit_order()`, `cancel_order()`, `modify_order()`, `startup_recovery()`, and this review adds broker-read-only `reconcile_order()`, `record_execution_outcome()` and a fail-closed SafetySupervisor `require_execution_authority()` wrapper. These new methods are not release authority or proof of complete policy and strategy projection integration.

## Required final service contract

- `submit_order()`: immutable request/decision identity; idempotent durable reserve BEFORE sending; account-scoped lock; never automatically replay UNKNOWN.
- `cancel_order()`: verify order ownership and intent type; protective exit cancellation is not automatically risk-reducing.
- `reconcile_order()`: read-only broker-book and journal observation, exact match to account/exchange/product/tag/order/trade id, monotonic projection, fail closed when ambiguous.
- `record_execution_outcome()`: append durable, immutable, causally linked event; reject conflicting fills and duplicate broker trade IDs.
- `require_execution_authority()`: combine policy, lifecycle, deployment scope, broker identity and risk approval; never rely on UI or caller-declared reduction.
- Correlation metadata: stable intent key, correlation ID, user/account/session, broker identity, strategy, route, generation, signal and authorizing revision. Never log credentials.

The additional named interfaces now exist in source, but **latest-SHA CI, full integration with legacy routes, cross-layer correlation and independent runtime recovery remain NOT CERTIFIED**. This additive source slice must not be interpreted as complete migration or live approval.

## Legacy transport mapping and disposition

| Route | Expected execution authority | Required proof | Current disposition |
|---|---|---|---|
| Manual Kite order API | Canonical executor, authenticated user and exact broker account | End-to-end HTTP → journal → isolated broker double, duplicate and UNKNOWN | Source migrated; review |
| SuperTrend Kite monitor | Canonical executor, inventory proof, lifecycle guard | Crash between pending-exit write and broker ACK; broker trade-ID projection | Source migrated; blocked |
| Snapback lifecycle | Family gate, risk proof, canonical executor | Stable strategy/session identity and post-restart recovery | Source hardening; blocked |
| Gamma Move | Canonical reduction bridge | Partial fill, product scope, immutable tag after lost ACK | Source hardening; blocked |
| Intraday and Adaptive Edge | Canonical reduction bridge | MIS vs NRML isolation, partial fill, crash-gap observation | Source hardening; blocked |
| Opening Volume | Canonical emergency close | Ledger projection and concurrent stop behavior | Source hardening; blocked |
| Legacy OrderRouter | Paper/shadow only where explicitly allowed | Assert live client cannot mutate broker | Paper-only exception; block live |
| Direct Kite transport / GTT / cancellation | Only within typed capability context | Source inventory, exact ownership, negative tests | Must be reviewed individually |

Run the AST inventory and then **manually inspect each emitted candidate**. The scanner intentionally cannot prove dynamic calls, runtime authorization or transport semantics. Any unknown real-money route remains blocked. Entry kill switch defaults off; do not weaken safe mode to improve test results.
