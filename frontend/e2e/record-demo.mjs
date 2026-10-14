// Records the demo video and screenshots against a running stack (itsdangerous index + model key).
//   node e2e/record-demo.mjs [outDir]
// Output: demo.webm and screenshots in outDir (default ../docs/media). Needs the installed Chrome.
import { chromium } from "@playwright/test";
import { mkdirSync, readdirSync, renameSync, rmSync } from "node:fs";
import { join, resolve } from "node:path";

const out = resolve(process.argv[2] ?? "../docs/media");
const tmp = join(out, "_rec");
const base = process.env.UI_URL ?? "http://127.0.0.1:3000";
rmSync(tmp, { recursive: true, force: true });
mkdirSync(tmp, { recursive: true });

const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext({
  viewport: { width: 1000, height: 720 },
  colorScheme: "light",
  recordVideo: { dir: tmp, size: { width: 1000, height: 720 } },
});
const page = await context.newPage();
const pause = (ms) => page.waitForTimeout(ms);

async function ask(question) {
  const box = page.getByLabel("Your question");
  await box.fill("");
  await box.pressSequentially(question, { delay: 35 });
  await pause(500);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await page.locator(".badge").waitFor({ timeout: 90_000 });
  await pause(1500);
}

await page.goto(base);
await page.getByText("Indexed repository:").waitFor();
await pause(1500);
await page.screenshot({ path: join(out, "ui-start.png") });

await ask("Where is the signature verified?");
await page.screenshot({ path: join(out, "ui-answer.png") });
await page.locator("a.cite").first().click(); // citation navigation: scrolls to and highlights the evidence
await pause(1800);
await page.screenshot({ path: join(out, "ui-citation.png") });

await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
await ask("How does itsdangerous send analytics events?");
await page.screenshot({ path: join(out, "ui-abstention.png") });
await pause(1500);

await context.close(); // flushes the video
await browser.close();
const video = readdirSync(tmp).find((f) => f.endsWith(".webm"));
renameSync(join(tmp, video), join(out, "demo.webm"));
rmSync(tmp, { recursive: true, force: true });
console.log("wrote", out);
