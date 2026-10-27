import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e', testMatch: ['administration-isolation.spec.ts', 'workspace-flow.spec.ts'],
  outputDir: process.env.STERLING_ADMIN_TEST_OUTPUT || './test-results/administration',
  workers: 1, reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:5193', browserName: 'chromium',
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {} },
  webServer: { command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5193 --strictPort',
    url: 'http://127.0.0.1:5193', reuseExistingServer: !process.env.CI, timeout: 30_000 },
});
