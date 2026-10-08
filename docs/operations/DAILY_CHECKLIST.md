# Daily checklist

Ten minutes before the market opens (09:15 IST). Do these in order; stop at the
first one that fails and fix it before continuing.

## Before the open

1. **Plug in the drive** if you record market data; the lake lives on it.
2. **Check the status.**
   ```bash
   cd ~/Sterling && ./scripts/sterling-status
   ```
3. **Log in to Kite.** See [BROKER_REAUTH.md](BROKER_REAUTH.md). The token
   expires every morning; this is normal.
4. **Confirm the status screen is clean.** Specifically:
   - Lake: MOUNTED
   - Backend: RUNNING, Ready: READY
   - Safe mode: OFF (if it is ON, read why before turning it off)
   - Live execution: ON (`GET /health` shows `live_execution: true`; `false` means the kill switch is engaged)
   - Unresolved orders: 0
5. **If anything is not clean, do not trade.** An unclear state is the one
   condition under which Sterling should stay out of the market.

## During the session

Watch, do not steer. Do not intervene in a position because it is losing; stops
and exits are part of each strategy's rules. Check the Kite app against Sterling
for the first entry of each strategy on the first live day.

If something alarming happens:

```bash
./scripts/sterling-safe-mode on "what you saw"
```

## After the close

6. **Run the records audit.**
   ```bash
   cd ~/Sterling/backend && .venv/bin/python -m study.snapback_evidence_audit --date $(date +%F)
   ```
   Read it. See [EVIDENCE_INTERPRETATION.md](EVIDENCE_INTERPRETATION.md).
7. **Check that nothing is unresolved.** `Blocked` counts are fine and expected.
   `Inconclusive` trades are worth looking at — they mean Sterling traded but
   cannot fully prove what happened.
8. **Back up.** `./scripts/sterlingctl backup`, then `restore-check`; see
   [BACKUP_AND_RESTORE.md](BACKUP_AND_RESTORE.md). Today's records cannot be
   recreated tomorrow.

## Weekly

- Verify one backup actually restores, rather than assuming it does.
- If no strategy has entered for several sessions, check the refusal reasons: something
  may be blocking every opportunity (a stray broker position, a stale feed, a cap).
