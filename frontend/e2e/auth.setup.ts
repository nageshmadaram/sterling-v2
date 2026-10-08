import { test as setup, expect } from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const authDir = path.join(__dirname, '.auth');
const authFile = path.join(authDir, 'kite.json');

setup('app shell + storageState', async ({ page }) => {
  if (!fs.existsSync(authDir)) fs.mkdirSync(authDir, { recursive: true });

  // Do not require, create or activate a Kite account here. Most browser specs
  // are UI-contract tests and stub their broker state explicitly. Requiring a
  // broker account in the shared setup makes every browser test fail for an
  // unrelated operational prerequisite and previously tempted setup code to
  // mutate a real account.
  await page.goto('/');

  // The navigation rail is part of the authenticated Sterling application shell
  // and does not require an active broker session.
  const shell = page.getByTestId('rail-more');
  await expect(shell).toBeVisible({ timeout: 25_000 });

  // Playwright storageState persists cookies/localStorage. Sterling JWTs are kept
  // in sessionStorage, so no bearer credential is written to this reusable file.
  await page.context().storageState({ path: authFile });
});
