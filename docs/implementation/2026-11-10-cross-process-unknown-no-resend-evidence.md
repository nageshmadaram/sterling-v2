# Canonical UNKNOWN exit — independent-process no-resend proof (synthetic only)

**Parent:** Draft PR #59 at immutable `543ac9e74f6586cd80a3ee55e1d7837cc0d48010`.
**Scope:** A new regression test only, no production source change. Production broker routing stays disabled and **0/12** independent release approvals are present.

## Risk being tested

An exchange may accept an order but lose the submit HTTP acknowledgement. The durable canonical order intent must remain `UNKNOWN` across process restarts; no worker may interpret the absent REST response as permission to submit the same SELL again. If a second order is sent, it can reverse a long option position into an unauthorized short exposure.

## Test design and evidence

1. On a disposable SQLite database with synthetic reconciled inventory, `submit_reduction` sends one SELL through a paper-only transport that throws a synthetic `httpx.ReadTimeout` *after* recording the call. The result is not successful and the intent is durably `UNKNOWN`.
2. Three **independent Python interpreter processes** are started concurrently and connect to the same isolated SQLite file. Each constructs a NEW paper-only transport whose `place_order` increments a counter and rejects duplicate sends. Each replays the same canonical reduction identity with a changed strategy-generation identifier and reason.
3. Each process must return the original immutable intent key, status `UNKNOWN`, durable journal state `UNKNOWN`, zero broker-submit attempts and a failed `claim_submission` CAS. The initial fake transport must still show exactly one original attempt.
4. Failures expose only sanitized synthetic test stderr. There is no configured Kite client, no real network call and no actual financial order.

**Test results:** The single new independent-process proof passed; a further combined canonical reduction, convergence, fresh-reduction and order-journal selection passed **193/193 tests** (new case included). Existing SQLite backup/restart, identity immutability, lost ACK and idempotent fill tests remain in the suite.

## Limitations

This is a process-level, **synthetic invariant** of the canonical journal and reduction sender, not end-to-end evidence of broker reconciliation or order-book ownership. It does not verify Zerodha token/account identity, actual broker timeouts, fired protective GTTs, order-history matching against independently fetched Kite records, partial-remainder SELL handoff, production rollout or independent risk approvals. A privileged writer mutating the SQLite journal directly is not within this test's threat model.

**Status:** DRAFT / UNMERGED / UNDEPLOYED / PRODUCTION NO-GO.
