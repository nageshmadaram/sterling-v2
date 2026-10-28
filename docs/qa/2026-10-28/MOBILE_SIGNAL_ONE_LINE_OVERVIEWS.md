# Mobile Signals: universal one-line overview

Date: 2026-10-28. Scope: Sterling Kite Signals page, all engines.

## Screenshot-grounded failure

The prior deployed screenshot (`8fa98e3f`) showed (a) the long
"Current-day API checks finished..." status, (b) two or more large
configuration/educational paragraphs, (c) a "Got it" card, and (d) wrapped
Rows/Columns/Modes controls before the first actual signal. A user could see
only part of one or two signals on a phone.

## Implementation

- `frontend/src/components/kite/board/StrategyIntro.tsx` is now the
  shared wrapper for SuperTrend, Snapback, Gamma-Unwind, EMA Trend,
  Adaptive Edge, Intraday, Navigator, and Price Rules. A short summary with
  mode/practice information appears as a single line with a chevron under
  mobile 900px. A tap expands full configuration/conditions and "how it
  works". Switching strategies resets expansion (`key={engine}`).
- Previously dismissed guides remain accessible *inside* the disclosure
  without a second large Got it button. Practice-only engines explicitly
  retain their no-live-orders notice in the collapsed summary.
- Do not assert a selected mode until saved settings have loaded.
  Default Swing is identified as the original preset, not a verified
  current trade setting.
- `frontend/src/components/kite/board/TodaySignalPriority.tsx`
  shows a one-line, expandable API receipt status. Its details explicitly
  say that an API response does not prove a scan ran or produced a signal.
  Failed feeds remain visible; opening the details does not fetch history
  or execute any order.
- `frontend/src/styles/globals.css` enforces single-line summaries,
  dynamic width and ellipsis for every mobile visual style; keeps expanded
  detail scrollable; makes display/scanning controls horizontally
  scrollable instead of wrapping into multiple rows. Desktop overview
  details remain visible without a forced mobile collapse.

## Source inspection receipts

A 12-case, read-only source inspection against the current `dev` content
confirmed: all eight summaries, the wired shared component and
reset-on-switch key, accessible expand/collapse, mobile ellipsis and hidden
content, practice-only notices, dismissed-guide recovery, native
current-day detail disclosure, no false claim of scan execution, and
updated frontend test coverage. **12/12 source assertions passed**.
This is not a browser layout test or a passed Vitest suite.

New/updated test sources:
- `frontend/src/components/kite/board/__tests__/StrategyIntro.mobile.test.tsx`
- `frontend/src/components/kite/board/__tests__/TodaySignalPriority.test.tsx`

## End-to-end acceptance — not yet verified

1. Deploy exact commit to Cloudflare/localhost and read the SHA in the
   running UI, not only GitHub.
2. At 320, 360, 393 and 412 CSS px, test all eight tabs in portrait and
   landscape. Summary must be *one line*, never overlay the list, and
   must expose its full text after tap.
3. Expand and collapse via touch/keyboard, then switch strategies; new
   strategy must begin collapsed. In Light and Dark themes, text and
   focus states must be legible.
4. Verify the first available signal appears directly below the compact
   toolbar. Count actually visible rows by screenshot at each height.
   **Three to five visible rows is a target, not a verified result**;
   it depends on row height and available phone viewport.
5. Verify saving/fetching mode settings changes only the summary label,
   never a trade or saved scan config. Practice-only engines never
   show executable Buy.
6. Run changed Vitest tests and full frontend type-check; separately run
   the rest of the required regression suites.

**Status: source complete, device/build/test execution unverified.
Live trading acceptance remains blocked.**
