# Product UI redesign and verification

This pass follows the first account-management release. It replaces isolated shell fixes with shared navigation, controls and modal behavior, and records source coverage separately from executed runtime checks.

## Design changes

- One neutral header, consistent wordmark and workspace identity. Account picture, profile/security, appearance, installation and sign-out live in the menu. Workspace selection uses compact monochrome cards with no promotional copy.
- Navigation labels come from the existing canonical registry. Both the command palette and runtime refuse hidden destinations; persisted and configured navigation still respects enabled pages. Home and workspace selection remain distinct actions.
- A header indicator replaces the full-width disconnected banner. Today's four-strategy feed checks use a one-line expandable summary, retaining all feed and scan limitations in the disclosure. No signal readiness or history admission rules changed.
- Settings use one concise page title, shorter section navigation, full-width content and a native mobile section picker. Single-pane mobile pages omit redundant pane tabs. The dashboard and sign-in screen drop greeting and promotional copy.
- Administration uses a directory and a selected-user view. Overview combines access, broker ownership, destinations and observed activity; Access, Brokers, Telegram and Activity each have a focused tab. Mobile selection opens the selected user with a clear return to the directory. Bulk access stays visible outside the selected-user pane.
- Assignment uses the shared confirmation dialog and reports the returned server owner. User switching protects unsaved access drafts. Concurrent permission refreshes preserve edits and retain revision checks; explicit reload discards/reloads the conflicting draft. Missing administrative connectivity is distinguished from denied authority.
- Profile, confirmation, broker status, command palette and layout customization share modal focus, background inertness, scroll locking, Escape and focus restoration. Palette and layout overlays are portalled above the application. The palette has a visible close control and no delayed-focus Escape gap.
- Light-theme secondary text has stronger contrast. Shared inputs, action sizing, radius, spacing and reduced-motion behavior apply across the application. Existing financial state colors and order semantics remain unchanged.
- Assisted login accepts a broker profile arriving after the component mounts. Export scripts use private canonical media templates and inline shared appearance assets; they do not recreate publicly accessible player HTML. Standalone documents are regenerated from those templates.

## Verification artifacts

- `docs/evidence/2026-10-28/ui-source-inventory.json`: deterministic JSX control sites and private/standalone media-document inventory. Source counts are not a claim that every runtime state was clicked.
- `.runtime/ui-audit/20261028/`: runtime results and screenshots for desktop/mobile, both themes, enabled Kite pages, released settings families, administrative sections, player shells, and selected overlays.
- `scripts/ui/verify-product.mjs`: repeatable browser audit against the compiled application and real backend reads. Broker/account/order/configuration writes are blocked; media admission and account preference changes inside the browser context are handled separately. Assignment confirmations are cancelled.
- Complete frontend test suite plus targeted regression checks cover navigation, forms, execution-ticket and replay behavior, media runtime, modal focus, configuration permissions and export idempotence. The final run passed 2,447 tests in 267 files. The runtime pass completed 190 rendered cases with zero uncaught browser errors; final CSS alignment changes receive targeted rechecks.

## Broker verification

A single provider credential test returned `tested`: Kite accepted the saved username and password. The provider still requires TOTP for sign-in. No TOTP was submitted and no authenticated broker session was created by the test. Manual login URL preflight also passed. No trading action or live broker reassignment was executed as part of the UI audit.

## Reproduction

```sh
npm --prefix frontend run ui:inventory -- ../docs/evidence/2026-10-28/ui-source-inventory.json
UI_AUDIT_URL=http://127.0.0.1:5189 \
UI_AUDIT_SESSION=/path/to/short-lived-verification-sessions.json \
npm --prefix frontend run ui:verify:read-only
npm --prefix frontend test -- --run --maxWorkers=2
npm --prefix frontend run build
```

The session file is local, temporary and never part of the repository. Browser screenshots are local artifacts because they can contain account details. Provider verification, order submission, exits, credential replacement and access changes need their own authorized operational checks; the read-only runtime pass does not claim to have executed them against live accounts.
