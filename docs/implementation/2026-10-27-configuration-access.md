# Account overhaul: configuration access and independent administration

Current status: [workspace flow and central administration](2026-10-27-workspace-flow.md). The notes below describe the earlier slice.

Development slice 2, following the account-policy foundation. This checkout is not a deployed or accepted release.

## Implemented behavior

`/administration` now loads a separate lazy page after Sterling identity verification. It provides existing account CRUD, per-user Kite/TV/Radio grant editing, independent Broker/Telegram/Integration visibility, and action/resource delegation. It does not mount the dashboard, preference bootstrap, live resync, feed badge, or broker-link handler. A remembered Simulation view and a `broker_login=1` navigation hint do not start Kite from Administration. Account status labels now say Active/Suspended rather than implying a live connection.

Schema version 2 adds configuration sections and scoped actions to the same durable database. Feature and configuration edits share an optimistic revision. Section changes, audit and invalidation-outbox insertion commit together. Missing, hidden, inactive, expired or unavailable policy denies configuration. UI polling every ten seconds and on focus removes revoked controls; server checks are authoritative on each request. Broker writes recheck permission after asynchronous exposure probes and after waiting for the credential lock.

Existing credential ownership no longer implies configuration authority. Ordinary users retain their assigned connection/session controls while technical key forms, add/remove actions, and Telegram/provider settings are filtered by policy. Permitted Telegram recipients/test controls use a separate panel with no bot-token form. Integration credentials use action-filtered existing forms. Old broker reveal is closed for every identity until MFA-bound reveal challenges are implemented; no raw key is returned. Delegated replacement never reads a saved secret into a form.

Administration can be opened directly at `/administration`, or from the link in Connect. It currently uses the existing `/auth/users` contracts as adapters; migration to the full final `/admin` API is pending.

## Configuration route inventory

The following are implemented HTTP guards, including direct endpoint-function calls. Provider and account ownership checks remain in addition to these guards.

| Surface | Policy |
| --- | --- |
| `POST /api/v1/kite/accounts` | Visible Brokers + own-scope create |
| `PUT /api/v1/kite/accounts/{id}` | Visible Brokers + scoped update; existing exposure checks; recheck after probe |
| `DELETE /api/v1/kite/accounts/{id}` | Designated admin + existing safe-detach checks |
| `POST /api/v1/kite/accounts/{id}/credentials/reveal` | Denied until MFA-bound reveal challenges exist |
| `PUT/DELETE /api/v1/kite/accounts/{id}/login-credentials` | Visible Brokers + scoped update; lock-time recheck; ownership |
| `GET /api/v1/kite/telegram` | Visible Telegram section; masked own-tenant projection |
| `POST /api/v1/kite/telegram` | Admin only; delegation cannot introduce shared bot tokens |
| `PUT /api/v1/kite/telegram/{id}` | Scoped recipients; any bot-token field additionally requires admin |
| `DELETE /api/v1/kite/telegram/{id}` | Admin only |
| `POST /api/v1/kite/telegram/{id}/test` | Scoped test; no implicit send on save |
| `GET /api/v1/truedata/credentials` | Visible Integrations section; masked own-tenant projection |
| `POST/PUT /api/v1/truedata/credentials[/{id}]` | Own-scope create / scoped update |
| `DELETE /api/v1/truedata/credentials/{id}` | Admin only |
| `POST /api/v1/truedata/settings` | Own-scope integration update |
| `POST /api/v1/truedata/diagnostics/run` | Own-scope integration test |
| `GET /api/v1/settings/capabilities` | Current active identity projection; no secrets; private/no-store |
| `GET/PUT /api/v1/auth/users/{id}/configuration-access` | Designated administrator; PUT revision required; unknown fields rejected |
| Existing Sterling user-management routes | Durable designated-admin check, independent of Kite |

Delegable actions in this slice: Brokers create/update; Telegram recipients/test; Integrations create/update/test. Resource scope is `own` or an exact current own-tenant resource ID; create requires `own`. Shared/global credentials and destructive broker/provider removal cannot be delegated. Reveal grants are rejected. The APIs also accept a finite grant expiry; the initial UI creates non-expiring explicit grants. UI-generated configuration payloads contain no credential values.

## Verification and limitations

Validation: 98 backend tests, 42 frontend tests, and two Chromium isolation tests passed. Frontend type-check and `git diff --check` also passed. The configuration suite was rerun after the final effective-action filter change (16 passed).

All automated database tests use disposable SQLite paths. Tests cover default denial; section independence; exact scopes; expiry; inactive users; admin-only delegation; feature/configuration revision conflicts; audit rollback; simulation isolation; old broker/provider mutations; no-secret reveal; Telegram token/test separation; and revocation during a broker exposure probe. Existing auth, user CRUD, login-vault and provider-route suites are included. Frontend tests cover independent administration, action filtering, explicit feature writes, identity-vs-simulation API scope, and existing login/settings behavior.

A synthetic Chromium browser probe exercised `/administration?broker_login=1` with a remembered Simulation preference at desktop and 390px phone widths. It recorded no Kite requests, no page errors, and no horizontal overflow. It mocked APIs and did not contact any broker or send any message. Repeat with `cd frontend && npx playwright test -c playwright.administration.config.ts` (set `PLAYWRIGHT_CHROMIUM_EXECUTABLE` when using a system Chromium). Only Vite’s development hot-reload socket is excluded from the no-application-WebSocket assertion. This is development layout evidence, not real-device or full accessibility certification.

This is a bounded configuration adapter, not the full central broker/integration registry. Provider changes still use existing stores; transactional secret rotation, central assignment/history, provider-write audit and crash recovery remain pending. Sensitive configuration and delegation are not yet protected by the planned MFA step-up/device-session lifecycle. The outbox has no delivery worker yet. Do not deploy this as a completed public account-overhaul release.

Feature grants can now be edited in Administration but are not yet wired through all trading, media, stream, callback and background-admission entry points. The launcher and protected media-document/session migration remain next work. Existing session controls, assigned broker use and safety workers retain their prior behavior. Existing broker-removal safeguards remain in place. No live database, production service, broker assignment, order or real notification was operated during implementation.
