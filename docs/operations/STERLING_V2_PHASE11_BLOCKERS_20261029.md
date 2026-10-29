# Phase 11 certification blocker register — NO-GO

**Production deployment: NO-GO. Live trading: NOT AUTHORIZED.** A passing offline regression is not evidence that any of these production gates passed.

| ID | Blocker (all open) | Independent acceptance evidence still required |
|---|---|---|
| B01 | Host/OS/runtime hardening | Signed host inventory, user permissions, isolation, container runtime and attack-surface inspection on actual operator host |
| B02 | TLS and public edge | Certificate chain, renewals, HSTS, DNS, correct virtual host, CSP, trusted proxy, real device capture |
| B03 | Operator provisioning / MFA / session lifecycle | Real admin/operator/viewer enrollment and revocation, privileged MFA enroll/rotate/recover, device/session proofs, lockouts |
| B04 | Production database isolation and audit | Physically isolated DB, owners vs runtime identities, effective inherited grants, DB-backup encryption, independent append immutability/WORM |
| B05 | Real monitoring and notification delivery | Host metrics, alert delivery confirmations from destination devices, error budgets, operator runbook, recoverability |
| B06 | Actual broker acceptance | Fresh broker login and identity, account/inventory/exposure, approved outbound address and callback proof; **no orders during Phase 11** |
| B07 | Market-data and market acceptance | Live session feed freshness/sequence, stale/fallback behavior on approved test infrastructure, disconnect drills |
| B08 | Legacy broker access | Disposition/remediation of **272 inherited classified** access findings (including legacy write/authority classes); zero new unreviewed patterns is not resolution |
| B09 | End-to-end application/device QA | Real browser, mobile, accessibility, auth navigation, performance, concurrent sessions and failure-path evidence |
| B10 | Production restoration and release rollback | Separate isolated restore of actual encrypted archive, hash-chain verification, exact schema/release rollback and operator signatures |
| B11 | Live trading certification | Broker-approved real execution/protection/recovery receipts; independently authorized separate process, never inferred from offline evidence |
| B12 | Production security operations | Dedicated secret vault, rotation, SBOM/vulnerability review, audit retention, external risk acceptance and release approvals |

## Evidence ledger policy

Each blocker requires an immutable evidence reference, observer, timestamp, relevant environment, and explicit pass/fail decision. No blocker is marked passed by a mock, a synthetic SQLite/Postgres run, a passing frontend build, a configured but unused credential, or a paper trading flag. Unverified means **blocked**, never passed-by-default.

## Release separation

`PASS (offline non-authoritative)` may be recorded only for validated test slices. `PRODUCTION NO-GO` and `LIVE TRADING NOT AUTHORIZED` remain unconditional for Phase 11. No production deployment or merge.
