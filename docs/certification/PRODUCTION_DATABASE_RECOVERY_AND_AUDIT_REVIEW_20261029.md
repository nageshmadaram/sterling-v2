# Sterling V2 — Production database, independent recovery and audit evidence

**Current disposition: PRODUCTION DEPLOYMENT NO-GO; LIVE TRADING NOT AUTHORIZED.**

## Distinguish existing offline evidence from required proof

| Area | What has passed in Phase 11 | What remains unverified and blocks acceptance |
|---|---|---|
| PostgreSQL migration / downgrade / restore | Disposable PostgreSQL16 schema, privilege-denial, `pg_dump` checksum, `pg_restore`, schema migration reversibility | Exact real cluster/DB identity and owners, encrypted backup custody, independent restore to a *separate* isolated host, service/app data integrity, RTO/RPO observation, operational rollback |
| Access control | SQL role denies CREATE/TEMP, DDL and forbidden mutations in a test DB | Effective real roles and membership, default privileges, function/sequence grants, search path trust, superuser/operator boundaries, policy on external role changes |
| Audit chain | Synthetic ledger hash checks, append-only triggers, read-only runtime registry | External immutable/WORM audit receipt, authenticated source attribution, audit-archive continuity, privileged/owner tamper threats and independent detection/recovery |

## Proof protocol (not executed)

1. Collect signed isolated **non-production** database and host identifiers and backup ownership before any action.
2. Gather sanitized role/grant evidence using an approved read-only inspection identity; *never* point migration/restore or denial scripts at a running production DB.
3. Have a separate independent operator restore a verified encrypted backup to a new isolated environment with distinct credentials. Record archive SHA256, restore timestamp, verified schema, audit chain, recovery objectives and approval.
4. Perform rollback/failure drills only on explicitly disposable copies, with signed before/after receipts. No downgrade or destructive mutation on retained evidence or production data.
5. Independently verify that offline runtime cannot submit orders or invoke a broker writer even if audit storage fails.

**Evidence received:** none from actual production-like environments. **D05 and R06 remain BLOCKED.** CI database tests do not count as independent real-world restoration.
