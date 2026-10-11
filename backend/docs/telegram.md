
## Broker login and current status

The desk now provides Broker / Login, Live health and Strategies workspaces in
addition to Signals, Positions, P&L, Scan, ALGO and Alerts. `/login`, `/health`
and `/strategies` are registered Telegram commands. The ALGO action controls
SuperTrend; Gamma-Unwind and Snapback retain their strategy-owned execution.
Archived strategy availability is shown with its actual saved enabled state.

Startup loads the explicit Kite credential environment allowlist as well as
Sterling settings. This repairs installations where `.env` credentials existed
but no broker account was provisioned. Existing process environment values win;
arbitrary environment variables are never exported from the file.

Broker connection transitions enqueue durable notifications outside market
hours, independently of signal-alert preferences. The configured private control
chat receives Connect Kite and Current health buttons. Successful Kite callbacks
notify the account owner after the encrypted session is saved. Network validation
failure does not erase a session or claim that its token expired.

Login is completed at Zerodha using an expiring signed account-bound state and
the existing callback/session exchange. Passwords, OTPs, API secrets and access
tokens are never requested in Telegram. The app and Telegram use the same account
store; login does not alter strategy settings or switch on ALGO.

**Device constraint:** Kite redirects to the URL registered in its developer
console. If that URL uses localhost, open Telegram's login button on the Sterling
computer. Phone login requires a reachable HTTPS deployment callback registered
with Kite, pointing to `/api/v1/kite/callback`. A Telegram button cannot make a
localhost callback accessible from a phone. No tunnel or public exposure is
created automatically.

Queued LIVE STATUS and TRADING READY cards now render the current health at
send time with a fresh checked timestamp. Historical fills, exits and protection
events preserve their original event details and times.
