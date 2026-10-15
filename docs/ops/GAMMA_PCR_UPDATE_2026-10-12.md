# Gamma requirements and PCR presentation — 12 October 2026

Reviewed the supplied Gamma update and alert examples using source fallback because graph tools were unavailable. Existing 0.8.6 implementation has HOI-1/HOI-2 OTM selection, 2.5% proximity, 09:10/09:20 level slots, 09:20/11:15/13:15/14:15 watchlist slots, five-minute evaluation, four-gate ENTRY/NEAR ENTRY/WATCH ONLY labels, scheduled watchlist/added/day-summary formatting. Existing 16:00 level refresh remains.

Fixed legacy OI tolerance admitting below the specified minimum: closed arming and live E1 now require at least 7% OI drop (or a stricter configured threshold). Removed stale HOI-1-only warnings. Watchlist broadcasts preview six contract rows without splitting stock pairs; added alerts preview three stocks per side. Gap percentage now uses the zone edge denominator consistently.

PCR and Flow Tape inner containers now have visible overflow and no max-height; the containing pane owns scrolling. Added a visible action key and theme-aware green/up Buy CE, red/down Buy PE and neutral Wait badges throughout PCR action cells. Signals remain informational and no order semantics changed.

Validation: 56 focused Gamma reference/API/alert tests passed; production frontend build passed; git diff --check passed. Authenticated browser confirms both PCR tables max-height none / overflow visible and colored action legend. Before controlled API restart, broker read-only check found zero positions, unresolved orders and active GTTs; replay idle with zero trades/signals. API health OK and checker running after restart. No funded orders or synthetic Telegram messages submitted.

Exact historical alert numbers cannot be certified from text examples alone: matching output requires the reference's same dated instrument master, spot/option quotes, OI and zone inputs. These checks establish rule/format behavior on test inputs, not identical historical observations or profitability.
