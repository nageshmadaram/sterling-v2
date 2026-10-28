# Executed acceptance evidence — 28 October 2026

Application commit: `862c03a53e6e2ec6742d7099b0696c118037d7f7`.
The local and public gateways serve this release. Backend Python remains at
`1960ace82` with identical Python code; the latest change updates the private
media templates/assets and an administrator UI label. Actual runtime provenance
is recorded in [runtime-final.json](runtime-final.json). Documentation and
QA-harness-only commits may follow the application commit.

## Executed checks

| Check | Observed result | Receipt |
|---|---|---|
| Production frontend build and TypeScript | Passed | [build-final.log](build-final.log) |
| Complete frontend unit suite | 278 files; 2,505 passed | [vitest-final.log](vitest-final.log) |
| Complete backend suite, excluding separately run offline admission scenarios | 12,585 passed; 30 skipped; 1,189 warnings | [pytest-final.log](pytest-final.log) |
| Offline admission/recovery scenarios | 42 passed; fake broker, no external network | [e2e-final.log](e2e-final.log) |
| Media browser journeys | 33 passed, including native play/pause, Light icon contrast, first visible Home frame and artwork geometry | [media-final.log](media-final.log) |
| Gateway static-asset containment | 6 passed | [gateway-merged.log](gateway-merged.log) |
| Exact Supertrend notification identity | 30 frontend / 46 server focused checks passed | [frontend](push-exact-source-tests.log), [server](push-exact-server-tests.log) |
| Authenticated live signal deep link | Opened the matching NIFTY FIN SERVICE signal detail page | [exact-signal-browser.json](exact-signal-browser.json) |
| User creation through the actual browser and API | Created a disposable member, persisted all selected workspace and settings permissions, signed in as that member and verified its effective access | [admin-create-browser.json](admin-create-browser.json) |
| Page/overlay visual sweep | 743 captures; seven widths; Light/Dark; no horizontal document overflow or uncaught browser errors | [visual-audit.json](visual-audit.json) |

The visual widths were 320, 360, 393, 412, 600, 844 (short landscape), and
1440 pixels. The sweep covers workspace pages, four released strategy boards,
settings sections, account/navigation overlays, profile UI, media, administration
sections, per-user details, broker assignment confirmation, user creation and
initial access, chooser and sign-in. It blocks private mutations except media
access credentials; preferences are fulfilled locally. It does not create users
or move broker accounts in the production database. Selected screenshots are in
[screenshots](screenshots/); the complete capture set remains under the local
`.runtime/acceptance-20261028/visual-phone-final/` directory.

## Controlled playback measurements

[controlled-media-timing.json](controlled-media-timing.json) records ten actual
`playing` events for WAV radio playback and ten decoded HLS video-frame callbacks
in Chrome mobile emulation. It also records Light/Dark computed palettes and the
video/ancestor geometry. These are controlled local fixtures, **not phone,
broadcaster, Wi-Fi/4G, speaker-audibility or real-network performance certificates**.

| Player | Samples | Median | P95 |
|---|---:|---:|---:|
| Radio | 10 | 58.9 ms | 80.2 ms |
| TV | 10 | 89.5 ms | 140.2 ms |

The expanded TV card and native video have matching bounds and corner radii;
outer wrappers are transparent with zero borders. The browser journeys also
check 320/393-pixel portrait and 844-pixel landscape geometry, and both Surprise
buttons change the active channel/station.

## Phone observations and remaining certification

The operator reported receipt of the current-address notification and successful
opening of Positions. They then reported that the NIFTY BANK signal notification
opened Supertrend. These observations prove phone delivery and current-origin
navigation, **not** the exact detail modal. A subsequent automated live check
exposed the source-time versus candle-close-time mismatch; it was fixed and
[the live detail check passed](exact-signal-browser.json). The final notification
using the stable source identity was accepted by the provider; its physical
receipt/detail opening remains pending in
[exact-signal-notification.json](exact-signal-notification.json).

