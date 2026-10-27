import { expect, it } from 'vitest';
import { configurationAllowed, type ConfigurationCapabilities } from '../useConfigurationCapabilities';
const policy: ConfigurationCapabilities = { user_id: 'member', revision: 1, active: true, administrator: false,
  sections: { brokers: { visible: true, grants: [{ action: 'update', resource_id: 'one', expires_at_ms: null }] },
    telegram: { visible: false, grants: [] }, integrations: { visible: false, grants: [] } } };
it('denies missing, hidden, expired and unscoped actions', () => {
  expect(configurationAllowed(undefined, 'brokers', 'update')).toBe(false);
  expect(configurationAllowed(policy, 'brokers', 'update', 'one')).toBe(true);
  expect(configurationAllowed(policy, 'brokers', 'reveal', 'one')).toBe(false);
  expect(configurationAllowed(policy, 'brokers', 'update', 'two')).toBe(false);
  expect(configurationAllowed({ ...policy, active: false }, 'brokers', 'update', 'one')).toBe(false);
  expect(configurationAllowed({ ...policy, sections: { ...policy.sections, brokers: { visible: false, grants: policy.sections.brokers.grants } } }, 'brokers', 'update', 'one')).toBe(false);
  expect(configurationAllowed({ ...policy, sections: { ...policy.sections, brokers: { visible: true, grants: [{ action: 'update', resource_id: 'own', expires_at_ms: 1 }] } } }, 'brokers', 'update', 'one')).toBe(false);
});
