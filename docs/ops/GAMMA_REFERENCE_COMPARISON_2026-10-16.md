# Gamma reference comparison — 16 October 2026

## Evidence and confirmed matches

This compares the operator-supplied 16 October GAMMA_Buzzer posts with read-only broker candles and the existing saved Sterling board. Graph tools are unavailable, so source inspection and isolated regressions were used. No expected signal was inserted into live history and no broker order or Telegram update was sent.

All 12 explicitly shown watch contracts are present in Sterling's saved board with November 23 expiry. Eleven of their twelve zone edges match the post after rounding to one decimal place. BANKBARODA differs: Sterling 224.7664 (224.8 rounded), reference 225.2. Do not hard-code the reference edge: the source zone inputs/configuration at the original scan are not provided.

The post omits the other contracts and some stock identities. Sterling's saved snapshot has 9 BREAKDOWN-PE and 20 BREAKOUT-CE contracts, but has unresolved recovery status and recorded broker-authentication failures. Its last completed-bar evidence predates the current session. It is not a trustworthy point-in-time 09:21 snapshot. Contract presence does not prove the same morning ranking, count, OI selection, quote or zone gap. Current authenticated read-only candle requests succeeded; the saved authentication failure is historical evidence, not proof that credentials are still invalid.

## INDIGO stop chronology and price source

Read-only broker daily candles establish:

| Candle session | Underlying close | PE stock-stop result |
| --- | ---: | --- |
| October 14 | 4718.5 | Below edge 4731.8: arm |
| October 15 | 4778.3 | Back above edge: stop |

The October 16 09:21 reference notification labels the second close “16 Oct”; broker history dates that value to October 15. Notification delivery date and completed-candle session date must remain separate. No October 16 closing information is used to explain a morning message.

The INDIGO OCT 4800 PE October 15 15:29 minute closes at 87.15, exactly matching the reference exit. The broker daily candle closes at 89.15, and its 15:30 minute closes at 89.50; these are distinct observations. From reference entry 151 and quantity 150, the 87.15 reference close gives -42.2848%, displayed -42.3%, and -9577.50 rupees, rounded -9578. This confirms reference arithmetic and market observations, not a Sterling broker fill. A real exit uses its confirmed broker execution, never an assigned retrospective candle price.

The existing D68 rule already arms below support and exits only after an armed PE closes back above it. New runner regression verifies arm, durable serialization/restoration, then exactly one mocked owned exit on the following completed session. The prior entry-price difference (150.85 historical candle proxy versus reference live quote 151) remains disclosed.

## Correction and validation

The daily stock-stop reader previously selected the last returned candle without checking its session or price validity. It now accepts only the requested session's positive finite close; adjacent-session, zero, negative, NaN and infinite values cannot arm or stop a position. Missing valid data leaves the existing sweep retryable. No stop direction, entry threshold, account authority or broker protection gate was relaxed.

Gamma service, historical, scanner, runner, API and reference regressions: **172 passed**, two existing deprecation warnings. Whitespace validation passed. No frontend change was needed for this comparison.

The new reader hardening is saved and tested but has not been activated through a restart in this check: the current replay state could not be verified from the available browser session. Existing service management and exits were left running. A controlled activation requires checking current broker exposure and retained replay first.

Local evidence: `.runtime/audits/gamma-reference-2026-10-16/watchlist-comparison.json` and `indigo-broker-candles.jsonl`. Existing saved daily history is preserved. Full morning-watchlist parity still requires the original complete 09:20/09:21 watchlist, dated zone/OI inputs and quote timestamps. The supplied abbreviated report cannot establish zero mismatches.
