import { expect, test } from "@playwright/test";

// Opt-in: needs the real stack with outbound access to github.com. It clones and indexes real
// repositories through the UI (about a minute for a small one).
test.skip(process.env.REAL_INDEX !== "1", "set REAL_INDEX=1 with a running stack");

test("indexes a real public repository from its URL and answers about it", async ({ page }) => {
  test.setTimeout(420_000);
  await page.goto("/");
  await page.getByLabel("GitHub repository URL").fill("https://github.com/pallets/markupsafe");
  await page.getByRole("button", { name: "Index Repository" }).click();
  await expect(page.getByRole("list", { name: "Indexing progress" })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("Index ready", { timeout: 360_000 });
  await expect(page.getByText("Indexed repository:")).toContainText("pallets/markupsafe");

  await page.getByLabel("Your question").fill("What does the Markup class do?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.locator(".badge")).toBeVisible({ timeout: 90_000 });
  await expect(page.locator(".badge")).toHaveText("Answered");
  const chip = page.locator("a.cite").first();
  await expect(chip).toContainText("src/markupsafe/");
  await chip.click();
  await expect(page.locator("li:target")).toContainText("src/markupsafe/");

  // the first repository is still there and selectable: indexing one did not replace the other
  const group = page.getByRole("group", { name: "Indexed repositories" });
  await expect(group.getByRole("button")).toHaveCount(2);
  await group.getByRole("button", { name: "itsdangerous" }).click();
  await expect(page.getByText("Indexed repository:")).toContainText("itsdangerous");
});

test("a repository that does not exist fails with a readable message and no server paths", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/");
  await page.getByLabel("GitHub repository URL").fill("https://github.com/pallets/no-such-repository-xyz");
  await page.getByRole("button", { name: "Index Repository" }).click();
  const failure = page.locator("div[role=alert]").filter({ hasText: "failed" });
  await expect(failure).toBeVisible({ timeout: 150_000 });
  const text = await failure.innerText();
  expect(text).not.toMatch(/\/data\/|\/app\//);
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
});
