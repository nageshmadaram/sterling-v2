# SuperTrend board refresh continuity

Baseline: `416bf6b4c9c5f98e1efbd3d9d683be5834fcedd0` (PR 271). This change concerns visibility and new-entry authority during incremental selected-mode scans. Strategy indicators, broker limits, position protection, exit rules and saved controls are unchanged.

## Problem and behavior

Previously, the first symbol flushed by a scan replaced ordinary rows for all other planned symbols. Those rows disappeared until their own history reads finished. A completed scan with no signal could also leave an old row in the merged database cache, allowing a later poll to restore it.

The scanner now starts with its prior board. A group is identified by source, underlying, selected mode and candle interval. Pending or failed groups remain visible as last-known information. A successful empty group retires its prior plans. An authoritative full projection is committed strictly, so a failed write cannot be reported as saved. Polling during a scan reads that projection and cannot rehydrate an earlier executable cache entry.

Refresh state is additive metadata on both rows and option legs: `pending`, `failed`, `replaced`, or null for a verified current observation. Mixed derivative/confluence groups retain unknown legs independently of verified siblings. Grouping separates modes and intervals. Insufficient contract history is unknown, rather than proof of no setup. A carried historical confluence plan is last known until reconfirmed.

The board shows **Refreshing**, **Refresh failed**, or **Last known**, with a reason for the new-entry block. Its entry/stop/trail values remain visible. This status does not tell the user to change strategy settings.

## Entry and management safety

Manual selection projects the chosen leg's refresh status onto its exact candidate. Verified scanner callbacks register contract-local evidence before automatic admission. The canonical SafetySupervisor checks the current scan authority again immediately before broker send, including after REST limiter/pool waits. The contract, source, mode, interval and signal bar must still match; an ended or superseded observation revokes the earlier decision. Tombstones survive repeated empty scans, so removing a row cannot free a queued old approval. Legacy simulated manual requests cannot omit evidence to evade a known refresh block; their mode/interval is frozen in the payload.

Explicit close, reduction, protection and reconciliation actions bypass entry admission as before. Red-count maps are still updated independently of signal rows. No production order, GTT, synthetic signal or notification is generated for these checks.

Cancellation remains in progress until its workers and durable publication drain. Unexpected sibling errors are gathered before finalization, preventing a late worker from publishing after the scan reports finished.

## Validation and limits

The focused tests reproduce the baseline display loss, grouped-mode collision, missing canonical boundary check and missing UI block. They cover pending/failure/empty/cancellation, strict cache failure, partial derivative/confluence groups, short history, same-bar ended contracts, older observations, legacy simulated requests and explicit management bypass. The queued-order tests exercise both an admission-to-submit race and the transport's post-await callback with a recording fake broker.

Full backend/TypeScript and frontend suites are recorded in `validation.json` after completion. Source hashes are frozen before the gate. Raw red/green logs live under `.runtime/qa/2026-10-26/board-refresh-continuity` in the primary checkout. Runtime integration and deployment evidence are separate from source validation.

This after-close work does not complete live-session acceptance. Two consecutive continuously observed trading days, qualifying midday Gamma recovery under three minutes, genuine Snapback restoration/refresh/AUTO retention and remaining account/device observations still need actual evidence. The known 26 October coverage gap and over-budget recovery remain failed/pending observations. Hosted CI billing and unavailable research datasets are external blockers.

A separate active chat is editing acceptance/release infrastructure in the primary checkout. That work is preserved. This isolated change must be integrated and tested with its final code before a shared rollout; a dirty primary checkout is never reset or overwritten for deployment.
