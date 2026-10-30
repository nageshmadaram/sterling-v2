import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { expect, it } from 'vitest';

const read = (rel: string) => JSON.parse(
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8'),
);
const manifest = read('../package.json');
const lock = read('../package-lock.json');

it('locks the Expo SDK 58 compatibility matrix and root manifest identically', () => {
  const expected: Record<string, string> = {
    expo: '58.0.21',
    'expo-constants': '58.0.14',
    'expo-image': '58.0.18',
    'expo-linking': '58.0.15',
    'expo-router': '58.0.28',
    'react-native': '0.88.1',
  };
  expect(lock.lockfileVersion).toBe(3);
  for (const [name, version] of Object.entries(expected)) {
    expect(lock.packages[`node_modules/${name}`]?.version, name).toBe(version);
    expect(lock.packages[''].dependencies[name], name).toBe(manifest.dependencies[name]);
    expect(manifest.dependencies[name], name).toBe(
      name === 'react-native' ? version : `~${version}`
    );
  }
});
