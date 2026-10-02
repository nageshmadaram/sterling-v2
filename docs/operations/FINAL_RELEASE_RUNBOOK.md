# Final release runbook

This sequence turns one exact, tested commit into Production Shadow. It does **not**
authorise real-money entries. Capital is earned later, lane by lane, through the
forward-evidence promotion policy.

**Central rule: engineering readiness authorises observation, not capital.**

## 0. Freeze development, not the manifest

Work only from the final candidate commit.

```bash
git status --porcelain          # must print nothing
git rev-parse HEAD              # record the full 40-character SHA
scripts/sterlingctl doctor      # inventory blockers; it is NOT expected to pass yet
```

At this point doctor may still report release certification/manifest blockers.
Do not turn those into manual PASS values. A code change after any later evidence
step creates a new candidate SHA and invalidates SHA-bound evidence.

## 1. Prove the code on the exact candidate SHA

Run the complete local suites and keep their output as an artifact. GitHub CI must
also run against this exact SHA.

```bash
cd backend && PYTHONWARNINGS=ignore .venv/bin/python -m pytest -q
cd ../frontend && npm ci && npx tsc --noEmit && npx vitest run --coverage=false
```

Run the documented Playwright projects as well. Then record remote CI from GitHub;
do not attest it by hand:

```bash
scripts/sterlingctl certify ci-sync
scripts/sterlingctl certify ci
```

All required contexts must be present and successful for this SHA.

## 2. Prepare the production environment without changing code

Complete the operational prerequisites that do not alter the Git SHA:

- choose one explicit persistent state root and make runtime, CLI and backup
  tooling resolve the same files;
- if upgrading an existing installation, take and restore-check a backup before
  moving any state; never repoint a service at a new empty database and call it flat;
- set a stable `STERLING_DEPLOYMENT_ID` for this installation;
- production secret migration and restart under the production keys;
- fresh Kite login;
- fresh broker-backed account binding verification;
- static-egress configuration and broker allowlist;
- deployment egress observation;
- current broker exposure/reconciliation;
- current backup plus isolated restore-check.

Typical commands:

```bash
scripts/sterlingctl secrets plan
# then, when ready:
scripts/sterlingctl secrets migrate --to-env-file /secure/path/sterling.env

# After the real Kite session is authenticated, declare the intended shadow
# account once. "bind verify" performs fresh READ-ONLY profile, positions,
# orders and protection reads and refuses on identity/deployment ambiguity.
scripts/sterlingctl bind declare zerodha "ACCOUNT HOLDER" CLIENT_ID shadow DEPLOYMENT_ID EGRESS_PROFILE
scripts/sterlingctl bind verify VERIFIED_BINDING_ID
scripts/sterlingctl bind activate VERIFIED_BINDING_ID
scripts/sterlingctl bindings

scripts/sterlingctl exposure
scripts/sterlingctl reconcile
scripts/sterlingctl egress record <PUBLIC_STATIC_IP> [ROUTER_GENERATION]
scripts/sterlingctl egress
scripts/sterlingctl backup
scripts/sterlingctl restore-check
```

An unreadable broker answer is UNKNOWN, never flat. Do not delete local test
artifacts or claim zero exposure until broker truth is readable and reconciled.

## 3. Live Kite acceptance on this exact SHA

During a moving market, run the release acceptance harness on the candidate SHA.
The acceptance must prove live market data, FULL depth, forced disconnect,
reconnect, fresh post-reconnect ticks, persistence/reload and writer health.
The harness must never place a real strategy entry order.

Record only the genuinely human-observed gates:

```bash
scripts/sterlingctl certify attest test_suites PASS "Operator" "logs/suites.txt"
scripts/sterlingctl certify attest kite_live_acceptance PASS "Operator" "logs/acceptance.json"
scripts/sterlingctl certify attest reconnect PASS "Operator" "logs/acceptance.json"
scripts/sterlingctl certify attest persistence PASS "Operator" "logs/acceptance.json"
```

