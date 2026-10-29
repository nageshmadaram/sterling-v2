# Sterling V2 — Phase 8 Offline Operator Observability Validation

**Date:** 2026-10-29  
**Status:** targeted Phase 8 frontend + backend + inherited PostgreSQL validation **PASS**.  
**PRODUCTION AND LIVE TRADING:** **BLOCKED / NOT CERTIFIED**.  
**Draft PR:** https://github.com/nageshmadaram/sterling-v2/pull/10  
**Validated code SHA:** `50d4acd04174ee619c143b09d64a1dcedd55ee63`  
**Evidence:** https://github.com/nageshmadaram/sterling-v2/actions/runs/40809268783

## Real CI evidence

| Test or gate | Result |
|---|---|
| React UI: 11 focused dashboard, risk, synthetic KPI, comparison, queue, schedule, decision and alert smoke tests | **11 passed** |
| React UI: empty and missing data, degraded/stale and untrusted audit fail-closed behavior | PASS |
| React UI: no authorization by mismatched strategy or malformed evidence | PASS |
| Frontend TypeScript `tsc --noEmit` | PASS |
| Frontend `npm run build` full production compilation in CI | PASS (artifact not deployed) |
| Static frontend audit: API GET only, no broker order or execution mutation | PASS |
| Backend signed-JWT owner-scoped GET-only strategy catalogue and verified offline gate status tests | PASS |
| Backend Phase 1–7 registry/broker/market-data/scheduler/journal/analytics focused regressions | **122 passed, 9 warnings** |
| Broker source access audit | **0 new unreviewed** (272 inherited classified patterns) |
| Prior Phase 5–7 GET-only/no-broker execution static guards | PASS |
| Disposable PostgreSQL16 synthetic analytical ledger migration, immutable triggers, rollback and source decision links | PASS |
| PostgreSQL16 isolated dump/restore and row-count check | PASS |
| Disposable downgrade/re-upgrade of inherited analytical schema | PASS |
| Full app regression, real phone, deployed operator UI, broker order/fill proofs | **NOT CERTIFIED** |

Warnings: seven inherited audit script invalid escape-sequence SyntaxWarnings
and two third-party TestClient deprecations; these were not treated as test
failure or as release approval.

### Iterations and exact evidence

- First code draft [run 40808929588](https://github.com/nageshmadaram/sterling-v2/actions/runs/40808929588):
  **11 frontend tests, 122 backend tests and PostgreSQL proof PASS.**
- Expanded the Phase 5 schedule presentation to show audited schedule
  configuration, pause/cancel and materialized occurrence counts without
  adding any scheduler worker or UI control. It is projected from the
  signed, hash-verified Phase 5 queue ledger using two new GET-only
  metadata endpoints.
- Revised [run 40809268783](https://github.com/nageshmadaram/sterling-v2/actions/runs/40809268783):
  **all three jobs SUCCESS** on exact code SHA `50d4acd`,
  11 frontend smoke PASS, 122 backend targeted PASS, nine warnings,
  zero newly unreviewed broker access methods, isolated PostgreSQL
  immutable/restore proof PASS, frontend production compilation PASS.

## Delivered, operator-visible and non-authoritative

The new `Kite → More → Offline Evidence` tab lazy-loads
`OfflineEvidencePanel` with styling borrowed from Sterling Kite tokens,
responsive density, focus-visible controls and mobile-scrollable More tabs.
It shows:

- Versioned strategy selector and registry state, owner scoped by signed JWT
- Explicit **hypothetical** net P&L, win rate, PF, expectancy, drawdown,
  Sharpe/Sortino and equity/rolling trade metrics
- Immutable version and symbol breakdowns, with no registry mutation
- Exact independent Phase 4 **offline** global/strategy gate states,
  approval policy, and explicitly unavailable strategy-health score
- Audited Phase 5 scheduling (configured/paused/cancelled, type,
  bounded occurrences and materialized items) and Phase 6 reconciliation
- Phase 6 decision reasons, quality metadata, timestamps, approval and
  risk IDs, expandable SHA256 audit references
- Informational KPI, stale queue, degraded-quality, incomplete audit
  and emergency-stop warnings, visually prioritized critical/warning/info

The frontend fetches only signed read-only
`/api/v1/offline-validation/*` endpoints. The two newly added backend
endpoints `GET /strategies` and `GET /strategies/{id}/status` expose
no mutation. All panel interactions (strategy selection, refresh,
tabs, audit-details disclosure) are local UI or GET-only. The UI
rejects any response claiming `execution_authorized=true`, unknown
provenance, wrong strategy or incompatible source. Missing, stale
(>90 seconds), invalid or untrusted sources hide KPI numbers rather than
displaying stale/misleading confidence.

No existing live Operations control, order router, broker write, or
execution authority was modified. The phase uses a draft frontend
production build solely for compile validation; it did not deploy it.

## Remaining hard blockers

1. **Real profitability is unknown.** Phase 7 metrics are based on
   explicitly synthetic operator-supplied hypothetical entry/exit
   prices and costs. The Phase 6 ledger contains decisions, not
   executed broker fills. None of these metrics proves trading edge.
2. **Legacy live authority remains outside this UI safety slice.**
   Phase 4/5 offline risk evidence and Phase 6/7 journals are not
   connected to the existing real broker order router. Their successful
   validation cannot protect or authorize real account orders.
3. Phase 4 exposure, market-data and health evidence can be synthetic.
   A strategy-health **score is unavailable**, not independently
   certified. The Phase 8 offline status is not a true broker status.
4. Pending alerts are not delivered externally and lack Telegram/push
   device acknowledgment. KPI warnings are display-only.
5. Full visual page-by-page review, actual mobile browser/touch audit,
   screen reader testing, focus overlays, large data/performance
   benchmarks and operator device acceptance are not performed.
6. Production deployment, actual JWT/device session proof, hosted
   tunnel endpoint validation, operator signoff, full repository CI
   and production database backup/restore are outstanding.
7. 272 legacy direct broker access patterns remain classified, including
   30 legacy broker-write and 4 execution authority findings; no new
   patterns does not mean no existing risk. PostgreSQL privileged
   INSERT/DDL and archival controls still need hardening.

**Keep PRs #1–#10 open as drafts, `main` unchanged, live trading OFF
for this validation slice. No merge, deployment, broker write path,
execution permission, or Phase 9 work.**
