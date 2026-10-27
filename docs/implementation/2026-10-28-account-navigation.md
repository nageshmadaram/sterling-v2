# Account navigation and administration — 2026-10-28

## Delivered

- Direct media links initialize the shared toolbar after the shell mounts, preserving visible search and display controls.
- One shell header per workspace. Profile/photo/security, appearance and sign-out are in a shared account menu. Kite identifies its workspace beside Sterling. The logo selects a workspace; Home returns to the configured enabled Kite page.
- Mobile navigation uses the same enabled-page policy as desktop, deduplicates tools, and evicts a newly hidden current page. Inspector annotations require explicit development opt-in.
- Broker disconnection no longer forces a blocking dialog. Explicit connection controls remain. Non-dashboard pages use the full content area; phone layouts show one pane and omit the ticker strip.
- Echo hides its uninitialized document until Home is initialized. Host appearance is sent through the existing opaque player bridge and applied independently of player skin, with light surfaces and readable text. No origin relaxation or media inversion.
- Administration centers on selectable user rows. Each user exposes workspace/settings access, current broker ownership, Telegram destinations, access requests and browser activity. Bulk feature changes validate all revisions before one atomic transaction. Existing account creation/suspension/password management remains under Accounts.
- Broker assignments show the current owner, confirm reassignment, and retain a visible result after refresh. Safe reassignment transfers encrypted profile login credentials but clears authenticated broker sessions. Historical execution ownership stays unchanged.
- Designated admins can replace broker login credentials without revealing saved secrets. Status distinguishes saved/missing passwords. Credential writes bind the observed owner and profile revision and are audited without secrets.
- Successful password logins and recent authenticated activity capture browser, OS, device, IP and trusted tunnel country. Online means authenticated activity within two minutes with an unexpired, unrevoked token. This is observed browser activity, not exact device/session inventory; earlier login history and precise geolocation are unavailable.

## Verification

- 36 account/configuration/credential tests; 66 workspace/account/today-priority tests; expanded workspace suite 21 tests, including new admin credential masking/audit test.
- 83 targeted UI tests; 150 merged signal/exit-protection tests. Production frontend build passes.
- Full rendered Kite runtime (not a stub), account menu, Echo/Space switching and light/dark messaging exercised at 390px and 1440px. Compiled frontend and central user directory exercised at 390px. Screenshots visually checked for actual inner-player contrast.
- Live manual Kite provider preflight returns a valid provider URL. The current owner initially had no saved Kite password. A read-only provenance check found encrypted credentials under the prior owner for the exact broker ID and matching verified Kite identity. After checking the assignment audit and absence of target credentials, an audited transaction moved the encrypted value to the current owner. Masked credential status now confirms the password is saved. No broker session was transferred. No broker sign-in, TOTP, order or reassignment was executed during verification.
- Before restart: read-only broker REST check found no open positions or pending orders. Consistent SQLite backup compressed under `.runtime/deployments/20261028-account-navigation/`; existing entry pause preserved.

## Source synchronization

Merged upstream `a18c842b` including signal/research/exit safety changes with the prior management release. Existing operator notes/evidence restored from the pre-merge stash without overwriting them. No force pushes or historical order ownership rewrites.

## Public transport

Public browser checks exposed intermittent Cloudflare 522 responses while the local app stayed healthy. The user service now explicitly selects HTTP/2 for Cloudflare transport; only the web/tunnel supervisor was restarted. The quick-tunnel public hostname changes on restart. The separately supervised broker backend remains running.
