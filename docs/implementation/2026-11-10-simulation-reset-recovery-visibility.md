# Simulation reset recovery visibility and virtual-writer admission

**Parent:** Sterling V2 draft PR #51, exact commit `3b682c5554389638ef5644552724f2d4818a543d`.
**Scope:** offline and simulated tenant safety only; review-only Gamma remains non-submittable. No broker orders, live SELL, GTT mutations, main merge or deployment. All independent release controls remain **0/12 accepted**; production **NO_GO**.

## Red-first findings

PR #51 durably fenced the canonical journal and Gamma review writers, but the wider simulation lifecycle could still change a tenant while its reset remained `ACTIVE` or `FAILED`:

- Ordinary `/sim/status` could initialize a virtual account, while `/sim/capital` could alter virtual capital without a durable reset-state check.
- `tenant.boot_tenant()` and the automatic virtual recovery path could bootstrap or reconcile a fenced tenant.
- The background simulation loop could evaluate virtual orders and GTTs, expire day orders, refresh watches and write equity snapshots while the reset was incomplete.
- A direct capital update trusted an advisory preflight, leaving a race to an intervening reset. The canonical journal and default SimLedger use the same SQLite file, making a writer-lock recheck possible.
- A reset starting between HTTP admission and tenant bootstrap caused an internal HTTP 500 rather than a controlled conflict.

The red-first coverage reproduced these defects before their source-level changes. A failed status fixture lacking the normal admin context was corrected rather than treated as application failure.

## Implemented boundaries

1. **Read-only recovery diagnosis** — Owner-only `GET /api/v1/sim/reset-health` returns `CLEAR`, `ACTIVE` or `FAILED`, `recovery_required`, an explicit `automatic_release_allowed: false`, and selected control-plane states. It neither boots the tenant nor discloses reset tokens, broker secrets or raw database errors. Invalid/unreadable markers return sanitized HTTP 503; non-owner access returns 403.
2. **Public endpoint refusal** — Ordinary `/sim/status` and `/sim/capital` refuse existing reset fences (409) before tenant creation or cash changes. A reset racing between the endpoint preflight and bootstrap is mapped to sanitized 409. Unexpected tenant/capital storage errors become sanitized 503, not an unhandled 500.
3. **Boot/recovery coordination** — Ordinary `tenant.boot_tenant()` checks the durable fence inside a SQLite writer transaction and refuses if fenced. Only the reset coordinator may bootstrap with its exact token while its fence is `ACTIVE`. A `FAILED` marker refuses even that token. Background `recover_tenant()` and its polling loop skip fenced tenants and do not acquire virtual clients or write `CLEAN` recovery control.
4. **Simulation runtime admission** — Each owner is checked before virtual evaluate, day-order expiration, watch refresh, equity snapshot and tick-triggered work. A fenced or unreadable owner is skipped; unrelated owners can still progress. Unreadable evidence generates a fixed error label, without leaking private marker contents. Pure fake-ledger unit tests explicitly model an unfenced journal; integration tests use actual SQLite fences.
5. **Capital write transaction** — `sim_runtime.set_capital()` rechecks the reset fence inside the default SimLedger's SQLite write transaction before modifying virtual funds. It does not claim atomicity for injected external database connections or arbitrary independent ledger writers.
6. **CI coverage** — Scoped Gamma Canonical Recovery now triggers on the modified runtime and tenant files and includes `tests/unit/simulation/test_sim_runtime.py` alongside tenancy mode/API/boot tests.

## Verification

- Full local Gamma/canonical/journal regression: **511 passed**.
- Full local `backend/tests/unit/simulation` directory: **570 passed**, with five non-failing dependency/route warnings.
- New cases include ACTIVE/FAILED fences, malformed markers, uninitialized read-only health, owner access, boot-token scoping, auto-recovery refusal, virtual runtime and tick admission, unrelated-owner isolation, capital write check under SQLite lock, and HTTP preflight-to-boot race.
- Python compilation, governance schema, source-only inventory, governance/inventory unit checks, and `git diff --check` are separately required to pass before publication. GitHub exact-commit CI outcomes must be added after publishing a new draft PR; local acceptance is not independent production approval.

## Remaining unproved guarantees

- A virtual pass may read a clear marker before another process creates a reset fence; until every individual virtual-ledger writer enforces the same transactional fence, the runtime preflight cannot prove absence of all in-flight writers.
- `boot_tenant()` admission and subsequent writes are not an atomic unit across all storage systems. An external or raw writer can bypass these application guards.
- Crash recovery of all virtual sub-stores, host restore, manual stale-`ACTIVE` investigation and release approvals remain unresolved. **No endpoint to clear a stale ACTIVE fence** is provided.
- Parent PR #15 full frontend/backend/browser acceptance, production partial-remainder SELL, authenticated fresh Kite inventory and GTT proof, protected stop handoff, UNKNOWN-order no-resend and all 12 independent gates remain unapproved.

**DRAFT, UNMERGED, UNDEPLOYED. Live broker execution remains NO-GO.**
