# Notification destination and temporary-origin recovery

Baseline: `d52d852de084b71b587378adb1522d3dcfcc8d56`.

## Two distinct failures

The user received push notifications but tapping one opened the retired `replies-evans-thesis-remainder.trycloudflare.com` hostname. Cloudflare DNS-over-HTTPS returned NXDOMAIN (Status 3). The current temporary origin was healthy. Registering on the current origin resolved navigation, confirmed by the user as “working.” Origin-bound subscriptions cannot migrate when a temporary tunnel hostname changes. No same-origin protection was weakened and no further notification was sent during this implementation.

Subsequent end-to-end inspection found an additional application defect: `/?notification=alerts&view=live` selected Positions in the shell header while the actual KiteTab content used the saved/default page. The live browser showed PCR; the isolated component reproduction also demonstrated Dashboard or a saved Orders page taking precedence. The existing shell tests mocked KiteTab, so they did not cover its independent initialization.

## Exact implementation

- Share `notificationDestination` between the shell and trading pane: alerts → Positions, PCR → PCR, signals → Dashboard.
- Give `useSavedSelection` an optional initial selection, checked before session storage. Existing callers retain their behavior.
- KiteTab passes that initial selection only for a known notification category with `view=live` and treats it as an explicit navigation choice. Late default-setting hydration cannot replace the requested destination.
- Ordinary clicks continue updating saved navigation after notification entry. Unknown categories and simulation/unrelated links retain the saved/default behavior. Exits, broker admission, AUTO, live execution, strategy rules and settings are unchanged.

## Verification

Ten component cases exercise the real KiteTab navigation and saved-selection hook, with heavy pane contents and broker surfaces mocked. Six failed on the original source; all ten pass with the fix. Cases cover all three categories with/without a saved page, unrelated links, and subsequent manual navigation. Existing shell tests also pass: 26 focused cases total.

An offline browser fixture uses the real KiteTab and hook with placeholder panes, blocked HTTP and CSP `connect-src 'none'`. The same alerts/live URL with saved PCR renders PCR before and Positions after; manual navigation to Orders still works. This is navigation proof, not broker/order or device receipt evidence.

Full frontend: 2,380 tests across 257 files passed; standalone TypeScript passed. Full `make verify` passed: 11783 passed, 30 skipped in 1524.08s (0:25:24); TypeScript and VERIFY OK. Failure sets remain empty and the exact 30-skip set is unchanged. Final log and source hashes are recorded in `validation.json`. Earlier release baseline: 11,783 backend passed with 30 skips, no backend/frontend failure set. No backend source has changed.

Private logs, original/fixed screenshots, source hashes and runtime checks are in `.runtime/qa/2026-10-25/notification-recovery-followup/`. Runtime release promotion is recorded after integration, separately from tests.

## Acceptance boundaries

Device receipt and recovered navigation have direct user confirmation. Full account/live-simulation ownership permutations, a complete market-load session, Gamma two-day recovery/speed/simulation, real Snapback saved-row restore, authoritative OOS research, hosted CI billing and sustained temporary-tunnel stability retain their prior requirements. Sunday health coverage is not a trading-session pass. Current same-release closed-market observations are healthy; no production order/GTT mutation is part of QA.
