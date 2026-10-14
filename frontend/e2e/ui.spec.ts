import { Page, expect, test } from "@playwright/test";

const HEALTH_OK = { status: "ok", version: "0.1.0", index_loaded: true, answer_enabled: true };

const EVIDENCE = [
  {
    rank: 1,
    file: "src/pkg/signer.py",
    line_start: 10,
    line_end: 30,
    category: "source",
    symbol: { qualified_name: "Signer.verify" },
  },
  {
    rank: 2,
    file: "tests/test_signer.py",
    line_start: 5,
    line_end: 20,
    category: "test",
    symbol: null,
  },
];

const CITATION = {
  evidence_id: "E1",
  file: "src/pkg/signer.py",
  line_start: 10,
  line_end: 30,
  symbol: "Signer.verify",
};

// Next adds its own empty role=alert route announcer, so the app's notices are matched as p[role=alert].
function answer(over: Record<string, unknown>) {
  return {
    question: "q",
    status: "answered",
    claims: [],
    missing: "",
    validation: { valid: true, reasons: [] },
    evidence: { evidence: EVIDENCE },
    model: { returned_model_version: "test-model", latency_ms: 1 },
    ...over,
  };
}

type Mocked = { url: string; status: number; body: unknown };

async function mock(page: Page, health: unknown, route?: Mocked) {
  await page.route("**/api/health", (r) => r.fulfill({ json: health }));
  if (route) {
    await page.route(route.url, (r) => r.fulfill({ status: route.status, json: route.body }));
  }
}

async function ask(page: Page, text: string, button = "Ask") {
  await page.getByLabel("Your question").fill(text);
  await page.getByRole("button", { name: button, exact: true }).click();
}

