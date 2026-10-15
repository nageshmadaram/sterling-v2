# Mobile feed recovery — 12 October 2026

Graph tools were unavailable; source review used the permitted fallback.

Confirmed regression: closed-session PCR returned sparse hardcoded samples carrying today's date, and durable storage contained only 2–3 marks per index. Provider read-only recovery returned 26 actual marks for each of NIFTY, BANKNIFTY, FINNIFTY, SENSEX and MIDCPNIFTY on 12 October. Removed the server's fabricated closed-session fallback, recover incomplete covered trading-day history after close with a five-minute retry bound, persist real observations, and merge partial responses only within the same session date.

Mobile recovery: pageshow and visibility/network return refresh PCR and trading queries. Read requests abort after 20 seconds so a suspended connection cannot remain fetching forever; writes retain their existing outcome handling. Stream ticks expire per instrument instead of unrelated ticks extending trust of an old symbol. Historical PCR grids ignore the current intraday clock when displaying a closed or different-date session.

Validation: 9 backend PCR checks passed; 32 initial frontend PCR/tick checks and 11 tick/view/resume checks passed; production build passed; git diff --check passed. Actual authenticated browser at 390×844 showed 26 populated rows in BOTH the PCR table and flow tape; signal rows and both saved watchlist quotes loaded. Original viewport, PCR navigation and minimized replay dock restored. API health OK, background checker running. Before restart broker read-only verification found zero positions, unresolved orders and active GTTs; idle replay held zero trades/signals. No funded orders submitted. Operator preferences and database preserved.

Limitation: market was closed during verification. Actual moving-price acceptance on the physical installed phone is not established by responsive browser verification. Reopen/reload the installed app to load the new build; verify moving quotes next trading session. Existing broader strategy audit is separate from these targeted checks.
