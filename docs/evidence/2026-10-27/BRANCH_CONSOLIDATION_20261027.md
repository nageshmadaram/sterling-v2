# Sterling GitHub branch consolidation (2026-10-27)

## Intent and scope

Operator requested preserving ALL previously committed work, synchronizing
`main` and `dev`, and retaining only those two branch names. This is a
**source-control consolidation**, not operational acceptance, remote deployment,
broker authorization or strategy-profit certification.

Pre-consolidation refs:
- main and dev: `8a54d2ec864e02e7fbab64dc93c055f9115f28f5`
- codex/implementation-spec-20261026: `c8d9e2f1c3284dfbb3641ba328ae9d914c92f47a`
- Last common ancestor: `2d769aad223b730247ffc98a9c70e79debf08be6`.

The two development histories diverged in commit topology. A PR merge reported
conflicts. A three-tree **Git blob SHA** audit was performed using recursive
repository trees for the ancestor, current dev and implementation branch.
Results, by file path:
- 3,186 files unchanged in all three versions.
- 79 files changed to byte-identical contents on both branches.
- 26 tracked files changed only on dev, plus 23 added only on dev.
- 4 files changed on both branches and differed at their tips.
- 7 files independently added on both branches and differed at their tips.
- **Zero** files had side-only changes relative to the common ancestor.
- **Zero** side-only added files; **zero** side-only deletions.

Therefore the implementation branch contributes **commit provenance**,
but no unique file-level paths or blob content requiring a replacement in
the final tree. Its 11 divergent file versions were explicitly inspected
with line-level content comparisons; their dev replacements contain later
fixes. Merge resolution is **choose dev for these files**, and retain
the branch head as a second parent of the merge commit. This choice does
not certify that all code is functionally correct.

## Eleven files with divergent content

Use the current dev blob for every path below:

1. `backend/app/engines/snapback/intraday.py`: dev adds option
   close-mark and adverse low-mark risk measurement.
2. `backend/app/services/snapback_live_exits.py`: dev carries subsequent
   broker exit ACK, cancellation identity, partial-fill and recovery fences.
   Older side code could restore stops after a zero broker inventory read and
   silently clip fills; do not reintroduce those behaviors.
3. `backend/app/services/snapback_live_positions.py`: dev adds durable
   cumulative booked-order quantity records for exit reconciliation.
4. `backend/tests/engines/snapback/test_intraday.py`: dev adds
   adverse-low / close-mark drawdown regressions.
5. `backend/app/engines/snapback/challenger.py`: dev accepts finite NumPy
   real scalars, rejects invalid numeric inputs and enforces typed horizons.
6. `backend/app/engines/snapback/research_reporting.py`: dev adds
   option mark-to-market reporting and validation.
7. `backend/tests/engines/snapback/test_challenger_cost_feasible.py`:
   dev adds NumPy and malformed-candle regressions and corrects timestamps.
8. `backend/tests/engines/snapback/test_research_reporting.py`:
   dev uses consistent explicit zero-trade fixtures for strict report checks.
9. `backend/tests/unit/test_snapback_exit_resolution_boundary.py`:
   dev adds extensive broker-exit identity and safety tests.
10. `docs/operations/SNAPBACK_REPAIR_CHECKPOINT_20261027.md`:
    dev includes additional risk/protection checkpoints.
11. `scripts/snapback_offline_acceptance.sh`: dev includes later
    focused regression suites and permits development review workflows.

Original side commits remain reachable as the second parent of the
consolidation merge and in Git history even when the old branch name
is later deleted. Historical code is retained **as history**, not restored
as active implementation.

## What this merge proves

- All source commits from both remote branches are ancestors of the final
  merge commit, with current `dev` source contents as the conflict resolution.
- The explicit audit file records the resolution; no force push is needed.
- The merge does not run, deploy, restart or reconfigure Sterling on localhost.
- No live broker order, stop, hedge or market execution was performed.
- Full frontend/backend tests on this exact merge SHA, CI, disaster restore,
  account-bound broker receipts, and authoritative prospective after-cost
  profitability remain **UNVERIFIED/NO-GO**.
- New real-money entries must stay disabled; exits and protection continue
  to follow the runtime's independently guarded controls.

Remote branch *name deletion* and inspection/cleanup of **local uncommitted
work** require access that GitHub-only source-commit operations do not provide.
Those must not be represented as completed until verified on the relevant host.
