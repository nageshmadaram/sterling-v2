# Sterling changes — 18 October 2026

These notes describe source changes and recorded checks. They do not certify live trading or close every item in the original audit.

## Changes users should notice

- Order updates use the same authenticated connection as live prices. The extra connection that subscribed before signing in has been removed.
- Routine position refreshes are less frequent. Order events still refresh orders, positions and trades.
- Expired sessions can renew after temporary connection trouble. Revoked sessions require sign-in, and anonymous order subscriptions are no longer attempted.
- Table filters survive a reload. Profile and column menus stay on-screen, including at phone widths and when the interface is scaled.
- Strategy screens and optional order controls load when needed. The shared dashboard file is about 27% smaller than the earlier measured build; shared libraries have separate cacheable files.
- EMA close requests no longer wait indefinitely for a background screen refresh before showing their result.
- The native mobile app has a sign-in screen, an editable server address, session renewal, sign-out, connection timeouts and clearer stale-data errors.
- Mobile orders reject invalid quantities and prices, ignore repeated taps while submitting, and do not automatically replay an order after an uncertain response or session renewal.
- Mobile price polling pauses when the screen is not being viewed or the app is in the background.
- Mobile safety settings show an unavailable state when they cannot be verified. The unsupported paper/shadow/live router buttons have been removed.
- Mobile dependencies have been upgraded from the old Expo 52 setup to a consistent Expo 58 setup. Android, iOS and web bundles build. A native application rebuild is required; this is not an over-the-air compatibility claim.

## Reliability and trading safeguards

- New deployments require an explicit setting to enable new live entries. Existing exits, protection and reconciliation remain independent of that setting.
- Discovering an outbound IP no longer approves it as the broker's registered address.
- A startup lease prevents two new execution processes from sharing the same trading database.
- Backend recovery requires verified process ownership. A missing port alone is not permission to replace an unidentified trading process.
- The supervisor uses a lightweight liveness endpoint, allows graceful shutdown, and no longer terminates unrelated Uvicorn processes.
- The launcher leaves an unidentified process on the development port alone and stops only its own Vite process group.
- Archived strategies start in maintenance mode so existing positions remain managed without originating new scans.
- An administrator-only deep health check reports worker tasks, database availability, broker connection, stream connections, Telegram polling and disk headroom. It does not replace broker acceptance testing.
- Startup refuses to run execution workers without the durable database. Failed database initialization closes its connection, and duplicate connection setup was removed.
- EMA recognizes a replaced database, and incomplete historical signal records are shown as ineligible instead of crashing the snapshot.
- Snapback manual-entry readiness and plan admission now respect market-session pauses and return the session refusal reason.
- Simulation quote freshness uses the same injected clock as its virtual exchange. Production still uses the real clock.
- Critical alerts continue to take priority over informational order messages. Regression tests now verify that intended behavior.

## Verification and maintenance

- Preserved the original seven-file uncommitted patch before making changes.
- Included the dev branch's EMA index-watch and instrument-selection work in the branch integration.
- Made native mobile checks mandatory in the main CI release gate, a dependency lockfile, API safety tests and platform bundle checks.
- Updated the frontend proxy check to authenticate before testing tenant rejection and isolated its credentials and state from the running deployment. The API and WebSocket proxy check passed.
- Added tests for duplicate execution ownership, unavailable database startup, deep-health authentication, restored database handling and phone menu placement.
- Removed about 1.7 GB of regenerable package caches. No trading database, credentials or user documents were deleted.
- Created an online backup of the running deployment's two declared databases and restored their contents in isolation.

## Still required before calling this a live release

- Verify the authorized broker account, stable outbound IP whitelist, broker callback URL, actual exposure and broker-held protective orders.
- Complete moving-market order/fill/stop/trail/exit and restart acceptance. Offline broker doubles are not real broker receipts.
- Complete the deployment freeze and strategy manifests. The full restore proof currently fails because the deployed lane manifests are missing, although database contents restored successfully.
- Provide more disk headroom; root remains approximately 96–97% used after cache cleanup. Do not delete or rename `sterling_paper.db`.
- Complete Docker smoke testing; the local Docker daemon did not answer the bounded check.
- Verify actual Telegram command/alert receipts, the current public Mini App address and physical-device behavior.
- Review the remaining upstream native dependency advisories: the audited tree has no critical advisory, but still has high/moderate advisories. The audit suggests incompatible downgrades for some of them; those were not forced.
- Capture a production browser CPU/long-task profile, API/WebSocket latency percentiles and a real-market performance baseline.

The companion issue-disposition CSV records all 151 supplied issue IDs and deliberately leaves unverified items open.