test.describe("with a mocked API", () => {
  test("names the indexed repository and offers example questions only for Flask", async ({ page }) => {
    await page.route("**/api/health", (r) => r.fulfill({ json: HEALTH_OK }));
    await page.route("**/api/v1/index/status", (r) =>
      r.fulfill({ json: { repository: "itsdangerous", files: 15, chunks: 156 } }),
    );
    await page.goto("/");
    await expect(page.getByText("Indexed repository:")).toContainText("itsdangerous");
    await expect(page.locator("button.link")).toHaveCount(0);

    await page.unroute("**/api/v1/index/status");
    await page.route("**/api/v1/index/status", (r) =>
      r.fulfill({ json: { repository: "flask_evidence", files: 83, chunks: 1027 } }),
    );
    await page.reload();
    await expect(page.locator("button.link")).toHaveCount(3);
  });

  test("renders an answer and navigates from a citation to its evidence", async ({ page }) => {
    const body = answer({
      claims: [{ text: "Signatures are checked by Signer.verify.", citations: [CITATION] }],
    });
    await mock(page, HEALTH_OK, { url: "**/api/v1/answer", status: 200, body });
    await page.goto("/");
    await ask(page, "Where is the signature checked?");
    await expect(page.getByText("Answered", { exact: true })).toBeVisible();
    await expect(page.getByText("Signatures are checked by Signer.verify.")).toBeVisible();
    const chip = page.getByRole("link", { name: /\[E1\] src\/pkg\/signer\.py:10-30/ });
    await expect(chip).toBeVisible();
    await chip.click();
    await expect(page).toHaveURL(/#ev-1$/);
    const target = page.locator("#ev-1");
    await expect(target).toBeInViewport();
    await expect(target).toContainText("src/pkg/signer.py:10-30");
    await expect(target).toHaveCSS("outline-style", "solid"); // the :target highlight
    await expect(page.locator("#ev-2")).not.toHaveCSS("outline-style", "solid");
  });

  test("a v2 chunk id (E1b) links to its file's evidence item", async ({ page }) => {
    const cite = { ...CITATION, evidence_id: "E1b", line_start: 40, line_end: 50, symbol: null };
    const body = answer({ claims: [{ text: "x", citations: [cite] }] });
    await mock(page, HEALTH_OK, { url: "**/api/v1/answer", status: 200, body });
    await page.goto("/");
    await ask(page, "x?");
    await page.getByRole("link", { name: /\[E1b\]/ }).click();
    await expect(page).toHaveURL(/#ev-1$/);
  });

  test("shows an abstention with what the evidence does not cover", async ({ page }) => {
    const body = answer({ status: "insufficient_evidence", missing: "Nothing about analytics." });
    await mock(page, HEALTH_OK, { url: "**/api/v1/answer", status: 200, body });
    await page.goto("/");
    await ask(page, "How does it send analytics?");
    await expect(page.getByText("Not enough evidence")).toBeVisible();
    await expect(page.getByText("Nothing about analytics.")).toBeVisible();
    await expect(page.locator(".cite")).toHaveCount(0);
  });

  test("shows a partial answer and its missing part", async ({ page }) => {
    const body = answer({
      status: "partial",
      missing: "Callers are not shown.",
      claims: [{ text: "Defined in signer.py.", citations: [CITATION] }],
    });
    await mock(page, HEALTH_OK, { url: "**/api/v1/answer", status: 200, body });
    await page.goto("/");
    await ask(page, "who calls it?");
    await expect(page.getByText("Partly answered")).toBeVisible();
    await expect(page.getByText("Callers are not shown.")).toBeVisible();
  });

  test("withholds an answer that failed validation and says why", async ({ page }) => {
    const body = answer({
      status: "invalid_output",
      validation: { valid: false, reasons: ["claim 1: no citations"] },
    });
    await mock(page, HEALTH_OK, { url: "**/api/v1/answer", status: 200, body });
    await page.goto("/");
    await ask(page, "anything");
    await expect(page.locator(".badge")).toContainText("failed validation");
    await expect(page.getByText(/claim 1: no citations/)).toBeVisible();
  });

  test("falls back to evidence only when no model key is configured", async ({ page }) => {
    await mock(page, { ...HEALTH_OK, answer_enabled: false }, {
      url: "**/api/v1/evidence?*",
      status: 200,
      body: { evidence: EVIDENCE },
    });
    await page.goto("/");
    await expect(page.getByText(/Evidence-only mode/)).toBeVisible();
    await ask(page, "signature", "Find evidence");
    await expect(page.getByText("Evidence (2 files)")).toBeVisible();
    await expect(page.getByText("src/pkg/signer.py:10-30")).toBeVisible();
    await expect(page.locator(".badge")).toHaveCount(0);
  });

  test("explains a missing index and disables asking", async ({ page }) => {
    await mock(page, { ...HEALTH_OK, index_loaded: false, answer_enabled: false });
    await page.goto("/");
    await expect(page.locator("p[role=alert]")).toContainText("No index is loaded");
    await page.getByLabel("Your question").fill("hello");
    await expect(page.getByRole("button", { name: "Find evidence" })).toBeDisabled();
  });

  test("reports an unreachable API", async ({ page }) => {
    await page.route("**/api/health", (r) => r.abort("connectionrefused"));
    await page.goto("/");
    await expect(page.locator("p[role=alert]")).toContainText("Cannot reach the API");
  });

  test("shows a quota error from the answer endpoint", async ({ page }) => {
    const body = { quota_exhausted: true, error: "daily limit reached", evidence: { evidence: [] } };
    await mock(page, HEALTH_OK, { url: "**/api/v1/answer", status: 429, body });
    await page.goto("/");
    await ask(page, "anything");
    await expect(page.locator("p[role=alert]")).toContainText(
      "Model quota exhausted: daily limit reached",
    );
  });

  test("shows a server error and recovers on the next question", async ({ page }) => {
    await page.route("**/api/health", (r) => r.fulfill({ json: HEALTH_OK }));
    let calls = 0;
    await page.route("**/api/v1/answer", (r) => {
      calls += 1;
      if (calls === 1) {
        return r.fulfill({
          status: 502,
          json: { quota_exhausted: false, error: "model unavailable", evidence: { evidence: [] } },
        });
      }
      return r.fulfill({ json: answer({ status: "insufficient_evidence", missing: "none" }) });
    });
    await page.goto("/");
    await ask(page, "first");
    await expect(page.locator("p[role=alert]")).toContainText("model unavailable");
    await ask(page, "second");
    await expect(page.getByText("Not enough evidence")).toBeVisible();
    await expect(page.locator("p[role=alert]")).toHaveCount(0);
  });

  test("a 503 detail message from the API is shown as written", async ({ page }) => {
    const body = { detail: "the answer layer is not configured" };
    await mock(page, HEALTH_OK, { url: "**/api/v1/answer", status: 503, body });
    await page.goto("/");
    await ask(page, "anything");
    await expect(page.locator("p[role=alert]")).toContainText("the answer layer is not configured");
  });
});

// Needs the real stack (Docker or local) with the itsdangerous index and a model key.
test.describe("against the real stack", () => {
  test.skip(process.env.REAL_STACK !== "1", "set REAL_STACK=1 with a running stack");

  test("a real question renders valid citations that scroll to their evidence", async ({ page }) => {
    await page.goto("/");
    await ask(page, "Where is the signature verified?");
    await expect(page.locator(".badge")).toBeVisible({ timeout: 90_000 });
    await expect(page.locator(".badge")).toHaveText("Answered");
    const chip = page.locator("a.cite").first();
    await expect(chip).toContainText("src/itsdangerous/signer.py");
    await chip.click();
    await expect(page).toHaveURL(/#ev-\d+$/);
    await expect(page.locator("li:target")).toContainText("src/itsdangerous/signer.py");
  });

  test("a real false-premise question abstains", async ({ page }) => {
    await page.goto("/");
    await ask(page, "How does itsdangerous send analytics events?");
    await expect(page.locator(".badge")).toBeVisible({ timeout: 90_000 });
    await expect(page.locator(".badge")).toHaveText("Not enough evidence");
  });
});
