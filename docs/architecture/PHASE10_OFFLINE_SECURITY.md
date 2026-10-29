# Sterling V2 — Phase 10 offline-only security and deployment envelope

**State:** draft, validation-only, no merge and **NO deployment**.
Standalone offline ASGI: `app.offline_security_profile:app`, not
`backend/main.py`. This isolates Phase 4–9 evidence from the existing live
Kite/order router. It does **not** claim legacy main.py is production hardened.

## Security boundaries

- Every `/api/v1/offline-validation/*` request requires a signed,
  unexpired, unrevoked Sterling access JWT checked against the existing
  account token version. No `X-User-Id` development identity, query token,
  simulation identity, anonymous viewer or header-supplied role is trusted.
- Role model: **viewer** denied; **operator** GET evidence and metadata-only
  alert acknowledgement, no resolve, no source creation/transport; **admin**
  GET and alert metadata operations only after TOTP second-factor step-up,
  with admin-only alert resolution server-side. Nothing can mutate strategy
  versions, broker state or gate from the HTTP route allowlist.
- MFA is a 6-digit RFC6238-compatible TOTP verified against a private
  user-id→base32 secret map, supplied from an absolute **non-symlink
  0400/0600** file outside the repository. The 5-minute JWT proof has its
  own issuer/type and is bound to the **exact** Sterling bearer token hash;
  refresh, logout/revocation or account switch invalidates the binding.
  Proof is kept only in frontend memory. Bad or absent MFA denies admin
  offline evidence and is never silently bypassed.
- Login/refresh/me/logout paths are individually mounted from existing
  Sterling auth handlers; the broad auth router and its other routes are
  **not** mounted. `remember` (30-day refresh) is absent. Access TTL is
  5 minutes and ordinary refresh TTL is 1 hour in the isolated profile.
  In addition, refresh from a prior legacy `remember` (30-day) token,
  or any token with a signed TTL longer than one hour, is explicitly
  rejected before the existing refresh handler runs. Backend controls and
  existing refresh token revocation apply; a separate per-device,
  durable refresh-token rotation store is **not** certified.
- Existing rate limiter protects the MFA proof endpoint in addition to the
  primary login path. It remains process-local rather than a validated
  distributed MFA lockout; production multi-instance rollout is **blocked**.
- Environment must explicitly be `production` (no development anonymous
  seam), `STERLING_OFFLINE_ONLY=true`,
  `STERLING_OFFLINE_REQUIRE_MFA=true`,
  `STERLING_OFFLINE_NO_BROKER=true`, `PAPER_TRADING=true`, verified
  distinct 32+ character Fernet/JWT secrets, private MFA file, PostgreSQL
  runtime URL. The preflight refuses unsafe/missing fields.
- Even with these checks, this is NOT a certification of historical
  `backend/main.py` endpoints, other standalone deployments, active
  Kite broker accounts, system OS/host identity, or real broker exposure.

## Separate database owner, app role and operator identity

Run `deploy/offline-only/privileges.sql` **only** on a freshly migrated,
isolated offline PostgreSQL database as its *separate migration owner*.
The script first refuses any database whose name does not match
`sterling_offline_*` or the disposable `sterling_alert_ci` CI database.
**Name matching alone does not prove isolation**; the operator must
independently attest host, cluster, DB identity and ownership.
The script creates a `NOLOGIN` role
`sterling_offline_runtime` (no superuser, DDL, role creation or database
creation), revokes public schema creation and grants only SELECT on
Phase 1–10 versioned offline ledgers, plus **INSERT** into the isolated
Phase 9 alert/Phase 10 health event tables for append-only receipts.
Database-level `CREATE` and `TEMPORARY` are revoked from PUBLIC
and the offline role, because temp-table/search-path shadowing can spoof
unqualified SQL queries. Startup also rejects database `CREATE`,
`TEMPORARY`, `BYPASSRLS` and inherited forbidden grants. Isolated CI
probes both temporary relation and schema creation denials.
There are **no UPDATE/DELETE/TRUNCATE/TRIGGER** permissions on these
tables, and no write privileges on strategy registry, gate, scheduler,
decision or hypothetical trade tables.

A separate operator creates a unique least-privilege LOGIN account
and explicitly grants membership in this NOLOGIN role. The runtime
credentials MUST NOT be schema/table owners. Startup
`offline_database_privileges.assert_runtime_privileges()` fails closed
if the runtime is owner, superuser, schema creator or has forbidden
registry/ledger grants. Read-only evidence services use the
`_strategy_readonly` owner-scoped SELECT rather than acquiring an
UPDATE-privileged `SELECT FOR UPDATE` lock. Existing mutation
methods and legacy authority are unchanged.

**Important limit:** a compromised runtime INSERT credential could
still forge a synthetic alert/health row or chain while it retains INSERT.
Append-only triggers stop rewriting/deleting rows, not forged inserts by
an authorized writer. An independent SECURITY DEFINER/SP-bound writer,
per-actor DB identity, immutable external notarization/WORM and
least-privilege production approval are still **blocked**. A
database/superuser who controls DDL can disable triggers; PostgreSQL
triggers do **not** defeat superuser access. No production anti-tamper
certification is claimed.

## Non-executing deployment scaffolding

- `backend/Dockerfile.offline`: separate non-root process and **only**
  `uvicorn app.offline_security_profile:app`.
- `deploy/offline-only/compose.yml`: no backend or PostgreSQL host port;
  internal-only Docker network (no external provider egress), no-new-
  privileges, read-only root filesystem, no always-on worker, no
  automatic migrations; frontend port **127.0.0.1:8088** only.
