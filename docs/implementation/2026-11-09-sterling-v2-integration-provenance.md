# Sterling V2 stacked review: reconciliation provenance and release barriers

**Repository:** `nageshmadaram/sterling-v2`. **Review only; NO-GO for live broker execution.**

This is an engineering handover, not an approval register. Repository and PR
metadata were inspected on 2026-11-09. All SHA prefixes below identify **PR
head commits, not deployed binaries**. A green job tests only the code and
conditions of its own run; it does not certify the other branches or the host.

## Legacy offline-validation stack (not automatically imported)

All fourteen were **open, draft, unmerged** at inspection. The base refs form
a stacked chain for #1–#13, with #14 separately based on `main`.

| PR | Exact reviewed head prefix | Primary implementation | Exact-head PR CI evidence |
| --- | --- | --- | --- |
| #1 | `d739034c` | Offline strategy registry foundation | CI, Regression Gate and Snapback release gate: FAILURE |
| #2 | `061172af` | Versioned registry and atomic audit | No PR-triggered workflow runs returned |
| #3 | `89eda42a` | Normalized broker read facade | No PR-triggered workflow runs returned |
| #4 | `42b2e867` | Broker provider boundary and legacy-access audit | No PR-triggered workflow runs returned |
| #5 | `150ac518` | Historical market-data reliability | No PR-triggered workflow runs returned |
| #6 | `9a430744` | Offline execution validation and risk audit | No PR-triggered workflow runs returned |
| #7 | `b45f4ba0` | Offline scheduler and backlog | No PR-triggered workflow runs returned |
| #8 | `414b71c1` | Append-only offline decision journal and reconciliation | No PR-triggered workflow runs returned |
| #9 | `e75a1eda` | Hypothetical offline performance ledger | No PR-triggered workflow runs returned |
| #10 | `3dadc55c` | Offline operator evidence UI | No PR-triggered workflow runs returned |
| #11 | `51888f94` | Offline alert inbox and retry audit | No PR-triggered workflow runs returned |
| #12 | `0a7c4e3c` | Isolated offline security / deployment preflight | No PR-triggered workflow runs returned |
| #13 | `ce702ab1` | Integrated offline production NO-GO evidence | No PR-triggered workflow runs returned |
| #14 | `e35eb40b` | Independent production evidence intake (12 blocked) | CI, Regression Gate and Snapback release gate: FAILURE |

**Disposition:** retain these as separate drafts and source/evidence references.
The absence of returned runs is **unverified**, not a passing result. Do not
cherry-pick registry migrations, offline order journals, broker providers,
schedulers or persisted offline execution from this stack without the complete
dependency graph, isolation review, and exact-head integration test evidence.
These components are not evidence of live-execution authorization.

## Current product-integrity and OpenAlgo review stack

| PR | Reviewed head prefix | Relationship / disposition | Evidence and remaining concern |
| --- | --- | --- | --- |
| #15 | `725f7ebb` | Parent controlled-execution and product-integrity draft | Exact-head CI FAILURE; Regression Gate CANCELLED; other focused runs succeeded; parent acceptance incomplete |
| #18–#20 | individual merged PRs | Surface registry, scoped feed read, tools | Changes already present in parent history; avoid duplication |
| #21 | `49a58f01` | Draft normalized broker/strategy read | Import only passive broker summary; keep parent canonical strategy registry contract |
| #22 | `034b5c09` | Draft Python/Flow validators and chart/scalp metadata | Do not copy incompatible schema onto #25 id-only graph validator; no Python/Flow execution |
| #23–#28 | individual merged PRs | API mapping, canonical lane read, offline authoring, Tools, view contracts, binding checks | Reuse parent implementation; no second feed or order path |
| #29 | `cdf10851` | Draft default-deny governance and exact-SHA evidence | OpenAlgo and inventory PASS, but finish workspace E2E FAILURE on outdated admin fixture |
| #30 | `92e9f05f` | Draft Gamma modal/selectors fix | Its focused correction is reconciled into #31 without duplicating controls |
| #31 | `bca2fd26` | Draft integration based on #29 | Exact-head OpenAlgo contracts PASS and source inventory PASS; no independent production approval |

### Continuation implementation (not automatically accepted)

The current continuation branch adds strict input-shape rejection to
`platform_broker_read.normalize_broker_read`: rejecting non-Boolean flags,
non-string view, non-integer/negative counters and tick values, and stale
subscription/tick metadata when the feed is not bound. The broker route still
refuses inconsistent evidence with HTTP 503; execution authorization stays
false. The local negative regression suite tests these refusals.

The updated `frontend/e2e/workspace-flow.spec.ts` replaces obsolete admin
combobox selectors with the actual directory / Access / Brokers / Telegram
views, mocks the canonical `GET /api/v1/admin/directory` and broker login
metadata, and verifies a reason-coded workspace grant and synthetic broker
reassignment. It does not contact Kite or deliver Telegram messages. Re-run
both affected CI workflows against the **new exact head** after publication.

## External review blockers (unchanged)

- `docs/implementation/CONTROL_MATRIX.md` remains authoritative for the
  proposed H01–G12 mapping: **0/12 accepted**, **12/12 blocked**, owners
  unassigned, independent approvals not provided.
- Restore, safe-mode, tenant/revocation, UNKNOWN fill, broker/protection,
  alert-delivery, host secrets and rollback evidence require independent
  witnessed tests, not source-only inferences.
- Exact-head CI for #15 and full parent Regression Gate must be resolved.
  The #15 CI job at reviewed SHA `725f7ebb` recorded **100 failed, 12920
  passed, 30 skipped, 8 errors** in the Python 3.12 backend suite and **8
  failed** Gamma history tests in Vitest. Many broker exit/recovery failures
  surfaced `canonical_inventory_unverified`; do **not** "fix" these by
  disabling inventory proof, safety fences, or UNKNOWN-fill protection.
  Browser matrix jobs also failed and require their own isolated diagnosis.
- Do not mount `/python`, `/flow`, `/strategy`, `/tools`, `/trading`,
  `/scalping` or new broker execution adapters through metadata-only reviews.
- PR #31 and all unapproved parent drafts remain unmerged and undeployed.

**Release decision: NO-GO. Local tests and selected CI PASS are not a
production authorization or a guarantee of strategy profitability.**
