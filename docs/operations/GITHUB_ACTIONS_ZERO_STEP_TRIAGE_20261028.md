# GitHub Actions: jobs fail before runner startup — 2026-10-28

## Evidence observed

- Example workflow: CI, run `40666201702`, PR #283, commit
  `0d4ab6cb895e252841a9ce64ab634c364388b8ca`.
- The GitHub Actions REST run reports `completed/failure` within roughly eight seconds.
  Jobs for backend tests, frontend type checking, mobile checks, and Docker smoke
  all show `runner_name: ""`, `runner_group_name: ""`, and `steps: []`.
- The workflow's timing API reports **0 billed Ubuntu milliseconds** across
  all ten jobs. This is not application test execution.
- GitHub check-run results show two annotations on multiple failed jobs,
  but the available connector cannot read their annotation details. Attempted
  job-log downloads returned `BlobNotFound`; they are not test logs.
- The repo's CI and regression workflows request GitHub-hosted
  `ubuntu-latest` runners. No actual runner assignment has been observed.

**Conclusion:** A passing regression or release gate is **not established**.
Neither this evidence nor a GitHub merge certifies Snapback profitability,
broker order protection, Telegram delivery, or live trading.

## What an authorized repository owner must check

1. Open [the affected CI run](https://github.com/nageshmadaram/sterling/actions/runs/40666201702)
   and a failing job, then read the two job/check annotations and any
   GitHub-hosted runner allocation error. Capture the precise messages
   and timestamp; do not infer them from empty logs.
2. In GitHub repository **Settings → Actions → General**, verify workflow and
   runner permissions. Check the organization/account's Actions billing,
   payment status, included usage, spending limits, and any current platform
   or policy restrictions. A blank runner name alone does **not** prove a
   billing problem; it only shows no runner was assigned.
3. Verify that the workflow file requests an eligible runner and that
   repository actions are allowed. Review a fresh *minimal* workflow only
   after the reported account/policy error has been resolved. Do not disable
   the existing safety gates to get a green badge.
4. Re-run the real CI, Regression Gate and Snapback Release Gate on the **exact
   merged SHA**. Require an assigned runner, executed step timestamps, logs,
   test counts and exit codes, frontend build and regression receipts.
5. Keep the G1/CI and host-deployment certification blockers open until
   those receipts exist. Host tests are separately required and must not be
   mislabeled as GitHub CI results.

## Safe operational boundary

Do not change Kite/broker secrets, restart live order execution, alter
risk-admission defaults or delete the Zenbook's local evidence in an
attempt to repair GitHub Actions. CI runner diagnostics are an account
control-plane task. GitHub cannot attest to a local running tunnel or phone
from source code alone.
