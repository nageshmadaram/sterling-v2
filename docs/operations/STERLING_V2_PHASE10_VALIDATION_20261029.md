# Sterling V2 — Phase 10 Offline Production Security Validation

**Date:** 2026-10-29
**Disposition:** Targeted isolated offline security/readiness validation **PASS**;
**actual production deployment, live trading, and final release certification BLOCKED**.
**Draft PR #12:** https://github.com/nageshmadaram/sterling-v2/pull/12
**Validated implementation code head:** `631b18b16282c8049efd20c52b7f5b7ba7ff1934`
**Successful 3-job workflow:** https://github.com/nageshmadaram/sterling-v2/actions/runs/40818346052

## Observed, real CI results

| Phase 10 acceptance slice | Result |
|---|---|
| JWT first-factor role, expired/missing/forged proof denial, owner isolation | PASS |
| Admin TOTP from permission-checked 0400/0600 secret file | PASS |
| Step-up bearer binding, short expiry, non-authoritative MFA response | PASS |
| Operator metadata-only access; viewer/virtual identity refused | PASS |
| Legacy remembered 30-day refresh denied on isolated endpoint | PASS |
| Explicit production-only, paper-only, no-broker security config failures | PASS |
| ASGI route inventory denies Kite orders, legacy execution, worker/stream paths | PASS |
| Authenticated offline monitor and bounded health-history view | PASS |
| Manual non-authoritative health snapshot, actor/time/hash replay | PASS |
| SQLite immutable health UPDATE/DELETE, forced insert rollback | PASS |
| Phase 1–9 backend/registry/scheduler/broker/alert safety regressions | **147 passed, 9 inherited warnings, 0 failed** |
| Existing Phase 8+9 UI and new admin MFA rendering/submit flows | **18 Vitest passed** |
| Frontend TypeScript check and release-mode compile (no deployed artifact) | PASS |
| Nginx API default-deny profile, backend non-trading entrypoint, loopback-only networking (static) | PASS |
| Docker Compose syntax with ephemeral synthetic config, no `up`/deployment | PASS |
| Phase 2B broker audit | **0 newly unreviewed, 272 inherited classified findings** |
| Disposable PostgreSQL16 additive health migration and Phase 9 immutable records | PASS |
| Role grants prevent direct registry/ledger UPDATE, DELETE, TRUNCATE, row-lock, DDL and trigger disable | PASS |
| Runtime-role functional offline analytics/alerts SELECT and health INSERT + hash-backed replay | PASS |
| PostgreSQL16 forced alert insert rollback, isolated pg_dump/pg_restore, row counts | PASS |
| Disposable-only Alembic downgrade/re-upgrade | PASS |
| Actual production infrastructure, authenticated IdP/MFA device enrollment, physical restore | **NOT TESTED** |
| Real broker identity/orders, actual live-disable verification, market connectivity, production TLS | **BLOCKED / NOT CERTIFIED** |

The nine inherited warnings are seven Python invalid string escape warnings
in the legacy Phase 2B audit tool and two third-party TestClient deprecations.
Warnings are not release waivers.

## Incremental test/fix record

