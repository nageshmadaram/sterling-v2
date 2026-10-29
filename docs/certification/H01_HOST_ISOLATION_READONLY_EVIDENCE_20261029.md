# H01 — Host, OS, runtime and network isolation: read-only evidence readiness

**Control status: BLOCKED / NOT VERIFIED.** Offline validation remains PASS. Production deployment **NO-GO**; broker order routing and live trading **NOT AUTHORIZED**.

## What was implemented (and what was not)

The standalone `certification/observe_host_h01.py` is an **unprivileged read-only local observation tool**, not a hardening agent, port scanner, credential validator, deployment utility or certificate. It reads a fixed allowlist of OS/kernel-provided facts (OS ID/version, kernel release, process NoNewPrivs, Seccomp, effective caps, AppArmor/SELinux availability and user-namespace mapping). It does not inspect network destinations, containers, account credentials, real broker sessions, databases or application processes.

A successful command creates **one new 0600, never-overwritten local JSON file** marked `NOT_VERIFIED`. It cannot change the production evidence register or assert H01 acceptance. A self-declared authorization reference is **not** proof of actual approval.

### Allowed procedure, *only after* real host ownership and written authorization

An independent host owner must confirm (outside this PR) that the target is an approved isolated **production-like, non-authoritative** host and that the operator may conduct the review. Do **not** run this on a live trading machine or without authorization.

Run as an **unprivileged user** from the pinned certification source revision; do not run as root or with sudo. Keep output out of the repository, shell logs, chat and PR artifacts.

```bash
# Run ONLY on an explicitly approved independent production-like host.
umask 077
mkdir -p -m 700 "$HOME/sterling-certification-private"
python3 certification/observe_host_h01.py \
  --environment-id "approved-env-001" \
  --authorization-ref "approved-ticket-001" \
  --output "$HOME/sterling-certification-private/h01-observation.json" \
  --approved-readonly-observation
```

Replace both opaque references with the *actual* approved IDs. This command makes no outbound connections, privileged modifications, or deploy changes. It deliberately **fails if the output already exists**. The report's `production_security_certified` and authority flags remain false. Never upload full local host reports to a public GitHub PR; use restricted evidence storage and reference only sanitized hashes and approved receipt IDs.

## Independent H01 acceptance matrix

| Required real-world evidence | Available in this PR? | Decision |
|---|---|---|
| Signed authorized host identity, change window, build digest and operator | No | BLOCKED |
| Actual OS baseline/patching and hardening audit, signed by host owner | No | BLOCKED |
| Container/runtime user, capabilities, filesystem/mount and process-boundary observations, including negative tests | No | BLOCKED |
| Network isolation ACLs, non-authoritative ingress/egress and observed broker-order network denial on the **approved environment** | No | BLOCKED |
| Separate independent reviewer, UTC observation, hashes of redacted evidence, owner signatures | No | BLOCKED |

The local snapshot can *support* the OS/kernel review but **cannot** alone satisfy any of the above. In particular, it does not prove a hardened container, kernel-policy enforcement on the actual service, or correct segmentation. Any observed missing control remains an open issue for the approved human security/operations team.

## Completion gate

**H01 shall remain BLOCKED** until all rows are externally verified in an approved environment and an independent human reviewer signs the receipt. At that point, other controls T02–G12 still require separate proof and **no release or trading authority is granted by H01 acceptance**.

**No H01 command has been executed on an approved real host in this repository-based review; no independently accepted evidence exists yet.**
