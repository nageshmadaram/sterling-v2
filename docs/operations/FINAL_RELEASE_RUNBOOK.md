# Release and go-live runbook

This sequence turns one exact, tested commit into the running live system. Sterling
trades real money, so the order matters. Release certification, the manifest freeze
and the failure-drill table are **records of what was deployed**: they are
reported, and they never block trading. The safety facts (safe mode, recovery
state, unresolved exposure, the account binding, fresh broker capital, production
secrets, backups, clock and disk) do block, and nothing here waives them.

## 0. Work from the final commit

```bash
git status --porcelain          # must print nothing
git rev-parse HEAD              # record the full 40-character SHA
scripts/sterlingctl doctor      # inventory; read every FAIL
```

A code change after any later step creates a new candidate SHA.

## 1. Prove the code on the exact SHA

```bash
cd backend && PYTHONWARNINGS=ignore .venv/bin/python -m pytest -q
cd ../frontend && npm ci && npx tsc --noEmit && npx vitest run --coverage=false
```

Prefer GitHub CI on this exact SHA. `scripts/sterlingctl certify ci-sync` records
its result for the manifest.

## 2. Prepare the production environment without changing code

- choose one explicit persistent state root and make runtime, CLI and backup tooling
  resolve the same files;
- if upgrading an existing installation, take and restore-check a backup before
  moving any state; never repoint a service at a new empty database;
- set a stable `STERLING_DEPLOYMENT_ID`, production secrets and
  `STERLING_LIVE_EXECUTION=true`;
- first-day caps: `STERLING_LIVE_MINIMUM_MAX_LOTS=1` and a loss budget
  ([../../DEPLOYMENT.md](../../DEPLOYMENT.md));
- fresh Kite login, then the broker account binding (declare, verify, activate:
  [BROKER_ACCOUNT_HANDOFF.md](BROKER_ACCOUNT_HANDOFF.md)) and the static egress address:

```bash
scripts/sterlingctl secrets plan
scripts/sterlingctl bindings
scripts/sterlingctl exposure
scripts/sterlingctl reconcile
scripts/sterlingctl egress record <PUBLIC_STATIC_IP> [ROUTER_GENERATION]
scripts/sterlingctl backup
scripts/sterlingctl restore-check
```

An unreadable broker answer is UNKNOWN, never flat. Do not claim zero exposure
until broker truth is readable and reconciled.

## 3. Live Kite acceptance (read-only)

During a moving market:

```bash
scripts/sterlingctl acceptance NIFTY 120
```

It reads the market feed and broker state, forces a disconnect and reconnect, and
checks persistence. It never places an order, whatever the kill switch says.

## 4. Rehearse the failures

Run the drills in [../operator/FAILURE_DRILLS.md](../operator/FAILURE_DRILLS.md) and
record each one:

```bash
scripts/sterlingctl drills
scripts/sterlingctl drills record <DRILL> PASS "Operator" "what actually happened" "evidence/ref"
```

## 5. Refresh broker truth immediately before the release

```bash
scripts/sterlingctl exposure
scripts/sterlingctl reconcile
```

Zero or flat must come from readable, current broker truth.

## 6. Choose and create the local immutable release tag

```bash
SHA="$(git rev-parse HEAD)"
TAG="sterling-live-1.0"   # choose the deliberate release name
git tag -a "$TAG" "$SHA" -m "Sterling release $TAG"
export STERLING_RELEASE_TAG="$TAG"
```

Do not push the tag yet. If a defect forces a new commit, abandon this unpublished
tag and choose a new one. Never move a tag that has been pushed.

## 7. Freeze only after every pre-freeze gate passes

Freezing records the exact build and lane identities so later drift is detectable.
`freeze` insists on what makes the manifest name an exact build: a clean tree, a
set tag, and a tag that points at the runtime SHA. Any other gate that is not
passing is listed as advisory, never blocking. Read that list before you go on.

```bash
scripts/sterlingctl freeze
```

The pre-freeze backup deliberately cannot contain a release manifest that did not
exist yet. Therefore **do not expect RELEASE_READY immediately after freeze**.
Seal the newly frozen state:

```bash
scripts/sterlingctl backup
scripts/sterlingctl restore-check
scripts/sterlingctl verify
scripts/sterlingctl certify
```

The final `verify` must report no drift. The final `certify` prints the full gate
table; `RELEASE_READY` means every gate passed, and the post-freeze backup is the
one that contains the frozen manifest.

## 8. Push the already-proven tag and deploy only that identity

```bash
git push origin "$TAG"
```

Deploy backend and frontend from that exact tag/SHA, with
`STERLING_RELEASE_TAG=$TAG`. For Docker builds, also pass the exact checkout
identity as build inputs:

```bash
export STERLING_BUILD_SHA="$SHA"
export STERLING_BUILD_TAG="$TAG"
export STERLING_EXPECTED_BUILD_SHA="$SHA"
docker compose build
```

The runtime recomputes the image's source digest; these variables cannot make
different bytes report as this SHA.

## 9. Doctor

After the release is running with production secrets, a current broker login,
deployment identity, static egress and market feed:

```bash
scripts/sterlingctl doctor
```

Fix every broker, safety-state, storage and secret failure. Items marked waived are
informational.

## 10. Start

```bash
scripts/sterlingctl start
```

It stops at the first step that is not a pass. When it completes, new entries are
allowed: Supertrend, Snapback and Gamma Unwind may originate, within the day-one
caps.

## 11. First live session

Follow [../operator/START_HERE.md](../operator/START_HERE.md) §3: one lot, watch the
first entry of each strategy in the Kite app, keep the kill switch within reach.

## 12. Stopping and rolling back

- Stop new entries: `STERLING_LIVE_EXECUTION=0` and restart, or
  `scripts/sterlingctl safe on "reason"`. Exits and protection keep working.
- Roll back by deploying the previous tag. Reconcile against the broker before
  allowing new entries; the order journal and positions live in the database, not
  in the code.
