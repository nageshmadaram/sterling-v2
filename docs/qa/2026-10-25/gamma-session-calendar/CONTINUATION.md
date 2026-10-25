# Gamma automatic scans on non-trading days

Baseline: `57c080852ffbbaaebb16ac0d368acbf12cbf8edf`.

## Finding and evidence

`gamma_move_runner._scan_uid` scheduled levels/watchlist selection and missing-watch recovery from clock slots without checking the exchange calendar. A read-only stored-history capture found Sunday 25 October records marked `live_recorded`, including scheduled-history recovery with 107 watched instruments for both the real-owner and simulation tenants. These older records have no saved PID/release lineage. They are not real trading-day acceptance and are not attributed to the observer's current release. The old live snapshot took 228.57 seconds; the simulation snapshot took 68.28 seconds. Neither closes the two-day recovery criterion.

The durable history intentionally keeps cumulative watches but only the latest `scan` metrics. Cumulative historical watch counts do not establish current runtime recovery. The private one-shot collector captures aggregates, source/timing fields and hashes through a single read-only SQLite query, with no DB initializer, broker calls or raw credentials. It creates a uniquely timestamped artifact. Any observed midday restart still needs its own before/after capture; an end-of-day latest-scan snapshot alone is insufficient.

## Exact fix

Use the existing verified NFO session calendar before claiming automatic level/watchlist slots, owner-data reuse or missing-watch recovery. Closed days return `scan_skipped=non_trading_day`. Unavailable calendars fail closed for scan work with `scan_skipped=calendar_unavailable` and a bounded exception type. The result does not contain raw exception text. Reconciliation runs before this gate; paper position polling, tick management, reference time sweeps and watched subscriptions remain after it. No trading switch or strategy threshold is rewritten. Regular-session clock slots are unchanged. Explicit manual scan/replay behavior is outside this scheduler fix.

Eleven regression cases cover real-owner/simulation tenants on Saturday, Sunday and a verified exchange holiday; unavailable and unexpected calendar errors; regular trading days; and an already disabled engine. Each closed/error case checks that no automatic slot/history work runs and that maintenance still executes. The disabled case uses an immutable config replacement and confirms no calendar dependency. After correcting an initial fixture assignment to the frozen config, the baseline has eight failing defect cases and three passing controls. All 65 focused calendar/production/scanner cases pass with the fix.

Full make verify passed: 11798 passed, 30 skipped in 1017.41s (0:16:57); TypeScript and VERIFY OK. Frontend: 2,391 tests across 257 files. Failure sets remain empty; the exact 30-skip set is unchanged. Source and log hashes are recorded in validation.json. Source is frozen during these gates. Hosted jobs, runtime promotion and exact before/after settings/exposure remain separately recorded; no new release pass is inferred from the focused result.

## Acceptance retained

No stale Sunday record is deleted, relabelled or promoted. Existing same-release market-day load, two-day Gamma recovery and speed/full-day simulation, real Snapback saved-row restoration, authoritative research, hosted CI billing, intended-device observations and sustained temporary-endpoint requirements remain open. The current operator settings, live-execution policy, original-only EMA/Gamma rules and funds/static-IP scope exclusions remain unchanged.

## Actual phone screenshot and header follow-through

The operator supplied a phone screenshot at 18:55 showing the current temporary hostname, Positions (0), the Home/Signals/Watchlist/Terminal/Replay navigation and a LIVE ACCOUNT label. The title above the Positions content incorrectly read PCR. This confirms visible Positions/navigation on that device; it does not show the Signals pane, Default/Selected labels, populated tables, order review or touch interactions. Those observations remain pending rather than receiving blanket mobile acceptance.

The shell initialized its own selected section from the configured default, while KiteTab restored the saved workspace page. The child's initial page-change event can run before the shell installs its passive listener. The shell also did not consume the visible phone-pane title; the existing event was handled only by a menu component that did not render it.

`SimpleTerminal.tsx` now restores the same saved section as KiteTab, gives only an explicit live notification priority over that saved page, and treats a restored selection as user navigation so late default hydration cannot overwrite it. A layout-effect subscription is installed before child passive effects publish the initial phone-pane title. The header shows that title for Signals/Watchlist/Terminal/details, falls back to the actual selected page when cleared and clears it on page/workspace changes. Trading actions, sizing, protection and defaults remain unchanged.

Eleven new frontend cases cover saved Positions/Orders/Holdings over configured PCR, three initial child-pane events, detail close, page navigation, two unrelated notification scopes and explicit live-notification priority. Baseline: ten fail and one control passes; all 66 focused navigation/menu/layout/notification cases pass with the fix. This includes a mocked child event contract and separate real KiteTab/Layout suites; it is not a device screenshot of the updated build. The full frontend gate was restarted for the new UI source; unchanged backend validation continues independently. The original screenshot is preserved privately and no final-device pass is inferred.
