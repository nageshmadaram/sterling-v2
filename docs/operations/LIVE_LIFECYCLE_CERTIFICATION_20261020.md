# Sterling strategy lifecycle certification — 2026-10-20

**CERTIFICATION VERDICT: BLOCKED / NOT CERTIFIED.**
This is an exact-source/code-evidence continuation. It is not authorization for
real-money entry, a broker order, or removal of SAFE_MODE.

The operator's pasted local logs at prior `c72501c4` establish:
- Frontend TypeScript/Vite production build: **PASS** (430 modules transformed).
- TV/header/frontend tests: **48 passed, 2 failed**. Both failures use
  `getByTestId('fake-tv-content')` to assert that the element is absent.
  The production view was **already correctly absent** in the failure DOM.
  This is a test-query bug, not proof of incorrect real trading behavior.
  Corrected source assertions use `queryByTestId`. Updated test run pending.
- No logs from a full backend suite, current broker lifecycle, physical full
  restore, actual Telegram phone receipt or moving-market disconnect were
  included in that terminal run.

## Strategy-specific validation inventory

| Lane | Lifecycle verification targets | What is **not** yet proven |
|---|---|---|
| Snapback | engine; signal/session validation, T+1 quote/eligibility, canonical execution contract, protection/exit, exposure/replay controls | fresh exchange/broker quote -> owned fill -> durable SL/TSL -> exit and P&L under this exact release |
| Gamma-Unwind | frozen reference; tier ENTRY vs watchlist, live safety admission, entry idempotency, broker quantities, GTT protection, uncertain exit reconciliation, alert notifications | live approved fill/partial fill, GTT placement/trigger/modify, exit and restart recovery with current broker session |
| SuperTrend | frozen core/parity fixture; forming-candle exclusion, scan/entry authority and stop lifecycle regressions | exact-release live scanner trace, broker SL/TSL movement, repeat signal dedupe, partial-fill and disconnect recovery |
| Shared | canonical entry, broker account binding, live minimum caps, ambiguity handling, replay evidence, Telegram durable outbox, release authority | current broker console whitelist and session, actual phone delivery, 12 physical/reconnect drills, full backup/restore |

`scripts/release_certification_probe.sh` is the non-trading evidence runner
for those test groups, plus frontend build and read-only operator posture.
It stores private per-suite logs outside the checkout and a SHA-stamped summary.
It does **not** declare full certification if simulated tests pass. Its
operational steps are read-only except routine local diagnostic recording.

## Core safeguards inspected

- `backend/app/services/gamma_move_runner.py`: signal tier admission,
  canonical `submit_entry`, reconciliation, broker stop confirmation,
  trailing-stop no-loosen check, durable pre-submission exit placeholder and
  ambiguous-stop/exit handling exist in source. Individual transitions remain
  unverified on a fresh real exchange.
- `backend/app/services/notifications/telegram_lifecycle.py`: per-user
  durable outbox, deduplication, attempts, retry/backoff, database-failure
  diagnostics and spool fallback exist. An unobserved provider response or
  destination receipt must never count as a delivered alert.
- `backend/app/core/broker_account_binding.py`: broker account ID and
  application account matching, read-only positions/orders/protection
  requirements and evidence references are implemented.
- `backend/app/core/deployment_identity.py`: recorded outbound IP is
  distinguished from a broker-approved static/whitelisted IP; this cannot be
  proven solely by changing `STERLING_STATIC_EGRESS_IP`.
- `backend/app/core/failure_drills.py`: 12 named failure drills, including
  ambiguous order acknowledgment, missing protection, disk full, stale feed,
  partial fill and broker disconnect. A code test is not an observed drill.
- `backend/app/core/backup_manifest.py` + restore-proof tests: checksums,
  data coverage and isolated restoration rules. A clean SQLite copy does not
  prove all required lane manifests or complete host restore.

## Required exact-release checks (ordered)

1. Use one clean, synchronized `main`/`dev` release commit and run
   `bash scripts/release_certification_probe.sh`. Save its report folder on
   independent storage, with permissions for operator only.
2. Fix every failing frontend/backend/contract test on that SHA and rerun
   the groups until zero failures. Do not carry over earlier-SHA results.
3. Confirm root disk reserve meets the deployment policy and physical backup
   destination has capacity; the last user reading was 4.2 GB free (97% full).
   Do not delete databases, journals, encryption keys or backups.
4. Produce a full backup, physically separate restore and verify each SQLite
   table, release/strategy manifest and private deployment configuration.
   The October 18 summary explicitly says only partial SQLite restore passed.
5. Confirm approved stable egress and redirect **in the broker console**;
   refresh current identity/account binding and read-only positions, orders,
   GTT/protection and open exposure. Expired authentication is a block.
6. During market hours with owner present, run the read-only acceptance
   `scripts/sterlingctl acceptance NIFTY 120`. Document feed disconnect and
   reconnect, no new exposure, confirmed stable broker session and timestamps.
