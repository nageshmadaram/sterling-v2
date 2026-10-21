# Radio / TV search contract — 22 October 2026

This increment extracts shared suggestion lifecycle ownership and command
validation from the player UI. The code-review graph tools were unavailable;
source inspection and regression harnesses were used.

## User-visible improvements

- A timed-out search offers **Retry suggestions**, preserving the text, keyboard
  focus, current channel and playback intent. No full player reload is needed.
- Responses are matched to a unique request ID as well as the query. An old
  response for the same text cannot replace the results of a newer retry.
- Recovery copy wraps on narrow screens and the retry action has a 44px target.

## Runtime boundaries

`mediaBridge.ts` validates the action allowlist, per-action string limits,
allowed themes, and optional bounded request IDs. Only copied command fields
cross the frame boundary. TV and Radio use the same parser, with kind-specific
commands retained. Preference/storage allowlists are unchanged.

`useMediaSuggestions.ts` owns each request's debounce, response deadline,
listener cleanup and retry lifecycle. Closing, composing, changing the query,
and unmounting cancel the previous session. Untagged legacy display messages
remain valid at the bridge but cannot complete a correlated search request.
The new player scripts echo request IDs; missing or stale player assets produce
an actionable timeout rather than silently showing unrelated results.

TV state callbacks now use current-value refs, matching Radio. Updating a
parent callback no longer removes message listeners or restarts the initial
15-second readiness watchdog. Initialization still recovers on a valid late
ready message from the exact opaque-origin frame.

## Artifacts and acceptance

Regenerate the TV mobile source into hosted TV, then regenerate both standalone
exports. Runtime tests check request IDs through both canonical documents;
workspace tests check queued-command validation and response forwarding;
header tests cover same-query stale responses, retry focus and unchanged
playback intent. Browser flows exercise the real opaque-origin documents.
Provider stream availability and physical-device native media behavior remain
external acceptance checks.

## Verified result

All 103 checks across 13 frontend suites and all 23 Chrome browser flows
passed, including request-ID echo through both real player documents.
The isolated production TypeScript/Vite build and artifact reproducibility
checks passed. TV preferences reload also clears a previous failure overlay
while waiting for the replacement frame to initialize.
