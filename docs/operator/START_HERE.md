# Sterling — Start Here

You do not need to read code or understand options to run Sterling safely. You
need this page and the four pages it points to.

> **If Sterling is not NORMAL, the correct action is never "make it trade".
> The correct action is "keep new exposure blocked until the uncertainty is
> resolved."**

---

## 1. What Sterling is

Sterling trades **real money** on your Zerodha Kite account with four primary
strategies:

- **Supertrend** — three trend lines of different speeds agreeing on a direction.
- **Snapback** — a sharp move away from a stock's own recent range that tends to
  snap back.
- **Gamma Unwind** — zones and watchlists on index and stock options.
- **Price Rules** — manual long index options and one-shot price conditions;
  open Connect → Price Rules and explicitly arm each plan.

Adaptive Edge, Intraday and Opening Volume are disabled by default and manage
existing positions in maintenance mode. Enable them per user under Settings →
Archived. Navigator remains advisory. Live entries place real broker orders.

## 2. How it protects you

Sterling refuses a new entry whenever it cannot be sure: unknown broker state,
stale data, an unresolved order, missing protection, a daily-loss halt, or an
exposure cap. Every refusal names its reason. It never blocks an exit, a protective
stop, or reconciliation. Supertrend and Gamma intraday entries require a stop held
at the broker; a position whose protection cannot be confirmed is repaired or
exited.

Day-one limits are on: one lot per entry (`STERLING_LIVE_MINIMUM_MAX_LOTS=1`, shipped
in the templates and Compose; raise it deliberately), a rupee loss budget, and a
daily-loss halt at -1,500 INR by default.

## 3. Your first session

1. **Before starting or restarting**, inspect the protected environment configuration
   and confirm `STERLING_LIVE_EXECUTION=false` for any session that has not been
   separately approved. If positions are open, first consult the emergency runbook:
   do not shut down a worker responsible for exits or broker protection.
2. Start the backend (`scripts/sterlingctl start`, or its managed systemd service)
   and log in to Kite through the Sterling UI. Account binding must match the
   authorized Kite identity. Check `scripts/sterlingctl status`, current broker
   exposure and unresolved orders before advancing.
3. Keep `STERLING_LIVE_EXECUTION=false` while checking broker binding, capital
   limits, stable/approved egress, current exposure, protection and restoration.
   Configure the account-owner-approved caps, including
   `STERLING_LIVE_MINIMUM_MAX_LOTS=1`, but **do not enable new entries yet**.
4. Complete the strict release and broker lifecycle gates in
   [the go-live runbook](../operations/FINAL_RELEASE_RUNBOOK.md). Only after
   independent approval by the authorized account owner may the live-entry
   setting be explicitly enabled for a tightly controlled session.
5. When the first authorized entry actually occurs, watch the broker app directly
   for the order, **actual fill**, and protective stop/GTT. If anything differs,
   press **Kill Switch & Halt**, check the broker, and follow
   [EMERGENCY_CARD.md](EMERGENCY_CARD.md). Do not increase size during the day.

## 4. The commands

Everything is one command. Run them from the Sterling folder.

```bash
scripts/sterlingctl status          # what Sterling is doing right now
scripts/sterlingctl doctor          # configuration and safety checks; read every FAIL
scripts/sterlingctl start           # runs the start sequence, stops at the first failed step
scripts/sterlingctl stop
scripts/sterlingctl safe on         # block new trades immediately
scripts/sterlingctl safe off --ack  # allow them again (only after you fixed the cause)
scripts/sterlingctl reconcile       # compare Sterling's view against the broker's
scripts/sterlingctl backup          # checksummed backup with a manifest
scripts/sterlingctl restore-check   # prove the latest backup actually restores
```

A `doctor` exit code of **2** means a check could not be run at all. That is not
a pass; treat it like a failure. Release identity and certification lines are
advisory: they never stop trading.

## 5. What the statuses mean

| Status | Meaning | What you do |
|---|---|---|
| `NORMAL` | All mandatory health checks passed | Observe. |
| `SAFE_MODE` | New risk is blocked by safety policy | Do not override. Read the reason. Reconcile if told to. |
| `DATA_ERROR` | Market data cannot support new exposure | Do not trade. Check the feed and the market session. |
| `BROKER_ERROR` | Broker state or connectivity is uncertain | Open the broker directly. Reconcile. Keep new risk blocked. |
| `EVIDENCE_ERROR` | The records store cannot be trusted | Stop new entries. Protect open positions. Repair storage. |
| `RECOVERY_REQUIRED` | A restart or state mismatch needs reconciliation | Run `reconcile`. Confirm zero unknown exposure. |

## 6. What you must never do

- Never turn safe mode off to "get a trade in".
- Never resubmit an order whose state you do not know. Ask the broker first.
- Never increase quantity after losses.
- Never reuse someone else's Kite login. A new operator connects their own account.
- Never delete or edit a trade or a failed session record.

## 7. Where to go next

| You want to | Read |
|---|---|
| Run the normal day | [DAILY_RUNBOOK.md](DAILY_RUNBOOK.md) |
| Live operations, login, safe mode, sizing, service install | [LIVE_RUNBOOK.md](LIVE_RUNBOOK.md) |
| Something is wrong right now | [EMERGENCY_CARD.md](EMERGENCY_CARD.md) |
| Recover from a crash, mismatch or corrupt database | [RECOVERY.md](RECOVERY.md) |
| Find out who holds which login | [ACCESS_CONTINUITY.md](ACCESS_CONTINUITY.md) |
| Rehearse failures | [FAILURE_DRILLS.md](FAILURE_DRILLS.md) |
| Execution path and kill switch | [../../EXECUTION.md](../../EXECUTION.md) |
