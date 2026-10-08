# Sterling — Daily Runbook

Three short routines: before the market, during it, and after it. Each step says
what to do when the answer is wrong.

New here? Read [START_HERE.md](START_HERE.md) first.

---

## Before the market

| # | Step | Command | If it is wrong |
|---|---|---|---|
| 1 | Start Sterling | `./sterlingctl start` | It stops at the first failed step. Fix that step; do not force the start. |
| 2 | Broker login | as in [LIVE_RUNBOOK.md](LIVE_RUNBOOK.md) §3 | No login means no broker facts. Sterling stays blocked; that is safe. |
| 3 | Health | `./sterlingctl doctor` | Exit 1 = failed. Exit 2 = **could not check**, which is not a pass. Fix broker, safety-state and storage items before trading; release-identity lines are advisory. |
| 4 | Safety switch | `./sterlingctl safe status` | If safe mode is on, read the reason. Only turn it off once that exact cause is fixed. |
| 5 | Kill switch | `GET /health` shows `live_execution: true` | `false` means `STERLING_LIVE_EXECUTION` is off: no new entry will go out. Intended? If not, fix the environment file and restart. |
| 6 | Broker reality | open the Kite app; compare positions and orders | Anything Sterling does not know about blocks new entries. Run `./sterlingctl reconcile`, then close or hand over the stray exposure. |
| 7 | Yesterday's backup | `./sterlingctl restore-check` | A failure means the records are not durable. Block new entries and repair storage. |

Doctor must be clean of broker, safety and storage failures **and** the broker must
hold nothing unexpected before new activity is allowed.

## During the market

Watch the system state, not the running P&L.

- A losing open trade is not a reason to intervene. Exits and stops are part of
  each strategy's rules; overriding them is how small losses become large ones.
- `BROKER_ERROR`, `DATA_ERROR` or `EVIDENCE_ERROR`: keep new risk blocked and
  follow the action shown on screen. Do not diagnose by guessing.
- If a real broker position disagrees with Sterling, **the broker is right**.
  Reconcile before anything else happens.
- If you do not know what state an order is in, do not resubmit it. Ask the broker.

To stop new trades immediately, at any moment:

```bash
./sterlingctl safe on "reason in your own words"
```

This never blocks exits, protection repair, or reconciliation. A blocked exit
would be more dangerous than the problem that triggered safe mode.

## After the market

| # | Step | Command |
|---|---|---|
| 1 | Confirm intraday positions are flat | `./sterlingctl status` |
| 2 | Review unresolved positions and record gaps | `./sterlingctl report` |
| 3 | Keep today's report | `./sterlingctl report > reports/$(date +%F).txt` |
| 4 | Back up | `./sterlingctl backup` |
| 5 | Prove the backup restores | `./sterlingctl restore-check` |

Then stop. Do not tidy the records: do not delete a losing trade, and do not
re-run a scanner to clear a failed session. A gap is permanent by design.

## When an alert arrives

Every alert states four things. If one is missing, that is a defect.

1. **What happened**, in plain language.
2. **Whether new trading is blocked.**
3. **Which positions are affected** (or "none identified", which is not the same
   as "none").
4. **The next safe action.**

| Severity | Means | You |
|---|---|---|
| `INFO` | Routine: entry or exit placed, backup done, no signals | Read it in the digest. |
| `WARNING` | Needs attention today: broker login pending, repeated no-fill, wide spread | Look at it during the day. |
| `CRITICAL` | Broker mismatch, unprotected fill, unresolved exposure, storage failure, daily-loss breach | Act now. New exposure is already blocked. |

**Every CRITICAL blocks new exposure automatically.** You do not need to react
fast to make Sterling safe; you need to react correctly. One root cause arrives as
one incident; fix the cause and the downstream alerts go with it.
