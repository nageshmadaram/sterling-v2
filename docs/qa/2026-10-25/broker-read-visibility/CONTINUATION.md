# Broker read failures and unknown exposure

Baseline `cb74544f202cd97c76212311020aecadac5bf769`. This follows the manual-ticket/source-preservation release. A successful Kite login removed the earlier 403 result, but direct authenticated positions/orders reads returned HTML HTTP 502 while profile and GTT reads succeeded. Those observations do not establish flat exposure, so runtime promotion remains gated on readable broker exposure.

## Reproduced gaps and exact fixes

| Surface | Prior behavior | Final behavior and source |
|---|---|---|
| Orders | Undefined data during loading/failure rendered “You haven't placed any orders today.” | OrdersPane renders shared BrokerReadStatus, explicit loading or unavailable state, current inventory unknown, and Retry orders. Confirmed-empty messaging requires a successful response. |
| Positions | Missing response defaulted to a fresh empty array each render, feeding an effect that always replaced selection; failures could also show zero open and zero P&L. | PositionsPane uses a stable empty fallback, displays unknown counts/exposure, hides inventory-derived totals/history without a snapshot, and keeps the independent Engine Positions access. |
| Portfolio positions/holdings | Missing broker data became zero counts and “No open positions” / “No equity holdings.” | PortfolioPane shows independent status/retry controls for each source, unknown counts on failed reads, and empty claims only after successful reads. |
| Cached rows after refresh failure | Last successful rows remained with no failure context. | Retain rows with an explicit last-known snapshot warning: quantities, status and P&L may be stale. Successful retry clears the warning. |
| Analyze and CSV Download | Could produce analysis or empty/current-looking exports from missing or failed reads. | Disable those controls while the respective source is absent or failed, including failed refresh of cached data. Ordinary success restores them. Existing exit paths remain unchanged. |

The shared component is `frontend/src/components/kite/BrokerReadStatus.tsx`; callers are `OrdersPane.tsx`, `PositionsPane.tsx` and `PortfolioPane.tsx` in that directory. It uses existing theme colors and accessible status/alert semantics. Raw upstream HTML is not presented as UI text. A retry refetches the existing query and is disabled while fetching.

Backend client `_handle` already raises on non-JSON HTTP failures; the API `_run` maps those to HTTP 502. No backend source change is necessary for this UI defect. The earlier caption allowlist fix still awaits runtime promotion if the old backend is running. Local application health does not prove that the positions/orders broker endpoints are available.

## Evidence

Ten new component cases failed before implementation. A bounded hook-call guard made the missing-data Positions render loop fail promptly rather than hanging the test runner. Six export/loading checks also failed before output gating. The final focused group passes **38 tests across five files**, including existing exit-selection, conversion and T1 holdings behavior.

An HTTP-disabled, CSP network-blocked fixture uses the real pane components and simulated read hooks. Before proof shows false empty-order text on a failed read. After proof shows unknown exposure without a snapshot, stale warnings with retained rows, a successful retry, and independent holdings status. No order/GTT mutation or broker order submit is part of the fixture.

Final gate passed: **11783 passed, 30 skipped in 1119.98s (0:18:39)**, **2,370 frontend tests across 256 files**, TypeScript and `VERIFY OK`. Baseline/head failure sets are empty and the exact skip set is unchanged. Final source hashes and log digests are in `validation.json`. A frontend run before export gating is retained as superseded evidence. The backend source is unchanged from baseline; its full gate also runs TypeScript after the final UI changes.

Private evidence: `.runtime/qa/2026-10-25/broker-read-visibility/` in the primary checkout. Runtime identity, broker availability and promotion are recorded separately after integration, never inferred from a passing unit test.

## Preserved scope and remaining acceptance

All strategy mathematics, original-only EMA/Gamma modes, execution admission, exits/protection, live execution, AUTO, saved sizing/risk/allocation and frozen research remain unchanged. The current temporary Cloudflare origin is retained. Main/dev are synchronized and the temporary implementation branch is removed only after integration and saved test-database verification.

The prior complete market-session, Gamma recovery/speed, Snapback real-row restore/AUTO retention, notification receipt, authoritative research, hosted CI billing and temporary-tunnel stability requirements remain open in the combined ledger and remaining-acceptance checklist. A verified login plus unreadable positions/orders is still unknown exposure. Funds/static-IP failures remain excluded as requested.
