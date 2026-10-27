# Implementation and acceptance — 26 October 2026

The complete requested specification is preserved in
[2026-10-26-implementation-specification.md](2026-10-26-implementation-specification.md).
[implementation-2026-10-26.json](implementation-2026-10-26.json) inventories every
FR, epic and release gate. UNKNOWN means acceptance has not been established,
including where substantial software already exists. The initial local checkout
was clean at `416bf6b4c9c5f98e1efbd3d9d683be5834fcedd0`, which differs from the
historical remote SHA in the proposal.

## Implemented source changes

* Real exposure increases require G0–G9 observations on the exact source/runtime
  SHA. G7 is required independently for SuperTrend, Snapback and Gamma, with
  exact account, deployment, mode, strategy version and server configuration.
  Price Rules remain individually armed manual plans; they do not acquire an
  algorithmic profitability claim. Archived engines cannot acquire new live
  permission through this authority. Existing safety and account checks still
  apply. Missing, expired, revoked or unreadable acceptance blocks entries.
* Release checks run before journal reservation and again at the transport send
  boundary, including after limiter/backoff waits. Durable checks run on a worker
  thread. Paper transports do not acquire a live permission requirement. Exits
  and protection retain their existing paths. A server-verified missing Snapback
  hedge for a protected, durable owned plan remains a risk-reducing repair.
  The journal retains server-derived release, mode, configuration and plan
  identity; changing source or risk settings during a pending submission refuses
  the send. Initial exposure defaults to one lot; nonfinite prices, capital and
  risk limits fail closed.
  Price Rules also re-read the owned submitting rule, revision and global entry
  toggle before reservation and after transport waits, so turning entries OFF
  prevents a pending entry from reaching the broker.
* Acceptance claims are append-only SQLite transactions with a hash chain,
  content hashes, distinct observer/reviewer, UTC dates, exact identities and
  source types. VERIFIED receipts are sealed inside the database and survive
  source-log deletion or isolated database restoration. Host/broker/operator and
  economic approvals require explicit expiration. The register is included
  in full-backup coverage through the SQLite online backup API. The register is
  tamper-evident, not proof against an administrator replacing/truncating the
  entire file; retain encrypted off-host backups and externally anchored hashes.
* G7 validates the independently reviewed report contract: preregistration,
  non-overlapping positive OOS windows, clustered confidence bounds, predefined
  sample adequacy and risk limits, adverse execution tests, real forward quotes,
  dataset/configuration identity, and explicit data-quality attestations.
  Validation of a report is not independent validation of its underlying data.
* CI success requires a real job id, assigned nonzero runner, executed steps,
  matching head SHA and completed successful job. Legacy green records without
  execution proof become UNKNOWN. Synchronization decodes multiline JSON and
  reads Actions job details; reruns invalidate prior green records immediately.
* Snapback research coverage checks every requested exchange session against its
  full timestamp grid. Matching truncated tapes, entirely absent sessions,
  duplicates, invalid OHLC, incomplete current sessions and unknown calendars
  cannot count as complete days. Retrospectively selected current contracts and
  modeled swing prices remain explicitly ineligible for promotion.
* The Operations screen reports BROKER, RISK, PROTECTION and AUTOMATION, separates
  R1/R2/R3, shows observation-only posture and exposes blocking gate codes.
  Unreachable or old payloads show UNKNOWN. A local stop id is never presented
  as independently confirmed broker protection.

## Read-only baseline

Run with a **new** output directory; prior evidence is never overwritten:

```bash
python3 scripts/implementation_audit.py baseline --output /secure/evidence/new-snapshot
```

This command does not import application runtime configuration, read `.env`,
connect to a broker, modify safety state or restart a service. It records source
identity, a tracked-tree fingerprint, dirty files, local upstream divergence,
host disk/CPU measurements, the full acceptance inventory, `baseline.json` and
`blocking-gates.md`. It does not infer production runtime, account exposure or
owner approval from the local checkout. A missing broker observation is UNKNOWN.

## Record acceptance

Recording acceptance is a privileged local operator action. There is no HTTP
endpoint that accepts user-supplied eligibility or automatically enables entries.
Prepare a reviewed JSON claim with the `AcceptanceClaim` fields and a real local
receipt. Then use the deployment's Python and an explicit persistent register:

```bash
backend/.venv/bin/python scripts/implementation_audit.py record /secure/evidence/claim.json \
  --register /persistent/sterling/data/manifests/acceptance.sqlite3
backend/.venv/bin/python scripts/implementation_audit.py report \
  --register /persistent/sterling/data/manifests/acceptance.sqlite3
```

For live runtime the default register is
`$STERLING_STATE_ROOT/data/manifests/acceptance.sqlite3`. Claims name
`source_sha`, `runtime_release_sha`, `observed_at`, `source_type`, `result`,
`test_command`, `log_hash`, `evidence_path`, `independent_reviewer`, `observer`,
`dataset_id`, `deployment_id`, `account_id`, `strategy_id`, `mode`,
`strategy_version`, `config_hash`, `expires_at` and `detail`.
The server computes mode and configuration identity; request payload fields
cannot override them. The strategy version is the immutable source SHA.
Gamma retains its native hold contract (`gamma_native`); no horizon is guessed.
G4, G7 and G9 are specific to each strategy/mode/configuration. A controlled
SuperTrend receipt cannot authorize a Gamma entry. The configuration hash also
covers shared risk and the live-minimum envelope, so approvals cannot silently
survive a change to those limits.

Append FAILED/BLOCKED/UNKNOWN to revoke an approval in the same scope. A prior
VERIFIED record is never edited or deleted. On another deployment or account,
host/broker/operator gates need new evidence. Copying a register cannot authorize
a different account, SHA, mode or configuration. Source/CI facts may be reused
only on the same immutable SHA. The master entry switch remains separate and OFF
by default. Recording claims does not change it.

