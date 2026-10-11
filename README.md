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
