import { useQuery } from '@tanstack/react-query';
import { api } from '../utils/api';

export type ConfigurationSection = 'brokers' | 'telegram' | 'integrations';
export type ConfigurationAction = 'create' | 'update' | 'test' | 'recipients' | 'reveal';
export type ConfigurationGrant = { action: ConfigurationAction; resource_id: string; expires_at_ms: number | null };
export type ConfigurationCapabilities = {
  user_id: string; revision: number; administrator: boolean; active: boolean;
  sections: Record<ConfigurationSection, { visible: boolean; grants: ConfigurationGrant[] }>;
};
export const CONFIGURATION_CAPABILITIES_KEY = ['sterling-configuration-capabilities'];

export function configurationAllowed(data: ConfigurationCapabilities | undefined, section: ConfigurationSection,
  action?: ConfigurationAction, resource = 'own'): boolean {
  if (!data?.active || !data.sections?.[section]?.visible) return false;
  if (!action) return true;
  return Array.isArray(data.sections[section].grants) && data.sections[section].grants.some(grant => grant.action === action &&
    (grant.resource_id === 'own' || grant.resource_id === resource) &&
    (grant.expires_at_ms === null || grant.expires_at_ms > Date.now()));
}

export function useConfigurationCapabilities() {
  return useQuery({ queryKey: CONFIGURATION_CAPABILITIES_KEY,
    queryFn: () => api.get<ConfigurationCapabilities>('/api/v1/settings/capabilities'),
    staleTime: 0, refetchInterval: 10_000, refetchOnWindowFocus: true, retry: false });
}
