# Sterling V2 — Next upgrade slice: evidence-first implementation
Date: 2026-10-29
Source of truth: [next upgrade spec](2026-10-29-next-upgrade-spec.md)
Branch: `upgrade/evidence-traceability-20261029` from `27e39e954ee67dbd5ec1d68ab9d15ff15de7dd7f`.
Scope: **source-traceability and evidence-preparation increment only**. This is not an application deployment, certification, broker connection or live-trading release.

## Source state
- The specification is the first change after historical main `939e611e`; current observed main for this branch is `27e39e954ee67dbd5ec1d68ab9d15ff15de7dd7f`.
- The independent 12-control no-go register remains in draft PR #14 on `cert/production-readiness-evidence-20261029` at `e35eb40b76df5796e335312e4866a00c0a977db8`. This work **references** it; this branch does not alter that source register.
- Draft PR #13's `ce702ab1622e53f6233e1bf9a235ef87fc297a8f` offline evidence is not real-environment production proof.
- Historical UI test counts in the supplied product documents are not rerun results. No manual/real-device verification has been attempted in this increment.
- Both real named owners and independent approvals are unknown; role labels are **not** named owners.

## Granular sequence and acceptance

| Task | Objective | Files inspected / edited | Proposed change and validation | Expected evidence | Current scope status | Risk / blocker |
|---|---|---|---|---|---|---|
| P1-T1 Source inventory | Trace already delivered UI, admin, settings and workspace work | Inspect five implementation docs, README, strategy and kitelake README, frontend shell and backend feature-access paths; edit `release-package.json` | Require real local source paths and prior documentation for each feature; pin spec blob identity | Deterministic inventory and validator result | Implemented in package; execution of validator separately recorded | Historical claims are not newly reverified |
| P1-T2 Release boundary | Separate source implementation, evidence and approvals | Edit `release-package.json` and this file | Fail closed on certification, trade, routing, deployment, merge or reviewer claims | Sanitized guard output | Implemented in package; validation pending | Current main must stay untouched |
| P2-T1 Control mapping | Enumerate H01–G12 and missing acceptance evidence | Inspect draft PR #14 register; edit `control-evidence-map.json` | Require 12 distinct BLOCKED records, evidence and verification per control, accountable role and explicit missing named owner | Complete blocked-control map | Implemented in package; validation pending | Named human owners missing on all 12 |
| P2-T2 Exception register | Track open dependencies explicitly | Edit `exceptions.json` | Ensure each open exception references known controls | Exception map and negative tests | Implemented in package; validation pending | Five exception groups remain open |
| P3-T1 Offline evidence checks | Prevent silent promotion from this package | Edit `scripts/verify_next_upgrade_evidence.py` and negative tests | Pure filesystem/JSON validation only; assert pinned spec, source paths, denied actions and evidence absence | Machine-readable sanitized verification JSON | Implemented; test execution separately recorded | No real-host or broker checks by design |
| P3-T2 Repeatable CI | Run checks without operational authority | Edit `.github/workflows/next-upgrade-evidence.yml` | Isolated-branch Python standard-library test job; read-only permissions | GitHub Actions run and retained report, if runner executes | Implemented; hosted execution unknown | GitHub Actions may not run immediately |
| P4-T1 Governance closeout | Provide review checklist and decision matrix | This document, package and exception record | Confirm blocked/no-go counts and refusal conditions | Reviewable package; no sign-off claim | Documented | No human reviewer or evidence supplied |
| P5-T1 Product policy hardening | Test UI/policy stale-state, shell and broker ownership behavior | `frontend/src/pages/WorkspaceRouter.tsx`, `frontend/src/pages/Administration.tsx`, `backend/app/core/feature_access.py`, relevant tests | Later isolated repro and narrow patch only after failing test, including mobile and desktop | Actual runtime/browser tests | BLOCKED for this increment | Cannot claim behavior verified from source inventory alone |

## Governance review checklist
- [x] Source spec identified and baseline SHA recorded
- [x] Product implementation sources and historical evidence references indexed
- [x] Twelve control requirements and accountable **roles** documented
- [ ] Human named owner assigned for each control
- [ ] Independent real-environment evidence received and verified
- [ ] Isolated mobile/desktop runtime suite rerun for this change
- [ ] Security, operations, provider, DB and independent release signatures supplied
- [ ] Independent new go/no-go decision issued

## Current decision
0 / 12 independently accepted; 12 / 12 BLOCKED. H01 tooling may have offline PASS evidence in the separate review branch; **H01 production acceptance remains BLOCKED**. The package is evidence-preparation only. Production certification **NOT ACHIEVED**. Production deployment **NO-GO**. Live trading, broker order routing and merge to main **NOT AUTHORIZED**.
