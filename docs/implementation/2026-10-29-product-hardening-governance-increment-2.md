# Sterling V2 — Product and governance hardening, increment 2 (2026-10-29)

**Source of truth:** [Next upgrade specification](2026-10-29-next-upgrade-spec.md), [increment 1 plan](2026-10-29-evidence-upgrade-slice.md), [increment 1 closeout](2026-10-29-next-upgrade-closeout.md), and the 2026-10-27/28 account, workspace and UI documents. `main` is frozen; this work is isolated to `upgrade/evidence-traceability-20261029` and carries no production or trading authority.

## Evidence-first execution

1. **Baseline:** `main@27e39e954ee67dbd5ec1d68ab9d15ff15de7dd7f`; implementation branch entered increment 2 at `74a6ef2434f8d65ba7874d7c8cdbb44f85cc2c58`; prior source-only evidence CI was PASS.
2. **Reproduce:** New frontend cases first committed at `8f7c453a024525b608b6cdbee7eeef17622adebb`. [Red CI 40887581789](https://github.com/nageshmadaram/sterling-v2/actions/runs/40887581789) ran **21 cases, 17 passed, 4 failed**. Three failures represented product defects (hidden-but-granted direct route, visibility-driven mount eviction and selector focus restoration); one was an invalid test query of a closed dialog.
3. **Narrow product remediation:** `9ba474d7a53b51b319bd3e8ce67c0a11b4fdef69` modified only `frontend/src/pages/WorkspaceRouter.tsx` (requires grant **and** visible workspace) and `frontend/src/components/WorkspaceChooser.tsx` (restores focus to a still-connected opener on close), plus the incorrect test assertion. The source does not change backend feature authorization, provider semantics, trading admission, or operational configuration.
4. **Backend governance:** Five new test functions in `backend/tests/test_workspace_access.py` cover common account/configuration policy revisions, rollback and credential transfer safety, administrator credential revision/owner checks and audit-atomicity, stale assignment preflight denial, and bearer-token negative tests in an isolated production-mode **test process**. No actual service, broker API or deployment was contacted.
5. **Executed final evidence:** [CI 40888338963](https://github.com/nageshmadaram/sterling-v2/actions/runs/40888338963) on `31496a6e1ec575964e74725b11a8496c743f407e` completed successfully: **21/21 frontend Vitest**, frontend `tsc --noEmit` PASS, isolated `npm run build` PASS, **62/62 backend pytest** on disposable databases (**5 warnings**), plus source-only no-go controls and the original **12/12 negative checks**. The generated frontend bundle was not published.

## Granular task register

| Task | Objective | Files modified / inspected | Validation and evidence | Status | Blocker / risk |
|---|---|---|---|---|---|
| A1 — Workspace policy | Refuse hidden direct routes and evict newly hidden feature content | `frontend/src/pages/WorkspaceRouter.tsx`; `frontend/src/pages/__tests__/WorkspaceRouter.test.tsx` | Test-first fail then 21/21 green; direct hidden and visibility refresh verified with mocked policy | IMPLEMENTED / VERIFIED in jsdom | Real browser and device handling not independently verified |
| A2 — Account/menu focus | Preserve accessible account selection and restore dialog origin focus | `frontend/src/components/WorkspaceChooser.tsx`, workspace UI tests, `AccountMenu.tsx` inspected | One test-only assertion corrected; focus regression red then green | IMPLEMENTED / VERIFIED in jsdom | Native browser focus, keyboard Escape and mobile assistive technology unverified |
| B1 — Policy state | Check feature/configuration revision parity, admin navigation and fail-closed authorization | `backend/tests/test_workspace_access.py`, existing account/config policy suites | Disposable SQLite tests and 62/62 overall suite | VERIFIED in CI fixtures | No approved live operator/host acceptance |
| B2 — Broker and credentials | Check CAS, transfer rollback, redacted audit and administrator session denial | Same test file; admin connections and vault modules inspected | No broker network calls, synthetic credentials; missing/member/revoked bearer denied | VERIFIED negative pathways in CI | Real operator MFA/step-up and actual provider rights not verified |
| C1 — Source changes | Change product only after reproduced failing tests | Two frontend product files, test file, CI workflow | Red CI 40887581789 then green CI 40888338963; type/build PASS | IMPLEMENTED | No deployment or release authorized |
| D1 — Evidence | Make source and test claims independently inspectable | `docs/evidence/2026-10-29-next-upgrade/hardening-regression-evidence.json` and this document | Commit/run identifiers, test counts, blocked-control map, no-go checks | IMPLEMENTED / SOURCE-VERIFIED | External evidence and human signatures outstanding |

## Controls and source separation
The prior [production register](https://github.com/nageshmadaram/sterling-v2/blob/cert/production-readiness-evidence-20261029/certification/production_evidence_register.json) remains the separate review record. The source/fixture checks here support preparation relevant to I04 (RBAC/identity), S03 (credential handling), B07 (broker ownership), L10 (legacy boundaries), and F11 (UI application assurance), but **none** is independent real-environment evidence. H01, T02, S03, I04, D05, R06, B07, M08, A09, L10, F11 and G12 all remain **BLOCKED**. Accountable roles are recorded; actual named humans are not yet assigned.

## Risks and next sequence
1. Run isolated Playwright in real Chromium at desktop and 390px widths, including direct URL, browser Back, visibility revocation, keyboard Escape, modal focus and screenreader semantics. Preserve screenshots locally without personal data.
2. Expand session governance with positive admin bearer flow, token rotation/expiry and MFA/step-up policy when such a development test contract exists; never connect a broker.
3. Review first-run warnings and dependency/runtime scanning in a non-authoritative environment.
4. Request named human owners and evidence receipts for all 12 controls in a separate approval process, without changing their blocked status or authorizing operational actions.

**Definition of done for this increment:** source test-first defects fixed, targeted CI and source-only validation pass, evidence references are immutable, and no operational authority changed. This is **not** full product/browser or production certification.

**Final decision:** 0/12 production controls accepted; 12/12 BLOCKED. H01 production evidence BLOCKED; independent sign-off NOT PROVIDED. Production deployment NO-GO; live trading, broker order routing and merge to main NOT AUTHORIZED. **Production certification remains not achieved. Release freeze remains active.**
