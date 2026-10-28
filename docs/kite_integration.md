# Zerodha Kite Integration (Indian Markets)

A complete, multi-tenant Zerodha **Kite Connect v3** broker integration: full REST
surface, the live KiteTicker binary WebSocket, per-user encrypted credentials, and a
dedicated **KITE** UI tab. It is a **standalone manual trading + portfolio + market-data
console** — no Sterling/Grok/scalping strategy is wired to Indian markets.

## Architecture

```
backend/app/services/exchanges/kite/
  constants.py      # endpoints, order enums, ticker segment maths
  errors.py         # KiteError hierarchy + error_type → friendly message
  models.py         # account CRUD + session + write-request Pydantic models
  session.py        # login_url(), checksum()  (pure, unit-tested)
  client.py         # KiteClient(TradingExchangeAdapter) — full REST surface
  instruments.py    # multi-exchange instruments cache + search + token resolution
  ticker.py         # KiteTicker — binary WebSocket tick decode + connection mgr
  ticker_manager.py # per-user ticker lifecycle → broadcasts to StreamManager
  accounts.py       # KiteAccountStore — per-user encrypted credential/session store
backend/app/core/
  auth.py           # get_current_user (pluggable multi-tenant user context)
  security.py       # Fernet encryption for secrets at rest
backend/app/api/v1/endpoints/kite.py   # 42 routes under /api/v1/kite
frontend/src/components/kite/           # KiteTab + Connect/Watch/Portfolio/Orders/GTT panes
frontend/src/hooks/useKite.ts           # React Query hooks + mutations
```

The legacy `app.services.exchanges.adapters.zerodha:ZerodhaAdapter` is now a thin
backward-compat shim re-exporting `KiteClient` (registry + factory unchanged).

## Multi-tenancy

Every credential, session and tick stream is scoped to a `user_id`.
`app.core.auth.get_current_user` resolves it from the `X-User-Id` header, falling back
to `"default"` for single-user/local usage. **This is the single seam** where a real
identity provider (JWT/SSO/session) plugs in later — swap that one dependency and the
whole `/kite/*` surface becomes authenticated with no other changes.

Secrets (`api_secret`, `access_token`) are encrypted at rest with Fernet, keyed by the
`STERLING_SECRET_KEY` env var. **Set `STERLING_SECRET_KEY` in production** (a warning is
logged and an insecure dev key is used if it is absent).

## Setup (per user, from the UI)

1. Create a **Kite Connect app** at https://kite.trade → get an **API key + secret**.
   Set the app's **Redirect URL** to the exact callback shown under Connect.
   For phone and Telegram login, use the published HTTPS callback, for example
   `https://your-sterling-host/api/v1/kite/callback`. A localhost callback only
   reaches Sterling when the browser runs on the Sterling computer.
2. In the app: **KITE tab → Connect & Keys → Add Kite Account** (API key + secret).
3. Click **Open Kite Login** and log in on Zerodha. The reachable callback exchanges
   the request token on the server and stores the session encrypted; Sterling
   detects completion even when the phone opens a different browser.
4. Toggle **Paper mode off** on the account to place live orders.

### Mobile and Telegram login recovery

