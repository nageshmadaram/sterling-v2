# README preproduction safety contract regression — offline review

**Parent:** Draft Sterling V2 PR #61, exact source `43d9c9a829606f06013ffba4c925add445fe1d9e`.
**Scope:** a pre-existing operator-documentation regression **test** and offline test-workflow coverage; no source, README, backend route or broker behavior change.

## Red-first failure (latest stack and historical PR #15)

`tests/unit/test_live_only_config_surface.py::test_readme_distinguishes_simulation_from_real_broker_execution` had asserted that the README literally say "Live view places real broker orders", that "Simulation uses a separate virtual workspace", and "does not certify real broker execution". The README was deliberately hardened to open with **"Pre-production review only — production deployment NO-GO; live broker order routing and live trading NOT AUTHORIZED."** It also says "Simulation is isolated and does not certify broker execution". The old test therefore failed even while the README accurately described current release controls.

In the historical PR #15 backend log, this is one of **100 failed, 8 errored** tests. On the immutable parent of this draft, the exact isolated test was reproduced red before this change.

## Test-only remediation and release boundary

- Replace the obsolete literal live-SELL claim with positive assertions that the README states pre-production review, production NO-GO, live broker routing and live trading NOT AUTHORIZED, and isolation/non-certification of simulation.
- Retain a negative assertion: the README must **not** regress to the old "Live view places real broker orders" claim. The README itself is not modified.
- Add this entire live-config/operator-doc test module to the existing non-authorizing scoped CI workflow, and trigger that workflow when the module or README changes.
- All other static and runtime safety assertions in the module remain in force: the master live kill switch, configurations, retired strategies and maintenance-only docs are not relaxed.

**Evidence on candidate:** Complete `tests/unit/test_live_only_config_surface.py` suite **75/75 PASS** (four non-failing warnings). Targeted old-head assertion failed before patch. Python compilation, strict inventory, governance source checks, workflow YAML and `git diff --check` must be reverified on this exact candidate prior to publishing; GitHub current-head CI is separate.

## Unresolved

This fixes **one historical documentation-test mismatch**, not all of PR #15 full backend CI; the many safety-sensitive legacy exit assertions and browser regressions still require separate review. No real Kite order, authenticated broker/GTT proof, cross-store recovery, complete partial SELL, or independent release-control approval is obtained by this patch. Production remains **NO-GO**, with **0/12** independent approvals.

**DRAFT / UNMERGED / UNDEPLOYED / NO LIVE BROKER ROUTING.**
