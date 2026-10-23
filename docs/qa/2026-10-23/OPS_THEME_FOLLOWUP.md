# Operations contrast and release asset storage follow-up — 23 October 2026

The authenticated Operations page correctly reported matching frontend/backend release identity after the backend restart, but its safety cards were difficult to read in light theme. Browser-computed styles showed a dark fallback surface (`rgb(18,20,26)`) beneath dark inherited text (`rgb(68,71,70)`), only **1.96:1 contrast**. The panel referenced an undefined `--k-panel` token. Other status and label tokens also fell back to colours unsuitable for the active theme.

## Fix

Operations now uses the existing palette's `--k-surface`, `--k-text`, `--k-ink-4`, `--k-green-deep`, `--k-red-crimson` and `--k-warn-ink` tokens. Cards explicitly set their foreground as well as their background. Critical error banners retain a red tint at a lower opacity so their small text also passes in dark theme; the stop button uses the base theme surface with its red label/border. Headings, row labels, disabled resume text, refresh actions, badges and external-position details follow the same palette.

The change does not alter strategy calculations, entry/exit admission, position management, order construction, stop/resume callbacks, labels or trading controls. The work is isolated from the running checkout. The production backend application (`backend/app` and `backend/main.py`) is byte-identical to base `fb3356135a2cef30af4d47802b23f3886185d60d`.

## Reproduction and verification

A rendered-component regression resolves CSS variables against the production `THEME_TOKENS`, composites translucent surfaces and measures WCAG contrast. It covers safety values/headings/labels, PASS/FAIL/UNKNOWN badges, four trading-mode banners, blocked/unknown admission warnings, and enabled operator actions in both light and dark. It never clicks a trading control. The initial implementation failed **7 of 12 cases**. After the initial correction, all 12 pass; the focused operations/theme group has **54 passing tests**, and TypeScript passes.

The initial complete frontend run passed **2,268 tests across 242 files**. Its initial direct `npx vitest` invocation in the clean worktree had two setup failures because generated `public/media` assets were absent. Re-running through the documented `npm test` pretest hook generated those assets and passed every case; no test was excluded and no media assertion was changed. Both logs are retained. Required `make verify` and the complete backend failure-set comparison against main are recorded below after completion. The completed comparison uses executable memory-backed scratch directories for disposable fixtures and pytest's standard `tmp_path_retention_policy=failed`. This reduces persistent-disk I/O without excluding tests or changing assertions. Earlier disk-backed runs were interrupted with SIGINT because of I/O contention and are retained as incomplete logs; their progress is not counted as completed verification. The final runs collect the final publisher code and all parameter cases from the start. Production data, backups and deployment assets remain on their original storage.

## Deployment checks

The preceding backend restart preserved live execution and every fingerprinted existing configuration row. Authenticated browser diagnostics verified matching frontend/backend commit and the expected startup source fingerprint. Fresh read-only broker profile/positions/orders/GTT calls succeeded before and after; both observations were flat, with no orders or GTTs. QA issued no broker order/GTT mutation requests. Funds and IP exclusions are not used as evidence of test success.

Release/evidence metadata and GitHub Actions availability remain separately reported. No readiness threshold, safety gate, permission check or hosted status was bypassed.

## Immutable release asset storage

The publisher previously allocated another copy of each retained hashed asset on every promotion, even though published releases are immutable. It now shares those files using hard links when supported and falls back to copying when the filesystem does not support linking. Existing asset names/bytes, failed-build rollback and the atomic release pointer are preserved; no historical assets are deleted. This avoids the transient disk-space spike that copying a large retained asset set caused during an update.

The publication regression now creates an asset unique to the first build, verifies it survives the next build unchanged, and checks that both releases share the same file when links are supported. It also forces an unsupported-link error and verifies the copy fallback. The reuse assertion failed against the old publisher. The initial disk-backed runs had already imported the old publisher when this issue was reproduced. They were interrupted and replaced with complete final-source runs using memory-backed fixture storage. The final head run includes the additional parameter case; the complete current publication/supervisor group also passes independently. No assertion is excluded from either complete run.

The complete current publication/supervisor group passes **84 tests**, including both retained-asset linking and copy fallback.

Review of the blocked and unknown admission states reproduced two further dark-theme failures at 4.05:1. Four additional cases cover both states in both themes. Their red warning text/border now use the card surface without a tint; all **16 contrast cases pass**. Trading labels and admission logic remain unchanged. The final complete frontend run passes **2,272 tests across 242 files**, and TypeScript passes. Both include this final warning correction. See `PASTED_REPORT_RECONCILIATION.md` for an item-by-item reconciliation of the separate user-pasted walkthrough; it explicitly retains unresolved findings, including reproduced scan stalls.

## Pasted walkthrough presentation follow-ups

The separate pasted walkthrough identified two remaining small presentation defects. EMA's strategy-owned practice action now visibly says **Paper**, retaining its accessible practice label and its own ticket; it does not gain a generic live order route. Short custom action labels longer than three characters receive enough width for the word. Grouped signal timestamps now use **24-hour IST**, consistent with individual rows, while retaining date/year and relative age. The morning/afternoon formatter expectations failed against the old 12-hour formatter before the correction. The full frontend rerun below includes these final changes. These are presentation fixes; they do not resolve missing EMA scan evidence or the reproduced Snapback CPU stalls.
