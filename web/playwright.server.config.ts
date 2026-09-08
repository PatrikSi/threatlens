import { defineConfig } from '@playwright/test'

const baseURL = process.env.THREATLENS_BROWSER_BASE_URL
if (!baseURL || !process.env.THREATLENS_BROWSER_CONTROL_TOKEN) {
  throw new Error('Use web/browser/server/run.py; real-server tests cannot target an existing deployment')
}

export default defineConfig({
  testDir: './browser/server', testMatch: '**/*.browser.ts', workers: 1,
  forbidOnly: Boolean(process.env.CI), retries: 0, timeout: 45_000,
  outputDir: './test-results/real-server',
  use: { baseURL, viewport: { width: 1440, height: 1000 }, trace: 'retain-on-failure' },
  projects: ['chromium', 'firefox', 'webkit'].map((browserName) => ({
    name: browserName, use: { browserName: browserName as 'chromium' | 'firefox' | 'webkit' },
  })),
  webServer: {
    command: 'npx vite --config vite.browser-server.config.ts', url: baseURL,
    env: { VITE_API_BASE_URL: '/api/v1' }, reuseExistingServer: false,
  },
})
