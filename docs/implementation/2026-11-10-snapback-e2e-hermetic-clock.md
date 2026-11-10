# Offline Snapback E2E clock discipline isolation — draft review

Parent draft PR #58 source SHA `0cc252d17664fb6352b8a1bc954358031642efc8`.

## Reproduced failure

The previous full backend test attempt ended after 848 passing, 8 skipped and 12 failed tests. All 12 early failures were in Snapback-centered offline admission, simulation and trade journal end-to-end tests. The Box host reported `NTPSynchronized=no`. A targeted `test_snapback_evidence_runner_is_not_running_and_does_not_gate_live_entries` failed with `clock_unsynchronised` even though the harness uses a frozen, synthetic market session and fake Kite exchange. The live system was right to refuse an undisciplined host clock; the offline scenario was wrong to depend on the CI host's real NTP state.

## Test-only remediation

`tests/e2e_live/machine.py` installs a per-synthetic-process `_ntp_synchronised` test double returning a positive, labeled synthetic clock-discipline result. `snapback_health` environment cache is cleared on each synthetic process initialization. This does not modify `snapback_preflight.check_clock`, live `snapback_health.live_environment_errors`, production source files or the actual synchronization prerequisite. It does not set `STERLING_SKIP_CLOCK_CHECK`, which is an unsafe family-mode escape hatch and is deliberately not used.

The fake machine already runs in disposable state directories, with its offline Kite REST transport and explicit socket-egress guard. It deliberately exercises live-shaped application routes but can neither obtain production credentials nor execute on a real broker. Clock-failure tests elsewhere continue to prove unsynchronised clocks block trading.

## Validation and release boundary

- Before change: targeted offline Snapback E2E read `clock_unsynchronised` and failed.
- After change: targeted evidence-runner gate and family-bound/unbound Snapback entry tests passed 3/3. All 40 preexisting cases in the Snapback admission, simulation, and trade-journal E2E modules passed (178 non-failing warnings). A NEW 41st negative E2E test deliberately sets the probe to unsynchronised and verifies Snapback rejects entry and the offline broker sees zero orders; this new case passed independently.
- The scoped offline Gamma CI now runs all 41 E2E cases, using the fake exchange and disposable state, on every change to this harness or the impacted E2E modules. Exact-commit GitHub results must be verified after publishing. The historical full PR #15 backend CI is not made green by these E2E-local changes.
- This is a **test-environment reliability fix**, not proof that a real workstation's clock is disciplined or that production Snapback is executable.

Release posture: draft, unmerged, undeployed, live broker routing unauthorized, independent approval 0/12, production NO-GO.
