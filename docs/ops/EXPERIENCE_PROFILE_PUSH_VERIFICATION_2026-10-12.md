# Experience, identity, push and feed verification — 12 October 2026

Base checkout: `8f40172fd7213d49ee64d1e3debe984efc074b0b`, synchronized with origin main/dev. Feature work remains local and uncommitted. Browser: http://127.0.0.1:5180/.

## Delivered

- Account-scoped SQLite UI preferences restore before the workspace mounts, migrate existing browser choices once, and retry failed saves through a durable per-account outbox. Appearance, navigation, ticker, layout, board filters, chart/watchlist and replay UI selections use this path. Existing trading configuration remains server-authoritative; unapplied trading drafts and credentials are excluded. Connect → Experience shows database acknowledgement or pending/error status.
- Sterling profile replaces broker identity in the top-right control. Display name and compressed photo are durable. Username/password changes require the current password, rotate token versions and preserve the stable owner UID. Session revocation disables registered push devices. Mobile Menu exposes the same profile dialog. Credential changes were tested with isolated fixture accounts; no operator password was changed.
- Connect → Push notifications provides explicit opt-in, browser permission/device registration, signals/PCR/risk alerts, selected indices, private previews, quiet hours, hourly limits, test delivery and device removal. Encrypted subscriptions and VAPID keys persist in SQLite. Worker delivery is independent of Telegram and trading. Expired subscriptions disable themselves. No device registration, permission grant or real push transmission was performed during this readiness check.
- PCR uses the existing exchange calendar. Closed sessions, holidays, weekends and unknown calendar coverage do not scrape upstream or appear live. Cached values are historical. Provider session date controls source classification, and only completed 15-minute buckets are emitted. Stock ticker PCR explicitly describes nearest-expiry partial strike coverage.
- Connect → Test data feeds searches symbols, probes the chosen provider without changing the active feed, displays provider/price/source timestamp/latency/freshness, and persists an explicit confirmation. Kite NIFTY 50 returned a recent timestamped continuous-session quote and was confirmed at 11:14:30 IST. Yahoo ^NSEI fetched successfully and remained labelled delayed/historical. TrueData probe implementation has fixture coverage; a real TrueData connection was not verified.
- Desktop and mobile share the compact KITE LIVE/SIMULATION switch. Experience navigation hide/show/reorder affects header, mobile menu and activity rail; Connect and Help stay reachable.
- Ticker settings offer classic/mobile card layouts and graph, PCR, graph+PCR, compact badge, detailed insight or price-only content. PCR metric, timestamp and ratio are optional. Runtime desktop proof showed index action, IST time, band and ratio alongside graphs. Original graph/classic selections and Astrology visibility were restored after testing.

## Validation

- Full frontend: **1,942 passed / 193 files**, `/tmp/sterling-experience-final-full.log`.
- Final feature focus: **62 passed / 9 files**, `/tmp/sterling-experience-final-focused.log`.
- Final activity rail/navigation regression: **9 passed / 2 files**, `/tmp/sterling-experience-navigation-tests.log`; tests include actual hiding and ordering of rail controls.
- Backend feature/auth/production-boundary focus: **46 passed**, `/tmp/sterling-experience-backend-final.log` (four warnings). Includes profile rotation, unchanged legacy username/password update, preferences owner isolation, encrypted push ownership/deduplication, holiday/no-scrape behavior and probe confirmation.
- Final production build passed, `/tmp/sterling-experience-navigation-build.log`; existing bundle-size warning remains. Dependency `pip check` passed; push dependency closure is pinned in requirements lock.
- Whitespace check passed. Graph MCP tools were unavailable; source inspection was used as the AGENTS fallback.

## Runtime evidence and limits

Browser sign-in recovered after an in-app browser tab crash, and Experience visibly reported “Saved to your account database.” Temporary desktop viewport was reset. Before the final API update broker UI showed Positions (0), 0 open, no orders today; replay showed 0 trades/0 signals with Stop disabled. No funded orders were submitted; automatic entries remain manual/off. Credentials, databases, encryption keys and local work were preserved. Telegram transmission remained blocked by the existing local readiness transport override.

Screenshots in `.runtime/local-readiness/`: `sterling-profile-controls.png`, `push-notification-settings.png`, `data-feed-test-kite.png`, `data-feed-test-yahoo.png`, `desktop-ticker-pcr.png`, `push-notification-settings-final.png`.

Physical mobile push delivery still requires a reachable HTTPS installation, supported browser and explicit permission/subscription. On iOS/iPadOS the web app must be installed on the Home Screen. This local loopback check does not establish physical phone receipt, and no remote exposure was published. Documentation: https://developer.mozilla.org/en-US/docs/Web/API/Push_API and https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/.

Post-update check: API health returned OK with checker running and zero positions; authenticated Trading Config showed recovery clean and every strategy automatic-entry mode MANUAL. Feed-test confirmation remained in SQLite across the restart. Push stayed disabled with browser permission default and no registered devices. The user-selected Cupertino appearance was preserved. The final browser viewport was restored and Push settings left open.

