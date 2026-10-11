import {describe,it,expect} from 'vitest';
import {redactDiagnostic} from '../diagnostics';
describe('diagnostic credential masking',()=>{
 it('masks JSON credentials and login query tokens',()=>{
  const input='{"api_key":"private-key","password":"private-password"} /callback?request_token=private-token&status=ok Authorization: Bearer private-bearer';
  const result=redactDiagnostic(input);
  for(const secret of ['private-key','private-password','private-token','private-bearer'])expect(result).not.toContain(secret);
  expect(result).toContain('status=ok');
 });
 it('preserves actionable error messages',()=>expect(redactDiagnostic('HTTP 503 /api/v1/funds (250 ms)')).toBe('HTTP 503 /api/v1/funds (250 ms)'));
});
