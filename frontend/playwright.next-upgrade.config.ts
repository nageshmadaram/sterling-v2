import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './e2e',
  testMatch: ['next-upgrade-readonly.spec.ts', 'administration-isolation.spec.ts'],
  workers: 1, retries: 0, reporter: 'list',
  outputDir: './test-results/increment-3',
  use: {
    baseURL: 'http://127.0.0.1:5194',
    browserName: 'chromium',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {},
  },
  webServer: {
    command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5194 --strictPort',
    url: 'http://127.0.0.1:5194',
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
