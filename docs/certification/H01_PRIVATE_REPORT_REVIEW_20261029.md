# H01 independent evidence handoff — bounded observation verification only

**Current decision:** `H01=BLOCKED`, **PRODUCTION DEPLOYMENT NO-GO**; **LIVE TRADING NOT AUTHORIZED**; **BROKER ORDER ROUTING NOT AUTHORIZED**.

## Purpose and limits

`certification/observe_host_h01.py` (existing) can collect a small set of host/process/kernel indicators locally on a **separately approved isolated production-like environment**. The new `certification/verify_h01_observation.py` validates only the **format and file integrity** of the resulting private JSON observation.

A successful result means `structural_validation=PASS`, **not** host hardening, network segmentation, runtime enforcement, independent acceptance or production certification. An SHA-256 digest identifies the bytes as received; without an independently authenticated known digest it does not prevent substitution or prove provenance. An authorization reference supplied by the observer is not itself proof of authorization.

**No observation has been run on an approved Sterling production-like host. No independent external evidence was supplied.**

## Read-only procedure for an approved independent reviewer

Prerequisites: written authorization and externally confirmed host identity, independent named human observer, non-authoritative isolated environment, signed scope/change window. No root execution, broker sign-in, production DB mutation, service restart, scanning, live feeds, orders, credential discovery or public report uploads.

After an authorized host observer collects the private report per `H01_HOST_ISOLATION_READONLY_EVIDENCE_20261029.md`, transfer it to a **restricted reviewer location**, validate ownership and storage permissions, and perform the following *offline* verification:

```bash
# Example only. Replace all IDs with independently verified approved references.
python3 certification/verify_h01_observation.py \
  --observation "/private/review/h01-observation.json" \
  --environment-id "approved-env-001" \
  --authorization-ref "approved-ticket-001"
```

- Refuses world/group-readable, oversized, symlink or malformed JSON reports.
- Refuses unexpected fields (including credential data), duplicate keys, mismatched environment/authorization references, malformed timestamps or modified status/authority fields.
- Computes the exact received-file digest without copying raw host facts to stdout; always reports `independently_verified=false`, `independent_signoff=NOT_PROVIDED`, `control_acceptance=BLOCKED`, and `production_deployment=NO_GO`.
- Do not paste raw host files, credentials, accounts, tokens, OTPs, security configuration dumps, environment values, or sensitive network details into GitHub or chat.

## Separate H01 acceptance requirements — not automated by either tool

An independent security/operations reviewer must confirm **all** of the following in a restricted, auditable evidence system:
1. Actual approved host identity, authority and signed scope; exact source/image digest and UTC observation.
2. Observed host hardening against an approved OS/kernel patching/permissions baseline.
3. Actual service/container process, mount, capability, user/namespace and privileged-escape tests, with failed attempts and independent witnesses.
4. Ingress/egress segmentation, explicit broker-order network deny, firewall/proxy boundaries and a controlled negative test on the **approved, isolated system only**.
5. Evidence copies retained immutably with authenticated digest provenance, independent observer/reviewer identities and signed dispositions.
6. Independent review of each defect, operational exception and closure decision.

These requirements **cannot** be proven with this collector or GitHub CI. If any is missing, H01 remains **BLOCKED** and all downstream certification controls remain blocked. Evidence should never authorize admin or broker writes.

## Current tracker disposition

`certification/production_evidence_register.json` intentionally keeps **12/12 production controls BLOCKED**, **0/12 independently accepted**. The NO-GO gate remains fail-closed and cannot be used to issue a production GO or live-trading approval. The separate Phase 11 offline PASS and the draft certification branch do **not** authorize a merge or deployment.
