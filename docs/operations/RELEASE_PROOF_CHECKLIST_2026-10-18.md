# Production acceptance: evidence still required (2026-10-18)

**Release state: BLOCKED.** This checklist is an evidence contract, not an
attestation. The 151-row issue-disposition CSV remains authoritative; do not
close its entries without objective artifacts. Every receipt and measurement
must identify the exact deployed commit, release tag, UTC timestamp and
operator/test environment. Redact access tokens, account numbers, PII and
positions before sharing outside the authorized operator group.

## Global constraints

- Keep `STERLING_LIVE_EXECUTION=false` until the separately authorized go-live.
  Unknown broker, feed, position or protection state is NOT FLAT and must not
  authorize additional exposure.
- Never delete, rename, truncate or reinitialize `sterling_paper.db`.
  Preserve original local staged, unstaged and untracked changes.
- The `sterlingctl acceptance` harness is **read-only**. It can validate
  live feed/reconnection/persistence, but it does not prove a broker accepted,
  filled, protected, trailed or exited an actual order.
- Record UNKNOWN/BLOCKED when host, broker, hardware or API evidence cannot be
  obtained. Do not substitute doubles, mocked receipts or GitHub CI output.

## Unresolved gates and minimum credible proof

| Gate | Acceptance evidence | Operator checks / safe first action |
| --- | --- | --- |
| Broker authority and protection | Current Kite identity agrees with authorized broker/developer console, registered callback URL, static whitelisted outbound IP and deployed `STERLING_DEPLOYMENT_ID`; timestamped broker positions, orders and held protective orders are reconciled against Sterling | `scripts/sterlingctl bindings`, `egress`, `exposure`, `reconcile`; independently inspect developer console and live broker order/GTT status. A locally observed public IP is **not** proof of whitelisting |
| Genuine moving-market lifecycle | Actual broker order IDs and timestamped accepted/partial/full fills, broker-side initial stop, each stop/trail acknowledgement, exit fill and restart/reconnect reconciliation for the exact build | Requires separately approved limited-risk live order test and a supervised market session; keep new entries halted until approved. A read-only market acceptance or simulator cannot close this gate |
| Deployment freeze and recovery | Exact source SHA, immutable local tag, clean source, valid frozen `data/manifests/release.json`, all corresponding `data/manifests/strategies/*.json`, matching state root, post-freeze backup checksums and **isolated restore PASS** | `scripts/sterlingctl verify`; if never frozen, follow `FINAL_RELEASE_RUNBOOK.md` before `backup` and `restore-check`. Do not fabricate old lane manifests or blindly re-freeze an already frozen different release |
| Disk headroom | Before/after `df -h /`, accountable inventory of same-filesystem large directories, adequate reserve after safe cleanup, and a successful disk-full/write-failure drill | `df -h /`; `du -xhd1 ~ /var 2>/dev/null`. Inspect before deletion; do not prune volumes/databases or delete `sterling_paper.db` |
| Docker smoke | Responsive daemon, production compose config validates without revealing secrets, images build, backend/frontend healthy, persistence survives a controlled restart | `timeout 15s docker info`; `systemctl status docker --no-pager`; `journalctl -u docker -n 100 --no-pager`. Run compose smoke only after a responsive daemon and safe secret configuration |
| Telegram and Mini App | Telegram API response plus independently observed **received** alert and command response, command authorization/replay behavior, current public HTTPS Mini App URL matching deployment settings, and successful physical Android device login/deep link | Keep bot keys private; capture redacted message IDs, chat receipt timestamps, URL/redirect configuration and actual device screenshots. HTTP 200 alone is insufficient |
| Native advisories | Current lockfile/SBOM and audit outputs listing direct/transitive affected versions, fixed version compatibility, reachability and a justified disposition for every high/moderate finding | Do not force incompatible downgrades. For an unfixed upstream issue, document mitigation, owner and review date |
| Production performance | One fixed production SHA, comparable market/session load, browser CPU and long-task trace, backend API and WebSocket latency p50/p95/p99, samples/units/error rate, before/after comparison and reproducible measurement method | Record a real production browser profile and host metrics; report `UNKNOWN` until measurements exist. Synthetic performance does not establish a real-market baseline |

## Release closeout

1. Establish a stable deployment, account and storage identity; safeguard a
   verified backup before any database or deployment migration.
2. Complete genuinely read-only broker and live-feed acceptance; reconcile
   external exposure and inspect broker-side protection.
3. Finish safe host remediation (disk, Docker), Telegram/Mini App physical
   checks, advisory triage and reproducible performance data.
4. Complete separately authorized, supervised actual-order lifecycle proof
   before asserting live-execution readiness.
5. Freeze an **exact** release, make a post-freeze backup, prove an isolated
   restore and run final verification and certification. Defer CI/CD workflow
   engineering until the release-critical runtime and safety items are fixed,
   but do not call the release certified while mandatory exact-SHA checks fail.
6. Update each of the original 151 issue IDs individually. Retain proof paths,
   observed dates, failures and open blockers. Do not infer closures from code
   commits, documentation changes or test doubles.