- `frontend/nginx.offline.conf`: an offline-only API allowlist.
  Every legacy `/api/*` route defaults to 404. Exact auth/MFA and
  evidence paths proxy to the isolated ASGI process. WebSockets/order
  routes are never forwarded.
- No actual production TLS endpoint or certificate is configured.
  A trusted TLS-terminating edge, host-name validation, HSTS after
  HTTPS verification, session/cookie transport policy, CSP testing,
  IdP/MFA recovery and provider-network policy are independent
  **pre-deployment blockers**. Never expose port 8088 to the internet
  or publicly tunnel this profile without completing them.
- `STERLING_OFFLINE_ENV_FILE` and `STERLING_OFFLINE_SECRET_DIR` must
  reference independent private operator-managed paths, not committed
  templates. The runtime MFA file must be owned/readable by UID 10001,
  mode 0400/0600. Secrets may be present in process environment while
  running; use a hardened host secret manager before production.
- Auth storage is a separate offline-only volume (`/app/data`); the
  process explicitly opens `STERLING_DB_PATH=/app/data/offline_auth.db`,
  initializes its own login-store schema, and refuses startup unless
  `user_store.has_active_admin_credential()` confirms a credential.
  It is **not** the signed-in legacy account store. An independent secure
  admin password bootstrap or a verified identity migration must precede
  first login. On a fresh volume without that bootstrap, startup refuses
  instead of granting anonymous access. The auth DB includes historical
  SQLite schema objects but the isolated HTTP router exposes no broker
  actions. The volume must be owned by UID 10001.

## Read-only monitoring and health evidence

`GET /healthz` reports process liveness and broker not configured.
`GET /readyz` tests isolated PostgreSQL connectivity. The authenticated
`GET /api/v1/offline-validation/monitor` reports DB/read-only
observability mode, worker disabled, broker not connected, market data
not independently probed, queue source needing per-strategy verification
and no external notification receipt. It is intentionally **NOT a
real-broker health API**.

An administrator may explicitly sample source audit status through
`OfflineHealthRecorder.sample_once()` (Python service only, no HTTP
write endpoint). New Alembic revision `f10a829d2c61` adds a
hash-chained, immutable `platform_offline_health_events` ledger with
operator actor/time, source audit status, and
`execution_authorized=false`. Authenticated GET
`/strategies/{id}/health-history` replays verified snapshots. There is
no unsupervised monitoring daemon or OS timer and no inferred healthy
broker, strategy profitability or execution permission.

## Backup, restore and rollback — policy

1. **Before anything**: freeze exact source revision, Docker image digest,
   immutable migration revision, source manifests, signed operator identity,
   sanitized settings and DB role grants; export an encrypted offline
   PostgreSQL 16 `pg_dump -Fc` to private storage.
2. Rehearse `pg_restore --no-owner --exit-on-error` into a **different**
   disposable database/container with independent credentials.
   Verify SHA256 of archive, row counts, alert and health hash chains,
   immutable triggers, role privileges, owner separation, and signed
   operator access. Restored roles and grants must be re-provisioned
   separately (PostgreSQL role metadata is cluster-scoped).
3. On a failed canary: stop the isolated frontend/backend containers,
   roll back the *container image/config revision* to the previous
   signed offline version. Do **not** enable the legacy broker router.
4. Database downgrade of append-only evidence is destructive;
   NEVER run `alembic downgrade` on production evidence. Restore into
   a **new isolated database** from the verified immutable pre-change
   archive, validate audit chain/owner/grants, then switch only the
   offline application after authorized review. Keep old data archived;
   do not overwrite it.
5. CI uses disposable PostgreSQL16 and independently rehearses
   upgrade, INSERT failure rollback, pg_dump/pg_restore, denial of
   privileged UPDATE/DELETE/TRUNCATE/DDL/row locks, and disposable
   downgrade/re-upgrade. This is **not** a real production restore.
6. The PostgreSQL gate also assumes the restricted NOLOGIN role and
   reads the already validated synthetic analytics, alert ledger, and
   appends/replays one immutable offline health snapshot using the actual
   runtime grants. Literal denial SQL inputs live in an isolated CI
   fixture; no static broker-access policy exclusions were added.
7. No CI/deploy job may push to `main`, publish a production artifact,
   invoke Docker Compose `up`, run a broker identity check or place
   an order. The configuration validation only parses Compose.

## Admin/operator/viewer matrix

| Action | Viewer | Operator | Admin with fresh MFA |
|---|---|---|---|
| Offline evidence, KPI, audit, alerts, health GET | Deny | Read | Read |
| Alert review acknowledgement | Deny | Metadata only | Metadata only |
| Alert resolution | Deny | Deny | Metadata only |
| MFA verify | Deny | Deny | Verify own TOTP |
| Offline health snapshot | Deny | Deny | Explicit service only |
| Registry/version/gate/queue change via isolated UI | Deny | Deny | Deny |
| Telegram/WhatsApp/email dispatch via isolated UI | Deny | Deny | Deny |
| Broker order, live switch, market-data worker | Deny | Deny | Deny |
| Migrations, schema owners, role DDL via runtime DB | Deny | Deny | Deny |

## Certification caveats and next gate

The Phase 10 targeted CI certificate must not be represented as
production-ready: external TLS, user bootstrap/MFA device enrollment,
real hardware/web browser test, independent vault, secrets rotation,
distributed brute-force controls, privileged append forgery prevention,
immutable external archive, production DB isolation, actual backup
restore, full app scans, monitoring worker/device receipts and
legacy broker access findings (272 previously classified) are still
separate acceptance gates.

**No merge, deployment, live broker, new execution authority or
Phase 11 work**. Prior Phases 1–9 PRs remain draft.
