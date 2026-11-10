import { defineConfig, devices } from '@playwright/test';

/**
 * Config for the Saved freebies spec only (`npm run test:e2e:saved`, FREEJ-28).
 *
 * The spec needs no login and no backend: the catalogue is the 24 fixture offers. There is no auth setup
 * project and no `webServer`: build and start the app yourself with `CATALOGUE_SOURCE=fixture` (a production
 * build, `next start`) and point E2E_BASE_URL at it. See docs/developer/freejoy-frontend.md#saved-freebies-freej-28.
 */
export default defineConfig({
  testDir: './src/__tests__/e2e',
  testMatch: /saved\.spec\.ts/,
  reporter: [['list']],
  timeout: 30_000,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:3000',
    ...devices['Desktop Chrome'],
  },
});
