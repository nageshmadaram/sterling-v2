import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './e2e', testMatch: 'ema-trend.spec.ts', fullyParallel: true, workers: 2,
  reporter: [['list']], outputDir: './test-results/ema-trend',
  use: { baseURL: 'http://127.0.0.1:5173', viewport: { width: 1440, height: 1000 },
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROME_EXECUTABLE || '/usr/bin/google-chrome', args: ['--no-sandbox'] },
    screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  webServer: { command: 'npm run dev -- --host 127.0.0.1', url: 'http://127.0.0.1:5173', reuseExistingServer: true },
});
