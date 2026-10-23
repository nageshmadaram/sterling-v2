> Historical record. Current implementation and verification: [open-gaps/IMPLEMENTATION_LEDGER.md](open-gaps/IMPLEMENTATION_LEDGER.md) and [VALIDATION.md](open-gaps/VALIDATION.md). Old failures/deployment states below are retained as history.

# Merge, synchronization and follow-up fixes — 23 October 2026

The trading QA changes are being integrated through `codex/trader-qa-remediation`. The base is `ff137155a083b2c52981545ff94400764ad6c188`; at the initial fetch, local and remote `main` and `dev` all pointed at that commit. This report supplements [the original audit and remediation](../2026-10-22/FIX_IMPLEMENTATION.md).

## Additional defects reproduced and corrected

| Issue | Reproduction | Fix and regression |
|---|---|---|
| Git-less backend and frontend images report unknown identity even when a build SHA is supplied | Backend diagnostics returned unknown instead of the verified artifact SHA; the old frontend Vite expression returned `UNKNOWN` when Git was absent despite a valid image build input. | Backend falls back to content-verified artifact metadata; malformed/tampered/missing identity remains invalid or unknown. Frontend prefers checkout Git and uses a validated explicit build argument only when Git is unavailable. Docker's builder stage receives that input. The archive publisher also binds its selected immutable SHA to the build; archives cannot silently inherit the surrounding checkout's branch SHA. Unit tests cover both paths; a complete build in a temporary Git-less context retains the synthetic expected SHA in its assets. |
| Valid JSON that is not an object crashes artifact verification | `[]`, `null`, a string and a number raised during `.get()` access. | Return an explicit invalid identity (`build_metadata_not_object`), preserving fail-closed diagnostics rather than throwing an unexpected exception. Four regression cases. |
| Duplicate Navigator chart routes dispatch to the stale implementation | Two identical GET routes were registered. The first route bypassed the maintained resolver and clamped requests differently from the module's visible function. | Remove the stale route, keep the maintained resolver and preserve malformed-candle HTTP 422 handling. Tests exercise the registered endpoint and verify unique route registration. |
| Favorable partial fills lower the approved Price Rules stop | With an approved entry limit of 100 and stop of 80, a first partial fill at 90 lowered the stop to 70. A subsequent average fill at 95 would then expose 25 points against a reviewed 20-point distance. | The confirmed-fill projection preserves the approved stop floor. The regression observes the first partial fill and the completed weighted fill, verifies the stop never moves below approval, and checks the resulting risk stays within the reviewed distance. |
| A transient historical-data timeout aborts Gamma recovery immediately | A timeout followed by a successful identical response was never retried. Exhaustion had no attempt context. | Historical GETs retry up to three times with pacing and bounded backoff for transport/network failures. Authentication failures are not retried; exhausted calls raise the original error and record attempt/token/interval context. No empty substitute data or order retries are introduced. |
| A missing target is copied as a zero reward multiple | A plan with entry/stop but no target copied `1 : 0.00 R`. | R remains unknown unless entry, stop distance and target are valid. Display/copy keep absent values unknown. |
| Navigator test database path leaks into later fixtures | Running chart-series tests before manual-entry tests left the global database path pointing at an unlinked database, causing two setup errors. | The fixture restores the database path with `monkeypatch`; the complete focused group passes together. |
| Temporary checkout makes the default operator database look disposable | The baseline's spawned-process guard test failed because the checkout lived under `/tmp`; the path-only predicate treated its default operator database as test data. | Explicitly refuse the canonical checkout database before considering temporary/named-fixture paths. New tests prove the temporary-checkout case is refused and ordinary temporary fixtures remain usable. Production database behavior is unchanged when the test guard is inactive. |

The original NHPC upstream failure has not been observed under a deployed new build. These changes address transient-read recovery and provide precise failure context; they do not certify that an external provider will always return data.

## Verification

