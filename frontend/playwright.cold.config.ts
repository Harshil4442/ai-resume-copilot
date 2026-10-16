import { defineConfig, devices } from "@playwright/test";

// Deliberately separate from mocked UI tests. The Python orchestrator owns the
// real BFF/API/SQL processes and environment. No server is reused implicitly.
export default defineConfig({
  testDir: "./journey",
  testMatch: "cold-no-ai.spec.ts",
  workers: 1,
  retries: 0,
  timeout: 120_000,
  reporter: [["list"], ["json", { outputFile: process.env.COLD_BROWSER_REPORT }]],
  use: { ...devices["Desktop Chrome"], baseURL: process.env.COLD_BROWSER_FRONTEND_URL,
    // Only this disposable loopback journey uses its own self-signed certificate.
    ignoreHTTPSErrors: true,
    actionTimeout: 15_000, navigationTimeout: 30_000, trace: "off", screenshot: "off", video: "off" },
});
