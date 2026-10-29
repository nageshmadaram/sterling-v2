# Sterling V2 — Provider, monitoring, legacy risk and application acceptance

**Decision:** **PRODUCTION NO-GO; LIVE TRADING NOT AUTHORIZED**. This is a review checklist, not permission to connect a live broker or submit orders.

| Control | Required independent operational evidence | Current status |
|---|---|---|
| B07 — real broker/session | Broker-approved account identity and ownership, session authentication policy, approved callback/outbound IP, readable account exposure, auditable **read-only** authentication acceptance. Must show all order/write routes absent or disabled; no order submissions, cancellations or alterations | **BLOCKED: no approved live-session receipts** |
| M08 — market-provider | Actual licensed/approved read-only provider observations: provenance, timestamp accuracy, sequence/gap handling, freshness thresholds, stale/replay rejection, reconnect and fallback drills under controlled non-authoritative conditions | **BLOCKED: only synthetic reliability results** |
| A09 — monitoring and alert delivery | Real monitoring endpoint metrics, trigger receipts, traceable user/device delivery confirmations, operator acknowledgment latency, durable retry and ambiguous-send drills; alerts must remain informational | **BLOCKED: no real destination/device receipts** |
| L10 — legacy broker access | Disposition and owner-signed remediation of **272 inherited classified broker-access findings** reported by Phase 11. Include boundary inventory, reachability, approved exceptions, write/authority paths, negative side-effect proof and independent follow-up scanner results | **BLOCKED: zero NEW unreviewed patterns is not legacy closure** |
| F11 — full application / resilience | Production-like representative desktop/mobile auth/operational regressions, performance, vulnerability and SBOM reviews, load and failover, device navigation and accessibility, all with observed receipts | **BLOCKED: no production-like comprehensive test report** |

## Mandatory boundary

Source-based checks only show that `app.offline_security_profile:app` mounts observation and informational metadata endpoints; they do **not** prove that an unrelated running Sterling instance has no exposed legacy order routes. For a real acceptance environment, require actual egress captures, approved network ACL evidence, running process/router inventories and independent denial observations. **No live trading credentials, orders or broker writes are required or permitted by this programme.**

**All external controls are blocked. Evidence from a local/synthetic broker client cannot be described as broker authorization or live market acceptance.**
