# Mobile shell and Android packaging handoff

The browser frontend now uses a native-sized mobile shell at viewport widths up to 900 CSS pixels. Page, Signals, Watchlist and Terminal are separate full-width views. Switching panes keeps their contents mounted; navigating to a page returns to Page. Desktop dock slots, sizes and minimized preferences remain stored separately and are not reclamped on narrow screens.

The document declares `viewport-fit=cover` and keyboard resize behavior. Shared shell styles reserve system safe-area insets. The viewport applier tracks `visualViewport.resize` and publishes its visible height so an Android keyboard can reduce the workspace instead of covering the form. Inputs use readable mobile sizing. The order ticket is constrained to the visible phone viewport. Tables scroll locally; primary navigation and settings sections remain reachable through horizontal navigation.

Price Rules has a Signals tab. The board reads the existing authenticated `/api/v1/price-rules` snapshot and shows actual published plans, conditions, states, quantities and broker protection. It never creates synthetic scanner recommendations or submits orders. Creation, review, arming, pausing and owned exits remain in the strategy management form. Live and Simulation views retain separate behavior.

A web app manifest provides standalone launch metadata. This is packaging preparation, not an APK or an offline trading app. No service worker caches account data, credentials or trading responses.

## Future Android wrapper acceptance

- Package the existing production frontend in the chosen Android WebView wrapper; keep the backend hosted. Never bundle broker secrets, Telegram bot tokens or server environment files in the APK.
- Use a stable HTTPS deployment origin. Configure the frontend API base and WebSocket origin for that deployment; verify backend CORS explicitly for the wrapper origin. Do not assume a local development origin works on a phone.
- Keep Sterling authentication and per-user access checks. Test session expiry, revocation, reconnect and app background/foreground transitions on a physical device.
- Open Kite sign-in in the system browser, with the registered HTTPS callback. Implement the wrapper return/deep-link flow before claiming native broker login works. Do not collect broker passwords or OTPs in the wrapper.
- Integrate Android back navigation and keyboard dismissal, edge-to-edge system bars, external links, file downloads and rotation. Keep confirmed exits and order actions explicit.
- Test 320/390/430px phones, landscape and tablets, keyboard-visible forms, TalkBack and large text. Verify every strategy, private endpoint, broker callback and Telegram handoff against the deployment.
- Create release signing and distribution outside the web build. No APK has been produced by this change.

Current development launch remains `./sterling.sh start` from the repository. The managed gateway serves the rebuilt frontend without rotating the current tunnel.

## Mobile app experience and browser installation

Phone and tablet widths up to 900px default to Mobile app style. Open Menu to
switch between Mobile app style and Full app style; the choice is saved on that
browser. App style uses a bottom pane bar, an all-pages sheet, labeled strategy
cards, safe-area padding and the visible keyboard viewport. Full style restores
the docked workspace with horizontal scrolling. Changing styles asks before
leaving an unsaved settings draft.

Menu → Install Sterling uses the browser's install prompt when offered. Otherwise
Menu → Install / add to home screen explains Chrome/Brave installation and Safari
Share → Add to Home Screen. The HTTPS site has 192px and 512px app icons, a
standalone manifest and an Apple touch icon. Installation keeps the same origin,
account/session and backend connection. It creates no new credentials and places
no orders. Browser installation availability remains browser-controlled.

Live financial screens require network access. No offline API responses, broker
credentials or trading data are cached by a service worker. This is an installable
web app; a signed APK and App Store package remain separate future deliverables.

## Page interior follow-up

Mobile app style now uses a native settings-section selector so all 16 sections
are discoverable without a long horizontal rail. It shares the existing draft
navigation guard. Full app style retains the desktop rail.

Orders tabs support touch and keyboard navigation. Positions/Holdings actions
remain visible without hover. Offline history stacks its chart below the
instrument list; Operations cards fit narrow screens. Help includes phone use.
Secondary mobile pages omit the pinned market strip to reserve content space.

Opening a chart or strategy detail focuses the main mobile pane through
`kite-focus-main-pane`; opening Home still returns to the dashboard.
Chart canvases receive concrete colours, and follow the app theme switch.
PCR feed and saved-session records are normalized before rendering; missing
quotes remain unknown and historical fallback is explicitly labeled.
