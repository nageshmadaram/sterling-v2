# Public gateway static-asset E2E fixture — ready vs unpublished release

**Parent:** draft PR #62 at exact SHA `b9751a5dd08e581188e78b9cbd09c242db182bd0`.
**Scope:** Test fixtures, one gateway negative test, one stale health-response test assertion, and scoped offline CI coverage only. No gateway, authentication, broker routing or production source changed.

## Red-first root cause

The historical PR #15 full backend CI reported `test_missing_asset_and_source_never_become_spa` as failed because an unknown static file unexpectedly returned HTTP **503** instead of **404**. The same failure was reproduced on the current stacked parent.

The existing fixture launched the **real Node gateway** with neither a built frontend nor a `STERLING_STATIC_ROOT` pointing to a release; it implicitly relied on an unrelated developer checkout containing `frontend/dist/index.html`. The gateway correctly responds **503 Frontend release unavailable** for static requests when its release is not ready, before checking asset existence. Therefore the old fixture did not establish the prerequisite for a meaningful missing-asset **404** check.

## Additional exact-head CI blocker on parent draft PR #62

The parent #62 scoped GitHub Gamma job [run 42837110078](https://github.com/nageshmadaram/sterling-v2/actions/runs/42837110078) passed canonical/simulation but **failed** its expanded operator-documentation test step: **74 passed, one failed**. The failing `test_health_reports_live_execution_not_paper_trading` asserted `live_execution is True` despite the *workflow itself* explicitly setting `STERLING_LIVE_EXECUTION=0`, the safe default. This was a stale test-contract assumption, not a runtime health defect. The test now asserts the health field matches `lane_registry.LIVE_EXECUTION_ENABLED` so CI must observe **False** under the real kill switch. The existing `test_health_tracks_the_kill_switch` remains intact. No runtime switch is enabled.

## Test-only correction

- Give the shared gateway test fixture its own disposable `synthetic-dist` containing an innocuous `index.html` and `assets/` directory; explicitly bind `STERLING_STATIC_ROOT` to that directory.
- Poll the gateway's actual `/__sterling_web_health` until it reports `ready=true`, not merely until the process accepts HTTP. Missing assets and forbidden hidden/script sources must then return **404** with no SPA fallback.
- Add a separate negative scenario with **no published release**. Require `/__sterling_web_health` to report `ready=false`, no release commit, and all tested static paths to remain **503 Frontend release unavailable**. Do not make the gateway pretend an unbuilt release is healthy.
- Add the real gateway module's **entire 121-test suite** to the scoped offline GitHub CI job and trigger it when this test module or static gateway implementation changes.

## Evidence and limits

- Exact parent isolated missing-asset test: **FAIL**, `503 != 404`.
- Fixed fixture targeted auth, desk and missing-asset checks: **3/3 PASS**.
- Complete public gateway test module with new missing-release assertion: **121/121 PASS**.
- Complete documentation and live-health test module **75/75 PASS** with `STERLING_LIVE_EXECUTION=0`.
- Combined **196/196 PASS** (four non-failing warnings) with the exact safety environment used by the scoped GitHub job. The prior #62 job was red, so this branch must obtain fresh exact-commit CI evidence before considering the specific job cleared.
- Offline tests use loopback listeners, synthetic frontends, disposable state and a fake upstream. They do not verify a real public Cloudflare tunnel, authenticated remote sessions, publication, live broker execution or production asset availability.

The user's historical full PR #15 backend CI and multiple Playwright regressions remain separate release gates. **DRAFT / UNMERGED / UNDEPLOYED / LIVE BROKER NO-GO / 0/12 independent approvals.**
