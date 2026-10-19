# Sterling mobile TV and launch audit — 2026-10-20

**Disposition: NOT CERTIFIED.** This is a code inspection and targeted
remediation record. Phone playback, published gateway reload and whole-system
live broker certification require external execution evidence. No order was
submitted, and no safety switch or production secret was changed.

## Source and runtime versions

- Standalone source: `sterling-iptv/sterling-iptv.html`.
- Sandboxed Sterling presentation: `frontend/public/tv/index.html`.
- React host: `frontend/src/components/tv/TvWorkspace.tsx`.
- Actual release server: `scripts/serve_frontend.mjs`; optional Nginx is
  `frontend/nginx.conf`.
- GitHub `main` had an additional user `iptv added` commit
  `a40595e4` after the previously integrated v14 source snapshot. That
  commit changed *only the standalone source*. The two HTML documents
  intentionally differ because the embedded version must remove arbitrary
  public CORS proxy relays, isolate storage and broker state, enforce CSP
  sandboxing, defer TV startup until preference hydration, and expose the
  isolated TV tools. New standalone features (for example additional
  startup/source controls) need a **separate, security-preserving integration
  parity review** before claiming full source parity.

## User-visible regressions investigated

| Area | Finding | Code disposition | Remaining proof |
|---|---|---|---|
| KITE underline | Pre-TV KITE live uses `--k-green`, Simulation `--k-amber`. Reused tab CSS incorrectly painted inactive TV green | Preserve original ViewSwitch CSS, set inactive extra destination underline transparent, active TV green, standalone Simulation amber; 10px gap retained | Screenshot/layout comparison on mobile and desktop |
| Mobile iframe | Child sent its `ready` message only once; on mobile it can race the host's listener and never receive TV preferences or start playback | Host sends hydration again at iframe load; child repeats ready until hydrated; host requires a verified ready message before declaring load | Real iOS/Android startup/network check |
| External HLS library | Playback directly called `Hls.isSupported()` even if a mobile browser blocks the external library | Feature-check before use; prefer Safari-native HLS; fallback to native HLS when JS is unavailable | Check iOS Safari, Android Chrome and specific streams |
| Autoplay | Sound-first startup and swallowed rejected `play()` promises leave a black player without recovery | Start muted; surface a **Tap to play** gesture; show a retry prompt on failure | Real mobile tap-to-play/audio unlock |
| Frame load failure | React previously considered iframe `onLoad` proof that IPTV scripts worked | Wait for verified `sterling.tv.ready`; show a retry control after 15 seconds | Confirm retry on mobile failure |
| CORS and mixed content | Untrusted stream hosts, HTTP URLs on HTTPS, missing Access-Control-Allow-Origin, geo-blocked and DRM streams cannot be made reliably playable by client code alone | No automatic forwarding of credential-bearing playlist links to public proxy services; retain direct HTTPS/native paths | Per-channel network evidence |
| Public gateway CSP | Live Node gateway previously served old trading CSP after release-file promotion without process restart | Separate TV CSP exists in source, but published gateway requires safe presentation-service reload | `curl -I` on **actual public** /tv/index.html must show sandbox/streaming directives |
| TV playlist persistence | v13 TV-only storage was not compatible with v14 startup/source keys | Allowlist six v14-only keys, preserve v13 preferences through safe migration; no auth/broker values travel | Browser reload and logout/login on both devices |
| Mobile browser mode | Switching a phone browser to desktop layout does not bypass platform autoplay, cross-origin, iframe or network restrictions | Native media engine and explicit gesture path address device behavior independent of viewport | Device proof still pending |

## Targeted tests

Run `npm run build`, the selected TV/header Vitest tests and
`node --test scripts/tests/tv_gateway.test.mjs` at the exact candidate SHA.
Static JS syntax was checked during editing, but **no fresh CI, local browser
or physical phone test is claimed** in this record. Captured test output
must be associated with the final SHA, not an older baseline.

## Broader Sterling artifact-by-artifact certification status

The authoritative October 18 register is
`docs/operations/ISSUE_REGISTER_151_20261018.csv`. Its 151
`OPEN_UNVERIFIED` acceptance states were not silently closed by this TV
patch. The latest meaningful older execution summary is
`docs/operations/READINESS_EXECUTION_20261018.md`. It records full-backend
diagnostic failures on a mixed source state, narrower passing suites, blocked
broker egress whitelist proof, incomplete live order/fill/SL/TSL/exit
acceptance, physical restore gaps and inadequate disk headroom.

| Workstream | Source inspected / evidence | Current disposition |
|---|---|---|
| Header / Spectrum CSS | Legacy `ViewSwitch`, `globals.css`, TV header, tests | Patched; exact-build visual test needed |
| IPTV/React frame and storage | `TvWorkspace`, embedded v14, source v14 | Patched mobile defects; phone test and latest source parity remain open |
| TV gateway and CSP | Actual Node server, optional Nginx, isolated gateway test | Source checked; published header/runtime restart proof open |
| Security/privacy | Unique iframe origin, no `allow-same-origin`, TV-only allowlist, no public proxy forwarding | Code-level safeguards present; external URL validation and penetration tests still pending |
| Performance | HLS buffer settings, lazy source parse, child/parent mount state | Mobile resource usage, on-device frame timing and stream throughput unmeasured |
| Broker identity/secrets | `broker_account_binding`, `deployment_identity`, prior audit | Production broker whitelist and rotating credentials require fresh proof |
| Trading entry/protection | `live_minimum`, modes, prior 151 issue register | New-entry enablement NOT approved; market lifecycle receipts and recovery drills absent |
| Snapback / Gamma-Unwind / SuperTrend | Existing engine/service/test inventory and prior audit | Earlier unit/regression evidence exists; not an exact-current-release end-to-end certificate |
| Telegram / reconnect | Existing delivery and recovery tests, operations record | Real phone notification and moving-market outage drills still open |
| Backups/restoration | Existing October 18 SQLite partial restore proof | Full frozen manifest + physical restoration not proven |
| Disk / deployment | Operator reported 97% occupied with 4.2 GB free | Sustained reserve/monitoring not demonstrated |
| CI/CD | Deferred per release sequence | Not a pass; schedule last, after app-level blockers |

No audit finding authorizes placing live orders or relaxing safe-mode,
Greeks, broker-binding, idempotency, reconciliation, SL/TSL or exposure
interlocks. Keep live entry disabled until the full proof chain passes.
