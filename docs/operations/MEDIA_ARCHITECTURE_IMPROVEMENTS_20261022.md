# Radio / TV architecture improvements — 22 October 2026

This increment strengthens the existing Radio and TV experience. It does not
claim to redesign unrelated trading functionality or certify every broadcaster.
Graph MCP tools were unavailable; the source and existing regression harnesses
were used. Parallel trading edits remain outside this change.

## Delivered boundaries and behavior

- **Shared search:** coalesce suggestion commands by 120 ms during typing;
  cancel queued work on query changes, close and unmount. Show pending, empty
  and delayed-response states separately. A four-second deadline bounds the
  pending indicator; a late valid response can still recover it.
- **Language input:** keep an independent composition draft so Telugu/Hindi IME
  input is committed only after composition ends. IME Enter cannot trigger
  playback. Closing an unfinished composition resets it for the next search.
- **Accessible navigation:** keyboard arrows and Home/End navigate appearance
  settings; Escape returns focus to the invoking control. Clearing search keeps
  the field open and focused. Suggestion focus stays on the combobox, with
  active-descendant selection. Reject empty and duplicate suggestion identities.
- **Artwork resilience:** retain verified artwork across metadata refresh
  failures. Back off metadata outages (30/60/120/240 seconds, capped at five
  minutes), coalesce outstanding images, and retry failed artwork when network
  connectivity returns. Keep existing URL validation, concurrency and queue
  bounds. Suggestion images decode asynchronously and omit referrers.
- **Background efficiency:** one deadline-based sleep callback per displayed
  minute replaces a per-second interval. Resume/page-show still checks elapsed
  deadlines. Avoid repeated status text mutations and prune detached triggers.
  Non-cached page exits release observer/timer work; browser-cached pages retain
  their session timer. Browser suspension can delay execution until resume.
- **Quiet recovery:** dismiss offline notices by close button or horizontal/up
  swipe. A new offline transition reveals the notice again; dismissing never
  changes playback intent or starts autoplay.
- **Artifact consistency:** the shared runtime is rebuilt into hosted assets
  and both standalone exports. Existing preference contracts, opaque iframe
  isolation and explicit playback intent remain intact.

## Architectural follow-through

The main structural debt is large canonical HTML documents with embedded player
logic. Extracting them into lifecycle-owned modules should be incremental:
first a typed host/frame command contract, then catalog/search indexing, then
playback adapters. Each extraction should preserve current artifacts and run
queue, ownership, autoplay, mobile and export reproducibility gates. A wholesale
rewrite would make the current player behavior harder to validate.

Further performance work should begin with real-device traces and stream timing
rather than an unsupported FPS claim. Authoritative EPG, native PiP, background
media and provider availability remain platform/provider acceptance checks.

## Validation

The 93-check media regression suite and all 21 Chrome browser flows passed.
After final keyboard refinements, 23 focused checks passed across search,
composition, settings focus, timer deadlines, artwork recovery and artifact
reproducibility. The isolated production TypeScript/Vite build passed.
