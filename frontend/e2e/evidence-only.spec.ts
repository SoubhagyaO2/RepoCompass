import { expect, test } from "@playwright/test";

// Run only against a real stack started without a model key (answer_enabled false).
test.skip(process.env.EVIDENCE_ONLY !== "1", "set EVIDENCE_ONLY=1 with a keyless stack");

test("a keyless real stack shows retrieved evidence and no written answer", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/Evidence-only mode/)).toBeVisible();
  await page.getByLabel("Your question").fill("Where is the signature verified?");
  await page.getByRole("button", { name: "Find evidence", exact: true }).click();
  await expect(page.getByText(/Evidence \(\d+ files\)/)).toBeVisible();
  await expect(page.locator(".evidence li").first()).toContainText("src/itsdangerous");
  await expect(page.locator(".badge")).toHaveCount(0);
});