## Telegram restoration — 12 October 2026

At the user’s explicit request to fix Telegram, removed only the temporary readiness PYTHONPATH guard from the user service drop-in. The prior override was backed up locally; STERLING_LIVE_EXECUTION and all saved credentials/targets remain unchanged. Inspected broker UI: zero open positions, no orders today; replay zero trades/zero signals with Stop disabled. Restarted API safely and verified healthy checker/zero positions. Read-only Telegram getMe and getChat checks both succeeded for the enabled saved target, without printing token or chat details. No synthetic message was sent; configured delivery resumes normally. Telegram delivery/outbox tests: 31 passed (/tmp/sterling-telegram-restoration-tests.log). Earlier readiness-only Telegram-block statements describe the historical check and no longer apply after this restoration.

## Manual strategy Buy correction — 12 October 2026

SuperTrend operator entries wrongly inherited the last-candle is_fresh condition. Removed that condition from current-session active entry eligibility and allowed running SuperTrend rows through both shared row actions and expanded quote ticket. Automatic scanner freshness is unchanged. Ended, replay and prior-session rows stay blocked. Regression tests cover active non-fresh, prior-day/replay/inactive and enabled/disabled row buttons: 34 passed in three files (/tmp/sterling-manual-buy-tests.log); production build passed (/tmp/sterling-manual-buy-build.log). Runtime Buy opened the SBIN OCT 930 PE order ticket; it was canceled without submission. Ticket showed available funds -6342.10 INR versus required 8625 INR for one 750-unit lot. Gamma had zero ENTRY-tier candidates; Snapback explicitly reported no eligible reversal. These eligibility/funding constraints were preserved. Screenshot: .runtime/local-readiness/manual-buy-ticket-restored.png.

## Settings refinement and Gamma selection — 12 October 2026

Settings share compact typography, native controls, definition lists, tables and reduced card decoration across user management, feed testing, trading configuration, Price Rules, Archived, Markets & Tools, Telegram/push notifications and Experience. Replaced promotional headings in feed/push/appearance panels. Added dock-width container queries: under 650px the section picker replaces the sidebar and fields stack, resolving squeezed forms even on a wide desktop window.

Gamma previously rendered index checkboxes but discarded scan_indices because its supplied configuration supports single-stock underlyings only. Removed those misleading controls; no index scanner was invented. Gamma now uses its 213 eligible-stock vocabulary rather than the generic liquidity registry. Runtime custom RELIANCE selection stayed checked and the draft bar remained visible. Test edits were discarded; saved stock scope, execution and broker settings were preserved.

Removed duplicate panel success/error notifications already emitted by Gamma/Snapback mutation hooks. Shared draft bar is portalled above the terminal root and no longer animates its position. Signal-engine order considers actual value changes rather than merely the presence of a draft. Clean navigation through all requested sections showed no spurious draft bar.

Validation: 51 passed across six settings regression files (/tmp/sterling-settings-refinement-tests.log), plus 8 user/Price Rules checks (/tmp/sterling-settings-additional-tests.log). Final production build passed (/tmp/sterling-settings-refinement-build.log); whitespace check passed. Screenshots: .runtime/local-readiness/settings-feed-refined.png and gamma-stock-settings-refined.png. No order, account edit, notification registration or strategy setting was submitted during runtime verification.

## Snapback mode / session state and stale module recovery — 12 October 2026

Added the persisted Snapback trading-mode selector beside Scan: Swing, Scalp and Intraday research. Changes use the same scalp/intraday timeframe/hold presets as Settings, without arming automatic execution or submitting orders. Existing 3-minute scalp configuration was retained during verification.

The observed execution blocker was cash_signal_auction. Classified it with session clock restrictions rather than runtime faults and added a readable reason. Snapshot now preserves underlying live capability while entry validation still refuses the closing auction; the board displays SESSION PAUSED. Intraday remains research-only, and funded-entry safety gates remain intact.

Rebuilds previously deleted hash-named assets still referenced by open tabs. Vite now retains old hashed chunks; both retained ConnectPane assets were verified reachable over HTTP. An already-deleted pre-fix chunk requires a reload; the app was reloaded and Connect successfully loaded. The error boundary offers a specific app-update recovery message and no longer clears authentication/session storage on errors or Retry.

Validation: 25 frontend tests including selector presets, clock-pause display and chunk-error credential preservation; 38 backend tests including auction classification and protected plan ownership. Production build and whitespace check passed. Logs: /tmp/sterling-snapback-mode-tests.log, /tmp/sterling-snapback-session-tests.log, /tmp/sterling-snapback-mode-build.log. Before API restart verified zero broker positions/orders and empty inactive replay. Post-restart health OK. No operator config/mode was changed for testing, and no orders were submitted. Screenshot: .runtime/local-readiness/snapback-mode-session-pause.png.

## Snapback multi-mode and cancellation implementation — 12 October 2026

