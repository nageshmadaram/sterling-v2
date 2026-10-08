import type { FullConfig } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const AUTH_DIR = path.join(__dirname, '.auth');
const STATE_FILE = path.join(AUTH_DIR, 'kite.json');

async function globalSetup(_config: FullConfig) {
  if (!fs.existsSync(AUTH_DIR)) {
    fs.mkdirSync(AUTH_DIR, { recursive: true });
  }

  // CI bootstrap must be read-only with respect to broker/account state. Earlier
  // setup code searched for a real Kite account and could activate it, making an
  // ordinary browser test mutate the operator's execution identity. Individual
  // specs stub the broker state they need; broker-connected acceptance belongs
  // to the separate live acceptance harness, never Playwright.
  //
  // Keep a valid empty storageState file for projects that declare one. Sterling
  // access tokens live in sessionStorage and are intentionally not persisted as
  // a reusable browser artifact.
  fs.writeFileSync(
    STATE_FILE,
    JSON.stringify({ cookies: [], origins: [] }, null, 2),
  );

  console.log('[global-setup] Complete. No broker/account state was changed.');
}

export default globalSetup;
