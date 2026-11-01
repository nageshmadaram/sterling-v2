import { api } from './api';

/** Mobile never infers trading authority from the presence of an auth token. */
export type WorkspacePolicy = {
  role?: string;
  administrator?: boolean;
  features?: { kite?: boolean; tv?: boolean; radio?: boolean };
};

const WRITER_ROLES = new Set(['admin', 'operator', 'user']);

export function canWriteKite(policy: WorkspacePolicy | null | undefined): boolean {
  return !!policy && policy.features?.kite === true && WRITER_ROLES.has(policy.role || '');
}

export function canManageGlobalRisk(policy: WorkspacePolicy | null | undefined): boolean {
  return canWriteKite(policy) && policy?.role === 'admin' && policy.administrator === true;
}

export async function currentWorkspacePolicy(): Promise<WorkspacePolicy> {
  // No cached policy or optimistic fallback: admin revocation and changes
  // become effective before each mobile mutation. The backend rechecks too.
  return api.get<WorkspacePolicy>('/api/v1/auth/me');
}

export async function assertKiteWriteAllowed(): Promise<void> {
  const policy = await currentWorkspacePolicy();
  if (!canWriteKite(policy)) {
    throw new Error('This is a read-only or unavailable Kite workspace.');
  }
}

export async function assertGlobalRiskControlAllowed(): Promise<void> {
  const policy = await currentWorkspacePolicy();
  if (!canManageGlobalRisk(policy)) {
    throw new Error('Only the designated administrator can change global trading safeguards.');
  }
}

/** Operator can engage a halt; lifting a global halt is admin-only. */
export async function assertKillSwitchChangeAllowed(nextEnabled: boolean): Promise<void> {
  if (nextEnabled) return assertKiteWriteAllowed();
  return assertGlobalRiskControlAllowed();
}

/** Mirror the backend's direction-aware loss policy; server is authoritative. */
export function loosensDailyLoss(
  current: { enabled: boolean; hard_halt_inr: number },
  next: { enabled: boolean; hard_halt_inr: number },
): boolean {
  if (!next.enabled) return !!current.enabled;
  if (!current.enabled) return false;
  return next.hard_halt_inr < current.hard_halt_inr;
}

export async function assertDailyLossChangeAllowed(
  current: { enabled: boolean; hard_halt_inr: number },
  next: { enabled: boolean; hard_halt_inr: number },
): Promise<void> {
  if (loosensDailyLoss(current, next)) return assertGlobalRiskControlAllowed();
  return assertKiteWriteAllowed();
}
