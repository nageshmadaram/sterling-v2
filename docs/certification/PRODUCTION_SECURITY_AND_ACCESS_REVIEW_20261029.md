# Sterling V2 — Independent production security / operator access review

**Review date:** 2026-10-29. **Decision:** PRODUCTION DEPLOYMENT **NO-GO**; LIVE TRADING **NOT AUTHORIZED**.
**Work conducted:** read-only source/document review of Phase 11 candidate `ce702ab1622e53f6233e1bf9a235ef87fc297a8f`; no host, vault, TLS terminator or real user session accessed.

| Control | Source-level observation (NOT host proof) | Real-world acceptance required | Status |
|---|---|---|---|
| H01 — OS/host/runtime | `deploy/offline-only/compose.yml` uses a loopback web binding, internal Docker network, read-only backend, dropped capabilities, no restart | Independent approved host inventory, OS patch/identity audit, actual container policy, DNS/ingress/egress reachability, isolation and non-root verification | **BLOCKED** |
| T02 — TLS/public edge | Compose binds `127.0.0.1:8088`; no verified external TLS endpoint, certificate chain or rotation receipt | Record approved endpoint, cert chain, validity, renewal drill, reverse proxy and headers, external-client observations, edge trust configuration | **BLOCKED** |
| S03 — secret custody | Dedicated secret-file inputs and secret checks exist as code/config only | Vault ACL, secure bootstrap, runtime key isolation, incident revocation, rotated secret reference and access-denial audit; never store raw secrets in GitHub | **BLOCKED** |
| I04 — real operator RBAC/MFA | Offline profile checks signed access tokens and TOTP for admins. **Concrete gap:** `backend/app/services/auth/user_store.py` role model supports `admin|user`, not production-provisioned `operator|viewer`; synthetic tests do not prove real enrollment | Securely enroll named admin/operator/viewer roles in an isolated non-authoritative identity plane; verify MFA enrollment/step-up/rotation/recovery/revocation, multi-instance controls, tenant ownership and role denials; prove NO execution permissions | **BLOCKED** |
| D05 — production DB privilege | `offline_database_privileges.assert_runtime_privileges` checks PostgreSQL role, DDL/TEMP, BYPASSRLS and table privileges. CI validates a disposable DB, **not** a real cluster | Independent runtime/owner IDs, schema/table/sequence/function/default privileges, public/inherited grants, effective transaction behavior, tamper-proof audit export and isolated nonproduction write-denial probes | **BLOCKED** |

## Acceptance protocol (none performed)

- Scope is a **production-like isolated acceptance environment**, not any currently trading Sterling host. Identify exact owner, environment ID, asset inventory, approved change window and observer before probing.
- Every observation needs timestamp (UTC), tested candidate image/source digest, configuration identifier, measurement, failure-mode test, redacted evidence hash, independent reviewer and signed decision.
- Role denial must include forged/expired token, tenant crossing, revoked login, missing MFA, insufficient operator permissions and admin non-execution. Do not store access tokens, OTP seeds, passwords or account identifiers in this repo.
- Denial tests are to run only on an approved isolated system with explicit ownership. Do not modify a running production DB, refresh broker sessions, bypass controls or start an app from this document.

**No real-world H01/T02/S03/I04/D05 evidence was supplied; 0 controls accepted. Offline CI PASS is not production security PASS.** 
