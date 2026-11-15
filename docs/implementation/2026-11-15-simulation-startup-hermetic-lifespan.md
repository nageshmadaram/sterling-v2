# Synthetic simulation-startup lifecycle: external Telegram isolation and owner-only HTTP assertions

**Parent:** Sterling V2 draft PR #63 at exact SHA `a1fac9356d6def4e723a2205aab7a8b1d5d4d94f`.
**Posture:** test-only review, unmerged, undeployed, live broker routing unauthorized, all 12 independent production controls still unapproved.

## Reproduced defects and root causes

- Historical PR #15 full backend CI reported six network-denial teardown errors in `tests/unit/simulation/test_tenancy_boot.py` where startup background Telegram workers attempted HTTPS to `api.telegram.org`. A simulation lifecycle test must never depend on a CI/developer host's real Telegram credentials or connectivity. The suite already has an autouse `socket.connect`/`socket.create_connection` guard; it correctly fails on attempted egress.
- On the exact parent PR #63 in an isolated, disposable SQLite test database, `test_startup_creates_the_tenant_and_runs_the_sim_loop_beside_live` and `test_a_failing_sim_runtime_never_stops_live_startup_or_shutdown` FAILED (2 failures, 6 passes): they assumed anonymous callers could get a 200 and owner-only simulation runtime data. With current request authorization, such access is not guaranteed and non-owner callers must be refused.

## Test-only remediation

1. `test_tenancy_boot.py`'s autouse fixture removes `TELEGRAM_TOKEN`, `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from its temporary process environment, replaces the legacy module's already-imported credentials with empty strings and prevents legacy DB restore during this **synthetic** lifespan test. The full application lifespan and notification worker task lifecycle are still exercised; no production Telegram module is changed.
2. Replace the two outdated anonymous/unauthorized `GET /api/v1/sim/status` success assumptions with explicit `X-User-Id: intern` **403 owner-only refusal** assertions. Continue to verify the virtual runtime with its actual in-process `sim_runtime.is_running()` state and the `/health` endpoint, including failure of the virtual start worker.
3. Add a regression test that injects an explicit fake Telegram bot token and chat ID into the test process and verifies the complete app lifespan runs while the strict no-network fixture observes zero attempts.
4. Add `test_tenancy_boot.py` to the Gamma scoped review workflow path filter. Isolate the 9 test cases in their own CI step with a disposable `/tmp` SQLite path, synthetic fake Telegram credentials and the live broker kill switch set to `0`. The remaining simulation tests retain their original environment.

## Validation (same candidate checkout)

- Old parent, isolated disposable DB: **2 failed, 6 passed** from authorization expectations.
- Updated startup module with fake Telegram credentials and socket guard: **9 passed, 5 non-failing warnings**.
- Updated entire `backend/tests/unit/simulation`: **621 passed, 5 non-failing warnings**, using disposable SQLite and `STERLING_LIVE_EXECUTION=0`.
- Workflow YAML parsing, unique test-step selection and path filtering validated. Exact-head GitHub workflow jobs must be examined independently after publication.

## Explicit boundaries

- These tests do not independently validate Telegram message delivery; operator receipt belongs to production control A09. They prevent unintended external egress in this simulation-lifecycle suite.
- A negative authorization response does not prove all role, workspace and protected-route behavior. Full HTTP authorization matrix remains pending.
- Nothing in this branch changes production `main.py`, notification transport, account permissions, risk checks, broker adapter, protective GTT behavior or durable order state.
- PR #15 full historical CI and Playwright remain failed, and a green scoped CI workflow is not production authorization.

**DRAFT / UNMERGED / UNDEPLOYED / LIVE BROKER EXECUTION NO-GO / 0 OF 12 INDEPENDENT APPROVALS.**
