# Twelve-control matrix, evidence and sign-off

Authoritative machine-readable control matrix: [PREPRODUCTION_CONTROLS.json](PREPRODUCTION_CONTROLS.json).
Evidence submissions: [PREPRODUCTION_EVIDENCE_REGISTRY.json](PREPRODUCTION_EVIDENCE_REGISTRY.json).

**Final review decision: NO-GO. 0/12 accepted; 12/12 blocked. No live trading or order-routing authority.**

These IDs are the required IDs. Their descriptive names and owner roles are proposed for assignment, **not previously approved control definitions**.

| ID | Issue / proof category | Proposed owner | Required evidence | Status / approval |
|---|---|---|---|---|
| H01 | Host, TLS and secret custody | Platform/Security (unassigned) | Host inventory, TLS probe, documented credential custody, independent attestation | BLOCKED; Independent host/security acceptance |
| T02 | Transport and static egress | Platform/Networking (unassigned) | Static egress owner receipt, callback verification, network isolation and deny tests | BLOCKED; Network-owner sign-off |
| S03 | Operator security and MFA | Security/IAM (unassigned) | Active user role matrix, fresh MFA device check, token revocation and protected route test | BLOCKED; Security reviewer sign-off |
| I04 | Broker account identity and session binding | Trading Operations (unassigned) | Broker profile readback, exact account/deployment/session correlation and mismatch negatives | BLOCKED; Broker-identity owner sign-off |
| D05 | Database migration and restore | DBA/Platform (unassigned) | Disposable PostgreSQL upgrade, content-hash parity, backup, off-host restore and rollback evidence | BLOCKED; DBA plus recovery approver |
| R06 | Order lifecycle restart and UNKNOWN recovery | Execution Engineering (unassigned) | Reserved/submitting/unknown crash drill, immutable order/trade ids, partial fills, no repeat send | BLOCKED; Execution owner and independent witness |
| B07 | Broker/exchange execution acceptance | Broker Integration (unassigned) | Broker/exchange order, cancellation, protection and reconciliation receipts with signed authorization | BLOCKED; Broker representative plus release authority |
| M08 | Monitoring and incident observability | SRE (unassigned) | Sanitized structured logs, failure taxonomies, dashboards, alert delivery and on-call acknowledgements | BLOCKED; SRE/on-call sign-off |
| A09 | Operator alert and notification delivery | Operations (unassigned) | Device receipt, retry/outbox behavior, reconcile and policy-alert negatives | BLOCKED; Operator receipt and witness |
| L10 | Lifecycle and safe-mode control | Execution/SRE (unassigned) | Kill-switch, safe-mode, durable halt, unclean restart and reconnect evidence | BLOCKED; Operations and safety approvers |
| F11 | Risk, accounting and performance validation | Risk/Research (unassigned) | RiskApproval provenance, capital limits, fill-ledger reconciliation, independent after-cost/OOS review | BLOCKED; Risk and research sign-off |
| G12 | Governance and release decision | Release Governance (unassigned) | Complete evidence registry with immutable SHA, exception register, dual review and explicit decision | BLOCKED; Independent release board approval |

## Independent sign-off checklist

- [ ] Source SHA/CI run identified, completed and independently reproduced.
- [ ] All read/write HTTP routes evaluated under admin/operator/viewer/revoked/stale session.
- [ ] Broker/strategy/manual/legacy call inventory reviewed with no unresolved live bypasses.
- [ ] Database restore, content equivalence, migration idempotence and failure drill witnessed on disposable infrastructure.
- [ ] All UNKNOWN and partially filled order lifecycles proven without a duplicate send.
- [ ] Actual host, security, TLS, egress, identity/MFA, alert delivery and on-call receipts collected.
- [ ] Per-strategy risk and post-cost economic evidence independently reviewed.
- [ ] Every control owner and reviewer assigned and approval records verified, not merely listed.
- [ ] A separate release decision authorizes a specific SHA, deployment environment, account scope and validity window.
- [ ] Independent release board signs off; before that the decision remains **NO-GO**.

The checkboxes are requirements, not assertions of work completed. Never edit the current JSON to claim acceptance on the basis of repository code. A later approved registry must be a distinct signed, immutable artifact with corroborating external evidence.
