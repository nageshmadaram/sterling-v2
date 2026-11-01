# Execution and compatibility route migration register

**Review only — NO-GO for production deployment, live broker routing and real-money execution.**

Source baseline: `2026-11-01-openalgo-upgrade-finish-spec.md`. This register
captures *verified code boundaries*, not an independent assertion that every
legacy call site is migrated. The source inventory remains the authoritative
machine-produced candidate list, while security/engineering reviewers must
classify dynamically constructed calls.

## Verified route contracts

| Route / method | Classification | Identity and policy | Canonical service / source | Remaining proof |
| --- | --- | --- | --- | --- |
| `POST /api/v1/compat/openalgo/orderstatus` | Read-only compatibility (despite POST method) | JWT `get_current_user`, Kite grant, scoped journal lookup, `kite_read_only` | `openalgo_compat.py` → `order_journal.find` → `canonical_order_status` | Account/tenant mismatch, missing journal, UNKNOWN, conflicting fill and writer/reader negative tests |
| Any other `/api/v1/compat/openalgo` route/method | **DENIED** | `compatibility_denied` at global feature-access dependency, before handler | No authorization or compatibility dispatcher | Confirm new routes remain blocked and no extra handler is introduced |
| `POST /api/v1/kite/orders` | Existing order mutation | Kite principal, writer role and canonical order authority; never inferred from API access alone | Existing `kite` router and execution services | Independently trace all submission and risk/kill-switch paths |
| `GET /api/v1/kite/orders` | Broker/order read | Account-scoped Kite access | Existing `kite` order read | Check subject/account isolation, cache/reconnect |
| `DELETE /api/v1/kite/orders/{order_id}` | Existing order cancellation | Kite principal and mutation policy; protective-order ownership must be established | Existing `kite` router | Independent test of cancellation ownership and protective exit safety |
| `GET /api/v1/kite/accounts` | Account configuration read | Kite feature grant and scoped identity | `accounts.get_active` and account policy | Role matrix and revocation under restart |
| `POST /api/v1/kite/accounts` and broker account activation paths | Configuration mutation | Account-specific `configuration` policy and independently enforced endpoint grants | Existing Kite accounts/configuration services | Stale grant and admin reason/audit matrix |
| `POST /api/v1/kite/postback` | Signed provider event | Signed provider callback; **not** a general operator order API | Existing postback/reconciliation service | Signature/identity mismatch and replay evidence |
| `GET /api/v1/platform/*` discovery | Read-only metadata | JWT, Kite feature gate and no-store; never a permission token | `platform_surfaces.py` and existing registries | Exact route parity and no side-effect scan |
| `/python`, `/flow`, `/trading`, `/scalping` | **UNMOUNTED** | No global route or execution authority | Offline syntax/graph metadata and existing Kite UI only | Independent sandbox and UI acceptance, not implemented |

The table is **not an exhaustive API inventory**. It is a reviewed
subset backed by named source modules. A route or call not covered here
is **unclassified for release purposes**, never implicitly approved.
An account/feature grant is necessary but not sufficient for trading.

## Canonical execution and reconciliation constraints

New exposure must remain through `canonical_strategy_entry.submit_entry`
and `CanonicalExecutionService`; cancellation and protective reductions
require existing canonical ownership and risk checks. Broker ACK, timeout,
idempotency marker, orphan trade, mismatched account, incomplete intent,
stale postback and unprojected fill cannot independently prove a settled
trade. UNKNOWN stays unresolved; it must not trigger a duplicate send.

Existing source inventory:
`python scripts/preproduction_inventory.py --strict --output <isolated directory>`.
It records source paths, potential broker mutations and unresolved aliases.
Its PASS can establish the known static allowlist, **not** runtime equivalence
for reflection, aliases, dynamic imports or external broker libraries.

## Release closure tasks (unapproved)

- [ ] Enumerate all broker-order and protective-cancellation callers; inspect
      every direct/indirect write and deny or route through canonical authority
- [ ] Prove account+workspace+role policy across viewer/user/operator/admin,
      Simulation and revoked tokens using exact-head integration tests
- [ ] Verify restart/timeout/partial-fill/UNKNOWN lifecycle with durable
      journal and independent broker evidence, never a resend assumption
- [ ] Independently review rollback, recovery, signature, signed provider
      callback, and every unresolved source-inventory exception
- [ ] Link the reviewed exact SHA, logs and owner's sign-off in a **separate**
      approval process; do not edit proposed controls to manufacture a GO

**Decision:** NO-GO. This file maps technical dependencies, not release authority.
