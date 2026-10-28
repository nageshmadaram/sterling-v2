# Sterling

Sterling is a live trading platform for Indian derivatives. A FastAPI backend and
React clients place real orders on Zerodha Kite for four strategies:
**SuperTrend**, **Snapback**, **Gamma Unwind** and **Price Rules**. Market data comes from Kite and
TrueData.

Live view places real broker orders. Simulation uses a separate virtual workspace
and does not certify real broker execution. Before live trading, read
[docs/operator/START_HERE.md](docs/operator/START_HERE.md) before the first session.

## Applications

- Web terminal: Kite dashboard, orders, holdings, positions, alerts, the four
  strategy screens, replay and backtesting.
- Mobile terminal: Kite watchlist, orders, holdings and positions.
- Backend: data ingestion, scanners, strategy execution, safety controls, audit
  persistence and Telegram alerts.

## Run the complete app

```bash
cd /home/nageshmadaram/Sterling
./sterling.sh start
./sterling.sh status
```

The launcher builds the frontend and starts/reuses the loopback backend, serves
one frontend/API/WebSocket origin at `http://127.0.0.1:5180`, and starts Cloudflare
remote access. `status` prints the current `app_url` and Telegram desk URL.
Running `start` again reuses the managed stack. Keep the computer awake and online.

For existing local Kite app registrations, the gateway also keeps
`http://localhost:5173/api/v1/kite/callback` reachable and forwards that callback
to the current app port. This listener accepts only the callback route, preserves
the signed login query, and exposes no other app or API endpoints. Set
`STERLING_CALLBACK_PORT=0` to disable it or specify another legacy local port.
Remote devices still need the public callback URL registered with Kite.

Healthy Cloudflare URLs are preserved. There is no scheduled rotation by default.
Backend maintenance alone must not replace the public address. Quick Tunnel
hostnames change when their tunnel process must be replaced; a named tunnel with
an owned domain provides a persistent hostname. Public links and Telegram menus
update after successful remote verification. Kite developer-console Redirect URL
registration must match the printed callback URL; Sterling cannot change that
broker setting automatically.

```bash
./sterling.sh stop
```

This stops the frontend/tunnel, while keeping the backend running for exits and
protection. Do not stop the backend while it manages broker exposure.

Connect includes **Operators & Users** for administrator login management.
Passwords are hashed and cannot be recovered; administrators can replace them.
Telegram **Sterling Login** opens the signed private management form. Price Rules
is available from Connect and the strategy strip; each plan is armed individually.

## One-command local + Cloudflare public tunnel + development UI

The existing Sterling stack supervisor can now start **one backend**, one
authenticated HTTP gateway and three access surfaces together:

```bash
cd /home/nageshmadaram/Sterling
./sterling.sh up
# Optional diagnostics only:
./sterling.sh urls
./sterling.sh status
```

On an interactive terminal, `./sterling.sh up` shows a **color-coded Sterling
workspace dashboard** with the local app, Cloudflare and Vite dev URLs,
their verification/connecting statuses, and the committed release SHA. Green
means a URL was verified by the running supervisor; amber means it is still
connecting; purple highlights the uncommitted-only developer frontend. The
dashboard shows **only verified public links**. Running `up` again shows the
same prominent dashboard without restarting a healthy manager. This is
presentation-only; it does not alter live trading or tunnel rotation.

For logs, piping, or `NO_COLOR=1 ./sterling.sh up`, the launcher prints the
same URL information without terminal escape sequences.

- Main local app: **http://127.0.0.1:5180** (the same gateway tunneled to the public internet).
- Local hot-reload Vite development view: **http://127.0.0.1:5174** (loopback only,
  shares the *same* backend and trading state; never an independent sandbox).
- Cloudflare: the verified generated `https://….trycloudflare.com` URL,
  or your configured named tunnel address.

### Committed release vs. uncommitted development

`./sterling.sh up` uses two **separate frontend sources** without opening
another Kite engine:

| Where you open Sterling | Source and update rule |
| --- | --- |
| Local `http://127.0.0.1:5180` | A build from an immutable Git commit; never live working-tree edits |
| Cloudflare HTTPS public URL | Exactly the same 5180 committed build and backend |
| Dev `http://127.0.0.1:5174` | Your local `frontend/` working directory, including uncommitted edits, with Vite hot reload |

**Automatic updates (after one-time host enrollment):** the supervisor
defaults to the reviewed remote `origin/main` and fetches that remote-tracking
ref approximately once per minute, without changing the local `main` or
`dev` worktrees. When a newer fast-forward commit exists, the supervisor
extracts only Git-committed frontend files into the private
`.runtime/stack/committed-source` workspace, compiles them and atomically
switches `.runtime/stack/published`. A failing build or unexpected remote
rewind leaves the previous published release intact. The same local:5180,
Cloudflare addresses now serve the new frontend without restarting
the long-lived gateway, tunnel or broker backend. Previous hashed assets
remain available for open tabs.

The existing `PublishedReleaseWatcher` checks
`/__sterling_web_health` every ~45 seconds. Open tabs display an
unobtrusive **New Sterling version ready** control and may refresh after
returning from the background when no settings draft or dialog is active.
It does **not** forcibly restart active media or overwrite an unsaved
trading form. New navigations get the latest HTML with
`Cache-Control: no-store`.
A sleeping/offline phone receives the updated UI on reconnection.

Uncommitted, untracked and modified files **never enter the public frontend
build**, even if `frontend/dist` was generated from those files previously.
The dev URL alone reflects them. Compilation failure leaves the last
successful committed release in service; `./sterling.sh status` and
`./sterling.sh urls` report `release_commit`, `release_error`, and
`release_stale`. The failed build's logs are written to
`.runtime/stack/release-build.log`.

