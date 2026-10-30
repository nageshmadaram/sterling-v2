import { beforeEach, expect, it, vi } from 'vitest';

const requests = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../src/services/api', () => ({ api: requests }));

import {
  assertKiteWriteAllowed,
  assertGlobalRiskControlAllowed,
  canManageGlobalRisk,
  canWriteKite,
} from '../src/services/workspacePolicy';

beforeEach(() => vi.resetAllMocks());

const enabled = { kite: true, tv: false, radio: false };

it('accepts only explicitly granted Kite writer roles', () => {
  for (const role of ['admin', 'operator', 'user']) {
    expect(canWriteKite({ role, features: enabled })).toBe(true);
  }
  for (const role of ['viewer', 'superuser', '', 'ADMIN']) {
    expect(canWriteKite({ role, features: enabled })).toBe(false);
  }
  expect(canWriteKite(null)).toBe(false);
  expect(canWriteKite({ role: 'admin' })).toBe(false);
  expect(canWriteKite({ role: 'operator', features: { kite: false } })).toBe(false);
});

it('global risk management requires actual designated admin state', () => {
  expect(canManageGlobalRisk({ role: 'admin', administrator: true, features: enabled })).toBe(true);
  expect(canManageGlobalRisk({ role: 'admin', administrator: false, features: enabled })).toBe(false);
  expect(canManageGlobalRisk({ role: 'operator', administrator: true, features: enabled })).toBe(false);
});

it('re-checks latest server grant before each prospective order', async () => {
  requests.get.mockResolvedValueOnce({ role: 'operator', features: enabled })
    .mockResolvedValueOnce({ role: 'viewer', features: enabled });
  await expect(assertKiteWriteAllowed()).resolves.toBeUndefined();
  await expect(assertKiteWriteAllowed()).rejects.toThrow('read-only');
  expect(requests.get).toHaveBeenNthCalledWith(1, '/api/v1/auth/me');
  expect(requests.get).toHaveBeenNthCalledWith(2, '/api/v1/auth/me');
});

it('fails closed on unavailable identity policy', async () => {
  requests.get.mockRejectedValue(new Error('Access could not be verified'));
  await expect(assertKiteWriteAllowed()).rejects.toThrow('Access could not be verified');
});

it('never sends a global risk mutation without a fresh designated-admin policy', async () => {
  requests.get.mockResolvedValueOnce({
    role: 'viewer', administrator: false, features: enabled,
  }).mockResolvedValueOnce({
    role: 'admin', administrator: true, features: enabled,
  });
  await expect(assertGlobalRiskControlAllowed()).rejects.toThrow('designated administrator');
  await expect(assertGlobalRiskControlAllowed()).resolves.toBeUndefined();
  expect(requests.get).toHaveBeenCalledTimes(2);
});
