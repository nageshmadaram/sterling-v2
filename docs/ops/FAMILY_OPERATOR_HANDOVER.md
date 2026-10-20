# Sterling family and operator handover

Prepared 20 October 2026. The owner's stated intention is to preserve Sterling
for their parents. This document preserves that intention and explains recovery;
it does not transfer broker-account ownership or authorise another person to trade.
Keep this computer, the project, and its private recovery material intact.

## What was verified

- Isolated full restore `2026-10-20T001438.369160Z` passed: 626,107 rows across
  `prospective_main.db` and `sterling_paper.db`, including schema, integrity,
  checksums and captured safety/binding/deployment state.
- Production-grade security validation passed against current saved credentials.
  The persistent environment label now matches systemd's production setting.
- An isolated copy of the current credential database was re-keyed successfully:
  seven encrypted values verified. Live keys were not rotated. Migration now
  includes push credentials, saved bot tokens and nested Telegram target tokens.
- Telegram accepted a test message, HTTP 200, message ID 18323. Delivery to a
  physical phone, notification appearance and recipient reading remain unverified.
- 191 focused backend tests passed, covering credential migration, restoration,
  certification, drills, mode policies, directional execution and manual entries.
  These use test brokers; they are not evidence of a real trade.
- Ten CLI tests passed, including cold-process reconciliation bootstrap and
  refusal when its credential database cannot initialize.
- Fresh real-broker binding verification succeeded at 00:18:27 UTC. A separate
  reconciliation at 00:20:06 UTC reported CLEAN: zero broker and Sterling positions.
- Manual SuperTrend entries now retain the selected mode's immutable deadline
  and timeframe, and refuse cached signals from another mode.

Private verification records are under `data/acceptance_takeover/`. Backup manifests
are under `data/backups/manifests/`; backup payloads are under `backups/snapback/`.
The restore above predates this handover's code fixes; use a newer backup when one
is available. A restored database also needs its matching private encryption key.

## What remains unverified

1. A new interactive Kite login was not performed. Existing session credentials
   successfully passed broker-backed identity/inventory checks. Future login
   requires the account holder or a properly authorised operator.
2. Broker reads were intermittent; refresh inventory before use. Zero positions,
   orders and protective orders were observed, so an open-position protection
   lifecycle was not exercised.
3. The observed public IP differs from the registered static IP. Ignoring its UI
   warning does not establish broker whitelist approval. Confirm the actual
   whitelist before recording a new accepted address.
4. No real entry/fill/initial stop/trailing update/exit was performed on this build.
   Account-approved allocation and risk settings still need an authorised operator.
5. Twelve physical failure drills remain unrecorded. Automated tests do not count
   as physical power, network, broker or device acceptance.
6. Exact-commit remote CI, moving-market acceptance and final release freeze are
   outstanding. A candidate tag or archive is not production acceptance.
7. Nominee details, future operator identity, developer-console access, hosting,
   secret recovery and account ownership have not been independently confirmed.

## Recovery sequence for a trusted operator

1. Read `docs/operations/FINAL_RELEASE_RUNBOOK.md` and
   `docs/operator/FAILURE_DRILLS.md`. Preserve private `.env` and any external key
   files securely; never send them in chat or commit them to Git.
2. Open Sterling's broker connection screen and complete a fresh Kite login.
   Verify the displayed account identity. Run:

   ```bash
   scripts/sterlingctl bindings
   scripts/sterlingctl bind verify zerodha-aa0595-20261010
   scripts/sterlingctl exposure
   scripts/sterlingctl reconcile
   scripts/sterlingctl doctor
   ```

3. Resolve actual inventory/protection discrepancies with the broker. Unknown
   exposure is not zero. Avoid restarting or rotating keys while protection or
   inventory is uncertain. Confirm the operator's capital/risk settings and the
   broker's whitelist rather than removing those checks.
4. During a moving market, run `scripts/sterlingctl acceptance NIFTY 120`.
   This checks feed/reconnect/persistence and never places an order. Separately
   supervise a deliberately approved real trade and retain broker order, fill,
   initial-stop, trailing-stop and exit evidence.
5. Confirm Telegram receipt on the actual intended phone. Run each physical drill
   from the drill runbook and record only observed results for the exact build.
6. Complete the continuity checklist using `scripts/sterlingctl continuity`.
   Parents should recover broker assets through the broker's nominee/legal
   process and use an authorised operator for Sterling.
7. Obtain a clean checkout of the chosen immutable candidate. Run the full CI
   matrix; then follow the release runbook for tag, freeze, post-freeze backup,
   isolated restore and `scripts/sterlingctl certify strict`. Every FAIL or
   UNKNOWN remains an acceptance blocker even if the normal report calls it waived.

For an urgent entry halt, use `scripts/sterlingctl safe on "operator handover"`.
Existing exit/protection handling continues. Do not clear recovery or safe mode
merely to make a dashboard green.
