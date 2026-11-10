# Gamma review-only canonical child: journal mutation fence

**Parent:** draft Sterling V2 PR #45, source SHA 8840fc212c0b71b0f5f5af6e71634a80da093872.
**Status:** Review only / synthetic / non-authorizing. No broker session, remote
execution, order, GTT, or production DB involved.

## Finding

The offline Gamma terminal-partial child is intentionally created as a
canonical RESERVED row tagged gamma_terminal_review_only=true and is
explicitly refused by order_journal.claim_submission. That submission
guard was necessary but insufficient for immutable review evidence:
other public journal APIs could still cancel its row, store a bogus order
observation and set a reconciliation flag, or silently accept a false timeout.

## Review-only remediation

Inside each journal's existing transaction, the same explicit immutable
payload flag now refuses these direct mutation paths:

- transition: no RESERVED -> CANCELLED removal of the evidence hold
- acknowledge: no fabricated broker acknowledgement
- submission_uncertain: no simulated transport outcome for a never-sent row
- observe_order: no fabricated filled or terminal broker observation
- mark_projected: no projection acknowledgement against synthetic evidence

All five refuse with gamma_offline_review_intent_immutable and leave the
original RESERVED row, non-execution flag, journal observation count and
checkpoint unchanged. claim_submission continues to return False for
review-only children. The checks apply only to the immutable offline child
payload, not ordinary canonical execution intents.

## Evidence

Six new/updated negative test cases first **failed** against the PR #45
source, confirming the reachable journal mutations. They **passed** after
the transaction-local guards. Run the exact-head offline Gamma workflow
plus the order-journal regression suite before interpreting this as a
tested change. CI remains source/contract evidence, not external approval.

## Still explicitly blocked

The prototype child cannot submit a broker order. A production parent-child
SELL requires a distinct independently reviewed admission protocol with
fresh authenticated Kite quantities, active protection and child status,
atomic stop handoff, account-bound journal identity and signed fill
projection, crash/UNKNOWN no-resend evidence, and approved twelve-control
production release review.

**NO-GO for production, main merge, deployment and live broker routing.**
