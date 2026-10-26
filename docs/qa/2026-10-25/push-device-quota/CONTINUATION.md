# Owner-scoped push device lifecycle

Baseline: `def6d126042703a50813dc49aa352d3bdbd63cb2`.

## Reproduced defects

The subscription API's 16-device exception checked endpoint existence globally. A subscription belonging to a different owner could therefore become the receiving owner's 17th device. Concurrent requests could both read count 15 and claim the final slot. Authorized removal left device-only delivery dedupe behind, so a later owner registering the same subscription could miss an event delivered only to the previous owner.

Delivery HTTP responses can arrive after removal, transfer or refresh. An old accepted send could recreate orphan dedupe, suppress the new owner's event or overwrite refreshed-registration metadata. An old 410/network failure could disable or taint the new registration. A late non-expiry server failure could also re-enable a registration already disabled while that request was in flight. These are isolated regressions, not claims that the operator's production device was modified or that a message was sent during QA.

## Exact fix

- `backend/app/api/v1/endpoints/push.py`: reserve count/transfer/upsert in one immediate SQLite writer transaction. The full-capacity exception requires both endpoint ID and receiving owner UID. Same-owner refresh stays allowed at capacity; under-cap transfer stays allowed and clears prior-owner dedupe; a refused transfer preserves old ownership and records. Authorized removal deletes device dedupe in its transaction. A different owner's idempotent removal cannot erase those records.
- `backend/app/services/notifications/web_push.py`: accepted delivery dedupe is inserted only while that device still belongs to the original owner. This prevents an old completion from recreating deleted/foreign dedupe. Metadata and failure updates require the original owner and exact encrypted-registration snapshot. Refreshed-registration status is preserved. Accepted same-owner events still dedupe across refresh. A non-expiry failure preserves current enabled state; only 404/410 disables a matching registration. No writer transaction spans the external HTTP call.

The 16-device policy, public routes/status bodies, delivery payload/destination, preference meanings, privacy and quiet-hour settings are unchanged. No existing registration or preference is migrated at deployment. Future registration/removal and delivery-completion paths use the corrected owner/generation checks.

## Regression evidence

Twenty new cases cover own-device refresh and full-quota foreign transfer in live/owner-mapped simulation contexts; below-cap and brand-new registrations; a controlled real-SQLite concurrent allocation race; authorized/unauthorized removal; accepted/expired/network responses during transfer/refresh; current-registration error controls; a late server error against a disabled registration; orphan prevention; and refreshed metadata with same-owner dedupe. The quota race controls the count-to-insert interleaving with a bounded wait and asserts one accepted / one HTTP 409 result and final count 16. It does not assert a particular transaction statement. Delivery responses use a stubbed transport and perform the intervening lifecycle action before returning.

Baseline: 12 failures and eight passing controls. With the fix, all 29 focused lifecycle/profile/push/PCR experience cases pass. Every user, subscription and delivery is synthetic. No production device is registered, removed or changed and no real push is sent in these tests.

Two earlier full runs were deliberately stopped before completion as related removal/in-flight defects were reproduced. Their partial logs are retained and are not release passes. Full make verify passed: 11818 passed, 30 skipped in 702.58s (0:11:42); TypeScript and VERIFY OK. Baseline and head failure sets are empty, and the exact 30-skip set is unchanged. Frozen source and log hashes are recorded in validation.json. Frontend source is identical to the previously verified 2,391-test tree across 257 files; its prior log/tree fingerprint is retained instead of claiming another unaffected frontend full run. TypeScript is rerun by make verify.

## Guarded rollout and acceptance

Before/after fingerprints cover notification preferences, device ownership/labels/opt-in/encrypted registrations and VAPID keys without recording their raw contents. Four trading strategy settings, live execution, online backup integrity and official broker read/exposure checks retain the existing guarded release procedure. Runtime/branch/push-state promotion is recorded separately after integration. No test notification is authorized or sent by this continuation.

The earlier user-confirmed Positions heading and Signals Default/Selected/control checks remain passed. Same-release market load, Gamma two-day recovery/speed/full-day simulation, real Snapback saved-row restore, authoritative research, hosted CI billing and sustained temporary-endpoint acceptance remain open. Subscription/delivery bookkeeping tests are not physical receipt, account-switch, notification-tap or live-fill acceptance. Original-only EMA/Gamma and all saved strategy/risk/AUTO/lot settings remain unchanged.
