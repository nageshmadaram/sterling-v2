# Sterling V2 — Independent evidence intake and certification procedure

**Initial certification decision: PRODUCTION NO-GO / LIVE TRADING NOT AUTHORIZED.**
This governance track is intentionally separate from draft Phase 11 PR #13. It starts from frozen `main`, changes only governance artifacts and **cannot** promote the offline candidate to production.

## Evidence submission format (one receipt per independent control)

Each receipt needs: `control_id` (H01 through G12), `environment_id` (approved isolated production-like host), `candidate_commit` or immutable image digest, `observer`, `observer_role`, `observed_at_utc`, `test_purpose`, `procedure_reference`, `redacted_evidence_reference`, `evidence_sha256`, `observed_result`, `failure_mode_result`, `independent_reviewer`, `reviewer_attestation`, and `review_decision` (pass/fail/blocked). Evidence may be stored in restricted external systems referenced by opaque IDs, **not pasted credentials or personal data**.

Keep four independent layers separate:

1. **Source review:** signed repository SHA, workflow outputs and mocked/disposable CI proofs. This exists for Phases 1–11 but **does not** satisfy production controls.
2. **Environment observation:** actual approved non-authoritative host identity, TLS, runtime/DB permissions, vault, and real device metrics. Not supplied.
3. **Independent negative/restore drill:** denial, safety, failure/recovery and external acknowledgment receipts with verified observer. Not supplied.
4. **Sign-off:** security, operations, database, provider and release approvers each sign their applicable evidence. Not supplied.

## Safety and sequence

- **Stage 0:** Current source-only review, all H01–G12 remain BLOCKED. Dedicated no-go verifier checks that no missing receipt is silently marked accepted.
- **Stage 1:** Separate written approval for an **isolated**, non-authoritative test environment. Independent host/identity/TLS/vault and DB checks must precede any real external provider observation.
- **Stage 2:** With separate provider authorization, collect **read-only** broker/session and market data evidence; no order/write calls, no real execution, no auto-trading/entry or live switch.
- **Stage 3:** Collect independent monitoring/device delivery, failover, restore and application-security regression receipts; preserve evidence and resolve legacy access findings.
- **Stage 4:** Independent reviewers evaluate each control and issue a new explicitly signed production go/no-go decision. **This programme itself cannot grant GO**; any future change of release authority requires new authorization and separate controls. Live trading remains separately prohibited.

No credentials, tokens, OTP seeds, secrets, raw account data, device IDs or sensitive logs should enter GitHub, CI artifacts or chat. Review must use redacted, integrity-hashed references.

A green `verify_production_no_go.py` means ONLY the blocked intake is structurally sound. It cannot attest host security or real acceptance and must not be interpreted as permission to merge, deploy, connect a broker or trade.
