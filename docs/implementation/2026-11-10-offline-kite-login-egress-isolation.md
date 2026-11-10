# Offline fake-Kite login preflight egress isolation — draft review

**Parent:** Draft Sterling V2 PR #60 at exact SHA `62e92c97fc5df324b766c75d9f361ef76b2e0d90`.
**Classification:** test harness + scoped CI only. **No application, broker order, production authentication, or security policy change.**

## Root cause reproduced on GitHub and locally

Draft #59 and #60 exact-commit scoped Gamma workflows each passed their Gamma, canonical and simulation steps, and passed **41/41** fake-Kite E2E test assertions. Both jobs still **failed** during teardown of exactly two login-flow scenarios because their socket guard recorded several attempts to connect to `kite.zerodha.com` (HTTP 443, backed by resolved Cloudflare IPs on GitHub runners). Offline tests must fail when any external connection is attempted; the guard did its job.

The "/kite/login-url" route invokes `app.services.exchanges.kite.login_preflight.check_login_key()`, which legitimately makes an unauthenticated read-only HTTP request to a real Kite login URL to check whether the stored key is rejected. The E2E fake's `LiveMachine` had mocked broker REST execution and login token exchange, but had not isolated this independent live-login preflight. The Box host failed DNS lookup, so the older local run happened to pass while GitHub's host resolved the domain and attempted outbound connections. Installing a strict `socket.getaddrinfo` test guard reproduced the same two teardown failures locally by recording the attempted external hostname **before DNS resolution**.

## Test-only changes

- In the disposable fake-Kite `LiveMachine._install_process_patches`, patch the **application's existing login preflight seam** per simulated process. It approves *only* the exact fake API key embedded in the harness and raises the same sanitized public invalid-key error for incorrect or empty keys. The production login preflight function and authenticated state binding are unchanged.
- Enhance the E2E network guard to **block external DNS queries**, as well as socket connects, and record bounded diagnostic stack labels. DNS is refused before it can reach the external network. Never whitelist `kite.zerodha.com`, disable the socket assertion, suppress cleanup errors, or mock the global HTTP stack permissively.
- Add a negative E2E test proving that the fake login preflight refuses incorrect and empty API keys without attempting DNS or a socket connection.
- Extend the scoped CI workflow path filter and test steps to execute the existing production login preflight and mobile login unit regression modules, plus the complete synthetic admission/simulation/trade-journal E2E selection.

## Evidence and limits

- On the exact parent code, the three login-flow tests produced **2 teardown errors** once the external DNS guard exposed attempted `kite.zerodha.com` lookups.
- After the test-only patch, all **3 targeted login-flow cases PASS**, with zero network attempts.
- Dedicated login preflight/mobile login test selection: **37/37 PASS**.
- Complete admission/simulation/trade-journal fake broker E2E **42/42 passed** (184 non-failing warnings), including the two formerly failing login tests, unsynchronised-clock refusal and new wrong-key refusal; no prohibited network attempts were recorded.
- Dedicated login-preflight/mobile-login suite **37/37 passed** (2 warnings). Python compilation, strict source-only inventory, governance/inventory unit tests, `git diff --check` and workflow YAML/path validation PASS.
- Exact-head GitHub CI must be verified **after** publishing; do not inherit old green or red statuses.

The network egress invariant remains strict. The suite is synthetic and does not establish live Kite login availability, real broker credentials or session validity, live SELL, stop/GTT handoff, unknown-order broker reconciliation, or operational release authorization. PR #15 historical full CI is not cleared by this test fixture correction.

**DRAFT / UNMERGED / UNDEPLOYED / 0 of 12 independently approved production gates / LIVE BROKER NO-GO.**
