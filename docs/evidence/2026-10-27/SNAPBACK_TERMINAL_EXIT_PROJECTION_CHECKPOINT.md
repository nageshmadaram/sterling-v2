# Snapback terminal exit projection — owner/quantity transaction fence

Date: 2026-10-27. Development branch only. **No live trading or profitability certification.**

## Problem found

After resolving a terminal CLOSE_POSITION order, the previous Snapback path updated
the persisted position by adding the broker's cumulative fill with no in-transaction
verification that its broker order ID, owner account/tenant, exact option/hedge
contract, requested quantity and remaining owned position were unchanged.
An intervening worker or out-of-date callback could overbook an option position,
write a fill against a changed owner, or clear a different working order after
the asynchronous broker query.

## Code changes

- `backend/app/services/snapback_exit_fill_projection.py` validates a terminal
  receipt and compares the fresh SQLite row against the original owner,
  authenticated account, instrument, leg, active broker order ID, requested units,
  and remaining position **inside the store's BEGIN IMMEDIATE mutation**.
  Duplicate projection is idempotent, and excess broker fills do not become
  silently truncated or overcounted quantities.
- `backend/app/services/snapback_live_exits.py` uses the pure helper,
  rejects corrupt persisted order states before the broker resolver call,
  rechecks owner and requested quantity at order-ID retirement, and raises a
  reconciliation halt on failed/vanished registry projection.
- `backend/tests/unit/test_snapback_exit_fill_projection.py` holds the
  standalone pure behavioral suite. The existing boundary suite has additional
  mocked asynchronous race and missing-row tests.
- `scripts/snapback_offline_acceptance.sh` now includes the pure suite.

## Evidence actually executed

An isolated local Python 3.13 sandbox reconstructed **byte-identical** files
from the committed private GitHub `dev` branch:

| Artifact | Verified Git blob SHA-1 |
|---|---|
| Pure projection source | `fe10e3a76540f1249f23678a0f08ced3bf54792e` |
| Pure test source | `b356fb16fd34334a2250a6961209b1cef4a86eab` |

`python -m py_compile` passed. `python -m pytest -q -p no:cacheprovider
test_snapback_exit_fill_projection.py` reported **30 passed, 0 failed**
in 0.15 seconds. The tests cover two legs, idempotence, missing broker
quantities, drift in account/tenant/instrument/order ownership, overflow
and malformed receipt values. These are standalone **unit tests only**.

The full Sterling/backend/frontend test suites, deployed runtime, Kite
account session, live broker order/protection lifecycle, external
restore/failover, and independent complete-contract after-cost economic
evaluation have **not been run** here. The additional integration test
cases committed to the live manager have not been executed. CI remains
out of scope at the owner's request; CI absence is not proof of success.

## Release verdict

**NO-GO** for live new entries. **Profitability remains unproven.** Retain
main untouched and the kill switch / entry authorization OFF until
genuine authenticated market and operator evidence meets the frozen
acceptance criteria. The owner is hospitalized and should not be asked to
operate a workstation to create apparent release receipts.
