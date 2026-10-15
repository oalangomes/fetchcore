import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  timeout: 30000,
  retries: process.env.CI ? 1 : 0,
  use: { baseURL: 'http://127.0.0.1:15173', trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: 'python -m uvicorn tools.explorer.api.app:app --app-dir ../../.. --host 127.0.0.1 --port 8765',
      url: 'http://127.0.0.1:8765/api/health',
      reuseExistingServer: !process.env.CI,
      timeout: 30000,
      env: { FETCHCORE_WORKSPACE: '../fixtures/demo-repo', FETCHCORE_REPOSITORY: 'demo/catalog', FETCHCORE_SOLR_URL: 'http://127.0.0.1:8983/solr', FETCHCORE_SOLR_COLLECTION: 'fetchcore' },
    },
    {
      command: 'npm run dev -- --port 15173',
      url: 'http://127.0.0.1:15173',
      reuseExistingServer: !process.env.CI,
      timeout: 30000,
    },
  ],
})