Do **not** attest `remote_ci`, `failure_drills` or `open_exposure` in one
line. Those gates are derived and the CLI refuses blanket attestation.

## 4. Record all twelve failure drills individually

Each drill is a human-observed record bound to the exact runtime SHA.

```bash
scripts/sterlingctl drills
scripts/sterlingctl drills record <DRILL> PASS "Operator" "what actually happened" "evidence/ref"
```

Repeat for every declared drill. Then:

```bash
scripts/sterlingctl drills
```

must report all twelve PASS. A missing drill remains UNKNOWN.

## 5. Refresh broker truth immediately before freeze

```bash
scripts/sterlingctl exposure
scripts/sterlingctl reconcile
```

The derived `open_exposure` gate must report zero/flat from readable current
broker truth. Never replace this with a human attestation.

## 6. Choose and create the local immutable release tag

A release tag does not change the commit SHA. Create it locally on the exact clean
candidate **before** manifest freeze, then export the same name:

```bash
SHA="$(git rev-parse HEAD)"
TAG="sterling-family-runtime-1.0"   # choose the deliberate release name
git tag -a "$TAG" "$SHA" -m "Sterling release $TAG"
export STERLING_RELEASE_TAG="$TAG"
```

Do not push the tag yet. If a code defect now forces a new commit, abandon this
unpublished candidate tag and choose a new release tag for the new SHA. Never move
a tag that has been pushed.

## 7. Freeze only after every pre-freeze gate passes

```bash
scripts/sterlingctl certify
scripts/sterlingctl freeze
scripts/sterlingctl verify
scripts/sterlingctl certify
```

`freeze` itself refuses:

- a dirty worktree;
- an unset tag;
- a tag that does not point at the exact runtime SHA;
- any pre-freeze FAIL or UNKNOWN gate.

After the manifest exists, `verify` must report no drift and `certify` must
print `RELEASE_READY`.

## 8. Push the already-proven tag and deploy only that identity

```bash
git push origin "$TAG"
```

Deploy backend and frontend from that exact tag/SHA. Set
`STERLING_RELEASE_TAG=$TAG` on the deployment. For Docker builds, also pass the
exact checkout identity as immutable build inputs:

```bash
export STERLING_BUILD_SHA="$SHA"
export STERLING_BUILD_TAG="$TAG"
export STERLING_EXPECTED_BUILD_SHA="$SHA"
docker compose build
```

The runtime recomputes the image's source digest; these variables cannot make
different bytes certify as this SHA. Do not rebuild from an unrelated working tree.

## 9. Require final doctor PASS

After the exact release is running with production secrets, current broker login,
deployment identity, static egress, market feed and frozen manifest:

```bash
scripts/sterlingctl doctor
```

Doctor must exit 0. FAIL and UNKNOWN are both blockers.

## 10. Start the authoritative sample cleanly

```bash
scripts/sterlingctl evidence-start
```

Require exact release tag/SHA, zero unresolved exposure, no identity drift, and a
fresh authoritative sample. Historical rows remain historical; they are never
re-attributed to this release.

## 11. Start Production Shadow

```bash
scripts/sterlingctl shadow
scripts/sterlingctl permission
```

Production Shadow uses real account state, market data, contracts, depth and
margin, but real strategy entry transport stays disabled.

The invariant remains:

```text
LIVE_EXECUTION_ENABLED = False
```

No global "Go Live" action is part of this release.

## 12. Real money remains a separate evidence decision

A lane may reach LIVE_MINIMUM only after its exact identity satisfies the declared
forward-evidence requirements, including the minimum session/trade sample,
day-clustered confidence bound, cost stress, outlier removal, drawdown/evidence
coverage, zero unresolved exposure, shadow execution quality and exact identity.

Until then:

```text
ELIGIBLE_LANES: NONE
REAL ENTRY ORDERS: DISABLED
```
