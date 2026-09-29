# Execution — signal → broker authority

Sterling currently has **two execution generations**, and they must not be
confused.

1. **Canonical execution** is the production target. It uses the durable Kite
   intent journal, account-scoped reservations, an atomic `RESERVED → SUBMITTING`
   claim, reconciliation for unknown outcomes, and the broker-side protection
   lifecycle.
2. **Legacy/direct execution paths** still exist for research, paper/shadow
   mechanics and risk-reducing exits. They are grandfathered behind the shared
   transport floor and are **not authorised to originate live exposure** in this
   release.

The global invariant is:

```text
LIVE_EXECUTION_ENABLED = False
```

No environment variable or UI toggle can override that constant.

## Canonical production path

The canonical authority is implemented around
`app/services/execution_service.py` and
`app/services/kite_engine/order_journal.py`.

For an exposure-increasing entry it:

1. resolves and validates the account/execution identity;
2. requires an explicit broker mode — missing mode is UNKNOWN and fails closed;
3. applies safety, capital and lane gates;
4. reserves an immutable intent in SQLite, scoped to user + broker account;
5. atomically claims the reservation before the broker boundary;
6. submits once;
7. records the broker acknowledgement, or marks the intent `UNKNOWN` when the
   submission outcome is ambiguous;
8. requires reconciliation before an UNKNOWN intent can release capital or
   permit another conflicting entry;
9. projects confirmed fills into the managed-position/protection lifecycle.

A transport timeout is **not** permission to replay an entry. Unknown outcome
means recovery/reconciliation.

## Legacy OrderRouter

`app/services/execution/order_router.py` remains a useful orchestration/test
surface for PAPER and SHADOW behavior and for explicitly risk-reducing actions.

```python
class RouterMode(str, Enum):
    PAPER  = "paper"    # simulation only
    SHADOW = "shadow"   # simulation/evidence only; never broker origination
    LIVE   = "live"     # legacy surface; new live exposure is not release-authorised
```

Its safety pipeline still includes resolution, kill/daily-loss/idempotency
checks, cooldowns, portfolio caps, microstructure vetoes, correlation sizing and
the Greeks hard gate. Greeks failure is fail-closed.

For ambiguous legacy submission it does **not** enqueue an automatic retry:
the outcome becomes `submission_unknown` / `RECOVERY_REQUIRED` and requires
broker reconciliation.

The legacy idempotency cache is not a durable exactly-once protocol. That is one
of the reasons this release keeps new live origination disabled outside the
canonical authority.

## Grandfathered direct entry transports

Some strategy services still contain direct broker-entry calls for historical
mechanics. They are explicitly tracked by the canonical-convergence regression
suite and each must pass through
`app.core.lane_registry.entry_transport_allowed(client)`.

Known grandfathered surfaces include the BrokerAgent and selected Adaptive Edge,
Gamma Move, Intraday, Opening Volume and legacy OrderRouter code paths. They may
exercise paper simulation while the broker client is explicitly paper. They
must not originate real exposure while the global live floor is false.

Do not remove the transport gate to make a test or strategy “work”. Migrate the
strategy to canonical execution instead.

## Generic Kite API write floor

The generic Kite REST facade is not a second production execution authority.

Live exposure-increasing generic writes are refused, including unsupported cash
orders, generic order modification, generic GTT creation/modification, mutual
fund purchases/SIP creation or mutation, and ATO alert order creation.

Risk-reducing operations such as cancellation, GTT deletion, redemption/SELL,
and disabling an existing ATO remain available where explicitly implemented.

Manual derivative entries that are admitted are routed through the validated
strategy/protection path rather than falling through to raw `KiteClient._place`.

## SHADOW definition

SHADOW means **no broker origination**. It may read real market/account state and
record simulated evidence, but it must not call an order/close/amend transport to
create exposure.

Any implementation that sends a real entry while labelled SHADOW is a defect.

## Release status

Passing unit tests is not capital permission. This branch is intended to reach
**Production Shadow** only. Real-money promotion remains blocked by exact-SHA
release certification, broker/deployment verification, failure drills, zero
unresolved exposure, lane-specific forward evidence and a future deliberate code
change to the global live floor.

See `docs/operations/FINAL_RELEASE_RUNBOOK.md` for the release sequence.