- Original complete `make verify`: **11,661 backend tests passed, 30 skipped; TypeScript passed**.
- Final combined `make verify`: **11,674 passed, 30 skipped; TypeScript passed**.
- Final frontend: **2,256 tests passed across 241 files**.
- Focused follow-up backend group: **137 passed**.
- Git-less frontend production build: **passed**, using a synthetic 40-character SHA and a temporary source context with no `.git` directory. This is a build-contract test, not a production release certificate.
- Publication and supervisor regressions after the archive-identity correction: **80 passed**. A real archive build inside another checkout changed from a wrong/missing declared identity to the supplied immutable SHA.
- Baseline comparison: **passed: original QA head 0 failures, baseline 1 failure; no new failures**.
- Database guard, legacy migration, authentication/product persistence and Price Rules group: **108 passed**, including both additional guard cases.
- Secret scan: **passed** on the final staged source and report files. Source whitespace check excludes raw evidence.

The baseline regression script began with the original QA source, whose full failure set was empty. The additional changes were applied after that head run completed, while its independent baseline checkout ran. The one baseline failure was the spawned-process live-database guard under a temporary checkout; this exposed an additional guard defect, reproduced with a failing test and corrected separately. The final combined full verification supplies the final trading-backend failure set. The later presentation-only publisher/config adjustment is additionally covered by the 80-test publication group and the real archive build; it changes no trading backend behavior. The late database-guard correction is scoped to test safety and is covered separately with database, authentication persistence, product persistence and Price Rules tests; the two new guard cases are not included in the broad run’s case count. The final report compares that set against the same captured baseline; it does not imply that an earlier head run tested later edits.

`TESTING.md` now describes the current suite size and realistic runtime, removes the obsolete Delta socket selector guidance, and asks for actual skip reasons. No current test is excluded through that removed-test selector in the final full run. Declared skips remain visible and are not release approvals.

## GitHub Actions evidence

The checks on the previous `main` commit failed before their test steps began. GitHub's annotation says:

> The job was not started because recent account payments have failed or your spending limit needs to be increased.

See [the captured annotation](evidence/github-baseline-ci-annotations.json). This describes the existing check on the base commit. The new pull request's checks must be inspected separately. A blocked, skipped or unstarted Actions job is not a passing CI result. Billing settings and required checks are not bypassed by these fixes.

## Integration and operational boundary

Repository synchronization uses a feature-branch push, a pull request to `main`, a normal GitHub merge, then fast-forward updates of local `main` and local/remote `dev`. Final PR identity, commit SHA and clean synchronization checks are returned with task completion. Required server checks are respected; no force-push or administrator bypass is used.

Git synchronization does not restart the trading backend, resume entries, change account controls or create broker orders/GTTs. Existing Git hooks record a backend-promotion notice. The existing presentation manager follows committed `main` revisions; loading changes to that manager itself requires its own controlled update. Production identity matching, authenticated browser acceptance and open-session broker lifecycle verification remain separate release work. Strategy AMO remains unavailable pending a validated next-session policy; the serializer/cancellation contract is tested with a fake HTTP transport.

## Evidence and source map

- [Full backend and actual skip reasons](evidence/backend-full.log), [final failure comparison](evidence/final-failure-comparison.json).
- [Full frontend result](evidence/frontend-full.log), [focused follow-up backend result](evidence/followup-backend.log), [publisher tests](evidence/publisher-tests.log), [database safety tests](evidence/database-safety-tests.log).
- [Regression gate](evidence/regression-gate.log) and [baseline failure set](evidence/baseline-failures.txt).
- [Git-less build result](evidence/gitless-build-result.json), [archive build before/after](evidence/archive-build-result.json), [normal checkout build](evidence/checkout-build-result.json).
- `backend/app/core/runtime_identity.py`, `backend/app/core/build_artifact_identity.py`: validated image provenance and metadata shape.
- `frontend/buildIdentity.ts`, `frontend/vite.config.ts`, `frontend/Dockerfile`, `scripts/sterling_stack.py`: correct build source and archive identity.
- `backend/app/api/v1/endpoints/navigator.py`: one maintained chart route with typed validation responses.
- `backend/app/services/price_rules.py`: reviewed stop floor survives favorable partial fills.
- `backend/app/services/gamma_move_scanner.py`, `gamma_move_historical.py`: bounded idempotent read recovery and useful diagnostics.
- `backend/app/services/db.py`: test-only protection of the canonical checkout database, including temporary checkouts.
- `frontend/src/components/kite/AdaptiveEdgePositionCalculator.tsx`: absent/invalid targets never produce an invented reward multiple.
