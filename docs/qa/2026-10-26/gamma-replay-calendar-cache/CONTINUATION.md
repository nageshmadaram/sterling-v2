# Gamma immutable calendar memoization and recovery phase measurements

Baseline: `9e593377dbc08205da4db36ed9e913b285671218`.

## Actual remaining gap

The previous release's fresh live scheduled-history recovery completed with 116 watchers, no issue and 215.58 seconds; simulation completed with 93 watchers, no issue and 79.01 seconds. The live three-minute target remains unmet. The earlier 242.65 seconds and later 215.58 seconds are different market-time observations, not a controlled A/B comparison. No full-day, two-day or midday-restart acceptance is implied.

An isolated CPU profile identified repeated timestamp-to-IST-date and date-to-session-open calculations during original E2/E1 replay. Pure immutable calculations were repeatedly recomputed for the same bars across successive prefixes and contracts.

## Exact changes

- `backend/app/engines/gamma_move/trigger.py`: bounded memoization of `ist_date` (8,192 entries) and `_session_open_ms` (512 entries). Only immutable timestamps/dates and their clock conversion results are retained; there is no account, market-data, engine or order cache. The original conversion expressions remain unchanged. Session-open values already needed for H's missing-volume calculation are computed once per represented day and reused inside that call.
- `backend/app/services/gamma_move_scanner.py`: optional caller-owned warmup timing dictionary. Completed normal scans and scheduled-history recoveries publish it as `scan.prime_timing`, including recorded-input, option-batch, underlying-read, engine-replay and total wall seconds. Counts describe logical selected-option/underlying reads, engine replay attempts and retained contracts. They are not HTTP request counts: caches, pacing and retries remain inside a logical read. Engine replay wall time includes executor queueing and is not a CPU measurement. Failure paths retain the same errors and completion/refusal semantics. Simulation owner-market-data reuse excludes the owner’s warmup timing field, so it cannot be mistaken for the virtual engine’s own work. No mutable global diagnostics store or timing threshold gate is introduced.

Gamma rules, fixed timeframes, original live-tick E1, recorded E1 restoration, OI re-arming, daily reset, missing-volume semantics, target/stop logic and broker pacing remain unchanged. EMA remains original-only practice/PAPER; other modes and all saved AUTO/risk/lot settings are untouched.

## Regression and CPU evidence

Eighteen new calendar cases cover repeated-work reduction, finite cache memory, epoch/fractional/midnight/year-rollover conversions and complete engine-state equality against uncached original calculations for entry, missing bars, zero-volume bars, next-day reset and re-arming. Three new warmup cases verify logical counters and nonnegative phase timing for successful, option-failed and underlying-failed reads. The existing scheduled-slot recovery test now additionally verifies timing publication without weakening its original historical-slot assertions.

The simulation reuse regression is parameterized with and without owner timings and retains its independent-engine/config/refusal assertions. On the baseline, eight cases fail and 77 controls pass. The fixed focused group passes all 85 cases. All transports are stubbed. Tests send no real notification or broker request, do not initialize production storage and do not publish production engines.

A separate frozen 20-engine cached-input sample was run three times per revision without a profiler. Baseline median 3.060814 seconds; candidate median 1.080660 seconds. All six complete engine/runtime-state hashes match. This is CPU-only exploration using newest matching token caches without account-lineage certification and empty recorded E1/OI inputs. It does not establish live duration, authoritative market parity or a promised three-minute recovery. Networking was prohibited; no runtime was published.

One earlier full run was deliberately stopped at 396 passes while the timing-attribution gap was reproduced. Its partial log is retained and is not a release pass. Full final make verify passed: 11847 passed, 30 skipped in 1113.08s (0:18:33); TypeScript and VERIFY OK. Exact skip set is unchanged; baseline and head failure sets are empty. Frozen source and log hashes are recorded in validation.json. Frontend source is unchanged from the prior verified 2,391-test / 257-file tree; no new frontend full run is claimed. TypeScript is rerun by make verify.

## Acceptance and rollout boundary

Before a restart, require fresh readable/flat broker exposure, intact online backups and unchanged trading/notification configuration fingerprints. Keep live execution enabled as requested. Retain the existing temporary hostname and manager process. Preserve merged work and ignored test databases before branch/worktree cleanup.

Newly completed recovery timings must be distinguished from older persisted history. The live 180-second target remains open until actually met on the released build. Full same-release market load, two consecutive real trading days with coordinated midday recovery, full-day Gamma simulation, actual populated Snapback restore, authoritative research, hosted CI billing and sustained temporary-endpoint/device acceptance remain separate. The frozen prospective Snapback evidence/configuration HALT is not bypassed. Prior user-confirmed Positions/Signals phone checks remain passed. No QA scan/order/GTT mutation or additional notification is authorized by this continuation.
