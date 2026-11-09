# Canonical live-reduction broker-book proof: exact quantity and account

**Review base:** integrated draft PR #41 at `2f540897e7e3fdedec4c3d84db7b6e6e5f8692c3`. Red-first regression was reproduced against the preceding PR #40 execution service, which PR #41 inherited unchanged.
**Release state:** review only, live broker execution NO-GO, 0/12 independent controls accepted.

## Reproduced fail-open synthetic evidence

The existing `CanonicalExecutionService.verify_broker_reduction` read the broker's `net` book immediately before a reducing order. Six negative tests returned `allowed=True` under the previous implementation:

- A reported fractional holding (`quantity=10.9`) was truncated to 10.
- A true zero `quantity=0` silently fell back to a contradictory `net_quantity=10`.
- Competing quantity fields (`quantity=10`, `net_quantity=20`) were not cross-checked.
- Two duplicate target bucket rows (`6` + `6`) were summed into fictional 12-unit exposure.
- `tradingsymbol` could match the order while an alternate `symbol` field named an unrelated contract.
- A client reporting the correct broker book for a different `_account_id` could directly satisfy the verifier.

A Boolean quantity was also covered as invalid evidence; it must never authorize a one-unit reduction. The method is reachable from both pre-reservation and final-before-send checks, so these parsing errors matter even when the strategy UI appears safe.

## Change

The reviewer adds a bounded exact Decimal-to-integer parser for signed broker net quantities (finite, integral, not Boolean), preserves explicit zero rather than using a truthy fallback, compares both quantity fields when supplied, refuses duplicate target buckets and mismatched symbol aliases, and binds the fresh read to the exact request account. The rest of the canonical journal and order pipeline are unchanged. No new broker route, execution mode or bypass is added.

## Validation and outstanding risks

- The negative tests failed before this patch and pass after; positive proofs cover an exact long SELL, short-cover BUY and a nonmatching other-symbol row.
- GitHub's offline Gamma/canonical workflow now runs the new tests whenever the service or test changes.
- Conservatively refusing duplicate/malformed net rows may require operational validation against the actual Kite raw-position schema. A failing proof blocks a SELL and should be reported and reconciled, **not** worked around by a raw broker call.
- Fresh position data alone cannot serialize independent in-flight orders or live GTT state. The distinct canonical partial-exit tranche and atomic broker/protection handoff remain P0 unimplemented. UNKNOWN sends must never be blindly retried.

No source-only test, broker payload mock or draft PR authorizes live order routing. Parent PR #15 full regression and independent 12-control acceptance remain blocked. No production merge or deployment.

**Decision: DRAFT / NO-GO / LIVE BROKER EXECUTION OFF.**
