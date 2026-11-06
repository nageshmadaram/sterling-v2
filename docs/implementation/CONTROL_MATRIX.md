# Pre-production finish control matrix

**Status: NO-GO. Review only; no funded/live broker execution authority.**

The authoritative control/status records are:
- [PREPRODUCTION_CONTROLS.json](../ops/PREPRODUCTION_CONTROLS.json)
- [PREPRODUCTION_EVIDENCE_REGISTRY.json](../ops/PREPRODUCTION_EVIDENCE_REGISTRY.json)
- [PREPRODUCTION_CONTROL_MATRIX.md](../ops/PREPRODUCTION_CONTROL_MATRIX.md)

This is an *implementation tracking view* of their required IDs, not a
replacement control registry or newly issued approval. All twelve controls
are **BLOCKED**, owners are **UNASSIGNED**, independent signoffs are
**NOT_PROVIDED**, and accepted external evidence is **0/12**.

| ID | Engineering implementation / candidate source | Required independent action | Current status |
| --- | --- | --- | --- |
| H01 | Runtime config, host/TLS and secret-management code | Platform/Security host and credential custody attestation | BLOCKED |
| T02 | Transport/callback and source inventory | Egress owner, network isolation and callback verification | BLOCKED |
| S03 | `auth.py`, `account_policy.py`, `feature_access.py` | MFA, role/revocation and current identity proof | BLOCKED |
| I04 | `accounts.py`, `account_binding_service.py`, `platform_session_read.py` | Account-scoped broker readback witnessed independently | BLOCKED |
| D05 | `alembic_guard.py`, disposable migration/restore CI | Recovery approver validates content-hash parity and rollback | BLOCKED |
| R06 | `canonical_fill_reconciliation.py`, `order_journal` | Restart/UNKNOWN/partial-fill no-duplicate-send drill | BLOCKED |
| B07 | `canonical_strategy_entry.py`, `execution_service.py` | Broker-side execution approval/receipts (not allowed in this review) | BLOCKED |
| M08 | Runtime failure logs and alert code | Sanitized structured observability and on-call receipts | BLOCKED |
| A09 | Notification/outbox services | Delivery and retry evidence on controlled devices | BLOCKED |
| L10 | Kill switch, SAFE_MODE, lifecycle code | Unclean restart, durable halt and reconcile drill | BLOCKED |
| F11 | Risk gates, capital, fill accounting | Independent risk/accounting and strategy validation | BLOCKED |
| G12 | This register, exact-SHA manifests, CI and exception list | Release board's independent decision with scope and expiration | BLOCKED |

## Gate interpretation

Passing GitHub Actions is **technical evidence** for a particular SHA,
not proof of host readiness, account identity, broker authority, accepted
risk, or independently reproduced recovery. A manifest built by
`scripts/preproduction_finish_review.py` is structurally constrained to
`NO_GO` and cannot issue a signed human approval.

The assigned human owner and independent reviewer for each item must be
recorded outside this repository's proposed control template. Review date,
artifact link, exact SHA and immutable evidence hash must be verified before
any control is considered for **separate** acceptance. Do not overwrite
these proposed JSON files with inferred acceptance.

**Current review decision: NO-GO. No operator may turn technical PASS into a trade.**
