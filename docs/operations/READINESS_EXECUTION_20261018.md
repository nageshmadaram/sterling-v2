# Sterling readiness execution — 2026-10-18

Production certification: **BLOCKED**. New-entry origination remains disabled;
durable SAFE_MODE is ON. No real-money order was placed or protective order disabled.

This is execution evidence, not a waiver of the original acceptance criteria.
The original 151 IDs and historical findings remain in
`ISSUE_REGISTER_151_20261018.csv`: 151 OPEN_UNVERIFIED acceptance states. Two narrow findings were verified
(SEC-01 default entry safety and QA-02 pre-runner CI diagnosis), but their
broader recorded acceptance requirements remain incomplete. Group-level
regressions do not certify every issue or replace its individual proof requirement.

## Preserved state and source

Initial clean local main/dev: `dcdce5f9904186230166e8275568a1b22d177f87`.
Both remote branches were verified at the expected PR #246 release fix
`18c2f1b563441d51bbcb89c88b3dcb52d43306ec`; main was fast-forwarded.
No local commit, credential, database, journal or existing backup was removed.

Verified fixes committed after that base:

- `1f112db6`: correct persisted safe-mode reporting and CLI environment loading.
- `e23711e1`: permit only the matching, durably ARMED and broker-protected Snapback
  hedge continuation; unrelated/uncertain exposure remains blocked.
- `d4b42724`: exclude mutable state from Docker build context; lock assisted-login dependencies.
- `25a3eb72`: separate actual global entry posture from strategy mode in Mini App;
  honor central production environment; restored operator credentials need no plaintext bootstrap password.
- `8a5233d0`: persist container databases under explicit state root, preserve artifact
  identity across runtime DB writes, supervise web gateway/tunnel.
- `9b91b919`: keep source revision changes from invalidating dependency build layers.
- `53adf6a1`: external backend service owns recovery; detect child exit during startup grace.
- `11899ee8`: exposure read polls no longer rerun schema migrations after initialization.
- `dbf7760f`: bound Uvicorn connection drain to 20 seconds, allow 60 seconds for
  supervisor cleanup, and use the canonical backend launcher in the deployment unit.

## Account and operator evidence

The owner completed normal interactive Sterling login and confirmed Telegram
message 18266 in this chat. The stable authenticated Mini App was independently
observed showing **Kite connected**, **SuperTrend ALGO OFF**, **New entries OFF**.
Current read-only broker profile matched the stored client and declared holder;
existing durable deployment identity was retained. A stale application-account
setting was corrected using that proof, and ACTIVE binding verification refreshed.
No password, OTP, token or secret is included here.

Fresh read-only inventory showed zero nonzero net positions, zero open orders,
and zero active GTTs. Thus there was no observed exposure requiring a protective
receipt. This does **not** prove a real entry/fill/SL/GTT/trail/exit lifecycle.
Owner approval is still required separately for each real-money order.

Observed egress differed from the registered broker address. Developer-console
whitelist and registered redirect approval remain unverified; local configuration
is not broker approval. Binding receipts expire and must be refreshed at release.

## Executed validation

Raw command output and JSON receipts are collected under
`/tmp/sterling-readiness-20261018`; durable evidence will be archived separately.
Results below retain their tested source identity rather than implying that all
suites ran on the final commit.

| Command / scope | Result | Source / evidence |
|---|---|---|
| `python -m pytest -q` full backend | 11159 passed, 10 failed, 10 errors, 30 skipped | `backend-complete.log`; mixed source diagnostic run, not final certification |
| Python 3.12 release/safety/auth/recovery selection, 22 files | 488 passed | e23711e1; `python312-release-managed.result.json` records exact argv |
| Offline release E2E regression suite | 41 passed | e23711e1 source; `e2e-live-hedge-fixed.log` |
| Hedge/safety boundary selection | 144 passed | e23711e1; `hedge-boundaries-verified.log` |
| `npm exec vitest run -- --maxWorkers=2` | 219 files, 2090 tests passed | e23711e1; `vitest-complete.result.json` |
| `npm run build` (TypeScript + Vite) | Passed | `frontend-build-final.log` |
| Chromium live-operations + chart-workspace contracts, isolated port 5273 | 8 passed | `playwright-isolated.log`; mocked broker, no live orders |
| `npm audit --json` | Zero reported vulnerabilities | `npm-audit.json` |
| TMA/login/menu regressions | 71 passed | `tma-controls-tests.log` / related logs |
| Production/operator controls | 97 passed | `production-operator-verified.log` |
| State/artifact/scheduler regressions | 94 passed | `container-state-fixes.log` |
| Runtime packaging/public gateway regressions | 124 passed | `runtime-packaging-tests.log` |
| Launcher ownership tests | 67 passed | 53adf6a1; `supervisor-owner-tests.log` |
| Isolated shell supervisor child-exit test | 1 passed | 53adf6a1; real shell/isolated worker, no live process signals |
| Exposure/ownership/read-only polling tests | 109 passed | 11899ee8; `exposure-readonly-tests.log` |

Earlier interrupted commands are not passes. Initial E2E failures were repaired
and rerun. The system `/usr/local/bin/python3.12` lacks `_sqlite3`; that interpreter's
collection failures are an environment defect. Managed CPython 3.12.13 with SQLite
3.50.4 and checked dependencies ran the successful release selection.

## Runtime, recovery and backup evidence

