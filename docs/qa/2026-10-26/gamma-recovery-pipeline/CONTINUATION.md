# Gamma recovery: bounded history-read overlap

Baseline: `a0d72888a1ae9d09c1b0cb562a868c845d202d19`.

## Observed gap

On 26 October, the live stored scheduled-history recovery completed with 116 watched contracts and an empty issue, taking 242.65 seconds. The simulation record contained 93 watched contracts, an empty issue and 52.93 seconds. The live result exceeds the three-minute recovery target. These storage snapshots alone do not certify two consecutive trading days, a coordinated midday restart or full-day simulation. No QA scan, order or setting mutation produced these observations.

The selected-option warmup loop awaited each option history response before beginning the next option. That serial network wait is avoidable while retaining request-start pacing. The observation does not attribute every second of live recovery to this loop or promise a specific post-change live duration.

## Exact change

`backend/app/services/gamma_move_scanner.py` reads selected option histories in bounded batches of two. All uncached attempts and retries still pass through the same existing `Pacer(2.6)` instance. Each batch finishes before engine replay or the next batch. Results are consumed in selection order, irrespective of response completion order. Underlying history remains one read per underlying, after sufficient option warmup. Engine construction, original rules, replay inputs, daily resets, additive retention, selection slots, signal timing semantics, account-scoped cache validation and partial-history refusal are retained.

No concurrent engine mutation or new order path is introduced. Cancelling the batch cancels its pending reads; partially warmed state is not published. At most two selected option reads are in flight, with the existing bounded request-start rate. The underlying warmup path remains sequential. Production strategy settings, broker accounts, AUTO modes, live execution and notification preferences are untouched by offline tests.

## Regression proof

Seven new isolated cases cover bounded overlap and ordered replay, one shared pacer/underlying read, partial option failure, shared-underlying failure deduplication, insufficient warmup, real request-start pacing through the unchanged uncached adapter and cancellation without partial publication. Transport is stubbed; no real broker calls occur. Baseline: three failures and four passing controls. Fixed focused group: 37 passes, including existing scanner, day-cache, selection-cache and untraded-strike recovery cases.

Full make verify passed: 11825 passed, 30 skipped in 1242.30s (0:20:42); TypeScript and VERIFY OK. Baseline and head failure sets are empty, and the exact 30-skip set is unchanged. Frozen source and log hashes are recorded in validation.json. Frontend source is unchanged from the verified 2,391-test tree; no new frontend test run is claimed. TypeScript is rerun by make verify.

## Remaining acceptance

This removes serial selected-option response waiting. It does not close the live under-three-minute gate until the released build is observed under real conditions. Full same-release market load, two actual consecutive Gamma recovery days, coordinated midday recovery and full-day simulation remain open. The 26 October morning already contains two released revisions before this patch. Zero Snapback saved rows cannot prove populated restoration. The frozen prospective Snapback preflight remains HALTED due retained configuration/evidence checks; those checks are not weakened. Authoritative research and hosted-CI billing gates remain open. Earlier user-confirmed Positions/Signals phone checks remain passed.
