import { describe, expect, it } from 'vitest';
import { snapbackPlanReviewText } from '../snapbackPlanReview';
export const preview = { plan_id: 'owned-plan', revision: 4, status: 'PUBLISHED', mode: 'LIVE', option_symbol: 'TESTCE', option_quantity: 50,
  limit_price: 100, stop_premium: 80, target_premium: 130, futures_symbol: 'TESTFUT', target_futures_quantity: 50, hedge_side: 'SELL', hedge_price_limit: 1000,
  cash_required: 5000, margin_required: 12000, risk_amount: 1000, expires_at: '2099-10-23T10:00:00+05:30' };
describe('canonical Snapback manual review', () => {
  it('shows both legs, stop, risk and separate cash/margin before authorization', () => {
    const text = snapbackPlanReviewText(preview, 'owned-plan', 4);
    expect(text).toContain('TESTCE'); expect(text).toContain('TESTFUT');
    expect(text).toContain('Stop: ₹80.00'); expect(text).toContain('Hedge margin: ₹12,000.00');
    expect(text).toContain('Option cash: ₹5,000.00'); expect(text).toContain('Planned stop risk: ₹1,000.00');
    expect(text).toContain('real entry'); expect(text).toContain('revision 4');
  });
  it('preserves the canonical zero-as-unset target without inventing an exit', () => {
    expect(snapbackPlanReviewText({ ...preview, target_premium: 0 }, 'owned-plan', 4)).toContain('No fixed premium target');
  });
  it.each([{ ...preview, revision: 3 }, { ...preview, plan_id: 'another' },
    { ...preview, margin_required: undefined }, { ...preview, stop_premium: 100 },
    { ...preview, target_futures_quantity: null }])('refuses incomplete or mismatched review data', value => {
    expect(() => snapbackPlanReviewText(value, 'owned-plan', 4)).toThrow();
  });
});
