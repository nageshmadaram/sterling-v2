/**
 * Explicit starter universe for EMA Trend research.
 *
 * These are exchange-qualified cash equities, not a guarantee of live Kite
 * availability, historical data completeness, or the existence of signals.
 * Never populate another owner's saved configuration automatically.
 */
export const NSE_EQUITY_STARTER = [
  'NSE:RELIANCE', 'NSE:TCS', 'NSE:INFY', 'NSE:HDFCBANK', 'NSE:ICICIBANK',
  'NSE:SBIN', 'NSE:AXISBANK', 'NSE:BHARTIARTL', 'NSE:ITC', 'NSE:LT',
  'NSE:KOTAKBANK', 'NSE:BAJFINANCE', 'NSE:HINDUNILVR', 'NSE:SUNPHARMA',
  'NSE:WIPRO',
] as const;

// Keep original Daily/Weekly onboarding bounded to five stocks.
export const EMA_TREND_QUICKSTART = NSE_EQUITY_STARTER.slice(0, 5);
