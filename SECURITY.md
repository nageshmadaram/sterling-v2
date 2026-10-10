# Security

## Secrets

- Kite API secrets and access tokens are encrypted at rest with
  `STERLING_SECRET_KEY` (`app/core/security.py`) and surfaced only as masked hints
  in API responses, never in full. `STERLING_SECRET_KEY` must be set in production;
  startup refuses a production deployment without distinct secret and JWT keys.
- App-level config comes from the environment and `.env` (`app/core/config.py`).
  `.env` is git-ignored; commit `.env.example` only. Operator files such as
  `~/.config/sterling/sterling.env` are mode 600 and live outside the repository.
- Never log secrets. The JSON log formatter logs only explicit fields; keep
  credentials out of `extra=`.
- No password, TOTP seed or access token belongs in this repository, a runbook, a
  backup note or a chat.

## Broker identity

- Every Kite account is live. A live account must be bound to the Zerodha user id
  that logged in; the id is unique across accounts, and an account's mode cannot be
  changed through the API.
- Credentials of a previous operator are never reused. A new operator connects their
  own account ([docs/operations/BROKER_ACCOUNT_HANDOFF.md](docs/operations/BROKER_ACCOUNT_HANDOFF.md)).
- Zerodha invalidates tokens daily at 06:00 IST, and the login needs a person at
  the 2FA step. Sterling cannot and must not log in for you.

## Authentication and operator actions

All API routes except the health probes require a Sterling login in production. Lifting
safe mode (`POST /api/v1/ops/safe-mode/resume`) requires administrator authority,
an explicit confirmation phrase, a reason, and, after an investigation-required
trigger, an acknowledgement; who and when are recorded. The backend binds to
`127.0.0.1` by default; do not expose it directly.

## HTTP hardening

The FastAPI app applies security headers on every response (`main.py`
middleware): `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy: strict-origin-when-cross-origin`, and a locked-down CSP
(`default-src 'none'; frame-ancestors 'none'` for JSON responses). CORS origins are
an explicit allow-list (`settings.cors_origins`).

## Request traceability

Each request carries an `X-Correlation-ID` (honoured if inbound, else minted) and
echoes it back, see [OBSERVABILITY.md](OBSERVABILITY.md).

## Audit

- The order journal records every intent, its state transitions and its broker
  acknowledgement; an unknown outcome is never resent.
- `kite_account_audit` records identity-affecting account events.
- Safe-mode changes append to `safe_mode_audit.jsonl` beside the safety state.
- Every order rejection carries a machine-readable `code` for alerting.

## Input validation

All API inputs are Pydantic models. New external inputs are schemas, not raw dicts.
The public account routes accept only credentials and a label; legacy fields sent
by old clients are ignored.

## Live-trading safety

New entries pass the canonical path in [EXECUTION.md](EXECUTION.md) and the controls
in [RISK_MANAGEMENT.md](RISK_MANAGEMENT.md). `STERLING_LIVE_EXECUTION=0` stops all
new entries without touching exits or protection.

## Identity and operator audit (live-trading posture)

- **Production** (`ENVIRONMENT=production` in the process environment *or* `backend/.env`):
  every route needs a Bearer access JWT. `X-User-Id` is ignored.
- **Development** grants the anonymous `default` admin only to loopback clients with a
  loopback `Host` (and, for writes/WebSockets, a loopback or `CORS_ORIGINS` `Origin`).
  Once **any live Kite account is stored the anonymous identity is refused
  everywhere**: sign in with a real operator account. Set/reset the password with
  `backend/.venv/bin/python backend/scripts/reset_admin_password.py`.
  Escape hatches (do not use with real money): `STERLING_DEV_AUTH_ALLOW_REMOTE=1`,
  `STERLING_DEV_AUTH_ALLOW_ANONYMOUS_LIVE=1`.
- `backend/tests/unit/test_route_auth_coverage.py` fails if a route is added without auth
  unless it is on its reviewed allow-list.
- **Audit trail**: every mutating API request and the Kite login callback appends one JSON
  line (who, route, status, peer, correlation id; never query strings or bodies) to
  `data/audit/operator_audit.jsonl` (mode 0600, rotated at 20 MiB; `STERLING_AUDIT_LOG`).
- Request bodies are capped at 8 MiB (`STERLING_MAX_BODY_BYTES`); API responses are
  `Cache-Control: no-store`.
- Kite REST traffic shares one limiter (`STERLING_KITE_HIST_RPS` default 2.8/s,
  `STERLING_KITE_API_RPS` default 8/s).
