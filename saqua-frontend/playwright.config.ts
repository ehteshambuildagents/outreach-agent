import { defineConfig, devices } from "@playwright/test";

/**
 * Deterministic E2E for the chat conversation-restoration lifecycle. Every /api
 * call is mocked in the spec (see e2e/restore.spec.ts), so these tests never hit
 * the real backend, spend no API money, and are fully reproducible. They exercise
 * the REAL Next app (middleware, routing, client state) against a dev server.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  timeout: 60_000,
  use: {
    baseURL: "http://localhost:3200",
    trace: "off",
    headless: true,
    // Next dev compiles a route on first hit; give navigation room on cold start.
    navigationTimeout: 45_000,
    actionTimeout: 15_000,
    // If only the full chromium build is installed (not the headless-shell binary),
    // point at it via PLAYWRIGHT_CHROMIUM_PATH; otherwise Playwright resolves its
    // own browser normally. Keeps the config portable across machines/CI.
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH }
      : undefined,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npx next dev -p 3200",
    url: "http://localhost:3200",
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
