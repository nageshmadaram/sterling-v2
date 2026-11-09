# Gamma terminal-partial: passive broker/GTT snapshot review

**Base:** Sterling V2 draft PR #36 at `1abe55e8ea5509e479cd7c521f13594b8cd6ed37`.
**Deployment decision:** DRAFT / NO-GO. 0/12 independent controls accepted.

## Objective

After a terminal partial exit (e.g., original SELL 50, confirmed 20 filled,
30 remaining), the durable journal/ledger and strategy-local Gamma registry
must not be mistaken for proof that the *external broker* still has 30 available
or that a protective GTT cannot race another SELL.

`assess_protected_remainder_snapshot` is a pure, read-only validator for
**caller-supplied** synthetic/external snapshot data. It is not wired to an
execution service or an HTTP endpoint. There is no broker network call,
order reservation, GTT cancellation/placement or state mutation. Successful
`protected_for_review=True` means only that the supplied snapshots are
**internally consistent** with a protected 30-contract remainder. Every result
has `execution_authorized=False`; there is no success-to-send conversion.

## Exact checks

- Nonempty matching expected/bound broker account ID and precise
  exchange/symbol/product. Only explicitly supported GTT products qualify.
- Strict positive integer remainder, confirmed positive parent fill and
  recorded positive broker GTT ID. No pending/ambiguous stop placement.
- The supplied snapshot-completion timestamp is within a conservative
  5-second maximum and not in the future (caller-supplied timestamps cannot
  independently prove actual broker freshness).
- A raw `{ "net": [...] }` broker-position book has exact positive quantity
  equal to the reconciled remainder in the requested bucket. Boolean,
  fractional, unknown or mismatched other-product exposure is rejected.
- The *recorded* GTT exists in the supplied broker book, has an active
  single-trigger condition and a single matching priced SELL LIMIT leg with
  the exact protected quantity. Malformed price, wrong product, wrong contract,
  triggered, cancelled, OCO and unknown states fail closed.
- Any other trigger on the same contract is conservatively refused, including
  triggers whose *condition* names another symbol but a SELL leg could still
  affect this contract. This review does not guess trigger ownership by
  matching prices or times.
- The broker order book contains exactly one known terminal partial parent
  SELL, with matching order ID, product, exchange, original request quantity
  and confirmed filled quantity; another SELL or nonterminal BUY is a blocker.

## Limitations and safety rules

These snapshots are observational and can be incomplete, copied, stale,
identity-spoofed, or internally inconsistent with changes occurring *between*
Kite endpoint reads. In particular, account-binding and timestamp fields
come from the caller and cannot authenticate the connection. The check does
not establish that a stop can be cancelled, that an order can be sent, or that
inventory is reserved across processes. A reported active stop means the
position must **remain protected**; it is not permission to remove the stop.

The existing production Gamma exit logic and canonical order executor are
unchanged. No automatic remaining-quantity SELL is implemented.

## Regression evidence

The new module has 67 offline tests including a consistent snapshot and
strict negative cases for wrong account, price/quantity rounding, stale or
future timestamps, misbound GTT legs, duplicate/conflicting SELLs, wrong
broker order fill and unverified stop status. The existing eight-suite
Gamma/canonical regression remains in the offline CI workflow; the new suite
is included in the same job. Exact-head CI must independently pass.

## Next P0 gate

A production review would still require authenticated broker requests
immediately before submission, a repeat fresh read under an account-wide
atomic reservation, explicit stop/trigger/child-order lifecycle handoff,
UNKNOWN / crash-recovery fencing, independently reviewed live routing policy,
parent PR #15 full-suite acceptance and witnessed 12-control approval.

**NO-GO FOR LIVE BROKER EXECUTION.**
