# Workspace flow and central administration implementation

This is the current development status, superseding the next-work notes in the first two implementation slices. The full specification remains a staged program. No application rollout was performed in this turn.

## Working user-facing flows

- A refreshed responsive Sterling Studio sign-in page has password visibility and an unchecked Remember this device option. Fresh sign-in always opens the chooser, preserving broker hints as navigation data. A single-feature account still chooses; a no-feature account gets an explicit empty state.
- The chooser and persistent workspace header expose only authoritative grants. Home, browser Back, workspace switching, Administration and sign-out share one route boundary. Missing policy or a failed policy fetch does not mount a feature.
- Kite is lazy-loaded only after choosing permitted Kite. The existing trading workspace is pinned to Kite; its old hidden media/Kite mounting is removed. TV/Radio have their own header, search/theme commands and player wrappers. Media sessions do not mount the trading runtime or broker-link handler.
- Central Administration manages ordinary users, feature grants and scoped configuration access. Separate Broker connections and Telegram integrations tabs manage assigned resources across Sterling users. Broker create/edit/remove and Telegram create/list/remove are functional adapters, without requiring the admin to have Kite enabled or to connect a broker.
- Admin broker creation selects an active Sterling user; the user's separate Kite grant is still required to connect. Editing credentials never reveals saved values. Safe-detach checks remain authoritative on credential replacement/removal, and edits reject a changed timestamp. Central broker mutations add redacted audits in the provider transaction.
- Login suspension revokes tokens, features and configuration delegations in the identity transaction, retaining broker records and system protection. Restoration does not resurrect prior feature/configuration grants. The UI accurately calls this Suspend; final identity archival is still a separate pending workstream.

## Enforced server boundaries

`/auth/me` now returns authoritative feature booleans, policy revision and designated-administrator status, with private/no-store caching. The application-level route policy defaults private API routes to Kite, while identity, central configuration, probes, protected media and provider ingress have explicit separate contracts. New private API routes inherit the Kite check. Central admin routes additionally require the designated administrator.

Streams verify Kite at connection/auth, subscription and outbound delivery, including local development connections. Interactive broker completion checks policy before exchange and again before persisting the result. The signed callback checks policy before proceeding. Signed broker postbacks remain available for reconciliation.

Canonical exposure increases check current durable Kite policy both on admission and at the transport's before-send fence. Verified reductions, protection, cancels and renewal/reconciliation workers retain their separate safety authority. Disabling a browser account does not delete its broker, flatten positions, or stop protective workers.

TV/Radio player documents moved from `frontend/public` to `backend/app/media_templates`. A bearer-authorized `/media/{feature}/access` creates a random, hashed, finite ten-minute media-only credential. Its HttpOnly cookie is restricted to the corresponding document path and uses Secure in production. `/media/{feature}/document` revalidates user status, token version, expiry and the exact feature. It emits a nonce-based, opaque-origin sandbox CSP. The cookie never authenticates trading/admin/session-refresh APIs, and the player receives no bearer token through URLs or messages.

Node and Nginx retire retained static player HTML, including HEAD and normalized/encoded paths. Vite development/preview blocks those documents and filesystem access to private templates. The media utility-class manifest is generated separately so frontend-only container builds retain styles without publishing HTML templates. JS/CSS player assets remain public; all private data APIs still require their own bearer authorization.

Remembered JWT refresh credentials now have finite expiry; old indefinite refresh JWTs are rejected. Normal sign-in stores tokens in session storage, with persistent storage only after explicit remembrance. This is a transitional improvement, not the target opaque device-session/rotation architecture.

## Validation

Final targeted checks: **296 backend tests passed**, **134 frontend tests passed across 14 files**, **4 Chromium browser tests passed**, and the isolated Node gateway test passed. TypeScript checking, the production frontend build and `git diff --check` passed. The build still reports the documented 543.45 kB Kite runtime chunk warning.

Backend tests use disposable SQLite databases and synthetic transports. They cover grant combinations, default denial, finite media cookies, logout/suspension/expiry revocation, cookie/trading isolation, real application CSP, central broker transaction/audit rollback, interactive-login revocation during exchange, canonical new-entry denial, account restoration, existing user/auth/provider behavior, execution mechanics, stream backpressure/identity and simulation isolation.

Frontend tests cover all eight chooser combinations, explicit single-feature entry, no automatic broker deep-link entry, feature revocation/unmount, no-feature administration, refreshed login, scoped settings, media bridge/sandbox and existing settings behavior. The production frontend bundle was built into `/tmp/sterling-workspace-build-20261027`, away from the published asset directory.

Four repeatable Chromium browser tests exercise desktop/mobile Administration isolation, TV-only sign-in → chooser → protected TV → Home → sign-out, and admin → create user → grant TV → save assigned broker → save Telegram destination. Browser APIs and provider transports are synthetic; no acceptance flow places a real order or sends a Telegram message. The media flow checks that the iframe receives the media-only cookie without a bearer header, while neither Kite requests nor its runtime chunk are loaded.

Commands: `backend/.venv/bin/python -m pytest` with the targeted account/auth/workspace/execution/stream/login suites; `cd frontend && npx vitest run` for targeted UI suites; `npx playwright test -c playwright.administration.config.ts` for browser flows; and `node --test scripts/tests/tv_gateway.test.mjs` for the actual isolated Node gateway. Set `PLAYWRIGHT_CHROMIUM_EXECUTABLE` to use a system Chromium. `tsc --noEmit` and `git diff --check` are also required.

## Remaining release gates

This is not a completed WP0–7 release. The independent unassigned central broker registry, safe reassignment coordinator, assignment lineage, general integration vault/versioning and rotation, full provider credential migration, device sessions and refresh rotation, admin MFA/step-up/recovery, final deletion/archive jobs, canonical username migration, comprehensive ingress/container/real-device/accessibility tests and measured performance/soak evidence remain open. Existing provider stores are still in use; this change does not claim all legacy secrets have moved to the target vault. The largest Kite chunk still exceeds the requested performance budget.

The broker adapters support managing existing assigned runtime accounts and creating a newly assigned connection; they do not present an unsafe reassignment button or claim to support independent unassigned records. Telegram alert destinations remain separate from the platform control bot. Secret reveal remains unavailable pending MFA-bound challenges. Public deployment is not approved by these development checks.

The requested administrator rename/password reset was performed separately against the running backend's existing user-management API. The same primary `default` identity was retained, its username became `admin`, and password sign-in was verified. Credential values are not stored in this document or source. No broker account/session, order, feature mapping or application deployment was changed by that recovery operation.
