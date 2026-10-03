# Strategies

Sterling's Indian-market strategy surface includes:

- Triple SuperTrend
- Value-Flow Navigator
- Adaptive Edge
- NIFTY opening-range breakout
- ATM premium imbalance
- Gamma Unwind V1 — GAMMA-UNWIND client 0.8.2 / S1.6.0 parity port
- OI Wall Flow
- Bear-to-Bearish
- PCR and opening-volume leader scans

Strategies consume normalized Indian-market candles, quotes, option chains, and depth. Orders route through the shared safety and idempotency layer before reaching Zerodha Kite.
