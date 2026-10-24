# Original EMA mode and signal explanation continuation

**Final automated gate:** 11,759 backend tests passed with 30 unchanged skips; 2,325 frontend tests passed across 250 files; TypeScript and `make verify` passed. Baseline and final failure sets are empty; final tested source hashes match. See [validation.json](validation.json). Deployment identity and post-restart checks are recorded separately in the operator deployment report and private maintenance artifacts.

The operator confirmed https://x.com/apoorvsinghasr/status/2110394084598960273 as the EMA source on 24 October 2026. The web fetch returned 403; the public post was successfully read in the in-app browser. It specifies Daily EMA execution with Weekly SuperTrend context. No hourly reinterpretation or extra EMA horizon modes are adopted. Gamma source rules and expiry-based holds are unchanged.

## Changes

- Gamma settings now show only its original expiry-based holding explanation, without the five-horizon availability strip. Gamma signal, exit and configuration logic are unchanged.
- Original EMA Daily/Weekly is the sole operational choice in settings, board scans/quick-start and new backtests. Connection/rescan descriptions no longer advertise four profiles.
- New configuration/scan/plan/arm/readiness/replay requests refuse retired EMA profiles. Unified backtest rejects them before provider access. Empty or duplicate scan selections cannot fall through to a legacy all-mode config.
- The automatic runner scans/originates only the original mode; it refuses retired-mode add-backs and cancels reserved, unsent retired BUY actions. Submitted/unknown orders continue reconciliation. Held legacy campaigns retain management/reductions/exits using their own historical profile.
- Read-only history, charts, saved replay results and exports remain. Operational config responses expose retired stored choices explicitly without rewriting data on read; a normal API config save persists the original selection with CAS, preserving non-mode settings.
- Engine/profile maths remain available for historical interpretation; original indicators, entry/exit calculations and source rule hash are unchanged. This is an operational mode restriction, not deletion of history or wholesale equivalence to every discretionary reading of the post.
- Short-mode Snapback explanations no longer claim optional EMA9/ADX checks when disabled; enabled ADX reports its actual cutoff. Signal math and controls are unchanged.

The [strategy guide](../../../strategy/STRATEGY_MODE_GUIDE.md) explains original SuperTrend HA/three-line rules, newer SuperTrend/Snapback horizons, Gamma's original E2/E1 and live swing capability boundary, and original EMA staged management.

## Verification and operational boundary

Three Snapback explanation cases failed before their fix, then all 33 focused intraday tests passed. Four original-mode API cases reproduced the added-profile behavior; the UI regressions reproduced the profile selectors. A reserved legacy BUY case demonstrated a submission attempt rather than cancellation before its runner fix. Existing unit/recorded-profile tests remain for historical rule interpretation; the new API/runner tests verify that operational callers cannot originate them.

The initial Snapback-only full run was intentionally interrupted after the user clarified the EMA source and expanded this patch; it is not a release pass. A preliminary original-only run was also interrupted to align the signal eligibility projection with the retired-mode policy. Neither is a release pass. The first full frontend run had three expected text-fixture failures after replacing the four-profile rescan description; that fixture was updated while preserving its assertions, and a complete final frontend rerun was started. Final source and result hashes, complete failure-set/skip comparison and deployment status will be recorded after the final gates complete.

No Gamma configuration, SuperTrend/Snapback live or AUTO control, risk limit, lot ceiling, frozen experiment or broker order/GTT is changed. The operator-authorized EMA active mode selection is the sole planned saved-setting adjustment, through its normal CAS config service after backup. Existing EMA power/AUTO/allocation/risk values are preserved.

Future observed-session, recovery, notification and authoritative research-data requirements remain pending exactly as in the previous ledgers. This continuation does not certify those requirements or solve hosted CI billing.

## Verification correction and disk headroom

The complete backend run passed **11,759 tests with 30 existing skips**, but its final TypeScript step rejected a narrowed array's `.includes` argument. An explicit `EmaTrendMode[]` declaration corrected the container type without changing emitted runtime logic; standalone TypeScript then passed. The complete required gate and frontend suite were started again on the corrected final source. The previous combined gate remains recorded as failed, not VERIFY OK.

After the long pause, disk free space was below the configured 2 GiB preflight threshold. Disposable finished test fixtures were cleaned; eight older, inactive backup directories were converted to permission-restricted gzip/tar archives. Every regular file was SHA-256 checked against its archived bytes and rechecked against the unchanged source before removing the redundant uncompressed copy. All 349 files are preserved, with archive checksums and exact restore commands in the private maintenance manifests. The latest uncompressed snapshots were retained. Observed free space recovered to approximately **3.2 GiB**. Production databases and frozen research records were not altered by archival.
