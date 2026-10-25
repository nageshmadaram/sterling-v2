import type { NavItem } from '../components/kite/KiteLayout';

/** Shared by the shell header and the actual trading pane. */
export function notificationDestination(value: string | null): NavItem | null {
  return value === 'pcr' ? 'pcr' : value === 'alerts' ? 'positions' : value === 'signals' ? 'dashboard' : null;
}
