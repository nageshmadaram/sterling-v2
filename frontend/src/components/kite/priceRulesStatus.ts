/** Broker setup messages for people reading the strategy board. */
export function priceRulesEntryReason(reason?: string): string {
  if (/KiteTokenError|Incorrect.*api key.*access token|session.*expired/i.test(reason || '')) {
    return 'Kite needs a fresh session. Reconnect in Account & Login before arming entries.';
  }
  const known: Record<string,string> = {
    price_rules_disabled: 'Price Rules is off. Turn it on under Trading Config → What is running before arming new entries.',
    current_live_broker_binding_required: 'Confirm the current broker account in Trading Config before arming entries.',
    bound_live_account_required: 'Connect and select a live Kite account before arming entries.',
    LIVE_EXECUTION_DISABLED: 'Live execution is turned off. Review Trading Config.',
    verified_static_egress_required: 'Verify the outbound IP registered with your broker before arming entries.',
    SAFE_MODE: 'Safe mode is active. Review the trading controls before resuming entries.',
  };
  return known[reason || ''] || reason?.replaceAll('_',' ') || 'Checking account and trading readiness…';
}

export function priceRulesNeedsLogin(reason?: string): boolean {
  return /KiteTokenError|Incorrect.*api key.*access token|session.*expired/i.test(reason || '');
}
