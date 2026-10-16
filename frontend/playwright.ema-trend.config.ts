import { defineConfig, devices } from '@playwright/test';

// Dedicated fixture server: no login setup, credentials, broker or running app.
// Each test intercepts every API request before opening the workflow harness.
export default defineConfig({
  testDir: './e2e', testMatch: 'ema-trend.spec.ts', fullyParallel: false, workers: 1,
  reporter: [['list']], timeout: 45_000,
  use: { baseURL: 'http://127.0.0.1:5188', screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
  ],
  webServer: {
    command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5188 --strictPort',
    url: 'http://127.0.0.1:5188/e2e/fixtures/ema-trend-harness.html',
    reuseExistingServer: false, timeout: 30_000,
  },
});