Kite uses the Redirect URL saved for the API key in its developer console. The
login link cannot override it; see [Kite's login flow](https://kite.trade/docs/connect/v3/user/).
Register the public HTTPS callback for automatic phone login. Temporary tunnel
addresses change on restart and must be updated in Kite, or replaced by a stable
hostname.

If Kite still redirects the phone to an unreachable localhost address, copy the
**entire browser address** after successful authentication. Paste it into Connect's
login field, the Mini App's **Finish mobile login** panel, or the configured private
Telegram bot chat after opening **Broker Login → Finish mobile login**. Sterling
checks the signed state against the current user and account before exchanging
the token. Login links expire after 15 minutes; request tokens are single use and
should be submitted immediately. Raw tokens without signed state are not accepted
through Telegram or the Mini App recovery action.

Every Telegram broker login menu includes **Copy login URL** and a selectable URL
for clients that lack native clipboard buttons. Sterling also provides open/copy
links when a mobile browser blocks the login popup. Passwords and API secrets stay
in Zerodha and Sterling's credential settings, respectively.

### Confirming login

The callback shows a persistent **Kite login successful** receipt with the account,
Kite ID and session expiry after Sterling saves the broker session. Telegram also
receives a success confirmation through the durable notification queue. The Mini
App and full app verify the receipt for the interactive login they started;
automatic token renewal cannot complete that confirmation.

Use **Broker Login → Check login status** to verify the current session with Kite.
The reply distinguishes a confirmed connection, an expired/missing login and a
temporary verification failure. If the phone is still on an unreachable localhost
page, finish the signed URL recovery or correct Kite's registered Redirect URL
before expecting a successful Sterling login receipt.

### Daily login
Kite `access_token`s expire daily (~6 AM IST) and there is **no refresh token** in Connect
v3 — the user must re-login each morning. The UI surfaces a "session expired — reconnect"
state when a `TokenException` is seen.

### Historical data
`/instruments/historical/*` requires Zerodha's paid **Historical Data** add-on; without it
those calls fail with a clear message.

## Endpoint reference (prefix `/api/v1/kite`, all `Depends(get_current_user)`)

| Group | Routes |
|---|---|
| Accounts | `GET/POST /accounts`, `PUT/DELETE /accounts/{id}`, `POST /accounts/{id}/activate`, `POST /accounts/{id}/test` |
| Session | `GET /login-url`, `POST /session`, `GET /status`, `POST /logout` |
| User | `GET /profile`, `GET /margins` |
| Market | `GET /instruments`, `GET /quote|ohlc|ltp`, `GET /historical` |
| Portfolio | `GET /holdings`, `GET /positions`, `PUT /positions/convert` |
| Orders | `GET/POST /orders`, `PUT/DELETE /orders/{id}`, `GET /orders/{id}/history|trades` |
| GTT | `GET/POST /gtt`, `GET/PUT/DELETE /gtt/{id}` |
| Margins | `POST /margins/orders|basket`, `POST /charges/orders` |
| Mutual funds | `GET /mf/holdings|orders|sips`, `POST /mf/orders`, `DELETE /mf/orders/{id}` |
| Ticker | `POST /ticker/subscribe|unsubscribe`, `GET /ticker/status` |

Order placement passes through `live_safety.assert_safe_to_trade` (kill-switch → 423,
daily-loss → 423, idempotency dedup). Paper-mode order/GTT writes return mock ids and
never hit the network.

## Live ticks (KiteTicker)

`ticker.py` decodes the big-endian binary tick frames (LTP / quote / full incl. 5-level
depth + OI). `ticker_manager` runs at most one ticker per user (from their active **live**
account) and broadcasts decoded ticks to the `kite_ticks:{user_id}` channel on the existing
`/api/v1/stream/ws` socket — no second public socket. Paper accounts don't open a ticker.

## Contract caveats

- **`get_product_id(symbol)`** returns the Kite `instrument_token` (int). For Kite this is
  *informational* — orders are placed by `tradingsymbol`+`exchange`, not a numeric id
  (unlike Delta). Returns `0` on miss rather than raising.
- **`cancel_order(order_id, product_id)`** ignores `product_id` (kept only to satisfy the
  `TradingExchangeAdapter` contract); cancellation uses `variety`+`order_id`.
- **`post_only`** is unsupported by Kite regular orders → raises `KiteOrderError`.
- Brackets: Kite has no per-order SL/TP bracket. Use **GTT** for protective exits; the
  generic `place_order` places the entry leg only (honors `stop_loss` only with an explicit
  `kite_order_type` of `SL`/`SL-M`).
- Kite orders/GTT are **form-encoded**; margin calculators are **JSON**. GTT `condition`/
  `orders` are JSON strings embedded in form fields.

## Rate limits

3 req/s (1 req/s for quotes). Quotes are batched via repeated `i=` params; UI polls at 5s.

## Tests

`backend/tests/test_kite_{session,instruments,orders,accounts,ticker,router}.py` — run with
`PYTHONWARNINGS=ignore python -m pytest tests/test_kite_*.py -q`.

### Assisted login with a human TOTP

In **Users & Broker Logins**, save a linked account’s Kite user ID and password,
select that account in **Login with saved Kite credentials**, and start login.
The same chooser is available in the footer’s Kite status card. Single-account
workspaces still show an explicit chooser. **Manual Kite login** remains a fallback.

The backend uses Playwright with an installed Google Chrome (`channel=chrome`)
in a fresh, headless browser context. It fills the normal Kite login page, submits
once, waits for a human-entered six-digit TOTP, and intercepts the signed callback
before exchanging and persisting the session. This uses browser interaction, not
a documented password/TOTP API. Kite page changes or additional challenges may
require manual login; no CAPTCHA handling or automatic password retries are used.

Attempts are owner-scoped, expire after five minutes, and can be cancelled.
Changing/removing saved credentials invalidates a pending attempt. The returned
broker identity must match the saved username before any session is persisted.
No TOTP seed, code, browser profile, screenshot, or trace is persisted by this flow.
Cloudflare is the only public tunnel managed by the launcher. Completion uses the existing
account-specific durable Telegram confirmation. Choosing a login does not change
the active account used for trading.

While assisted login is submitting credentials or waiting for/verifying TOTP,
Sterling can show a read-only viewport preview of the actual Kite browser page.
The live view is collapsed by default. Only while expanded, the owning user’s authenticated client requests frames every two seconds. All
inputs, text areas and editable fields are masked, captures are rejected if the
browser leaves HTTPS `kite.zerodha.com`, and preview responses are `no-store`.
Frames are not saved on the backend and are cleared from the UI when login ends.
The progress list remains available if the preview cannot be captured.

**Test saved credentials** submits the selected account’s saved username/password
once and stops when Kite accepts the primary credentials; it does not submit a
TOTP or exchange a request token. Acceptance is distinct from a connected broker
session. Only explicit broker credential errors are reported as requiring a
password/user-ID update. Browser startup, connectivity, unexpected form, extra
challenge and session-save failures have separate sanitized diagnostic codes.
Raw browser errors, response bodies, passwords and OTPs are not logged.
