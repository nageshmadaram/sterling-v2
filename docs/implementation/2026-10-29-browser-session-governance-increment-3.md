# Sterling V2 — Increment 3: browser, session-security and governance evidence

**Date:** 2026-10-29. **Working branch:** `upgrade/evidence-traceability-20261029` (unmerged). **Source under test:** `8386da02c53992ee01905604175c4ab045fc5e0c`. Frozen `main` observed at `27e39e954ee67dbd5ec1d68ab9d15ff15de7dd7f`.

**Authoritative references:** [Next-upgrade specification](2026-10-29-next-upgrade-spec.md), [increment 1 evidence plan](2026-10-29-evidence-upgrade-slice.md), [increment 1 closeout](2026-10-29-next-upgrade-closeout.md), [increment 2 report](2026-10-29-product-hardening-governance-increment-2.md), and October 27–28 account/workspace/UI audit documentation.

## Task-by-task execution and evidence

| Task | Objective | Files changed / inspected | Validation performed | Evidence artifact / status | Blocker |
|---|---|---|---|---|---|
| A1: Browser responsive | Validate hidden routes, Back, chooser focus, keyboard Escape and viewport overflow | New `frontend/e2e/next-upgrade-readonly.spec.ts` and `frontend/playwright.next-upgrade.config.ts`; inspect workspace router/chooser | **Real Chromium** with local Vite and mocked API at 390px and 1440px | [Passing CI 40893974347](https://github.com/nageshmadaram/sterling-v2/actions/runs/40893974347), 6/6 cases with A2 | No physical-device evidence |
| A2: Revocation/admin | Unmount current media after visibility policy refresh and remove admin UI after revoked role | Same browser tests; inspect `Administration.tsx`, `WorkspaceRouter.tsx` | Wait for actual React Query policy polling; check no iframe/admin mount after refusal | Chromium 6/6 PASS, synthetic screenshots in CI artifact | No independent real-identity validation |
| B1: Session lifetime | Positive bearer admin identity, finite JWT type/expiry, refresh and logout version revocation | Two added tests in `backend/tests/test_workspace_access.py`; inspect `auth.py`, `tokens.py` | Production-mode **test process** with synthetic users and disposable DB | Backend targeted 64/64 PASS (5 warnings) | Real operator enrollment and MFA not implemented/accepted |
| B2: Owner-bound credentials | Synthetic credential replacement must be encrypted, redacted, audited and denied to members/refresh tokens | Same backend test file; inspect admin broker login endpoint | Assert real auth dependency in test client, encryption, audit and negative session paths | Included in 64/64, no broker API calls | Independent production vault/secret controls BLOCKED |
| C1: Control procedures | Prepare read-only evidence instructions for H01–G12 | New `docs/evidence/2026-10-29-next-upgrade/increment-3-governance-intake.json` | Check exact 12-control source map, required evidence, zero receipts | Source-only checks 18/18 PASS on tested code | No approved host/provider access |
| D1: Ownership | Map proposed accountable teams and independent review roles | Same governance intake JSON | Assert all named owners/collectors/reviewers null; no signatures invented | Proposed teams for all 12; actual assignment 0/12 | Human owner assignment required |
| E1: Governance review | Record source-based run evidence and no-go decision | `docs/evidence/2026-10-29-next-upgrade/increment-3-validation-result.json`, this report, `scripts/tests/test_next_upgrade_increment3_intake.py` | Dedicated CI source tests and no-go assertions | Passing source checks 18/18 on tested commit; new receipt check introduced here | Review signatures NOT PROVIDED |

## Red-to-green engineering trace

The first browser run [40892694235](https://github.com/nageshmadaram/sterling-v2/actions/runs/40892694235) failed on outdated fixture endpoints, blocked third-party assets being treated as unauthorized API writes, and refresh timing. The second [40893572749](https://github.com/nageshmadaram/sterling-v2/actions/runs/40893572749) exposed the older administration fixture and denied preference PATCH requests. The harness now deliberately **denies** preference PATCH before network I/O and uses the actual current Administration contract; no product runtime code was changed in Increment 3.

The final source run [40893974347](https://github.com/nageshmadaram/sterling-v2/actions/runs/40893974347) passed **all four CI jobs**: Chromium 6/6 (390/1440), frontend Vitest 21/21, TypeScript and isolated build PASS, backend Pytest 64/64 (five warnings), source-only no-go checks 18/18. All API responses in Chromium were synthetic. Synthetic media-document POST was intercepted locally by Playwright; denied preference PATCH was never sent to a backend. No production data, physical device, broker connection or live execution was exercised.

## Ownership and evidence-review constraints

The [twelve-control intake](../evidence/2026-10-29-next-upgrade/increment-3-governance-intake.json) has **proposed** teams and distinct independent reviewer roles for H01, T02, S03, I04, D05, R06, B07, M08, A09, L10, F11, G12. **No named human has been assigned or verified; no independent evidence has been accepted.** The original certification register in draft PR #14 remains untouched. Each prospective redacted receipt needs an immutable candidate hash, approved environment, observation time, named independent reviewer, SHA-256 digest and explicit approval. No person, signature or real-world evidence has been inferred from successful CI.

## Next recommended tasks

1. Assign and independently verify human owners, evidence collectors, reviewers and their organizational responsibilities; leave controls BLOCKED until acceptance.
2. Specify an MFA-bound step-up and opaque device-session contract with independent review before any credential-reveal functionality; current JWT lifecycle tests are not MFA acceptance.
3. Obtain separate written permission for isolated non-authoritative host/TLS/vault/DB/backup and monitoring evidence; store only redacted signed references.
4. Obtain provider authorization before any broker/market read-only observation. **Do not connect, place orders, route broker writes, or enable live execution.**
5. Conduct separate real-device accessibility, performance and resilience testing, and independent code/security review.
6. Have independent human governance reviewers decide each control and issue a separate release decision; this package cannot issue approval.

## Final decision boundary

- **OFFLINE VALIDATION: PASS** (historic Phase 11 offline validation)
- **PRODUCTION DEPLOYMENT: NO-GO**
- **LIVE TRADING: NOT AUTHORIZED**
- **BROKER ORDER ROUTING: NOT AUTHORIZED**
- Production controls accepted **0/12**, blocked **12/12**; H01 production evidence **BLOCKED**.
- Independent release sign-off **NOT PROVIDED**; merge to main **NOT AUTHORIZED**.

**Production certification remains not achieved. Release freeze remains active.**
