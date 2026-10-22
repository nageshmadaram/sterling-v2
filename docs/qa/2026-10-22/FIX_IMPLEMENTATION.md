# Trader QA remediation — local implementation

> Follow-up merge/sync verification: [23 October report](../2026-10-23/SYNC_AND_FOLLOWUP.md) adds image/publisher identity, partial-fill risk, read recovery, chart route, test database safety and missing-target R fixes.

This report supersedes the original audit's statement that no application fixes were made. The original screenshots and logs remain baseline evidence. The changes below are local source changes; the production backend and published frontend have not been restarted or deployed.

Release disposition: **local automated checks passed; production sign-off still pending**. Funds and outbound-IP mismatch remain excluded from the defect assessment. Passing mocked broker tests does not prove exchange acceptance, live market performance, or the identity of an already-running process.

## Changes by finding

| Finding | Implementation | Disposition |
|---|---|---|
| QA-01 | `runtime_identity.py` captures backend checkout SHA, source fingerprint, declared SHA, dirty state and timestamp once at startup. Authenticated `/api/v1/ops/runtime-identity` compares that frozen evidence with current source and frontend build/API contract. Operations displays mismatches and unknown responses. Frontend embeds build SHA and contract at build time. | Code implemented. Existing running process still requires a controlled release and reconciliation; no false runtime certification. |
| QA-02 | Shared manual/automatic entry gate validates each row's actual holding mode and allowed interval. Freshness starts after candle close, expires after the smaller of one interval or ten minutes, and rejects prior-session, future/forming, invalid-interval and out-of-session bars. | Fixed locally; all ten supported interval/mode combinations covered for spot and derivatives. |
| QA-03 | Removed unrestricted stale-row fallback and silent AMO conversion. An admitted row supplies its own protection plan, preventing selection of a stop from another row for the same symbol. A merged parent timestamp cannot refresh an old leg: admission uses the exact contract’s own signal timestamp. Regular and explicit AMO requests with stale signals submit nothing. Strategy AMO is explicitly unavailable until a separately reviewed next-session policy exists. | Fixed locally by refusing unsupported exposure. MockTransport still verifies AMO serialization and cancellation without network sockets. |
| QA-04 | Escape no longer submits any exit. Existing explicit position exit actions retain server quantity/ownership limits. | Fixed locally; keyboard regression asserts zero requests. An optional bulk-exit feature was not introduced. |
| QA-05 | Missing entry/SL/TSL/target values display as unknown; no 20% synthetic stop, breakeven synthetic trail, or invented 1R. Copy uses “Not supplied” and unknown R, and states plan authority. Stop-distance risk and full premium exposure are distinct. | Fixed locally. |
| QA-06 | Strategy-owned plan inputs and lot buttons are read-only; updates follow the latest supplied server snapshot. Editable generic sizing remains a preview subject to server admission. | Fixed locally; regression verifies immutable inputs and snapshot refresh. |
| QA-07 | Canonical acknowledgements return engine ownership, intent key, protection state and authoritative plan. OrderWindow does not enqueue browser GTT protection for engine-owned orders and hides percentage protection controls on option tickets. Legacy browser option GTT requests are quarantined instead of creating duplicate protection. | Fixed locally; live generic GTT admission remains closed. |
| QA-08 | Distinguishes acknowledgement/fill-pending, AMO acknowledgement, unverified protection, rejection and uncertain outcomes. Uncertain submissions explicitly require reconciliation before retrying. HTTP acknowledgement never claims a fill or a verified stop. | Fixed locally; actual mutation-hook regressions use mocked responses. |
| QA-09 | Snapback no longer promises after-market execution during session pause; manual execution is disabled when the server reports a session blocker. | Fixed locally. |
| QA-10 | Explicit navigation and detail requests restore a minimized main pane and clear focus on another maximized pane. | Fixed locally; desktop and mobile regression coverage. |
| QA-11 | Removed all invented IPO/corporate-action listings. Endpoints return empty arrays with `X-Data-Status: unavailable` and `X-Data-As-Of: unknown`. Dashboard states feed unavailable and disables unsupported calendar tabs. | Fixed locally. Connecting a verified market-calendar provider remains a future feature. |
| QA-12 | Repaired both incomplete `useKiteStatus` mocks. Updated assertions for intentional journal, footer, and closed-session behavior. | Full frontend result recorded below; no skipped failing tests. |
| QA-13 | Configured Simulation that cannot be entered now explains owner authorization in the cycle switch's tooltip, as the button layout already did. | Scope/discoverability clarified. No owner authorization bypass or additional strategy arming mode introduced. |
| QA-14 | Corrected unset live-execution default, launcher paths, and manual/automatic lot-cap help. | Fixed locally. |
| QA-15 | Footer distinguishes live account mode from entry readiness, surfaces stale/unobserved exposure, labels active rows “setups”, labels scheduling “AUTO SCAN”, and spells out three-red-candle exits to avoid confusion with risk multiples. Increased footer status font size. | Targeted readability/status fixes implemented. Final device/theme visual acceptance remains a deployment smoke check. |
| QA-16 | Historical recovery failures include symbol, operation, exception type, bounded detail and session. Gamma never substitutes receipt time for a missing exchange timestamp; missing/out-of-session ticks cannot create a bar or signal. A bounded diagnostic list retains source and receipt times. | Defensive fixes implemented. The observed NHPC recovery failure's original upstream cause cannot be established from the old running process; retain investigation until reproduced under the verified build. |
| QA-17 | Gamma guide now states that T1 (+20%) moves the stop to entry without selling; T2 (+40%) exits the remainder. | Fixed locally; strategy exit implementation unchanged. |
| QA-18 | Added Price Rules to journal strategy registration, labels, statistics and filters. Confirmed entry/partial/exit fills and verified protection project using account/rule/order identity. Cumulative observations update one event; replay cannot duplicate P&L. Manual and broker-stop/target reasons are retained. Backfill v2 reads only validated durable rule facts with a recorded fill timestamp; it makes no broker calls. | Fixed locally; tests cover partial exits, cumulative entry, replay and backfill. |

