import { expect, it } from 'vitest';
import { resolveBuildSha } from '../../buildIdentity';
const gitAbsent = () => { throw new Error('Git is not installed in the image'); };
it('uses the image build input when Git is absent', () => {
  expect(resolveBuildSha(gitAbsent, 'a'.repeat(40))).toBe('a'.repeat(40));
});
it('keeps checkout identity authoritative over an environment override', () => {
  expect(resolveBuildSha(() => 'b'.repeat(40), 'a'.repeat(40))).toBe('b'.repeat(40));
});
it.each([undefined, '', 'UNKNOWN', 'bad', 'a'.repeat(39)])('rejects unknown or malformed image identity: %s', value => {
  expect(resolveBuildSha(gitAbsent, value)).toBe('UNKNOWN');
});
