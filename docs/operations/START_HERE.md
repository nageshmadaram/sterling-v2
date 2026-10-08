# Start here

This file assumes you have never written software. Everything below is a command
you type, or something you read on a screen.

Sterling trades the Indian derivatives market with **real money** on your Zerodha
Kite account, using three strategies: Supertrend, Snapback and Gamma Unwind. It is
built to refuse a trade whenever it cannot be sure, and to never block an exit.
For the operator view see [../operator/START_HERE.md](../operator/START_HERE.md);
this page is the command-line side. See also [WHAT_NOT_TO_CHANGE.md](WHAT_NOT_TO_CHANGE.md).

## The one command that matters

```bash
cd ~/Sterling && ./scripts/sterling-status
```

Read it top to bottom. It tells you whether Sterling is healthy, whether it is
allowed to open new positions, and if not, why.

## What the words mean

**Safe mode ON** — Sterling will not open any new position. It will still close
positions it already has, repair their protection, and talk to the broker. Safe
mode is a brake on new risk, never on getting out of existing risk.

**BLOCKED** — Sterling is refusing to do something. This is normal and usually
correct. The status screen prints the reason on the next line.

**Kill switch** — `STERLING_LIVE_EXECUTION=0` in the environment turns every new
entry off until you remove it and restart. Exits and protection are unaffected.

## Starting Sterling

```bash
cd ~/Sterling && ./scripts/sterlingctl start
```

That is the whole command. It runs a fixed sequence and prints each step:

 1. put the system into RECOVERY_REQUIRED — nothing is known to be right yet
 2. the pre-start doctor
 3. start the service
 4. wait for the application to answer
 5. report the release identity (advisory, never blocking)
 6. check the broker session is connected, and is the account we are bound to
 7. reconcile against the broker
 8. check the market subscriptions came back
 9. check ticks are actually arriving, not merely that a socket is open
10. check the records store accepts a write
11. clear RECOVERY_REQUIRED

It stops at the first step that is not a pass, and it treats "could not check" as a
stop, not as a pass. If it stops, the system stays in RECOVERY_REQUIRED and will not
open new positions. Fix the thing it names, then run it again.

## Stopping Sterling

```bash
./scripts/sterlingctl stop
```

The stop refuses while a position is open or an order has not been reconciled,
because shutting down then would leave money in the market with nothing watching it.
If you must stop anyway, add `--force`; expect to reconcile by hand afterwards.

## Logging in to the broker

Kite (Zerodha) logs you out every morning. Sterling cannot log in for you, on
purpose: no password or token is stored anywhere in this system. See
[BROKER_REAUTH.md](BROKER_REAUTH.md).

## The drive

If you record market data, it lives on a USB drive. If the status screen says the
lake is MISSING, plug it in. Data not recorded on a given day cannot be recovered
later. See [BACKUP_AND_RESTORE.md](BACKUP_AND_RESTORE.md).

## If something looks wrong

Turn off new trading first, ask questions second:

```bash
./scripts/sterling-safe-mode on "describe what looked wrong"
```

That is always safe. It never prevents closing a position. Then read
[INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md).

## If you have taken over from someone else

Two things have to be settled, and neither is a code change:

- **Which broker account it trades through.** That is a recorded binding, not a
  shared password. See [BROKER_ACCOUNT_HANDOFF.md](BROKER_ACCOUNT_HANDOFF.md).
- **Who holds access to everything it depends on** — the developer console, the
  static IP, the encrypted secrets, the alert channels. See
  [ACCESS_CONTINUITY.md](ACCESS_CONTINUITY.md), and run:

```bash
./scripts/sterlingctl continuity
```

## What Sterling is not

It is not an income machine, and nobody should plan around money from it. Start
small, keep the day-one caps, and raise size only by decisions made in advance.