- Initial [run 40817431069](https://github.com/nageshmadaram/sterling-v2/actions/runs/40817431069):
  146 backend tests, 18 frontend tests, PG16 role denials/restore PASSED.
  A static source check failed because a comment contained the substring
  "from main"; no trading import or route was added.
- [run 40817782303](https://github.com/nageshmadaram/sterling-v2/actions/runs/40817782303):
  static check passed. A newly added legacy `remembered` refresh rejection
  test returned HTTP 422 due to a request-binding mismatch; corrected to
  validate the JSON body before checking signed refresh TTL. The security
  behavior was not considered certified until that test ran.
- [run 40817982950](https://github.com/nageshmadaram/sterling-v2/actions/runs/40817982950):
  147 backend tests and PG16 proofs passed. The broker access scanner flagged
  **three literal SQL writes embedded in a negative privilege test**
  (`UPDATE/DELETE/INSERT`), correctly classifying them as newly
  unreviewed direct registry mutation patterns. Rather than suppressing or
  adding a blanket baseline exception, the exact nine denied SQL statements
  were moved to an explicit *disposable role-denial fixture* loaded in tests.
- Final [run 40818346052](https://github.com/nageshmadaram/sterling-v2/actions/runs/40818346052):
  **ALL 3 jobs SUCCESS**, 147 backend PASS, 18 frontend PASS,
  TypeScript and production compile PASS, zero new unreviewed broker
  patterns. PostgreSQL role-denial probes plus **functional restricted
  operator reader and health event append** passed. Standalone Compose
  configuration parsed successfully; nothing was deployed.

## Delivered, limited-scope artifacts

- `backend/app/core/offline_security.py` — strict signed-role and
  per-admin TOTP step-up checks; JWT proof bound to current bearer;
  no embedded/committed TOTP secret values.
- `backend/app/offline_security_profile.py` — independent, strictly
  allowlisted ASGI route set, no main.py router or legacy broker orders.
  Signed auth endpoints only, no remembered sessions, admin MFA
  perimeter, read-only /healthz, /readyz, /monitor and audit health GET.
  Dedicated operator identity DB and credential startup preflight.
- `backend/app/core/offline_database_privileges.py` and
  `deploy/offline-only/privileges.sql` — runtime DB role preflight
  and isolated Postgres SELECT / append-only INSERT grants with
  no registry/schema/DDL/delete/update privileges.
- Read-only registry ownership helper for evidence readers, preserving
  existing mutation methods and execution authority unchanged.
- `f10a829d2c61` additive health history migration and
  `OfflineHealthRecorder` — explicitly invoked, immutable, SHA256-
  linked health records; absent broker/provider/worker evidence
  stays explicitly unavailable.
- `backend/Dockerfile.offline`,
  `deploy/offline-only/compose.yml`,
  `frontend/nginx.offline.conf` — non-root backend, internal-only
  Docker network, loopback-only frontend, default-deny API proxy,
  no broker-worker image command and no Docker `up` in validation.
- In-memory admin MFA proof in the existing evidence UI and
  isolated-proofs-only request headers. The Phase 8/9 operator view
  remains separately auditable and non-executing.
- Targeted `test_offline_security_phase10.py` and
  `test_offline_health_phase10.py`, PG16 privilege-denial script and
  explicit JSON denial fixtures, security CI.
- `docs/architecture/PHASE10_OFFLINE_SECURITY.md` — role matrix,
  safety configuration, restore/rollback policy and blocked gates.

## Hard remaining production blockers

1. **Not production deployed or broker-certified.** The existing main.py
   trading router, live Kite authentication and execution controls were
   not altered or independently validated. The secure profile is a
   *separate entrypoint*, not a replacement for the deployed Sterling
   app or a proof that legacy live authority is safely disabled.
2. **Authentication bootstrap and MFA enrollment are incomplete.**
   CI uses synthetic JWTs and a fixture TOTP secret. A real operator
   account store, MFA enrollment/rotation/recovery, device login,
   multi-instance lockout/session revocation and independent IdP proof
   remain outstanding. The separate profile refuses startup without
   an active strong admin credential and its required secret file.
3. **TLS and edge controls are not deployed.** The frontend is bound to
   loopback, not exposed publicly; a verified TLS terminator, HSTS,
   certificate management, DNS/host identity and end-to-end browser CSP
   proof are still necessary. Compose static validation is not a
   running canary or a production image vulnerability assessment.
4. **Write privilege caveat.** The runtime cannot UPDATE/DELETE audited
   records or mutate strategy/gate/queue tables. It still holds INSERT
   permission for alert and health records. A compromised account could
   forge an append event. Independent security-definer writers,
   privileged DB hardening, WORM/external notarization, per-actor
   identity and superuser/DDL protection are NOT certified; privileged
   DB owners can still bypass triggers.
5. **Health is explicitly incomplete.** Broker connectivity is
   `not_connected`; market-data health, worker health, actual broker
   exposure and external alert delivery have no authoritative proofs.
   `OfflineHealthRecorder` is manually sampled, not an always-on
   monitoring or incident delivery worker.
6. **Restore is disposable only.** No production isolated restore,
   verified immutable archive, off-site encrypted backup, secret
   rehydration, production rollback or operator-controlled canary
   was performed. CI downgrade is destructive and must never be used
   against retained evidence.
7. **Full app/mobile and alert delivery remain unverified.** No real
   browser/mobile walkthrough, screenshot/video evidence, Telegram/
   WhatsApp/email actual channel receipt, app load/availability SLO,
   accessibility acceptance or production network inventory.
8. **272 inherited direct broker-access findings** remain in the
   pre-existing scanner inventory, including 30 legacy broker-write
   and 4 legacy execution-authority patterns. Zero *new* findings
   does not mean those existing paths are approved.
9. **No Phase 11, merge, deployment, order authority, or live switch.**
   All twelve stacked PRs must remain open drafts. Main is unchanged.

**Final disposition: targeted Phase 10 offline safety proof PASS;
production readiness and any live trading certification BLOCKED.**
