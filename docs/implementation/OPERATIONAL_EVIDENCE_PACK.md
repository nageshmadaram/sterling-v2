# Sterling V2 — engineering evidence pack (pre-production)

**NO-GO: technical verification is not production authorization.**

This index is the review package required by
[the finish specification](2026-11-01-openalgo-upgrade-finish-spec.md).
It points to implementation and repeatable evidence but does not claim
operational sign-off.

## Sources and exact-SHA evidence

| Evidence category | Technical artifact or command | What it does **not** prove |
| --- | --- | --- |
| Static broker-order and API route inventory | `scripts/preproduction_inventory.py --strict`, `.github/workflows/preproduction-source-inventory.yml` | Runtime routing, reflective calls, account entitlement, broker-side acceptance |
| Compatibility authorization | `backend/tests/unit/test_openalgo_compat_policy.py`, `test_openalgo_compat.py` | Full OpenAlgo parity or order submission authority |
| Canonical fill/reduction | `backend/tests/services/test_canonical_fill_reconciliation.py`, product integrity/Snapback workflows | Live exchange fills, restart/witnessed reconciliation, order settlement certification |
| Account and role policy | `backend/tests/test_workspace_access.py`, `feature_access.py`, account policy | Production MFA, token revocation on real devices, correct live account ownership |
| Database migration/recovery | disposable PG workflow, `alembic_guard.py`, SQLite restore checks | Operational migration permit or independent off-host restore signoff |
| Platform API and UI discovery | `.github/workflows/openalgo-platform-contracts.yml` | Live Python/Flow host or trading/scalping routes |
| Twelve proposed release controls | `docs/ops/PREPRODUCTION_CONTROLS.json` and `PREPRODUCTION_EVIDENCE_REGISTRY.json` | Any owner assignment or signed approval |
| Source-bound review metadata | `preproduction-inventory-<commit-sha>` Actions artifact: `finish-review.json` | Certified deployment; always reports 0/12 and NO_GO |

### Reproduce the non-authorizing manifest

In an isolated review checkout, after confirming HEAD:
```sh
python scripts/validate_preproduction_controls.py
python -m unittest discover -s scripts/tests -p 'test_preproduction_finish_review.py'
python -m scripts.preproduction_finish_review \
  --head-sha "<exact 40-character reviewed Git commit>" \
  --output "/tmp/sterling-v2-review/finish-review.json"
```

The CI workflow provides the checked-out `github.sha` and run ID and
uploads the manifest with its other static inventory. It records SHA-256
hashes of the proposed source registries. Those hashes show what the
CI reviewed; they do not certify external evidence. An arbitrary CLI
`--head-sha` is a caller assertion, not independent Git verification.

## Pending independent evidence / failure scenarios

- [ ] Exact account binding and session-owner correlation on approved isolated infrastructure
- [ ] Viewer/operator/admin/revoked/Simulation route matrix with live restart and reload
- [ ] Broker ACK without fill; order ID mismatch; duplicated/contradictory trades
- [ ] Crash during submit, partially reconciled reduce, timeout, duplicate intent, reconnect
- [ ] Signed provider postback and protection/exit ownership evidence
- [ ] Isolated SQLite and PG restore with content-hash parity and rollback witness
- [ ] Audited admin reason codes with before/after records and sanitized log review
- [ ] Alert delivery/retry/receipt and independent operations escalation review
- [ ] Separate ownership, reviewer and authority sign-off for all twelve controls

## Release gate

1. Check exact running artifact SHA against CI and source inventory.
2. Require all **current SHA** workflows to finish successfully; historical
   passing SHA results are not transferable.
3. Verify isolation tests, source bypass inventory, and unknown fill protection.
4. Collect human-owned external evidence and independently review each control.
5. Record separate signed GO/NO-GO decision with environment, account scope,
   date and validity window. A software workflow cannot issue this decision.

**Present result: 0/12 accepted, 12/12 blocked; NO-GO for deployment and
live broker routing.** Review-complete is not equivalent to authorized-to-trade.
