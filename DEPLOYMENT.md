# Deployment

## Local development

```bash
make setup        # python venv + pip install, npm install
make backend      # uvicorn main:app --reload --port 8000
make frontend     # vite dev server (port 5173)
```

Backend venv lives at `backend/.venv`; all make targets use it.

## Docker

Production compose is fail-closed. A fresh deployment requires distinct
encryption/JWT secrets plus a bootstrap admin password before Compose will even
start the containers.

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
deployments, inject them from the platform's secret store. `PAPER_TRADING`
defaults to `true` in Compose; changing it is an explicit operator decision.

`docker-compose.yml` (repo root) builds the backend from `backend/Dockerfile`
and the frontend from `frontend/Dockerfile`. CI builds and boots both production
images and probes both services before the Sterling Release Gate can pass.

The backend host port is bound to `127.0.0.1:8000` by default; external clients should reach the API through the frontend reverse proxy (or a separately configured authenticated/TLS ingress), not by exposing the backend port directly.

## Environments

- Set `ENVIRONMENT=production`, `PAPER_TRADING` as appropriate, and real
  `CORS_ORIGINS`.
- Enable `LOG_JSON=true` in production so logs are machine-parseable
  ([OBSERVABILITY.md](OBSERVABILITY.md)).
- Provide exchange credentials through the exchanges API / secret store, not the
  image.

## Persistence

Today persistence is SQLite (`backend/sterling_paper.db` and friends). The
SQLAlchemy layer (planned, [MIGRATION.md](MIGRATION.md) Phase 5) makes the engine
URL configurable for PostgreSQL — point it at a managed Postgres for production
durability and horizontal read scaling.

## Health & readiness

`GET /health` is the container liveness endpoint. Production startup itself is
the configuration/readiness gate: invalid encryption/JWT posture, unreadable
credential state, or a missing bootstrap identity on a fresh database aborts
startup instead of serving traffic. Docker Compose waits for backend health
before starting the frontend.

## Pre-deploy checklist

1. Exact release SHA has a green **Sterling Release Gate** and **snapback-release-gate**.
2. The mandatory **Production Docker Smoke** job built and booted both images.
3. `STERLING_SECRET_KEY` and `STERLING_JWT_SECRET` are distinct, random, 32+ character secrets from the deployment secret store.
4. A bootstrap admin exists, or `STERLING_ADMIN_USERNAME` / `STERLING_ADMIN_PASSWORD` are supplied for the first boot.
5. `CORS_ORIGINS` contains only the real frontend origins; no development origin is exposed unintentionally.
6. Keep `PAPER_TRADING=true` through deployment smoke/reconciliation. Enabling live execution is a separate operator action after broker/account/protection verification.
7. `LOG_JSON=true` and the persistent `STERLING_DB_PATH` volume are configured.
