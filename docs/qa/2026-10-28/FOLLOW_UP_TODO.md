# Sterling follow-up TODO — 28 October 2026

## Deferred by the operator

- [ ] Phone push re-registration, delivery and exact signal navigation. The operator
  explicitly deferred this check on 28 October. Do not send another test until
  they resume it. First verify the currently served public origin; temporary
  tunnel addresses can expire. Open that exact origin, sign in to the member
  account owning AA0595, and enable notifications on that device. Then send one
  clearly labeled test with real signal metadata and verify that tapping it opens
  that exact signal detail with Setup chart and Option chain. Record browser,
  phone, origin, title, delivery time and destination. Test background/lock-screen
  behavior separately. Provider acceptance alone is not proof of phone delivery.

## Awaiting device evidence

- [ ] Review the new screenshots the operator said they would attach: the latest
  phone player appearance still "looks off." Do not mark the redesigned layout
  accepted until the actual screenshots have been reviewed and corrections
  retested. Retain the requested desktop layout rollback.
- [ ] Record channel/station, browser, network and separate initial/switch timings
  for both players. Latest operator report: "takes almost 2/3 seconds," without
  naming a player, stream or switch. Earlier report: BBC Earth US 1080 took about
  two seconds once. Neither observation is a formal multi-sample latency result.
- [ ] Confirm photo persistence after reloading on the phone. The operator already
  confirmed that cropping/saving works; repeat that flow only if a new defect
  appears.
- [ ] Recheck native mobile video edges, portrait/landscape, sheet dragging,
  play/pause centering, artwork/title spacing and both themes after screenshot
  corrections. Browser emulation cannot certify the physical video compositor.

## Automated verification still running

- [ ] Collect `.runtime/acceptance-20261028/refinement-pytest-release.log` when the
  full backend process finishes. Its sampler retry test failed before the test
  was changed from a fixed sleep to an event-based recovery wait. The corrected
  sampler module passes all 25 tests. Inspect the full traceback and any further
  failures, rerun the corrected cases, and retain honest final receipts before
  marking the complete backend suite passed. This item was not deferred by the
  operator; it remains active verification work.

Automated browser/API evidence is recorded separately in the acceptance register.
These TODOs are not a scheduled reminder or an assertion of complete certification.
