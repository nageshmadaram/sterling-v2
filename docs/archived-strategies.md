# Archived strategies

Open Live → Settings → Archived to enable or disable Adaptive Edge, Intraday Pack (Pivot Break, MA Ribbon, VWAP SuperTrend), Opening Volume Leaders, and Value-Flow Navigator for the signed-in Sterling user. SuperTrend, Gamma Unwind, Snapback and Price Rules remain supported independently.

Every archived engine defaults disabled, including accounts with old enabled configs. Settings are durable and isolated by authenticated user. A missing, malformed or unreadable archive permission denies origination. Failed saves are displayed as errors; the UI does not optimistically claim success.

Enabling starts the native scanner with existing strategy settings. Adaptive Edge and Intraday AUTO reset off on restoration. Opening Leaders still requires the shared AUTO switch and approved economic evidence. Adaptive Edge promotion and Intraday validation requirements remain enforced. Navigator is advisory and cannot originate standalone live orders. All strategies still require broker identity, capital, reconciliation, SAFE_MODE and global execution admission.

Disabling revokes canonical entry permission before updating native config. Canonical live execution checks permission again immediately before broker submission. Existing positions continue reconciliation, protective stops, trailing and exits; disabling does not flatten holdings or cancel protection. Orders already submitted before disable still require reconciliation and may fill.

Endpoints: authenticated GET `/api/v1/config/archived-strategies` and PUT `/api/v1/config/archived-strategies/{strategy}` with exactly `{"enabled": true|false}`. Users cannot select another settings owner in the request. Enabling an archived strategy does not enable global live trading, clear SAFE_MODE, or place a test order.