The operator confirmed photo saving on mobile, the previous chip/shelf and
player-corner/Surprise changes, and BBC Earth US 1080 starting in approximately
**two seconds** (one observation; browser/network and switch time unspecified).
Their follow-up found a dark radio play/pause icon, excessive artwork/title
spacing and a SW/AM startup flash. These are fixed in `862c03a53`; all 33 media
journeys pass, with the follow-up phone recheck still pending. Photo persistence
after reload, no zoom/orientation, real radio startup and channel-switch timings
remain unconfirmed. [Operator observations](phone-observations.json) preserve
the exact scope of the phone evidence. Browser emulation does not replace them. Two legacy-origin registrations were disabled. The current
member registration's origin was initially backfilled from the operator's
explicit current-address confirmation; that migration is disclosed in
[push-current-origin.json](push-current-origin.json), rather than being presented
as browser-attested origin data. New subscriptions provide their actual origin.
Quick-tunnel addresses remain temporary: moving to another address requires a
new site registration; disabling an old subscription cannot repair its retired
DNS name or rewrite notifications already delivered.

AA0595 was connected after restart, and a fresh read-only
[Kite profile request](kite-live-profile.json) verified the real AA0595 session.
A cached status poll may set `validated=false` because that poll did not repeat
the external validation; it still reports Connected. Snapback calculation
was enabled with automatic execution off and completed a 200-stock scan that
returned a row. Later snapshots naturally change as scans run. Gamma has
completed reads without qualifying armed rows; EMA results remain paper/modelled
and explicitly not live eligible. No qualifying signal, order fill, stop drill
or profitability is invented. Archived strategies keep their opt-in/release
restrictions. Entry kill/release/account-binding gates remain enforced.

**Complete physical-device and trading-release certification remains pending.**
GitHub Actions runner provisioning, Python 3.13/Docker execution, physical broker
protection/recovery drills and forward economic evidence are not certified by
these local receipts. No live-entry promotion is authorized by this work.

## Reproducing user creation without production writes

The included fixture starts the real API handlers with a temporary database,
synthetic administrator/JWT keys, no worker lifespan and denied external
network connections. The browser script requires a matching fixture nonce
before it can create a user. Run these in separate terminals from the repo root:

```bash
backend/.venv/bin/python scripts/ui/fixtures/admin-api.py
```

```bash
STERLING_WEB_PORT=5192 STERLING_CALLBACK_PORT=0 \
STERLING_STATIC_ROOT="$PWD/frontend/dist" \
STERLING_API_TARGET=http://127.0.0.1:8205 node scripts/serve_frontend.mjs
```

```bash
node scripts/ui/verify-admin-create.mjs
```

Build the frontend first. Set `ADMIN_ACCEPTANCE_RELEASE` when using a known
immutable asset directory; otherwise the harness records the release as unasserted.
The host guard rejects non-loopback targets before transmitting any credential.
Results go under `.runtime/admin-create-acceptance/`.
The API fixture writes a mode-0600, temporary synthetic session file; never
commit it. The observed run used the immutable published application assets.

## Storage and provenance

Cleanup removed regenerable npm/pip/browser/software-metadata caches and
completed disposable pytest directories. Trading databases, account credentials,
current/protective runtime state, user files and untracked pre-existing evidence
were retained. A regenerable archive of the oldest inactive UI release was removed after
verifying its source commit remains in Git; current and previous releases were
retained. Superseded generated browser captures were cleaned after the final
passing run; current captures, historical logs and selected evidence remain. Compressed consistent database backups
remain local, mode 0600, outside Git. Reported bytes for disposable directories
are logical file sizes, not a promise of equal physical space reclaimed.

Earlier failed attempts (including the initial broad backend failures and the
expired verification-session visual run) remain in local runtime logs. The
committed final receipts supersede those attempts; they are not erased or
relabeled as passes. The SHA-256 manifest identifies the evidence files.
