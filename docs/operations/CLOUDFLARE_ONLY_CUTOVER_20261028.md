# Cloudflare-only presentation cutover — 28 October 2026

## Decision and scope

The operator has retired ngrok from Sterling. `./sterling.sh up` now serves:
- The committed, atomically published frontend on loopback `127.0.0.1:5180`.
- **One** verified Cloudflare HTTPS tunnel to the same gateway.
- The Vite hot-reload frontend on loopback `127.0.0.1:5174` for development.

The presenter no longer launches, discovers, probes, retries or exposes ngrok. Legacy `STERLING_TUNNEL_PROVIDER=both` settings are mapped to Cloudflare by the new launcher. No second broker/API is started. Neither the one-minute reviewed `origin/main` fetcher nor frontend asset promotion restarts the trading backend.

## Security and public access

The gateway no longer trusts secondary-tunnel request headers as proof of HTTPS or client identity. Only the supervisor-associated Cloudflare hostname is considered for Cloudflare forwarding metadata. Telegram `/menu`, `/url`, notification links and the durable public-access record expose one Cloudflare origin. Previously saved secondary links are ignored by the updated backend and overwritten on successful public-access publication.

`backend/.env` explicitly enrolled with `STERLING_PUBLISH_REF=origin/main` and `STERLING_PUBLISH_FETCH=1` takes precedence for **static UI publication only** over a stale service-level publisher ref. This does not alter Kite settings, risk switches, orders or protection workers.

## One-time host cutover — NOT performed from GitHub

**Do not assume this change is active on the Ubuntu host because the GitHub merge completed.**
An authorized on-host operator must:
1. Verify the unique process listening on 8000 is the existing trading backend; record PID. Confirm active exit/protection state before touching the presentation layer.
2. Preserve uncommitted and untracked files. Fetch the updated code without resetting or overwriting any dirty local worktree. If safe merge/install of the updated launcher and gateway is not possible, stop.
3. Restart only the Sterling presentation manager using its actual service ownership. A quick `trycloudflare.com` hostname can rotate. Do **not** stop the broker backend, uninstall a system-wide package, or assume Kite's registered callback automatically changes.
4. Verify the broker PID is unchanged and `./sterling.sh status` shows `mode=quick` or `named`, `release_ref=origin/main`, `release_auto_fetch=true`, `public_ready.cloudflare=true`, and **no** ngrok child, URL or errors.
5. Confirm `./sterling.sh urls` shows only Cloudflare + local/dev addresses. Verify Telegram buttons and the actual public auth boundary from a phone.
6. Merge a harmless frontend-only canary and collect timestamps, new frontend SHA, device refresh behavior, and rollback evidence. A valid build should publish automatically; a rejected build must preserve the last good assets.
7. Only with independent broker authority verify callbacks, identity and exit/protection continuity. A UI deployment does not certify live trading or profitability.

## Evidence boundary

GitHub code review is not host execution. The host's last supplied receipt was `manager_pid=3000050`, `mode=both`, `release_ref=refs/heads/dev`, with Cloudflare ready and ngrok quota-failed. There is **no** subsequent evidence showing the upgraded presentation manager was installed or the old ngrok process stopped. Maintain the host cutover and device acceptance checklists in issue #277 until receipts arrive.
