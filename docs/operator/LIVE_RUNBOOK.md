# Sterling — Live Runbook

Everything you need to run Sterling day to day. You do not need to know Python,
Git, Docker, SQLite or options trading to follow it.

No passwords, broker credentials or bot tokens appear in this file. They are kept
where section 11 says.

---

## 1. What Sterling does

Sterling trades real money on your Zerodha Kite account with three strategies:

- **Supertrend** — three trend lines of different speeds agreeing on a direction.
- **Snapback** — a sharp move away from a stock's own recent range that tends to
  snap back.
- **Gamma Unwind** — zones and watchlists on index and stock options.

There is no trial mode. When the Kite account is connected and a strategy is armed,
its orders are real. Other strategies (Adaptive Edge, Intraday, Opening Volume,
Navigator) are maintenance-only: they manage and exit positions they already hold
and never open new ones.

## 2. What stops it

Sterling refuses new entries, on purpose, whenever it cannot be sure. Every
refusal names its reason. Exits, protective stops and reconciliation are never
blocked. Three levers are yours:

| Lever | Use it when | How |
|---|---|---|
| Safe mode | You want new trades to stop now | `./sterlingctl safe on "reason"` |
| Kill Switch & Halt (runtime halt) | Same, from the browser | The **Kill Switch & Halt** button in the Kite trading controls |
| Master kill switch | You want entries off across restarts | `STERLING_LIVE_EXECUTION=0` in `~/.config/sterling/sterling.env`, then restart |

Kill Switch & Halt deletes nothing, abandons no position, and does not stop Sterling
exiting or protecting what it holds. It is always safe to press.

## 3. How to log into the broker

Sterling needs a Zerodha Kite login every trading day, before **09:15 IST**.
Zerodha ends every session at 06:00 IST and requires a person at the 2FA step.

1. Open Sterling in the browser.
2. Go to the KITE tab.
3. Click **Login** and complete the Zerodha login in the window that opens.
4. Check that the broker shows as connected.

The first login after an account is created (or converted, section 12) binds that
Sterling account to your Zerodha identity. A login by anyone else is refused.

## 4. How to tell Sterling is healthy

```bash
./sterlingctl status
curl -sS http://127.0.0.1:8000/api/v1/ops/live-status   # needs your login token
```

| Reading | Meaning | What to do |
|---|---|---|
| `NORMAL`, recovery `CLEAN` | Entries are allowed | Nothing |
| `SAFE_MODE` | New entries blocked by policy | Read the reason; see section 7 |
| `RECOVERY_REQUIRED` | Sterling is re-checking its records against the broker after a restart or a mismatch | Wait for recovery; run `./sterlingctl reconcile` if it persists |
| `NO_LIVE_ACCOUNT` | No stored account is live and connected yet | Log in to Kite; see section 12 on an existing database |
| `DEGRADED`, "broker login required" | The Zerodha session expired | Do section 3 |

`DEGRADED` while the market is closed, with only "broker login required", is normal
in the evening.

## 5. "Market data stale"

Sterling is connected but receives no fresh prices. Check your internet, redo the
broker login, and if it persists for more than 15 minutes in market hours, contact
the person in section 11. **Sterling will not enter on stale prices.** Existing
positions keep being managed.

## 6. "Broker holds positions Sterling did not open"

Sterling blocks new entries while the broker account holds exposure it did not
originate (a position you opened by hand in the Kite app, or leftover automation
such as a GTT it does not manage). Close or hand over that exposure, then run
`./sterlingctl reconcile`. This protects you from stacking Sterling's risk on top
of positions it cannot see into.

## 7. Safe mode: why, and how to resume

Safe mode turns on automatically for: unknown broker exposure, a position mismatch,
a failed reconciliation, missing or failed protection, an unresolved order, a
market-data integrity problem, or a runtime identity mismatch. It also survives a
restart. A clean first boot starts in normal mode only if Sterling can prove it is
clean.

To resume, fix the cause first, then either:

```bash
./sterlingctl safe status
./sterlingctl safe off --ack      # your statement that you looked and it is resolved
```

or, as an administrator, the **Resume trading** button in the Kite trading
controls (it calls `POST /api/v1/ops/safe-mode/resume` with the confirmation phrase
`RESUME TRADING`, a reason, and an acknowledgement when the trigger needs
investigating). The name and time are recorded. Never resume to
"get a trade in".

## 8. Size discipline

- On the first live day, run **one lot**: `STERLING_LIVE_MINIMUM_MAX_LOTS=1`, and
  leave `execution_lots` and `max_lots` at 1 on the Supertrend and Gamma settings.
- Keep a rupee loss budget you can accept (`STERLING_LIVE_MINIMUM_DAILY_LOSS_INR`).
  The daily-loss halt (-1,500 INR by default) applies on top.
- Never increase size because it lost money and you want it back, and never
  because it won and it feels like it is working.
- Watch the first entries of each strategy in the Kite app: the order, the fill and
  the protective stop should all be there.

## 9. If something looks wrong

Block new risk first (section 2), then follow
[EMERGENCY_CARD.md](EMERGENCY_CARD.md). The broker app is the source of truth.

