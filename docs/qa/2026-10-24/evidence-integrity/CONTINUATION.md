# QA evidence integrity continuation

Baseline: `1df5351b728661200798fd0ec62b4bed529de4ab` (PR 252). Continues every original walkthrough finding and implementation-spec item 7–21 through the existing [combined ledger](../../2026-10-23/open-gaps/IMPLEMENTATION_LEDGER.md) and [walkthrough reconciliation](../../2026-10-23/PASTED_REPORT_RECONCILIATION.md).

## Fixed evidence gaps

| Finding | Exact fix | Verification |
|---|---|---|
| Full-day healthy probes plus an empty/truncated backend log could accept the market-load criterion | `scripts/trading_session_audit.py` independently records earliest/latest valid deployment-IST log timestamps and requires coverage from 09:15 through 15:30 alongside continuous healthy same-release probes. Sample coverage and log coverage remain separate fields. | CLI regressions exercise empty log, start at 10:00, and complete session bounds with identical healthy probes. Only complete bounds can pass this individual criterion. |
| An unreadable stall could disappear from the count | Unparseable timestamp/duration/format stalls are counted explicitly and refuse the observed threshold. Malformed numeric durations cannot crash the report or imply zero stalls. | Undated, malformed numeric, NaN, infinite and negative-duration cases refuse acceptance. |
| Another owner's successful EMA scan could resolve the intended owner's failures | `scripts/ema_scan_audit.py --owner` filters the persisted owner in SQLite, defaulting to `default`; embedded JSON cannot override it. Comparison also requires matching owners. Database access remains `mode=ro` and `query_only`. | Cross-owner comparison and actual two-owner SQLite tests preserve the database byte hash and leave unresolved pairs pending. |
| A newer failed EMA scan could be hidden behind an earlier completed pass | Compare the latest completed/failed terminal job after the historical failure for the same owner. A later failure leaves all historical pairs unresolved and exposes the terminal status/job. Running/cancelled jobs do not certify recovery. | A historical failure → completed pass → newer failure sequence remains pending. Existing completed-with-pair-errors regression remains pending. |

Focused tests: 16 passed, including all existing session-summary checks. The initial TDD run reproduced five failures; a second red case reproduced malformed-duration parsing. The complete `make verify` passed: **11,737 backend tests, 30 unchanged existing skips, TypeScript and VERIFY OK** in 14m33s for pytest. Baseline and final failure sets are both empty. The unchanged frontend retains its prior 2,308-test/248-file pass. Exact source/log hashes and skip comparison are in [validation.json](validation.json).

## Actual read-only evidence

The account-scoped rerun still resolves all 38 exact historical symbol/mode failures against a completed 60-result scan with zero errors; [ema-recovery.json](ema-recovery.json) contains the nonsecret pair comparison. This is scan coverage, not execution or edge certification. No provider request or broker mutation occurs in either audit.

The 23 October historical log spans the session, but continuous release-specific probes do not: acceptance remains unverified. On 24 October the market day has not completed and the regular NFO calendar is closed (Saturday); it cannot inherit Friday's evidence. Neither report claims full-session acceptance. Timestamp bounds and availability probes do not establish strategy-specific recovery, fills, protection acknowledgement or notification delivery.

## Operator decisions and remaining observations

The operator explicitly chose the existing temporary Cloudflare tunnel. A named tunnel or permanent hostname is not a required follow-up. Intermittent public probes must remain visible as degraded/reconnecting; ngrok's quota error remains external. No provider reconfiguration or URL rotation is part of this change.

Enabled global live execution, strategy/AUTO, sizing, lot ceilings and frozen research controls are preserved. Future trading-session observations remain pending: two consecutive days with nonzero Gamma watches after midday recovery, scheduled/recovery timing, real Snapback rows surviving restart and AUTO toggles, market-load thresholds and actual missing-session notification receipt. Authoritative expired-contract/OI/tick/fill data, independent holdout lineage and matched realised exposure remain unavailable. None is manufactured or replaced by a source/test pass.
