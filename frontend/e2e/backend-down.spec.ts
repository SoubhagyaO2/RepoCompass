import { expect, test } from "@playwright/test";

// Run only while the backend is stopped (docker compose stop backend): a real network failure,
// not a mocked one. The UI proxy then cannot reach the API.
test.skip(process.env.BACKEND_DOWN !== "1", "set BACKEND_DOWN=1 with the backend stopped");

test("with the backend really stopped, the UI says it cannot reach the API", async ({ page }) => {
  await page.goto("/");
  const notice = page.locator("p[role=alert]");
  await expect(notice).toContainText("Cannot reach the API", { timeout: 60_000 }); // the proxy is slow to fail
  await expect(page.getByRole("button", { name: /Ask|Find evidence/ })).toBeDisabled();
});
