# Simulation reset preflight for immutable Gamma review child

**Review parent:** draft Sterling V2 PR #47, 25fd499226fda51b21205b6c93757245fcaa28da.
**Scope:** simulation reset only. All broker execution and main deployment
remain NO-GO, with 0/12 independent controls accepted.

## Reproduced failure

order_journal.clear_for_tests correctly refuses to delete Gamma
review-only child evidence. However, simulation/mode.py::reset_simulation
previously disarmed strategy mode, reset virtual cash and purged the
simulation trade journal BEFORE calling order_journal.clear_for_tests.
A cleanup refusal was swallowed into its failed-step report and the
remaining reset steps continued, potentially clearing the recovery fence
while immutable journal evidence remained.

The HTTP API consequently responded 200 for an incomplete reset. This was
reproduced in both a service-level and a real HTTP TestClient regression.

## Fail-closed change

The existing order journal now exposes assert_cleanup_safe(uid), a
read-only preflight guarded by the same durable SQLite writer transaction
and immutable-review-intent predicate as clear_for_tests.

Simulation reset calls this preflight while holding the process-local
reset lock BEFORE disarming any strategy, altering cash, deleting journal
rows or resetting control-plane status. A known review-only child raises
ModeBlocked with code immutable_review_evidence. An unreadable or
malformed preflight raises ModeUnavailable with a generic error message;
no internal database details are returned.

The reset HTTP route maps ModeError to its existing structured refusal
representation. A blocked reset returns HTTP 409, not misleading 200.
Ordinary simulation reset behavior remains unchanged.

The canonical cleanup function repeats the immutable check under its own
SQLite transaction before deleting any row. Thus a newly found conflict
at cleanup still blocks journal deletion.

## Tests and remaining limitations

Tests verify that after a rejected preflight, the synthetic account's
virtual cash, simulation strategy mode, original control-plane state and
frozen canonical child remain unchanged; the HTTP API returns 409 and
does not reset the tenant. Ordinary reset regressions also execute.
An unavailable evidence preflight returns sanitized HTTP 503 and leaves
virtual cash and mode unchanged.

Local review evidence: 103/103 simulation service/API/boot tests and
484/484 Gamma/canonical/journal tests passed in isolated offline mode.
The parent release controls still report 0/12 independent approvals;
exact-commit GitHub Actions must independently re-run the selected suites.

This preflight is NOT a cross-database transaction or external-process
serialization boundary. Another writer can race between preflight and
the later per-subsystem reset. The entire simulation reset still spans
independently committed stores. A durable per-tenant reset fence and
separate crash/rollback acceptance are needed before guaranteeing global
reset atomicity.

No production Gamma partial child SELL is implemented, and no broker
order, GTT, credential or real-money position is touched.

DRAFT / UNMERGED / UNDEPLOYED / LIVE BROKER EXECUTION NO-GO.
