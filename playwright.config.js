const { defineConfig } = require("@playwright/test");
module.exports = defineConfig({
  testDir: "e2e",
  timeout: 60000,
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: "http://localhost:8765",
    headless: true,
    channel: process.env.PROCTIX_TEST_BROWSER_CHANNEL || undefined,
    trace: "retain-on-failure",
    launchOptions: {
      args: [
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
      ],
    },
    permissions: ["camera", "microphone"],
  },
  webServer: {
    command: ".venv/bin/python scripts/e2e_server.py",
    url: "http://localhost:8765/health/live",
    reuseExistingServer: false,
    timeout: 30000,
  },
  reporter: [["list"], ["html", { open: "never" }]],
});
