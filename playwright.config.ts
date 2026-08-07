import { defineConfig, devices } from '@playwright/test';

// The only thing this repo ships to a browser is the reporting site under
// site/, so that is what the end-to-end suite drives. Astro's preview server
// serves the real static build (base `/lab`, as GitHub Pages does), which
// means the specs exercise the same output that gets deployed. baseURL carries
// only the origin, so specs navigate to the `/lab/` base path explicitly.
const PORT = Number(process.env.E2E_PORT ?? 4321);
const BASE_URL = process.env.E2E_BASE_URL ?? `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  // Skipped when E2E_BASE_URL points at an already-running site (e.g. the
  // deployed Pages build).
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: `npm run build && npm run preview -- --host 127.0.0.1 --port ${PORT}`,
        cwd: 'site',
        url: `${BASE_URL}/lab/`,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
});
