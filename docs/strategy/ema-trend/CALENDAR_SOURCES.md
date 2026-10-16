# EMA Trend historical calendar coverage

Verified 2026-10-16 against primary NSE circulars. This strategy-local historical adapter does not expand the global live-entry calendar.

Regular NSE history is supported from **2024-11-01**. Earlier dates remain unknown because the early-2024 special disaster-recovery sessions have not been modeled. BSE history before the shared verified calendar remains unknown. Unverified future dates still fail closed.

- [NSE/CMTR/59722: 2024 capital-market holidays](https://nsearchives.nseindia.com/content/circulars/CMTR59722.pdf): November1, November15 and December25 in this coverage window.
- [NSE/CMTR/64960: November20 2024 election holiday](https://nsearchives.nseindia.com/content/circulars/CMTR64960.pdf).
- [NSE/CMTR/65587: 2025 capital-market holidays](https://nsearchives.nseindia.com/content/circulars/CMTR65587.pdf): the fourteen regular-weekday closures are transcribed in the strategy-local calendar.
- [NSE/CMTR/65729: February1 2025 regular Saturday session](https://nsearchives.nseindia.com/content/circulars/CMTR65729.pdf): normal market09:15–15:30, explicitly included.

Muhurat sessions use non-regular hours and are excluded by this versioned regular-session-only policy. The existing shared calendar owns2026 behavior. Source policy and dataset manifests must identify this limitation; do not claim all historical special sessions or unrestricted multi-year coverage.

Weekly aggregation requires all expected regular sessions and excludes a forming week. The Saturday budget session contributes to its week's OHLCV. This permits100 completed weekly observations by October2026 without guessing2023/early2024 sessions or reducing indicator warmup.
