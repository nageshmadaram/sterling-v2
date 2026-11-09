# Gamma partial-exit: stricter offline review DB boundary and process-crash proof

**Parent review:** draft PR #37 at `e8c15e830f2738030c71f28e6ccebd6ccd5b1048`.
**Deployment:** NO-GO; unmerged review work only. No broker call, GTT modification,
canonical order reservation, execution transition, or new endpoint.

## Failure modes addressed

The earlier test-only `gamma_terminal_review_holds` prototype relied on the
shared `db.is_disposable_path` predicate. That helper intentionally accepts a
non-temporary path when its basename includes `test`. Such a filename heuristic
is inadequate for a persistent review claim that must never touch an operator
SQLite database.

The claim now requires **all** of the following before opening a DB:

- The Python process is in the existing test-only context, and the existing
  shared disposable-path predicate also permits the path.
- The path is an absolute filesystem path, not a SQLite memory/URI reference
  or a direct symbolic link. Its fully resolved destination must be **inside
  the system temporary directory** and have a `.db`, `.sqlite` or `.sqlite3`
  suffix with an existing parent directory.
- The process is not configured with `STERLING_LIVE_EXECUTION` enabled. Even
  if a temporary DB happens to exist, that mode is rejected before creating
  the review schema or reading an inventory row.

The guard is a conservative **test-only** protection, not an authentication
control, a secure ephemeral-storage proof, or permission to deploy. The OS
temporary directory is not a broker environment. Review records are still
non-authorizing, `REVIEW_HELD` only, and have no transition into executable
canonical `kite_order_intents`.

## Process-level durability evidence

The enhanced test suite includes subprocess tests (distinct Python interpreters)
using the same disposable SQLite file:

1. Two independent processes attempt the same terminal partial-exit review.
   SQLite's `BEGIN IMMEDIATE` writer lock allows one immutable insert; the
   second reopens the durable row as an idempotent replay. Only **one** review
   record and **one** original canonical parent intent exist afterwards.
2. A subprocess is deliberately terminated with `os._exit(79)` from an
   `AFTER INSERT` SQLite TEMP trigger before transaction commit. WAL rollback
   removes both the in-flight review table creation and its uncommitted row.
   A fresh process can then create the first valid review hold without a
   duplicate, ghost reservation, or resend.
3. Non-temp filename containing `test`, symlink alias, SQLite URI, in-memory
   address and relative path all fail the guard before any mutation.
4. Live-flag variations (including an unrecognized value) fail closed. A
   previously recorded offline review claim cannot be accessed by turning
   live mode on after restart; it becomes available for review again only
   after the test-only environment is restored.

All process tests use synthetic account/symbol identities, isolated SQLite,
no external Kite credentials, and no broker networking. They verify only
local durability, not exact broker truth or production transaction scope.

## Still required before a second SELL

The terminal 20/50 partial fill still cannot authorize a new SELL for 30.
The reviewed production path needs a *separate* canonical parent-child journal
reservation protocol, fresh authenticated broker position and protective GTT
checks immediately before submission, durable UNKNOWN/no-resend handling,
concurrency guarantees spanning the broker, validated fill projection,
independent deployment/rollback checks, full parent PR #15 CI, and acceptance
of all twelve external controls. These are not implemented here.

**Release decision: DRAFT / NO-GO / LIVE BROKER EXECUTION OFF.**