Replaced the single configuration-mode dropdown with account-persisted scan-mode checkboxes (at least one selection). Scan submits all selected modes. The backend uses isolated transient state/config copies for alternative modes, labels those rows as non-executable previews, and leaves the strategy execution config unchanged. Config changes clear alternate-mode previews. A per-owner interactive scan task allows Stop cancellation; cancelling scanner work does not cancel protection or exit dispatchers. Background scans cannot interfere with an owned interactive scan. Existing no-body scan callers remain supported.

Validation: 25 frontend board tests; 39 backend scan-control/protected plan/position tests. Production build passed. API deployment is pending user authorization because the app currently has an in-memory replay at “Starting replay”; that replay is being preserved. The authorization question offers archiving available replay records before restarting, or deferring the restart. No replay was stopped, no API restart performed and no new multi-mode scan submitted during this pending step.

## Fixed strategy footers and approved scan activation — 12 October 2026

Moved SuperTrend controls outside the scrolling table; Gamma/Snapback use a shared flex column with an independently scrolling row area and anchored strategy footer. Expanded Gamma history/coverage remains in a bounded footer disclosure. Control popovers stay usable; Snapback mode checkboxes expand inside the footer. Removed Gamma/Snapback search inputs and clear hidden search filters. Shared SignalBoard explicitly owns the same font stack on all three boards.

Runtime bounding checks showed SuperTrend and Gamma footer bottom 1159px against signal-pane bottom 1160px. Gamma search count was zero. Screenshot: .runtime/local-readiness/gamma-fixed-footer.png. Snapback was not visible during that check. Later investigation traced its failed enabled-state query to an indentation error in the cancellation cleanup; the error and tab visibility behavior were corrected without changing execution configuration.

The user approved archiving and restarting to activate the prior multi-mode/cancel backend. Archived available starting replay summary (zero trades, zero signals) and screenshot under .runtime/local-readiness/replay-archive-2026-10-12-starting.{txt,png}. Restart completed; API health OK and zero positions. Scan cancellation and multi-mode API are now active. No funded order or execution-mode change was made.

Validation: 87 frontend tests across four board/entry files; prior cancellation/protected-plan backend suite 39 passed. Final build and whitespace checks passed. Logs: /tmp/sterling-signal-footers-tests.log and /tmp/sterling-signal-footers-build.log.

## PCR restart-cache restoration — 12 October 2026

An API restart after market close cleared the in-memory PCR payload. The correctly gated closed-session response then bypassed browser books and blanked the desk. Closed-session empty responses now restore the requested/latest recorded browser book, labelled historical with its original timestamp. Future successful server PCR payloads are saved in SQLite and recovered after a restart without scraping on holidays or outside the session.

Runtime verified today’s PCR prints through 15:30 restored before and after backend restart, with explicit market-closed/historical status. Before restarting checked zero broker positions/orders and zero replay trades/signals, Stop disabled. API health remained OK. Validation: 21 frontend PCR tests, 7 backend parser/single-flight/restart-cache tests; build passed. Logs /tmp/sterling-pcr-cache-{tests,backend,build}.log. Screenshot .runtime/local-readiness/pcr-after-close-restored.png. No new funded order or Telegram test was submitted.

## Gamma display / Snapback restoration — 12 October 2026

Removed redundant LIVE/NOT LIVE/UNKNOWN and ALGO ON/OFF board labels; configuration remains in Settings. Historical context and actual entry-block reasons remain. Standalone option row text now uses the same muted contract color and regular weight as SuperTrend legs; underlying text uses the same direction color and weight.

Boards remain accessible when scanners are off or enabled-state requests fail. Snapback had not been deleted: its failed snapshot/config import made the old visibility filter hide its tab. Fixed the cancellation cleanup indentation error that caused the import failure. Runtime verified Snapback snapshot loads, Scan/Modes controls are present, and SESSION PAUSED is reported after close. Configuration was not changed.

Validation: 55 frontend board tests and 39 backend cancellation/plan/position tests passed; build and whitespace checks passed. Screenshot: .runtime/local-readiness/snapback-tab-restored.png. Logs /tmp/sterling-board-visibility-{tests,build}.log and /tmp/sterling-snapback-restoration-backend.log.

## Strict Today only filtering — 12 October 2026

Removed the prior-day active/running/watching exemptions from shared board filtering and the SuperTrend source-row filter. Today only now includes only rows whose signal date equals the current IST/effective replay session day; unknown dates are excluded. Older active positions remain in Positions. Toggling off restores the full board view.

Validation: 81 tests across shared view, SuperTrend Today only, Gamma and Snapback boards passed; production build and whitespace checks passed. Regression includes prior-day running/watching/weakening rows and undated rows. Logs /tmp/sterling-today-only-{tests,build}.log. Browser reload was issued but final live DOM verification was blocked by an unresponsive in-app browser/CDP command; no live-check claim or new screenshot was made. No backend restart or trading action occurred.
