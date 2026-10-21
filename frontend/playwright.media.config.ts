import {defineConfig} from '@playwright/test';
const baseURL=process.env.MEDIA_BASE_URL||'http://127.0.0.1:5188';
export default defineConfig({
  testDir:'./e2e/media',fullyParallel:false,workers:1,timeout:30_000,
  reporter:[['list']],use:{baseURL,viewport:{width:390,height:844},
    launchOptions:{executablePath:process.env.MEDIA_CHROME_PATH||undefined},
    trace:'retain-on-failure',screenshot:'only-on-failure'},
  webServer:process.env.MEDIA_BASE_URL?undefined:{command:'node scripts/serve-media-preview.mjs',url:baseURL,reuseExistingServer:!process.env.CI},
});