### Separately authorized initial certification

G9 requires actual lifecycle receipts, so it cannot be the prerequisite for its
own first controlled test. General automation remains blocked. After G0–G6 and
G8 pass, an owner and independent reviewer can explicitly record a
`CONTROLLED_LIVE_AUTHORIZATION` claim (source `OPERATOR`) for one precise existing
intent. Its sealed JSON receipt must include `schema_version: 1`,
`purpose: "ONE_CONTROLLED_CERTIFICATION_ORDER"`, `owner_authorized: true`, and the
exact `uid`, application `account_id`, `strategy_id`, `generation_id`, `signal_id`,
`exchange`, `symbol`, `side`, integer `quantity`, `order_type`, `price`,
`trigger_price` and `product`, plus finite positive `max_capital_required` and
`max_order_notional` limits. The initial certification order must be a LIMIT
order, fit its notional ceiling, and fit the independently measured risk approval
capital. The claim must match the
server's release/configuration identity and expire within 15 minutes of its
observation. The configured initial limit must be exactly one lot, independently
checked against broker metadata by canonical admission.

This grants only that exact journal reservation; changing any key or quantity
refuses. Ambiguous outcomes still cannot be resubmitted. It does not change
G7/G9, certify R2/R3, turn on a master switch, or grant any other signal permission.
The operator then observes actual fills, protection and exit and records G9
receipts. Negative economic evidence continues to block autonomous origination.
No such authorization has been created or exercised by this implementation.

## Local validation

The final frozen-source backend run passed **11,951 tests**, with **30 skipped**
across all 747 test files. Its source fingerprint was unchanged throughout the
run. The frontend passed **2,413 tests** and its TypeScript/production build.
Mobile passed **9 tests**, TypeScript, Expo dependency alignment and the offline
Android/iOS/web export. Python compilation, eight shell syntax checks and the
diff whitespace check passed.

[Validation results, limitations and copied receipts](../evidence/implementation-20261026/validation.md)
include the complete backend file lists, individual skip reasons, source
fingerprint and SHA-256 log hashes. Missing historical real-data fixtures and
retired-strategy tests are skipped rather than represented as successful proof.
The checkout remains uncommitted; local results do not certify an immutable
release SHA, satisfy GitHub CI, or authorize live entries. All 44 requirement,
epic and gate acceptance statuses remain UNKNOWN pending independent acceptance;
the dependency security gate is explicitly BLOCKED.

## Outstanding acceptance by epic

### Dependency security

The frontend lock now pins Vite 8.3.4; its npm audit reports zero known findings.
The Python lock updates Certifi to 2026.10.19; `pip-audit --disable-pip --no-deps`
reports zero known findings for that lock. The existing host environment was not
upgraded or restarted; the patched Certifi package was tested from an isolated
dependency directory. A source lock audit does not certify a running host.

Mobile dependencies were updated within Expo SDK 58 and aligned to its expected
patches. Type checking, API tests and `expo install --check` pass. Five high npm
audit findings remain, including propagated dependency findings from
[node-forge](https://github.com/advisories/GHSA-86w9-cpqp-85rv) and
[Zod](https://github.com/advisories/GHSA-mrhx-c7xc-m3w5). The node-forge advisory
lists no patched version; Expo's toolchain still depends on Zod 3. A forced SDK
downgrade or an incompatible dependency override has not been applied. Security
acceptance remains BLOCKED until compatible remediation or independently
authorized scope-specific mitigation exists; no exception has been granted.

| Epics | Work and evidence still needed |
|---|---|
| E00 | Production host/process/source and read-only broker inventory; actual safety flags; encrypted baseline backup |
| E01 | Current candidate committed and all required remote jobs actually executed; account/runner eligibility resolved by GitHub account owner |
| E02 | Representative moving-market load benchmark, declared numeric SLO, repeated host restart/reconnect drills |
| E03 | Owner-approved static IPv4, permanent DNS/TLS, current callback, fresh account binding and console proof |
| E04 | Complete exact-candidate cross-lane lifecycle/security/failure matrix and independent safety acceptance |
| E05 | Encrypted off-host full backup, physical isolated restore, account-owner and delegate handoff |
| E06 | Immutable date-effective datasets, complete option/OI history, causal universe and full provider attribution |
| E07 | Frozen SuperTrend parity/lifecycle acceptance and a new independently validated economic hypothesis; recorded October sweep remains rejected |
| E08 | Independent real-option/hedge OOS coverage, restart and hedge lifecycle acceptance; research filtering is not economic acceptance |
| E09 | Production watchlist recovery/load/coverage benchmark and adequate independent OOS evidence |
| E10 | Full Price Rules execution/mobile acceptance; EMA remains experimental and outside live originators |
| E11 | Frozen hypothesis/trial registry, complete dataset, independent OOS/forward analysis and broker-attributed realized economics |
| E12 | Actual operator-device Telegram receipts, alert outage/retry observation and measured error budgets |
| E13 | Desktop/mobile human acceptance, emergency/handoff and accessibility walkthrough |
| E14 | Separate IPTV/Radio experience backlog and device acceptance; do not tie media work to broker exit uptime |
| E15 | Immutable candidate, owner sign-off, separately authorized broker lifecycle receipts and restricted monitored launch |

These are unresolved acceptance requirements, not PASS statements. Repository
tests cannot provide static-IP console approval, physical off-host restoration,
phone receipt, independent human sign-off, or future trading performance. No
live order, deployment restart, credential change or live-entry enablement is
authorized by a successful software test.
