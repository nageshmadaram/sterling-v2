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

import { recordDiagnostic, diagnosticSnapshot, diagnosticBundle } from '../diagnostics';
it('masks quoted secrets with spaces, short credentials and cookies', () => {
  const result = redactDiagnostic('{"password":"p a s s","secret":"x","cookie":"sid=private"}');
  expect(JSON.parse(result)).toEqual({ password: '[redacted]', secret: '[redacted]', cookie: '[redacted]' });
});
it('bounds capture and retains stable source-aware ids', () => {
  for (let i=0; i<1100; i++) recordDiagnostic('DEBUG', `request ${i}`, { source: 'network', status: 200 });
  const logs = diagnosticSnapshot();
  expect(logs).toHaveLength(1000);
  expect(logs[0].id).toBeLessThan(logs[999].id);
  expect(logs[999].source).toBe('network');
  expect(diagnosticSnapshot()).toBe(logs);
});
it('exports valid JSON and redacts backend exceptions at the export boundary', () => {
  recordDiagnostic('ERROR', 'password="local secret"');
  const json = diagnosticBundle([{ message: 'api_key="broker secret"', level: 'ERROR' }], [], null, 0);
  expect(() => JSON.parse(json)).not.toThrow();
  expect(json).not.toContain('broker secret');
  expect(json).not.toContain('local secret');
  expect(JSON.parse(json).sources.backend).toBe('available');
});
