/** Read-only description of an owned server revision, never an editable order. */
export function snapbackPlanReviewText(value: unknown, planId: string, revision: number): string {
  const p = value as Record<string, unknown> | null;
  if (!p || p.plan_id !== planId || p.revision !== revision || p.status !== 'PUBLISHED' || p.mode !== 'LIVE') {
    throw new Error('Snapback plan changed or is already armed. Refresh its status before retrying.');
  }
  const number = (key: string, minimum = 0): number => {
    const n = p[key];
    if (typeof n !== 'number' || !Number.isFinite(n) || n < minimum) throw new Error(`Snapback review is missing valid ${key}.`);
    return n;
  };
  const quantity = number('option_quantity', 1);
  const entry = number('limit_price', 0.05);
  const stop = number('stop_premium', 0.05);
  const cash = number('cash_required', 0.05);
  const margin = number('margin_required');
  const risk = number('risk_amount', 0.01);
  if (!Number.isInteger(quantity) || stop >= entry || typeof p.option_symbol !== 'string' || !p.option_symbol) {
    throw new Error('Snapback review has an invalid contract, quantity or stop.');
  }
  // The canonical store uses zero for no target; plan_preview exposes it as null.
  const target = p.target_premium == null || p.target_premium === 0 ? null : number('target_premium', entry);
  if (target != null && target <= entry) throw new Error('Snapback target must be above the entry.');
  const money = (n: number) => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(n);
  let hedge = 'No futures hedge in this plan';
  if (p.futures_symbol) {
    const units = number('target_futures_quantity', 1);
    const limit = number('hedge_price_limit', 0.05);
    if (!Number.isInteger(units) || !['BUY', 'SELL'].includes(String(p.hedge_side)) || margin <= 0) {
      throw new Error('Snapback hedge identity, direction or margin is unavailable.');
    }
    hedge = `${p.hedge_side} ${units} ${p.futures_symbol}, limit ${money(limit)}`;
  }
  const expires = typeof p.expires_at === 'string' ? Date.parse(p.expires_at) : NaN;
  if (!Number.isFinite(expires)) throw new Error('Snapback plan expiry is unavailable.');
  const expiry = new Date(expires).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', hour12: false });
  return [
    `This authorizes a real entry using server revision ${revision}.`,
    `BUY ${quantity} ${p.option_symbol}, limit ${money(entry)}`,
    `Stop: ${money(stop)}`,
    `Target: ${target == null ? 'No fixed premium target' : money(target)}`,
    `Hedge: ${hedge}`,
    `Option cash: ${money(cash)}`,
    `Hedge margin: ${money(margin)}`,
    `Planned stop risk: ${money(risk)}`,
    `Expires: ${expiry} IST`,
    'Amounts are the server plan snapshot; charges, gaps and slippage can change the outcome. The server rechecks admission and expiry when armed.',
    'The strategy owns protection, trailing stops and exits. Cancel leaves this plan unarmed.',
  ].join('\n');
}

export class SnapbackReviewCancelled extends Error {
  constructor() { super('Snapback review cancelled; the plan remains unarmed.'); }
}