## 10. Backups and restore

```bash
./sterlingctl backup          # checksummed backup with a manifest
./sterlingctl restore-check   # proves the latest backup actually restores
```

Backups land under `~/Sterling/backups/`. Keep one copy outside this machine (an
external drive or cloud folder) and copy the newest backup there weekly.

To restore after damage or on a new machine: install Sterling, copy the newest
backup over, and have the person in section 11 run the restore. It refuses a
damaged backup, so a bad copy cannot overwrite a good database. Sterling starts in
`RECOVERY_REQUIRED` until it has re-checked its records against the broker; that
is correct.

## 11. Who to contact, and where credentials live

Primary contact: **Nagesh Madaram**. Tell them the exact words on the screen, the
time it started, and whether the broker shows connected.

Credentials are **not** in this document, the code or GitHub. They are in the
household password manager, together with the Zerodha login and its 2FA recovery,
the machine login and the location of the off-machine backup.
See [ACCESS_CONTINUITY.md](ACCESS_CONTINUITY.md).

## 12. First boot on an existing database

If the database was written by an older build, Sterling converts a stored Kite
account that is not in live mode into a live account at boot, once and with an
audit record. It keeps the account id and API key and secret, and clears the
saved session and broker identity so your next login re-binds it.

It converts only when nothing is open for that account. If it is not safe, the
account is left alone, a critical alert is sent, and no real order can go through
it. Resolve what the alert lists (close or flatten it), then restart. A second
account cannot log in as the same Zerodha user until this one is converted or
deleted. Afterwards log in to Kite (section 3).

---

## Installing the backend as a service (one time)

The backend must come back by itself after a reboot or a crash.

```bash
mkdir -p ~/.config/systemd/user ~/.config/sterling
install -m 600 /dev/null ~/.config/sterling/sterling.env
cp deploy/systemd/sterling-backend.service ~/.config/systemd/user/
cp deploy/systemd/sterling-backend.watchdog.service ~/.config/systemd/user/
cp deploy/systemd/sterling-backend.watchdog.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now sterling-backend sterling-backend.watchdog.timer
loginctl enable-linger "$USER"
```

`loginctl enable-linger` keeps the service running after you log out.

### The environment file

`~/.config/sterling/sterling.env` holds the settings and secrets (mode 600, never
committed). Start from `deploy/sterling.env.example`: Kite API key and secret,
`STERLING_SECRET_KEY`, the Telegram token, the account identifiers and paths.
Set `STERLING_SECRET_KEY` before first use; changing it later makes stored secrets
undecryptable and you would re-enter the Kite credentials once.

The unit sets `STERLING_BIND_HOST=127.0.0.1`. Leave it: `0.0.0.0` puts an
authenticated broker session on every device on the network.

### Checking it

```bash
systemctl --user status sterling-backend
curl -fsS http://127.0.0.1:8000/api/v1/health/live
curl -sS http://127.0.0.1:8000/api/v1/health/ready | head -40
```

`/health/live` answering means the process is up. `/health/ready` may answer 503
for ordinary reasons (a non-trading day, a pending morning login); the watchdog
therefore probes liveness only.

### Stopping it

```bash
systemctl --user stop sterling-backend
```

Stop waits up to 90 seconds so the runtime can finish its transaction and drain
queued alerts. Do not `kill -9` a running session.

---

## Unattended morning start-up

Nothing needs typing except the Kite login. On boot Sterling opens its stores,
provisions the Kite account from `KITE_API_KEY` / `KITE_API_SECRET`, checks safe
mode, recovers each live account against the broker as soon as it has a session,
and starts the three strategy loops and the maintenance loops. The alert outbox
sends a WARNING at 08:45 IST and a CRITICAL at 09:10 IST if the login has not
happened. That is the system reporting the truth, not a fault.

---

## If the operator dies, or hands Sterling on

Sterling is built to outlive whoever set it up. It is deliberately **not** built to
keep trading an account after its holder has died: transactions in a deceased
person's account are not lawful.

1. **Stop the service.** Place no further orders on the previous account.
2. **Flatten and reconcile.** A handover is refused while any position holds
   exposure or any order has an unknown outcome.
3. **Legal transmission.** The nominee or legal heir claims the assets through the
   broker's own process into **their own account**.
4. **New credentials.** The new operator creates their own API key and secret.
   **Never reuse the previous holder's password, API key or access token.**
5. **Rebind.** Add the new Kite account, log in, and update
   `STERLING_FAMILY_USER_ID` and `STERLING_FAMILY_ACCOUNT_ID` to it.
6. **Start.** Sterling refuses new entries until the new account reconciles clean.

Reusing the old login to "keep things running" is trading in another person's
account. If tempted, leave Sterling stopped: a stopped Sterling loses nothing.

---

## What Sterling is, and is not

It is an automated trader with hard limits, not a salary. Its edge is
concentrated: a small number of larger winners pay for many small losers, so long
discouraging stretches are normal. **Sterling should never be the only thing a
household lives on.** Scale size only by decisions made in advance, never because
a few trades went well or badly.
