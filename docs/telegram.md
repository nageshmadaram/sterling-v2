# Telegram configuration and behavior

Open Live → Settings → Notifications. The page separates the server's interactive control bot from per-user notification destinations.

The control bot reads `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from server configuration, with encrypted legacy database values as fallback. It accepts controls only when both the Telegram chat ID and sender ID match the configured operator's private chat. Groups can receive notifications but cannot control trading. The historical control bot belongs to the `default` operator; other users manage their own notification destinations.

Send `/menu` to get current controls. The worker installs `/start`, `/menu`, `/help`, `/status`, `/signals`, `/positions`, `/pnl`, `/scan`, `/alerts` and `/algo`. `/kite` remains an alias. Old unsupported commands show help; unsupported old buttons show the current menu. Buy and Square-off buttons require a fresh confirmation, expire, and use random handles that cannot be reused across restarts. Orders still go through the canonical execution service. SuperTrend supplies the interactive signal and scan views; other strategies retain their own entry authorities.

Scans run as one background task per control chat, leaving the menu responsive. A repeated scan request reports that the existing scan is running. Shutdown cancels and awaits scan tasks. ALGO requires confirmation and the same admission preflight as the app. The Alerts toggle affects signal cards only; trade lifecycle and protection alerts continue.

Settings shows worker state, queue count, saved signal-alert preference and credential errors. **Check menu connection** performs `getMe`, `getChat`, `getWebhookInfo` and `getMyCommands`; it does not send a message, consume Telegram updates or trade. A webhook or another polling process must be resolved by giving Sterling its own bot. Do not run a second `getUpdates` consumer to test the production bot.

To add an alert destination, create a bot with BotFather, open it and press Start, obtain your chat ID, then Add and Test. Edit preserves a blank token, replacing a token/chat invalidates reachability, and disabled/deleted destinations stop receiving future notifications. Tokens are encrypted at rest and never returned raw. A token that cannot be decrypted remains visible with an instruction to replace it; Edit and Remove remain available. Test reports a credential error instead of HTTP 500.

Signal cards cover SuperTrend, Gamma Unwind and Snapback. Explicitly restored Adaptive Edge, Intraday Pack and Opening Volume Leaders also participate. Navigator remains advisory in the app. Price Rules receives canonical submission-outcome notifications rather than interactive SuperTrend entry buttons.

The notification worker drains the durable lifecycle outbox before signal cards, including outside market hours. Failed deliveries remain retryable, successful deliveries are acknowledged per destination, Simulation lifecycle notifications route to the owner's destinations, and signal cards obey session hours and user preferences. Transport handles transient errors, Telegram flood waits and HTML formatting fallback. Snapback operational monitoring also uses the same destination store and transport.

Credential setup inspects Telegram tokens nested in per-user JSON as well as dedicated credential columns. Verified migration must preserve both encrypted credentials and successful-delivery acknowledgements. Never restore an old development encryption key just to read one bot token.

## API artifacts

| Route | Behavior |
| --- | --- |
| `GET /api/v1/kite/telegram` | List the caller's destinations; masked token hints and credential errors |
| `POST /api/v1/kite/telegram` | Add a caller-owned destination |
| `PUT /api/v1/kite/telegram/{id}` | Edit or enable/disable a caller-owned destination |
| `DELETE /api/v1/kite/telegram/{id}` | Remove a caller-owned destination |
| `POST /api/v1/kite/telegram/{id}/test` | Send an explicitly requested test message |
| `GET /api/v1/kite/telegram/status` | Worker status for its operator; caller-owned destination/queue/preference summary |
| `POST /api/v1/kite/telegram/control/check` | Read-only remote checks, restricted to the control operator |
| `PUT /api/v1/kite/telegram/preferences` | Persist the caller's signal-alert preference as a strict boolean |

`polling` means the worker completed a successful Telegram update request. `POLL_CONFLICT` means Telegram returned HTTP 409; the bot must not be shared with another update consumer. `starting` can appear during the first long-poll interval. Broker connection is a separate prerequisite for live positions, P&L, scans and trading; a missing broker session is reported to the operator.

### Invalid API key and popup failures

Both login routes perform a bounded read-only check of Zerodha's public login
page. A confirmed invalid-key response becomes an actionable Connect-screen
credential repair message. No API secret, password or access token is sent in
this check. A transient broker/network outage does not prove credentials invalid
and does not clear a saved session.

The app login popup opens a visible preparation page, validates the returned
broker URL, and ends an unresponsive URL request after 15 seconds. Failures close
the popup and appear in the footer connection card as well as notifications.
Environment seeding no longer replaces an already selected account or overwrites
its saved secret, so credential corrections made in Connect survive a restart.

### Saving credentials before first login

Connect permits initial credential correction when the selected live account has
no broker identity, login history, access/refresh/public token, local exposure or
account audit history. This removes the impossible prerequisite of logging in
with the incorrect key before saving its replacement. The account lock and
security posture checks remain active. Previously connected accounts still
require broker flatness proof; deletion and activation retain their existing
checks. A successful save publishes the same durable account that Telegram reads.