## Protection and journal invariants

An acknowledgement leaves confirmed quantity at zero until broker evidence arrives. Protection belongs to the engine and is only reported verified after the broker trigger is inspected. Browser ticket percentages cannot replace a strategy's server stop. Missing stops and unsupported session policies refuse new exposure rather than implying protection.

Journal projection reads durable confirmed observations. It neither submits orders nor modifies stops. The stable journal key includes account and rule ID; exit events use broker order IDs with cumulative merge semantics. Entry fill timestamps come from exchange/order evidence. Old closed rules without recorded fill times are skipped by backfill rather than assigned invented timestamps. Backfill reruns are idempotent and remain tenant-scoped.

## Validation

Final command outputs and source hashes are saved under `evidence/fixes/`. `VALIDATION.md` records final counts and exact commands. Baseline audit logs remain unchanged.

Backend tests use isolated temporary state, fake broker clients or HTTP MockTransport. The AMO wire test prohibits socket connections and checks the serializer's POST and DELETE routes, contract, quantity, limit price and product. No real order, broker GTT, account setting, strategy arming, deployment or production restart was performed.

The production frontend build is generated into `/tmp/sterling-qa-fixed-build`, separate from published artifacts. The source patch and source manifest permit review of the exact local change set; they are not an immutable release certificate.

## Remaining release work

1. Review and commit the local changes. Produce immutable backend and frontend artifacts from the same reviewed commit. Capture actual built frontend assets and backend startup identity; a mutable checkout alone is insufficient.
2. Use the existing controlled release procedure with entry origination disabled, durable-state backup and broker reconciliation. Verify holdings, unresolved intents, open exit children and protection before any switch is resumed. Do not solve a mismatch by merely editing an environment label.
3. Verify Operations reports the expected startup SHA/source fingerprint and frontend contract, with no identity blockers. Confirm the real process and published assets actually changed.
4. Perform authenticated browser smoke checks for all four strategy boards, their plan viewers, Operations, Orders, GTT, Journal and CSV. Check narrow desktop/mobile panes and both themes. Reproduce NHPC recovery with the new structured diagnostic.
5. During an open session, observe one valid signal-to-entry-to-confirmed-fill-to-verified-stop lifecycle for each authorized strategy, including partial fill, cancellation, TSL/target and exit reconciliation. Closed-market mocked tests cannot certify live ticks, exchange timing or broker protection activation.
6. Keep strategy AMO unavailable until next-session signal validity, expiry, account binding, stop policy and holding deadline are explicitly specified and tested. Price Rules remains a separate exact-contract strategy; it does not gain five holding modes or a TSL through a documentation change.

The original trading-session runbook still describes the end-to-end acceptance workflow. Its original AMO discussion is superseded by the explicit refusal described above. EMA remains an additional paper/research surface, not a fifth approved live originator.

## Source and artifact map

| Area | Source | Regression evidence |
|---|---|---|
| Signal/session admission | [service.py](/home/nageshmadaram/Sterling/backend/app/services/kite_engine/service.py) | `test_trader_qa_20261022.py`, `test_live_manual_execution.py` |
| Plan binding | [protection.py](/home/nageshmadaram/Sterling/backend/app/services/kite_engine/protection.py) | Engine protection suite and stale-leg regression |
| Startup identity | [runtime_identity.py](/home/nageshmadaram/Sterling/backend/app/core/runtime_identity.py) | Frozen identity/drift regression |
| Price Rules journal | [price_rules.py](/home/nageshmadaram/Sterling/backend/app/services/price_rules.py) | Lifecycle, cumulative fill, replay and backfill regressions |
| Missing plan levels / immutable strategy plan | [AdaptiveEdgePositionCalculator.tsx](/home/nageshmadaram/Sterling/frontend/src/components/kite/AdaptiveEdgePositionCalculator.tsx) | Copy/readonly/snapshot regressions |
| Protection ownership | [OrderWindow.tsx](/home/nageshmadaram/Sterling/frontend/src/components/kite/OrderWindow.tsx), [PendingGttProtectionWatcher.tsx](/home/nageshmadaram/Sterling/frontend/src/components/kite/PendingGttProtectionWatcher.tsx) | Legacy option request quarantine and canonical acknowledgement tests |
| Gamma data discipline | [gamma_move_scanner.py](/home/nageshmadaram/Sterling/backend/app/services/gamma_move_scanner.py) | Missing/out-of-session timestamp regressions |
| Complete patch and hashes | [source.patch](evidence/fixes/source.patch), [source-manifest.json](evidence/fixes/source-manifest.json) | Review against the recorded base commit |