The new default is equivalent to:

```dotenv
STERLING_PUBLISH_REF=origin/main
STERLING_PUBLISH_FETCH=1
```

You can deliberately disable remote publication or pin to the local branch:

```dotenv
STERLING_PUBLISH_REF=main
STERLING_PUBLISH_FETCH=0
```

**One-time bootstrap is still required on an older laptop installation.**
The repository provides `scripts/enable_remote_ui_updates.sh` for an
authorized healthy operator: it safely enrolls the existing backend
`.env` (without touching trades, Git worktrees or secrets) and confirms
remote `origin/main` is fetchable. The GitHub merge cannot edit a
running process's environment or install a new launcher. Once the updated `scripts/sterling_stack.py` reaches the laptop
and the existing presentation supervisor is restarted under its normal
operator/service management, all *future* remote `main` merges publish
automatically while Sterling is running. On an older already-installed
supervisor, the same behavior can be enrolled through its existing
`STERLING_PUBLISH_REF=origin/main` and
`STERLING_PUBLISH_FETCH=1` environment options plus a supervisor restart.
This is a **one-time healthy-operator/host action**, not something to ask a
hospitalized user to execute. A quick Cloudflare tunnel may change hostname
during this one-time restart; later atomic frontend updates do not rotate it.
A named Cloudflare tunnel is needed for a guaranteed permanent external URL.

This remote setting publishes reviewed `main` only; it does **not** merge
feature branches, deploy backend Python changes, or confer live-trading
certification. CI and device checks remain separate release evidence gates.

**Trading safety boundary:** this automation updates only the static React
frontend. The existing backend on port 8000, its Python code, Kite session,
orders, exits, order reconciliation and risk controls are not restarted or
deployed from Git. Backend code upgrades require a separate operator-certified
release. A frontend commit alone cannot make backend-dependent new pages or
new live trading strategies operational; never enable live entry merely
because frontend URLs are synchronized.

Configure Cloudflare named-tunnel credentials in `backend/.env` without committing secrets:

```dotenv
STERLING_TUNNEL_PROVIDER=cloudflare
STERLING_CLOUDFLARE_HOSTNAME=
STERLING_CLOUDFLARE_TUNNEL_TOKEN=
STERLING_DEV_FRONTEND=1
```

The single command `./sterling.sh up` is **idempotent**. When an
already-running Cloudflare manager is healthy and owns its port 5180 gateway,
the command reuses the same running processes and URLs. When an old-version
manager or a failed/orphaned web gateway is detected, it gracefully replaces
**only the presentation/tunnel manager**; the existing Kite backend remains
running. At startup the manager inspects the 5180 listener with `ss` and
`/proc`, terminates it only if it is the exact same-user, same-checkout
Sterling Node gateway, and verifies its *new* web PID owns 5180 before
publishing tunnel URLs. Unknown/unrelated listeners are refused, not killed.

On Telegram, **/menu** and **/url** expose the verified Cloudflare origin.
The Mini App and Kite OAuth callback use the same primary Cloudflare address;
registering that address in the Kite developer console remains an operator action.

To switch an old single-tunnel launcher or recover a recognized orphan
gateway, simply run `./sterling.sh up`. The manager never forces an
unidentified process off a port and never restarts the Kite trading backend;
unverified ownership produces an explicit error requiring inspection.

## Local development

    make setup
    make backend
    make frontend

Backend: `http://localhost:8000`  
Frontend: `http://localhost:5173`

## Safety in one paragraph

Every entry goes through one canonical path: explicit effect classification, a
request-bound risk approval, safety admission asked twice, fresh broker capital
evidence, an idempotent order journal, and no resend after an unknown outcome.
`STERLING_LIVE_EXECUTION=0` stops all new entries; exits, protection and
reconciliation always keep working. Details: [EXECUTION.md](EXECUTION.md),
[RISK_MANAGEMENT.md](RISK_MANAGEMENT.md), [SECURITY.md](SECURITY.md).

## Primary API surfaces

- `/api/v1/kite`: broker session, accounts, orders, positions, kill switch
- `/api/v1/kite/engine`: Supertrend engine
- `/api/v1/snapback`: Snapback plans and operations
- `/api/v1/config/gamma-move`: Gamma Unwind settings, scan and arm
- `/api/v1/ops`: live status and the safe-mode resume action
- `/api/v1/backtest`
- `/health`

## Documentation

| Topic | Read |
|---|---|
| Running Sterling day to day | [docs/operator/START_HERE.md](docs/operator/START_HERE.md) |
| Emergencies | [docs/operator/EMERGENCY_CARD.md](docs/operator/EMERGENCY_CARD.md) |
| Strategies | [STRATEGIES.md](STRATEGIES.md) |
| Execution and kill switch | [EXECUTION.md](EXECUTION.md) |
| Risk controls | [RISK_MANAGEMENT.md](RISK_MANAGEMENT.md) |
| Deployment and first-day settings | [DEPLOYMENT.md](DEPLOYMENT.md) |
| Security | [SECURITY.md](SECURITY.md) |

### Live-entry opt-in

New deployments start with new live entries disabled. Set
`STERLING_LIVE_EXECUTION=true` only after verifying the broker account, approved
outbound IP, durable state and restart reconciliation. An unset, blank or invalid
value blocks new entries. Exits, protective stops and reconciliation continue
independently. Restart the backend after changing this setting.

The outbound-IP observer records what it sees; it does not approve that address
for the broker. Confirm the broker whitelist before accepting the address with
`sterlingctl egress accept <ip>`.
