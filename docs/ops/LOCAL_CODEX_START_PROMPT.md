# Local Codex startup prompt

Open the local **Sterling** project in Codex, using `/home/nageshmadaram/Sterling`.
Paste the following prompt. The changes are on both `origin/main` and `origin/dev`;
the previous transfer ZIP is no longer needed once Git synchronization succeeds.

```text
Work in /home/nageshmadaram/Sterling on my Linux computer. Synchronize the
validated Sterling changes from nageshmadaram/sterling and run the app locally,
then audit live-account readiness for SuperTrend, Snapback and Gamma Unwind.

Read AGENTS.md, docs/design/STRATEGY_PRODUCTION_AUDIT.md,
docs/ops/STERLING_OPERATOR_HANDOFF.md and README.md first. Use available graph
tools as AGENTS.md requires; if unavailable, document the source-review fallback.

1. Inspect git status, current branch, local HEAD, remote refs, services and ports.
   Fetch origin. Preserve all local edits, untracked files, local commits,
   backend/.env, credentials, encryption keys, databases, journals and broker
   state. Fast-forward the checked-out main/dev branch when possible. Otherwise
   integrate the published changes while retaining local work and resolve exact
   conflicts. Do not reset, clean, force-push or silently discard changes.
   Verify the latest synchronized source includes the replay-owner isolation
   tests, common signal columns, instrument formatter and audit/handoff documents.

2. Inspect existing running Sterling services and outstanding broker exposure
   before restarting anything. Reuse the intended durable state and account.
   A running backend may manage live positions: verify protection and unresolved
   orders before a controlled restart, and preserve continuing exits. Do not
   replace a live database with cloud development state or rotate saved keys.

3. Use the supported pinned toolchain and frozen dependencies:
   backend/requirements.lock.txt with the intended backend virtual environment,
   and npm ci in frontend when installation is needed. Build the frontend. Run
   appropriate affected regressions, including replay ownership, owned strategy
   execution, diagnostics, instrument display and board/chart workflows. Tests
   must use isolated state and no real broker orders.

4. Run the existing backend and the built frontend locally on loopback. Reuse
   healthy matching processes; otherwise start uvicorn from backend with the
   intended virtual environment and one worker, and serve the frontend using
   scripts/serve_frontend.mjs. The gateway defaults to 127.0.0.1:5180 and the API
   to 127.0.0.1:8000. Check actual health and frontend/API/WebSocket behavior;
   a listening port is insufficient. Avoid the Cloudflare/Telegram publication
   path for this local-only start. Tell me the working local browser address.

5. Keep new entries disabled for this readiness check. For any new backend you
   launch, set STERLING_LIVE_EXECUTION=0; exits/protection/reconciliation continue.
   Do not silently change an already running operator's configuration. Reuse
   configured credentials without printing values. If secure keys or broker
   credentials are missing, finish independent setup and name the missing
   requirements; do not invent them or store credentials in the repository.

6. Verify Sterling authentication, the intended Kite account and broker user,
   daily broker session, production crypto, market-data freshness, lot/expiry
   identity, capital/risk and daily-loss settings, readiness, owned-entry refusal
   reasons, reconciliation, unresolved exposure and stop protection. Verify the
   local computer's actual broker-registered static egress; a Cloudflare inbound
   tunnel does not make outbound broker traffic static. Do not fabricate an IP
   observation, pass verdict, fill or historical result. A required broker 2FA
   step is the operator's; explain the precise next action without asking for
   passwords or tokens in chat.

7. Verify all three signal tables, expanded evidence, readable symbols, chart
   navigation, replay ownership/clock/read-only behavior, Help and terminal
   diagnostic download in the real local browser. During market hours, run the
   existing read-only moving-market acceptance only when an authenticated
   broker account and fresh data are available. Do not submit a funded order
   or enable new entries as part of this check.

Report the synchronized commit and branch, services actually started/reused,
local URL, checks executed, exact live blockers and next operator actions.
Distinguish software tests, modeled replay and actual broker evidence. Existing
ATM/OTM SuperTrend research was negative after costs; do not claim profitability
or family income is established. Continue resolving concrete local blockers
until the workflow works or a specific credential/hosting action is required.
```

Cloud validation before publication: backend 10,506 passed / 30 skipped;
frontend 1,892 passed; production build and seven Chromium workflow checks passed.
The cloud test instance had no Kite account and submitted no broker orders.
