# Contract exits and PCR provenance continuation

Baseline: `35ad7a15a14833fa6741f9f915e1089c1b9a8ce4` (PR 253). Continues the [combined implementation ledger](../../2026-10-23/open-gaps/IMPLEMENTATION_LEDGER.md), [original walkthrough reconciliation](../../2026-10-23/PASTED_REPORT_RECONCILIATION.md), and [QA evidence fixes](../evidence-integrity/CONTINUATION.md). Their pending observations remain pending.

## Reproduced and corrected

| Finding | Exact fix / acceptance |
|---|---|
| A closed contract's disabled Buy tooltip quoted another leg's trailing stop | `useBoardRowActions` uses the closed contract's `exitProgress`. A known contract-local trailing exit at 185 says `Stop hit 185.00`; it cannot inherit another contract's 100 threshold. A legacy derivative without its own exit reason says `Closed`. The control remains disabled. |
| Exit marks, flags and expanded details repeated the underlying's stop as the leg's | `supertrendAdapter` uses `closedReasonText` with the selected leg for marks, flags and exit details. `SignalBoard` uses the contract's exit progress in the expanded closed-leg paragraph. The raw diagnostic is preserved and explicitly labelled `Group exit reason` for derivatives or `Underlying exit reason` for shared spot/confluence rows, explaining that its threshold may belong to another contract. |
| Missing contract trail silently borrowed the underlying diagnostic's number | `closedReasonText` no longer parses a different contract's numeric threshold as a substitute. Unknown stop reads `Stop hit` without a made-up level. The raw engine/group reason remains separately labelled for diagnosis; it does not certify that another contract hit its stop. |
| Historical/closed PCR provenance required opening a popup | `PcrPane` shows a compact persistent status stamp above the heading: historical/not-current with exact observation timestamp, market closed, waiting, unavailable or observation timestamp. The detailed information dialog remains. Source live on an older date is identified as historical. |
| Historical PCR mixed dates with live spot/change, changing its interpretation | Recorded PCR keeps its recorded spot and price change. Live quotes are used only for a live-source, same-session, non-closed feed. Separate regressions prove historical 22,600 stays 22,600 despite a 28,000 live quote, and an active current feed still uses 28,000. |

## Contract-local state boundary

Deeper review reproduced a more consequential root cause: a grouped derivatives parent kept only the first contract's exit reason. The UI could therefore label another active contract closed, assign it the wrong exit rule, show the first contract's running counter, or call an older contract fresh because a different leg just fired.

`OptionLeg.exit_reason` is now optional in backend/API/frontend models and is stamped by the contract evaluator before grouping. Grouping preserves it across repeated compilation. Only an actual ungrouped premium row can supply missing token, timestamps, alignment, counters or exit evidence; an already-compiled/legacy summary cannot populate unrelated contracts. Unknown legacy timestamps cannot shadow a new dated scan of that contract. Null red counts stay unknown, preserving the existing protection monitor's last-known-count semantics.

The adapter uses a derivative leg's own reason, counter and alignment. Derivative group summaries say `Per contract`, omit the first leg's exit badge/counter/alignment and use a real recorded underlying reference or unknown, never the group's zero/premium as spot. Active legs remain running despite another leg's exit. Older running legs do not inherit a newer leg's fresh classification. Legacy unknown contract dates display no borrowed group date and explicitly explain that a fresh scan is needed; manual entry is refused until authoritative evidence exists; global live/AUTO controls remain unchanged. Legacy closed legs with no contract-local reason display `Closed`; a current quote is not used to fabricate a historical exit. Shared spot/confluence exits remain shared by design.

Five initial regressions reproduced the provenance/contract-hint defects; another reproduced mixed historical/live spot values. The focused board group passed 89 checks. The final new UI checks cover the corrected fixtures, including current-feed quote retention and explicit feed failure. Final focused checks pass: 245 scanner/protection tests and 14 new UI tests; TypeScript is clean. Final complete verification: **11,742 backend tests passed, 30 unchanged existing skips, TypeScript and VERIFY OK**; **2,322 frontend tests passed across 250 files**. Backend baseline and final failure sets are empty. The frontend-only legacy date/group refinements were completed during the unchanged final backend run; the entire final frontend suite was rerun, and make verify type-checked the final source after backend completion. Exact hashes and skip comparison: [validation.json](validation.json).

The first full frontend run passed 2,316 tests across 250 files. Its companion backend run passed 11,737 with 30 existing skips, then TypeScript rejected incomplete test-fixture types. Intermediate verification was also restarted when deeper contract-local-state and running-counter/freshness defects were found; interrupted runs are retained and never counted as release passes. The fixture correction uses a typed quote holder and the existing PCR normalizer rather than asserting an incomplete query-result/series shape. No application code or test assertion changed for this correction. Initial logs are retained as an incomplete combined gate; they are not represented as VERIFY OK.

## Scope across strategy modes

- SuperTrend spot/confluence retain their shared signal exit rule; derivatives use contract-local diagnostics, counters, alignment, dates and freshness. Navigator-originated row semantics and explicit targets remain unchanged.
- Gamma and Snapback retain their strategy-owned entry/review/arm/exit paths; this continuation adds no generic order path or AMO promise.
- EMA/Adaptive practice-only presentation and refusal of generic live orders remain intact. The original mode/lifecycle coverage and all findings are retained through the linked ledgers.

The manual lifecycle remains board/source/mode/as-of → exact contract/plan review → canonical server admission → owned position/protection → signal/SL/TSL/time exit → journal/notification. These changes correct evidence on that lifecycle; they do not substitute for fills or a full observed trading session.

## Visual and operational boundaries

The real PCR component, with isolated offline feed/quote adapters, was rendered in light and dark 390px containers. The status stamp fits without horizontal overflow; measured text contrast is 9.74:1 and 15.42:1. Exact measurements: [visual-check.json](visual-check.json). This proves the stamp's wrapping/contrast, not the entire app's mobile/session acceptance. Browser screenshots work; semantic/native input commands timed out, so popup interaction is not claimed.

No order, GTT, cancellation, strategy maths, configured risk limit, live/AUTO setting or lot ceiling is changed. Contract-local evidence feeding the existing monitor no longer inherits an unrelated summary value. Source prices/SL/TSL/targets are preserved. Historical exit labels do not claim broker-confirmed fills. The existing temporary Cloudflare endpoint remains the operator's chosen approach; no named hostname is required.

Future complete trading-session measurements, Gamma watches after midday recovery on two consecutive days, real Snapback row persistence/AUTO retention, notification delivery and authoritative research-data/holdout/exposure requirements remain open. Broker login after the daily expiry is an operator action; missing authentication is reported as unknown exposure rather than flatness.
