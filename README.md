# Sterling

Sterling is a live trading platform for Indian derivatives. A FastAPI backend and
React clients place real orders on Zerodha Kite for exactly three strategies:
**Supertrend**, **Snapback** and **Gamma Unwind**. Market data comes from Kite and
TrueData.

There is no trial mode. A connected Kite account can trade real money, so read
[docs/operator/START_HERE.md](docs/operator/START_HERE.md) before the first session.

## Applications

- Web terminal: Kite dashboard, orders, holdings, positions, alerts, the three
  strategy screens, replay and backtesting.
- Mobile terminal: Kite watchlist, orders, holdings and positions.
- Backend: data ingestion, scanners, strategy execution, safety controls, audit
  persistence and Telegram alerts.

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
