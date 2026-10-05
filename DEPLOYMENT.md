# Deployment

Sterling is a live-money trading app. There is no trial mode: a deployment with a
connected Kite account can place real orders as soon as it is running. Read the
first-day section before you start it.

## Local development

```bash
make setup        # python venv + pip install, npm install
make backend      # uvicorn main:app --reload --port 8000
make frontend     # vite dev server (port 5173)
```

Backend venv lives at `backend/.venv`; all make targets use it. `make clean` only
removes caches; it never touches the database.

## Docker

Production compose is fail-closed. A fresh deployment requires distinct
encryption/JWT secrets plus a bootstrap admin password before Compose will start.

```bash
export STERLING_SECRET_KEY="$(openssl rand -hex 32)"
export STERLING_JWT_SECRET="$(openssl rand -hex 32)"
export STERLING_ADMIN_USERNAME="admin"
export STERLING_ADMIN_PASSWORD="<set-a-strong-unique-password>"
export CORS_ORIGINS='["https://your-sterling-host.example"]'

make docker-up      # docker compose up --build -d
make docker-logs    # follow logs
make docker-down
```

Do not reuse the two generated secrets and do not commit them. For managed
deployments, inject them from the platform's secret store.

`docker-compose.yml` builds the backend from `backend/Dockerfile` and the frontend
from `frontend/Dockerfile`. The backend host port is bound to `127.0.0.1:8000`;
reach the API through the frontend reverse proxy or a separately configured
authenticated, TLS ingress, never by exposing the backend port.

## Host install (systemd)

`deploy/sterling.env.example` is the template for `~/.config/sterling/sterling.env`
(mode 600, outside the repository). `deploy/systemd/` holds the service and its
liveness watchdog. Full steps: [docs/operator/LIVE_RUNBOOK.md](docs/operator/LIVE_RUNBOOK.md).

## Live execution settings

| Setting | Meaning |
|---|---|
| `STERLING_LIVE_EXECUTION` | **Kill switch for new entries.** `true` (default) = on. `0`, `false`, `off`, `no`, `disabled` = no new entry originates. A typo also turns entries off. Exits, protection and reconciliation always run. Restart to apply. |
| `STERLING_LIVE_MINIMUM_MAX_LOTS` | Optional cap on lots per entry. Set `1` for the first session. |
| `STERLING_LIVE_MINIMUM_DAILY_LOSS_INR` | Realised loss at which new entries stop for the day. Default: the account's daily-loss hard halt (1,500 INR). |
| `STERLING_LIVE_MINIMUM_GROSS_EXPOSURE_INR` | Optional ceiling on capital deployed at once. Default: broker-verified capital. |
| `STERLING_LIVE_MINIMUM_MAX_DEPLOYED_PCT` | Share of verified capital that may be deployed (1 to 100, default 100). |

A limit that is set but unusable (not a number, zero, negative) refuses entries
until fixed; it is never silently replaced by a default.
`STERLING_FAMILY_MODE` is retired: leave it unset. All three strategies run.

## Environments

- Set `ENVIRONMENT=production` and real `CORS_ORIGINS`.
- Enable `LOG_JSON=true` in production ([OBSERVABILITY.md](OBSERVABILITY.md)).
- Kite API credentials come from the environment (`KITE_API_KEY`,
  `KITE_API_SECRET`) or the Kite settings screen, never from the image. Accounts
  are always live; a leftover `KITE_*` mode flag from an old environment file is
  ignored (the startup log says so).

## Persistence

State is SQLite. The main database is the file named by `STERLING_DB_PATH`
(accounts, order journal, positions, configuration). The file name is historical;
keep it so an existing installation finds its state, and never point a service at
a new empty directory and call it flat. Back it up with
`./scripts/sterlingctl backup` and prove it with `./scripts/sterlingctl restore-check`
([docs/operations/BACKUP_AND_RESTORE.md](docs/operations/BACKUP_AND_RESTORE.md)).

## Health and readiness

`GET /health` is the container liveness endpoint; it reports `environment` and
`live_execution` (false only while the kill switch is engaged). Production startup
is the configuration gate: invalid encryption/JWT posture, unreadable credential
state, or a missing bootstrap identity on a fresh database aborts startup.

## First boot on an existing database

On boot Sterling converts any stored Kite account that an older build left in a
non-live mode into a live account (same id and API credentials; sessions and the
broker identity binding are cleared). It does so only when no position or journal
intent is open for that account, and writes an audit row. If it is not safe, the
account is left alone, an error is logged and a critical alert is sent.

- The pre-Kite position table is ignored: nothing in the product writes it, so its
  rows are not broker exposure.
- After conversion the account is disconnected. Log in to Kite from the Sterling
  UI; that login binds the account to your Zerodha identity.
- `GET /api/v1/ops/live-status` shows `NO_LIVE_ACCOUNT` until a live account exists.

## First-day checklist

1. `STERLING_SECRET_KEY` and `STERLING_JWT_SECRET` are distinct, random, 32+
   characters from the secret store; a bootstrap admin exists.
2. `CORS_ORIGINS` lists only real frontend origins.
3. `STERLING_LIVE_EXECUTION=true`, `STERLING_LIVE_MINIMUM_MAX_LOTS=1`, and a loss
   budget you can accept. Leave Supertrend and Gamma `execution_lots`/`max_lots`
   at 1.
4. Supertrend has `max_daily_loss_pct` set and `stop_mode` is `broker` or `both`;
   Gamma intraday uses `broker` or `both`.
5. Kite login done (it expires daily at 06:00 IST), `/health` answers, and
   `./scripts/sterlingctl doctor` has no FAIL.
6. A backup exists and `restore-check` passed.
7. You know the three ways to stop entries ([EXECUTION.md](EXECUTION.md)).
