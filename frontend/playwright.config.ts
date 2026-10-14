import { defineConfig } from "@playwright/test";

// Runs in the machine's installed Chrome (no browser download). UI_URL points at a running UI.
export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.UI_URL ?? "http://127.0.0.1:3000",
    channel: "chrome",
    headless: true,
    screenshot: "only-on-failure",
  },
});
