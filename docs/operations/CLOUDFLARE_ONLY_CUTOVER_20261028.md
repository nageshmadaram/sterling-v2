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

## Automatic frontend publishing and disk headroom

Before extracting frontend source, building, or copying release assets, the
frontend publisher verifies at least **2 GiB of free disk space** on the host
filesystem containing `.runtime/stack`. A missing disk-usage reading or a
lower free-space value aborts that UI update with
`release_error=ReleaseStorageBlocked` and `release_stale=true`.

The last successfully published frontend stays available; the trading backend,
Kite process and existing local working files are not modified by this
publisher guard. The guard is a lower bound, **not** an assurance that npm can
never consume extra storage. Actual disk use and backup safety remain separate
release acceptance checks. Do not disable the guard to force an update on a
nearly full trading host.


## Interrupted frontend-release recovery

The gateway serves an immutable SHA-named frontend release via an atomic
`published` symlink. A legacy/interrupted build may have left a nonempty,
incomplete SHA-named directory. After a replacement build is fully staged,
the publisher may reclaim **only** that unreferenced incomplete destination
before promoting the new release. It refuses to remove the active target,
a symlink destination, or any complete immutable release. The previous
published frontend remains available until the replacement is ready.
This recovery is distinct from, and does not certify, a real-device rollout.


## Bootstrap and Git fetch preflight

The same 2 GiB minimum reserve now also guards disk-consuming preparation
before the release publisher starts: creating an absent Python environment,
installing dependencies via pip/npm, and fetching `origin/main`. If the
operating system cannot report available disk, the operation fails closed.

If `./sterling.sh up` must replace an existing presentation manager and its
new startup would first need dependency installation, the launcher checks disk
space **before signaling the current manager**. At low headroom it leaves the
existing frontend/tunnel running and does not modify Kite. Already-installed
dependencies are reusable without an unnecessary bootstrap, but a new
frontend build still needs its own separate disk preflight.

These guards apply only after they are installed on the real Sterling host.
They are a minimum reserve rather than a bound on npm/pip's peak usage;
disk reclamation and host deployment require separate operator evidence.


## Served release integrity

`/__sterling_web_health.release_commit` now identifies the SHA of the
immutable `releases/<sha>` directory actually reached by the gateway's
`published` symlink. This avoids showing the old commit during the small
period between atomic symlink promotion and writing `published.json`, or
after an interrupted manifest update. The Python publisher independently
derives its current commit from the same complete managed release, so stale
metadata does not incorrectly erase its ancestry baseline.

For non-production gateways using a plain static `dist` directory, the
health endpoint retains the manifest compatibility fallback. A reported SHA
is evidence of the *local gateway's* active build, not proof of a live
Cloudflare tunnel, device reload, successful test suite, or broker readiness.


## Invalid public release target must fail closed

If the public `published` symlink is broken, redirected outside the managed
`releases/<sha>` directory or missing a complete `index.html`, the gateway
reports `ready: false` and `release_commit: null` instead of repeating a
stale `published.json` value. Static SPA/assets return HTTP 503 while this
condition exists; API and broker proxy endpoints remain separate. A valid
managed release restores static readiness without restarting the gateway.
This is source behavior pending real-host and device verification.
