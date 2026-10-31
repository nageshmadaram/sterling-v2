# Operational recovery and evidence drill — Sterling V2

**NON-PRODUCTION ONLY. NO BROKER WRITES. NO-GO.** A repository script does not prove host recovery; this runbook describes the missing independent validation.

## Safety invariants

- Keep `STERLING_LIVE_EXECUTION` disabled and retain durable safe mode throughout these exercises. No production tokens or real account sessions in fixtures.
- Never direct a migration at the active Sterling operational SQLite database. Existing migration preflight must prevent this. Use disposable database copies and a separate PostgreSQL instance.
- UNKNOWN/SUBMITTING orders are reconciled by read-only evidence, never resubmitted. On broker-evidence mismatch, latch RECOVERY_REQUIRED and preserve the durable journal.
- Production rollback means independently restoring the snapshot and verifying row-level content; blindly downgrading Alembic migrations is not an accepted recovery process.

## Required isolated drills

| Drill | Procedure | Evidence required | Status |
|---|---|---|---|
| SQLite restore | Hash backup/manifest, restore to new path, verify schema, counts, content, journal and immutable execution ids | Before/after hashes and integrity logs | NOT_EXECUTED_ON_EXTERNAL_HOST |
| PostgreSQL | Upgrade disposable DB, rerun migrations, compare schema/data, fail injected partial migration, restore independent snapshot | Connection target proof (redacted), upgrade/restore logs | NOT_EXECUTED |
| Crash after reserve | Halt synthetic process between RESERVED, SUBMITTING and broker acknowledgement | Journal state and no second dispatch on restart | NOT_INDEPENDENTLY_WITNESSED |
| UNKNOWN reconcile | Simulate lost ACK; recover only via exact broker tag/order/trade identities | No-double-send negative, alert and durable halt record | NOT_INDEPENDENTLY_WITNESSED |
| Partial fill | Duplicate trade ID, partial fill, product mismatch, terminal reconciliation | Idempotent ledger hash and remaining exposure proof | NOT_INDEPENDENTLY_WITNESSED |
| Auth revocation | Rotate token/role/grant during a mounted session; refresh without privilege | HTTP and browser proof | NOT_INDEPENDENTLY_WITNESSED |
| Offline reconnect | Disconnect transport/notification and recover without mutating broker | Sanitized alerts and escalation receipt | NOT_INDEPENDENTLY_WITNESSED |

## Local source-only commands

```bash
python scripts/verify_sqlite_recovery.py --help
python scripts/audit_alembic_rollback.py
python scripts/validate_preproduction_controls.py
python scripts/preproduction_inventory.py --output /tmp/sterling-inventory-review --strict
```

Use a fresh output directory for each inventory run. These commands are **suggested and not claimed to have run on an external host**. The SQLite tool needs review of its arguments before invoking a restore; never execute an unreviewed restore against the operational DB.

Archive exact SHA, run id, UTC time, sanitized stdout/stderr, hashes, negative-case assertions, independent witness and recovery time. Credentials, bearer headers, private keys and full broker account identifiers must not enter the evidence package.
