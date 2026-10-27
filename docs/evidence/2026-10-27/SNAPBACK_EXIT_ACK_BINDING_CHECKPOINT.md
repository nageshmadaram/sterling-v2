# Snapback exit acknowledgement identity — development checkpoint
**Date:** 27 October 2026
**Branch:** dev (not production/main)
**Live/new entry status:** OFF, not broker-certified

## Defect fixed
A canonical risk-reducing exit could receive a broker order ID, then fail to
persist that ID because the SQLite position row was missing, the disk was
full, the owner/account had changed, or a second broker exit order was already
registered. Returning the ACK without a durable owner-bound record could
produce a second SELL/BUY on the next recovery attempt.

`SnapbackExitManager._submit_exit` now uses an exact transaction-scoped
`bind_acknowledged_exit` guard. The guard checks position ID, tenant, broker
account, option/hedge instrument, exact previous working order ID and requested
quantity, and remaining units. If persistence fails, the executor engages
the new-entry kill switch and returns an unresolved outcome; the caller must
reconcile the broker before any retry. It does not restore a competing stop.

## Executed isolated source verification
- Source GitHub blob SHA-1:
  `43fb1fa8bddcb5ae6caa21b06e2e879076702a7f`.
- The Python file used in local testing passed `git hash-object` with that
  **exact same SHA-1**, proving that the pure binder matched committed code.
- `pytest` result on isolated pure module: **20 passed, 0 failed**
  (`snapback_exit_ack_binding_junit.xml`).
- Cases: option and hedge ACK acceptance, idempotent duplicate IDs, five
  ownership/contract drifts, six invalid hedge quantities, remaining-quantity
  over-close, two contradictory working-order identities/quantities, blank
  IDs, unknown legs, and a partial option position's exact remaining exit.
- Regression tests and a full-app mock canonical submission suite are committed
  under `backend/tests/unit/test_snapback_exit_order_binding.py` and
  `backend/tests/unit/test_snapback_exit_resolution_boundary.py`.
  The latter was not executed on the complete Sterling environment in this
  conversation; the offline acceptance launcher now includes the former.

## Still blocked
Nothing here proves the user's actual Kite session, stop placement/release,
real order fills, exchange executions, hedge lifecycle, restart/reconciliation,
complete after-cost portfolio P&L, or forward profitability. No live money was
authorized or sent. This is a test-backed functional safety repair, NOT an
economic or live broker certificate. Keep `main` unchanged and new risk OFF.