7. Demonstrate provider and actual phone receipt of signal + entry/protection/
   trail/exit Telegram events, correct dedupe, outage/retry and tenant isolation.
8. Rehearse the 12 independent failure drills on isolated infrastructure.
   Broker order/fill/SL/TSL/exit proof involving real money requires explicit
   authorized operational procedure and safe maximum caps; no programmatic
   proof here places an order automatically.
9. Only after all technical and human gates pass, seal an immutable release
   manifest/tag, prove deployment identity and operator handoff. CI/CD is
   intentionally deferred to the final release stage as requested.

**Source caution:** The current `scripts/sterlingctl` text identifies several
release certification fields as informational/waived. Thus `doctor` returning
zero **must not be read as full certification**. Real broker interlocks
(safe mode, account binding, exposure, secrets) still block actual entries.
This report requires independent evidence for the waived fields as well.

**Critical constraints:** do not enable live-entry, place real orders, alter
broker session secrets, or override safe-mode, Greeks, SL/TSL, exposure
reconciliation, idempotency or account binding to make tests pass.

### Open issue accounting

Use `docs/operations/ISSUE_REGISTER_151_20261018.csv` as the canonical
acceptance register. Its 151 `OPEN_UNVERIFIED` acceptance items cannot be
marked closed without per-ID evidence, verified on the final release SHA.
No item was automatically closed by this change.

## 2026-10-20 03:11 IST — exact-commit owner probe results and fixes

The operator ran `scripts/release_certification_probe.sh` on
`5e51c8d9a5ace2481411d82c50e6a558c870f8a8`. The summary was
provided in the conversation; private machine logs remain on the operator
host and were **not uploaded to public GitHub**. Verified test outcomes:

- Snapback: **323 passed, 2 failed, 1 skipped**. The two failures were in
  `test_snapback_validation` and `test_snapback_execution_contract_path`.
- Gamma-Unwind: PASS in the selected isolated regression group.
- SuperTrend: PASS in the selected isolated regression group.
- Telegram durable outbox: PASS in the selected isolated regression group.
- Broker/backup safety: **110 passed, 1 failed** in
  `test_full_backup_contents::test_a_full_backup_proves_its_own_restore`.
- Frontend build, selected frontend regressions and isolated TV gateway: PASS.

Code-level corrections on the next SHA:

1. Snapback's validation test previously asserted that `intraday` was an
   unsupported live lane. The current planner explicitly maps intraday to a
   live lane. That stale test now checks current support and separately
   checks an **unknown mode is refused**.
2. The isolated evidence-failure test attempted a paper entry with
   the lane-origination gate **correctly refusing** it. Only that test now
   stubs the lane decision to isolate the evidence subsystem, while a new
   regression separately demands `LANE_NOT_ORIGINATING` when admission
   refuses. **Production lane admission is unchanged.**
3. Backup restore test created a temporary evidence database, but the probe
   intentionally exported `STERLING_EVIDENCE_DB` pointing to another,
   non-existent isolated path; full restore correctly rejected that missing
   authoritative source. The test now sets `STERLING_EVIDENCE_DB` to its
   fixture and separately checks that a missing authoritative path still
   **fails the restore proof**, even if the fallback observations DB exists.
4. The probe no longer labels readable binding or exposure inventory as
   `PASS`: read success is `OBSERVED` until broker truth is independently
   verified. It checks both fetched Git branch SHAs and protects operator
   log output from shared terminal disclosure.
5. `scripts/sterlingctl certify strict` now uses the existing
   `CertificationReport.release_ready` verdict, returning non-zero if
   any required gate is FAIL/UNKNOWN or exposure is not provably zero.
   It does **not** remove old advisory reporting or change live-entry gates.

### Observed operator blockers from the owner probe

- `doctor` failed **allocation_capital**, **risk_limits**,
  **deployment_identity**, **static_egress**, and **account_binding**.
  Network reachability could not be proved.
- The host's observed outbound IPv4 **does not match its previously
  registered broker egress**. Never simply mark the new IP approved in
  Sterling: the broker developer console must independently confirm
  an actually stable/whitelisted address and callback.
- `bindings` command exited zero but did not prove an ACTIVE usable
  binding (this is why its status is now `OBSERVED`, not certified).
  `exposure` exited zero but cannot by itself certify current ownership,
  freshness, broker-side GTT/triggers and account binding.
- `drills` reported all **12 NOT RUN** for this exact build.
- Disk reserve remains below the conservative probe check of **10 GiB**
  free on the checkout filesystem. Check the actual deployment policy and
  separately encrypted backup destination; no live backup was attempted.
- Broker console approvals, physical market reconnect drill, real lifecycle
  receipts, Telegram destination receipt and full restore remain unproven.

**Evidence status of these corrections: SOURCE PATCHED, NOT YET RERUN on
the corrected exact SHA.** No operational acceptance can be inferred from
the unit test cleanup. Full certification stays blocked.
