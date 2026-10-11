import {describe,it,expect} from 'vitest';
import {priceRulesEntryReason,priceRulesNeedsLogin} from '../priceRulesStatus';
describe('Price Rules readiness messages',()=>{
 it('turns broker token errors into a reconnect action',()=>{
  const reason='New exposure refused: KiteTokenError: Incorrect `api key` or `access token`.';
  expect(priceRulesEntryReason(reason)).toContain('Reconnect in Account & Login');
  expect(priceRulesNeedsLogin(reason)).toBe(true);
 });
 it('separates account confirmation from expired broker sessions',()=>{
  expect(priceRulesEntryReason('current_live_broker_binding_required')).toContain('Trading Config');
  expect(priceRulesNeedsLogin('current_live_broker_binding_required')).toBe(false);
 });
});
