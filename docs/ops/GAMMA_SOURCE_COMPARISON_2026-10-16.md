# Gamma source comparison — 16 October 2026

## Conclusion and correction of earlier evidence

Sterling was not running identical rules to the supplied project. The actual package identifies itself as client 0.8.2 / S1.6.0. Its README, strategy YAML, edition handling, engine/scanner registry, zones, strike selection, four gates and trade rules were inspected. Setup/start/check launchers and the Windows launcher were inspected as code; none was launched. No credentials were read from that package and its files were not modified. Graph tools were unavailable; source review was the fallback.

The earlier close-only trigger was an inference from abbreviated Telegram outputs, not a verified source rule. That inference was wrong for this package. It made the INDIGO/POWERINDIA candle reconstruction line up with selected messages, but cannot be used as parity evidence. Prior reports and artifacts remain preserved and are explicitly superseded for those trigger/parity conclusions.

## Proven differences and fixes

| Boundary | Supplied source | Correction / intentional difference |
| --- | --- | --- |
| E2/E1 | `watch/engine.py`: closed 5m A+B+C+H arms E2; only an ARMED engine can emit on a live tick above the base high with forming-bar volume above closed SMA20 | Restored `live_breakout` default. Close-only mode remains explicitly experimental, never described as source parity. |
| OI tolerance | YAML and `a_holds`: 8% line minus 1 percentage point tolerance = 7%; custom thresholds retain their tolerance | Restored defaults 0.08/0.01 and original configurable tolerance arithmetic. |
| E1 OI rebound | `ContractEngine.on_tick` does not repeat condition A after arming | Removed Sterling's extra OI-drop veto at E1, which could suppress an otherwise valid source breakout. Four final tier gates remain. |
| Direction | V1 `any` = VWAP side OR drift; `all` = both; input bars sorted chronologically | Existing OR correction retained; chronological ordering now also matches source. |
| Edition/HOI | `edition.py` forces client HOI-1 even though YAML top_n_oi=2 | Later user-provided group requirement explicitly requests HOI-1 and HOI-2. Kept two strikes, documented as an intentional update, not unchanged 0.8.2 client behavior. |
| Schedule | Shipped YAML marks 09:20/16:00 and builds 09:20/13:15 | Later explicit group requirements add 09:10 marking and 11:15/14:15 builds. Kept these updates. |
| Execution | Reference README: no order function | Sterling's separate owned execution, risk, reconciliation and protection adapter remains. No reference signal permits bypassing these controls. |
| Metadata | Previously claimed 0.8.6 source/parity although that source was unavailable | Actual inspected source version now 0.8.2; blanket signal/historical parity claims are false. Operator updates published separately. |

No operator configuration was overwritten. Read-only inspection found the persisted Gamma configuration had no entry_trigger override and already stored 0.08/0.01 OI values, so the corrected default applies without a database migration. Existing saved history and diagnostic reconstructions were not replaced.

## Executable source comparison

`backend/scripts/compare_gamma_reference.py` imports only the supplied pure rule modules and compares identical deterministic synthetic inputs against Sterling. It never starts a service, watcher, broker adapter or Telegram router, and never invokes the supplied settings loader. A separate temporary audit environment supplied the pure modules' dependencies; Sterling's frozen dependencies were not changed.

Completed comparison: **516 cases, zero differences in the checked outputs**:

- 100 closed-bar A/B/C/H evaluations, including ATR, SMA, base and missing-slot fields.
- 100 zone paths, including live zone values and formation/confirmation indices.
- 100 OTM strike rankings/floors (two selections intentionally model the later requested HOI update).
- Four live tick paths, including no-breakout, insufficient volume, normal breakout and OI rebound after arming.
- 200 direction/zone gate paths, including reversed input order.
- 12 PE/CE stock-stop arm/stop/equality paths.

Source SHA-256 values and the machine-readable result are retained locally in `.runtime/audits/gamma-source-0.8.2/comparison.json`.

Reproduce from `backend` with an audit Python containing the reference pure modules' dependencies:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /tmp/sterling-gamma-reference-venv/bin/python scripts/compare_gamma_reference.py \
  --reference /home/nageshmadaram/Documents/GAMMA-UNWIND-client-0.8.2-linux \
  --output ../.runtime/audits/gamma-source-0.8.2/comparison.json
```

Affected Gamma scanner/historical/runner/API/reference regressions: **175 passed**, two existing deprecation warnings. Diff whitespace passed. No frontend source was changed for this correction.

## Activation and remaining limits

Before controlled API restart, the authenticated replay UI showed zero trades/signals and a disabled Stop control (idle). Broker read-only queries returned zero nonzero positions, unresolved orders and active GTTs. The existing supervised backend was restarted and `/docs` returned HTTP 200; the frontend at http://127.0.0.1:5180/ returned HTTP 200. Existing account settings, credentials, user entry controls and databases were preserved. No funded order or synthetic Telegram message was sent.

These results establish agreement for sampled identical-input rules, not every historical or live signal. The October group posts refer to later behavior; no full 0.8.6 source/configuration was supplied. Exact historical parity still needs the same dated universe/master, zone seed candles, tick/OI peaks, forming-bar cumulative volume and event ordering. The original 09:21 quotes and complete group watchlist are unavailable. BANKBARODA's 224.8 versus 225.2 zone discrepancy is not resolved by hard-coding the post; sampled zone calculations agree when given identical data. Source and seed/data-version differences must be distinguished from rule defects.