The backend and public web gateway use enabled user systemd services. The configured ngrok hostname served the authenticated Mini App earlier; it now returns ERR_NGROK_725 because monthly bandwidth is exhausted. Local backend and gateway endpoints remain reachable and anonymous protected requests return 401. Tunnel capacity and registered broker redirect still need provider/owner resolution. The expired Cloudflare quick tunnel is not treated as a working URL.
A backend worker exit exposed conflicting recovery ownership; the web manager now
waits for the configured backend service instead of starting a competing worker.
The earlier SIGHUP was traced to the Git post-commit hook: it signalled a single-worker Uvicorn process, terminating it. The hook now records pending promotion and performs no broker-process signals or dependency installation. A foreground presentation service waits for an existing owner rather than exiting into a start-limit restart loop.

The root filesystem reached 99% occupied. No user data was deleted to make
checks pass. New backup evidence resides on the separate `/mnt/OS` filesystem.
Four real SQLite online backup/isolated restore copies passed integrity and
checksum checks, including both discovered `sterling_paper.db` files. Proof:
`/mnt/OS/SterlingReadinessBackups/20261018/independent-sqlite/proof.json`.
Canonical backup/restore-check correctly fails because required physical
`lane_manifests` are absent. No manifest, receipt, frozen release or acceptance was
fabricated; isolated database restoration is not full release restoration.
New configuration backup copies are encrypted; recovery key remains in a private
local state directory. Live credentials were preserved and are not in Git.

Docker daemon/configuration and actual image builds were exercised. Isolated
container persistence, artifact identity and crash-restart results are recorded
separately; synthetic containers never mount the live database or broker secrets.

## GitHub infrastructure finding

Expected-release jobs had `runner_id=0`, `steps=[]`. GitHub's check annotation:
“The job was not started because recent account payments have failed or your
spending limit needs to be increased.” This is an account infrastructure failure,
not a failed source test. No billing or spending authorization was changed.
Evidence: `actions-jobs.json`, `check-annotations.json`, `actions-cause.json`.

## Remaining certification gates

- Complete passing exact-commit backend/frontend/browser/release/protection suites.
- Resolve GitHub account runner-start failure and rerun remote checks.
- Prove broker-approved stable egress/redirect and fresh account binding.
- Prove real approved lifecycle/protective-order receipts, partial fills,
  idempotency, trailing and exits; no diagnostic substitutes for these.
- Complete moving-market reconnect/crash drills, authenticated WebSocket traces,
  lifecycle Telegram receipts and operator acceptance.
- Supply genuine physical frozen lane artifacts and pass full isolated release restore.
- Demonstrate sustained backend availability, host disk headroom and operational recovery.

Until these gates genuinely pass, production certification remains BLOCKED and
new-entry switching stays OFF.


## Subsequent remediation and exact-candidate receipts

Fresh real profile, holdings, positions, order and GTT reads passed again at the
later continuation. Stored account and holder identities matched; session expiry
was in the future. There were zero holdings rows, nonzero positions, open orders
or active GTTs. SQLite quick_check returned ok and an IMMEDIATE write transaction
was acquired and rolled back without changing user records. Receipt:
`broker-latest-private.json` (private; not committed).

The mixed-source broad run exposed import errors from loading the old state-path
module before new imports were edited into other modules. Those import failures
were absent in a fresh process. Two genuine Snapback retry regressions were
repaired: a durably allocated retry must prove every prior canonical hedge order
terminal with zero fill, no reconciliation conflict, correct quantity and order
identity. Unknown or partial outcomes remain blocked. The request must own the
current recovery revision; unrelated exposure and all operator safety gates stay
blocked. Relevant reruns: 97 passed plus 45 continuation safety tests.

A documentation check had incorrectly classified the physical database filename
and warnings about the limits of offline validation as obsolete execution modes.
It now checks mode offerings specifically; original database-preservation and
acceptance requirements remain intact. Quote-age tests use a fixed observation
clock rather than turning host slowness into a false stale-quote arithmetic bug.
Those documentation/clock/watchdog tests passed 71 checks.

The Mini App now distinguishes provider text/HTML denial from structured API
credential rejection, preserves session material on tunnel outages, and clears
cached financial/position values on actual authorization loss. Two actual Chromium
regressions passed using intercepted synthetic endpoints, with no broker writes.
Backend TMA/login/watchdog selection: 23 passed. The safe-mode CLI now loads the
same secure configuration and initializes alert storage before changing the halt;
34 environment/safe-mode checks passed. No authentication was bypassed.

Final exact-candidate command receipts and outcomes are stored in the durable
execution evidence directory. A passing unit or browser run never substitutes
for broker-approved network identity, full restored release artifacts, operator
acceptance or separately authorized real-order protection and recovery drills.


A strict lock-only installation additionally found four missing transitive pins:
attrs, charset-normalizer, requests and urllib3. The complete lock now produces
69 compatible packages under CPython 3.12; dependency audit reports no known
vulnerabilities. The earlier isolated environment had drifted to Pydantic 2.14.1
instead of the declared 2.13.5, causing fake-clock schema failures. That run was
stopped and is not a release pass. With corrected dependencies, all 20 admission
E2E checks passed. A protected hedge refused before broker submission can retry
only when the durable refusal and strict absence of a canonical broker reservation
agree; any existing or ambiguous reservation still blocks that exception.

All branch tips were preserved in a verified full-history Git bundle at
`/mnt/OS/SterlingReadinessBackups/20261018/branches-before-cleanup.bundle`.
The redundant remote release-fix branch had no commits outside main and was
removed after ancestry verification. Only main/dev remain as branches; detached
worktrees and all their files were preserved. Subsequent candidate commits must
also be synchronized and their exact SHA reported with final test receipts.
