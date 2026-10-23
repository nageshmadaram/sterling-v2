# Remaining-gap continuation

Baseline: `78f2565cd4dd07a72dd54649f7ed4055eb6f271f` (PR 251). This continuation keeps global live execution, strategy/AUTO, sizing and frozen research settings unchanged. No broker mutation is used for QA.

## Additional resolved implementation gaps

| Finding | Exact change / verification |
|---|---|
| Missing-session warning depended on a visible strategy sidebar | `KiteSessionBar` moved to the live KITE workspace shell. It remains visible independently of dock/nav layout and is absent from simulation/media workspaces. Unknown/transient status is amber and explicit, including a failed refresh with cached connected=true. Confirmed logout has a connection action and does not imply broker positions closed. |
| Notification deep link opened TV/Radio instead of trading | `SimpleTerminal` explicitly selects KITE for recognized live-account notifications. Tests cover both saved media homes and preserve live/simulation ownership. |
| Research language implied live certification | Snapback evidence now labels research-model/harness results, separates them from broker/protection/risk readiness, and removes unsupported universal edge/exposure claims. Help no longer declares the entire intraday mode research-only. Raw harness reasons remain visible, attributed to the harness. |
| Expanded ticket could buy after a fresher quote crossed its stop | `BoardTicket` independently checks its observed premium before generic entry. At/below stop and invalid/unknown premium refuse entry. It displays signed percentage difference from the canonical entry without changing entry/SL or inventing a 20% tolerance. |
| Practice strategy had an expanded generic order path | Adaptive Edge detail now refuses generic live entry as well as hiding the row action. Strategy-owned entry paths remain authoritative. |
| Reported 38 EMA failures not reconciled | `scripts/ema_scan_audit.py` uses SQLite mode=ro/query_only without app initialization or provider requests. Exact failed job `4eff47285db0352c7e6fcf07`: 38 restated-candle failures. Later completed job has 60 results, zero errors and all 38 exact symbol/mode pairs. Full nonsecret pair comparison is in `docs/evidence/2026-10-23/ema-restatement-coverage.json`. This establishes scan coverage, not fills or strategy edge. |
| Logged-out scheduler behaviour unverified | New Gamma regression executes 4,000 failing cycles without a broker session and asserts one warning, an explicit returned error, then a repeat at the 600-second boundary. No broker method is reached. |
| Future market-load acceptance lacked continuous coverage | Read-only `scripts/trading_session_probe.py` samples only local liveness and listener/release identity every 30 seconds, never contacts the broker or changes controls. `trading_session_audit.py --probes <JSONL>` requires covered normal NFO trading calendar, pre-open through post-close samples, healthy same-release records and no gap over 90 seconds. Missing/failed/mixed-release evidence cannot pass. PID changes are recorded but do not claim Gamma recovery. Overall live acceptance remains pending until all required strategy observations exist. |
| Tests depended on the wall-clock hour | Three existing board assertions reproduce failing on baseline after 23:00: a fixture candle open at Date.now closes on tomorrow's date. Fixtures now use an explicit market-hour Date only; every assertion is preserved. Structural ticket parity receives a valid observed quote; missing-price refusal has dedicated tests. Generate media fixtures with `npm test`/`npm run media:styles` before Vitest, matching the repository pretest hook. |

## Verification and deployment

Final automated gate passed: **11,730 backend tests / 30 existing skips**, **2,308 frontend tests / 248 files**, TypeScript and **VERIFY OK**. Both backend failure sets are empty. The final alert-border color adjustment also passed the focused warning/ticket group; it changes no behaviour. Log hashes and summaries are in `validation.json`. Runtime evidence is recorded separately after integration. Baseline backend full pass is reused from the prior phase only because `git diff 3a0f2354 78f2565c` is empty: the tested and merged trees are identical (11,719 passed, 30 skipped). Both old and new diagnostics/failure sets are retained; initial frontend failures are not silently excluded.

Mobile warning component render: four missing/unknown × light/dark cases at 390px, no horizontal overflow, all text contrast ratios above 4.5:1. The preview uses example cached status with queries disabled and no broker/API connection.

The optional user service is `deploy/systemd/sterling-qa-session-probe.service`; install after integration with `systemctl --user enable --now sterling-qa-session-probe`. Its private JSONL lives under `.runtime/qa/session-probes.jsonl`. Run `scripts/trading_session_audit.py --log backend/logs/backend.log --day YYYY-MM-DD --db <database> --probes .runtime/qa/session-probes.jsonl` read-only. Special/unknown calendar sessions require separate evidence and never inherit a normal-session pass.

## Outstanding observations and external blockers

Closed markets cannot provide two complete release-specific trading days, midday Gamma recovery with nonzero watches, actual scheduled/recovery timings, saved real Snapback rows surviving restart/toggle, or notification receipt on a missing-session morning. The source fixes and offline tests remain separate from those observations. Authenticated runtime-identity panel observation was limited by browser input failures; anonymous access correctly returns 401. GitHub Actions is billing-blocked, and ngrok is externally blocked; the verified Cloudflare provider remains usable.

Authoritative expired-contract/OI/tick/fill datasets, independent research holdout lineage and equal realised-exposure matching remain unavailable. The recorded Snapback research does not satisfy strict live-promotion gates. No profitable outcome, real fill, protection acknowledgement, live-session pass or research promotion is fabricated. A general price-distance cutoff is an explicit strategy-policy choice; the UI now makes the actual distance visible and blocks known stop violations.

Rendered warning text contrast (light missing, dark missing, light unknown, dark unknown): 8.38:1, 13.68:1, 8.96:1, 12.40:1. All four fit the 390px viewport without horizontal overflow.
