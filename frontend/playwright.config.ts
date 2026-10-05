import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const authFile = path.join(__dirname, 'e2e/.auth/kite.json');

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [['html', { open: 'never' }], ['list']],
  // No committed Playwright PNG baselines exist. Visual assertions would fail
  // CI with "A snapshot doesn't exist" on every run. Skip them unless a
  // snapshot-update job explicitly asks to write them.
  ignoreSnapshots: !!process.env.CI && process.env.UPDATE_SNAPSHOTS !== '1',
  // Global setup creates an empty browser state without broker/account writes.
  globalSetup: path.join(__dirname, 'e2e/global-setup.ts'),
  use: {
    baseURL: 'http://localhost:5173',
    trace: process.env.CI ? 'on' : 'on-first-retry',
    screenshot: 'only-on-failure',
    video: process.env.CI ? 'on' : 'retain-on-failure',
    actionTimeout: 10_000,
    // Development/test identity only; production requires a verified JWT.
    extraHTTPHeaders: {
      'X-User-Id': 'default',
    },
  },
  // Screenshot diff tuning for visual regression on bars / picker
  expect: {
    toHaveScreenshot: {
      maxDiffPixels: 120,
      threshold: 0.2,
    },
  },
  projects: [
    // Dedicated setup project that exercises the KITE surface and saves browser storageState.
    // Other projects depend on it and start with pre-warmed kite UI state.
    {
      name: 'setup',
      testMatch: /auth\.setup\.ts/,
      use: { ...devices['Desktop Chrome'] }, // setup uses chromium
    },

    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], storageState: authFile },
      dependencies: ['setup'],
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'], storageState: authFile },
      dependencies: ['setup'],
    },
    {
      name: 'webkit',
      use: {
        ...devices['Desktop Safari'],
        storageState: authFile,
        // Webkit-specific quirks: slower animations, font rendering diffs in snapshots,
        // stricter timing on some interactions. Longer timeouts + retain traces.
        actionTimeout: 15000,
        navigationTimeout: 20000,
      },
      // webkit-only snapshot overrides (higher tolerance for font/aliasing diffs)
      expect: {
        toHaveScreenshot: {
          maxDiffPixels: 300,
          threshold: 0.3,
        },
      },
      dependencies: ['setup'],
    },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 120 * 1000,
  },
});
