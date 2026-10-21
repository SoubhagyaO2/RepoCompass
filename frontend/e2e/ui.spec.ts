import { Page, expect, test } from "@playwright/test";

const HEALTH_OK = {
  status: "ok",
  version: "0.1.0",
  index_loaded: true,
  answer_enabled: true,
  indexing_enabled: true,
};

function repo(name: string, over: Record<string, unknown> = {}) {
  return {
    id: `gh__${name}`,
    name,
    status: "ready",
    error: null,
    files: 15,
    chunks: 156,
    files_total: 15,
    default: false,
    ...over,
  };
}

// GET /v1/repositories returns `list`; a POST is left to whichever route handles it.
async function repos(page: Page, list: unknown[]) {
  await page.route("**/api/v1/repositories", (r) =>
    r.request().method() === "GET" ? r.fulfill({ json: list }) : r.fallback(),
  );
}

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
  test.beforeEach(async ({ page }) => {
    await repos(page, [repo("itsdangerous", { default: true })]);
  });

  test("names the indexed repository and offers examples only where it has them", async ({ page }) => {
    await page.route("**/api/health", (r) => r.fulfill({ json: HEALTH_OK }));
    for (const [name, examples] of [
      ["itsdangerous", 3],
      ["flask_evidence", 3],
      ["some-other-lib", 0],
    ] as const) {
      await page.unroute("**/api/v1/repositories");
      await repos(page, [repo(name, { default: true })]);
      await page.goto("/");
      await expect(page.getByText("Indexed repository:")).toContainText(name);
      await expect(page.locator("button.link")).toHaveCount(examples);
    }
  });

  test("clicking an example question runs it immediately", async ({ page }) => {
    let asked = "";
    await page.route("**/api/health", (r) => r.fulfill({ json: HEALTH_OK }));
    await page.route("**/api/v1/answer", (r) => {
      asked = r.request().postDataJSON().question;
      return r.fulfill({ json: answer({ claims: [{ text: "Found.", citations: [CITATION] }] }) });
    });
    await page.goto("/");
    await page.getByRole("button", { name: "Where is the signature verified?" }).click();
    await expect(page.getByText("Answered", { exact: true })).toBeVisible();
    expect(asked).toBe("Where is the signature verified?");
    await expect(page.getByLabel("Your question")).toHaveValue("Where is the signature verified?");
  });

  test("evidence code is syntax highlighted", async ({ page }) => {
    const code = ["def verify(self, value):", "    # check it", '    return "ok"'].join("\n");
    const evidence = EVIDENCE.map((e) => ({ ...e, text: code }));
    const body = answer({ claims: [{ text: "x", citations: [CITATION] }], evidence: { evidence } });
    await mock(page, HEALTH_OK, { url: "**/api/v1/answer", status: 200, body });
    await page.goto("/");
    await ask(page, "where?");
    await page.getByRole("link", { name: /\[E1\]/ }).click();
    const block = page.locator("#ev-1 pre");
    await expect(block.locator(".hljs-keyword").first()).toHaveText("def");
    await expect(block.locator(".hljs-comment")).toContainText("check it");
    await expect(block.locator(".hljs-string")).toContainText("ok");
  });

  test("the first screen shows the workflow, the index status and an empty evidence panel", async ({ page }) => {
    await page.route("**/api/health", (r) => r.fulfill({ json: HEALTH_OK }));
    await page.goto("/");
    const flow = page.getByRole("list", { name: "How RepoCompass works" });
    await expect(flow.locator("li")).toHaveText([/Question/, /Retrieval/, /Evidence-backed answer/]);
    await expect(flow.locator("li").first()).toHaveAttribute("data-state", "active");
    await expect(page.getByRole("status")).toContainText("Index ready");
    await expect(page.getByRole("status")).toContainText("Written answers on");
    await expect(page.getByText(/Retrieved source code appears here/)).toBeVisible();
  });

  test("the Ask button is disabled until there is a question, shows progress, then recovers", async ({ page }) => {
    await page.route("**/api/health", (r) => r.fulfill({ json: HEALTH_OK }));
    await page.route("**/api/v1/answer", async (r) => {
      await new Promise((resolve) => setTimeout(resolve, 800));
      await r.fulfill({ json: answer({ status: "insufficient_evidence", missing: "none" }) });
    });
    await page.goto("/");
    const button = page.getByRole("button", { name: "Ask", exact: true });
    await expect(button).toBeDisabled();
    await page.getByLabel("Your question").fill("anything");
    await expect(button).toBeEnabled();
    await button.click();
    const working = page.getByRole("button", { name: "Working…" });
    await expect(working).toBeDisabled();
    await expect(working).toHaveAttribute("aria-busy", "true");
    const flow = page.getByRole("list", { name: "How RepoCompass works" });
    await expect(flow.locator("li").nth(1)).toHaveAttribute("data-state", "active");
    await expect(page.getByText("Not enough evidence")).toBeVisible();
    await expect(flow.locator("li").nth(2)).toHaveAttribute("data-state", "active");
    await expect(page.getByRole("button", { name: "Ask", exact: true })).toBeEnabled();
  });

  test("an abstention is an explicit state, not an empty answer", async ({ page }) => {
    const body = answer({ status: "insufficient_evidence", missing: "Nothing about analytics." });
    await mock(page, HEALTH_OK, { url: "**/api/v1/answer", status: 200, body });
    await page.goto("/");
    await ask(page, "analytics?");
    await expect(page.locator(".card.answer.insufficient_evidence")).toBeVisible();
    await expect(page.getByText(/does not guess/)).toBeVisible();
  });

  test("clicking a citation opens the cited code in the evidence panel", async ({ page }) => {
    const code = ["def verify(self):", "    return True"].join("\n");
    const evidence = EVIDENCE.map((e) => ({ ...e, text: code }));
    const body = answer({
      claims: [{ text: "It is verified.", citations: [CITATION] }],
      evidence: { evidence },
    });
    await mock(page, HEALTH_OK, { url: "**/api/v1/answer", status: 200, body });
    await page.goto("/");
    await ask(page, "where?");
    await expect(page.locator("#ev-1 pre")).toBeHidden();
    await page.getByRole("link", { name: /\[E1\]/ }).click();
    await expect(page.locator("#ev-1 pre")).toContainText("def verify(self):");
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
    await mock(page, {
      ...HEALTH_OK,
      index_loaded: false,
      answer_enabled: false,
      indexing_enabled: false,
    });
    await repos(page, []);
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

test.describe("connecting a repository", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/health", (r) => r.fulfill({ json: HEALTH_OK }));
  });

  test("the connect section sits above the question box", async ({ page }) => {
    await repos(page, []);
    await page.goto("/");
    await expect(page.getByText("Paste a public GitHub repository URL to get started.")).toBeVisible();
    const connect = await page.getByRole("heading", { name: "Connect a repository" }).boundingBox();
    const question = await page.getByLabel("Your question").boundingBox();
    expect(connect!.y).toBeLessThan(question!.y);
    await expect(page.getByRole("button", { name: "Index Repository" })).toBeDisabled();
    await expect(page.getByLabel("Your question")).toBeVisible();
    await expect(page.getByRole("button", { name: "Find evidence" }).or(page.getByRole("button", { name: "Ask", exact: true }))).toBeDisabled();
  });

  test("a URL that is not a GitHub repository is refused without calling the server", async ({ page }) => {
    await repos(page, []);
    let posts = 0;
    await page.route("**/api/v1/repositories", (r) => {
      if (r.request().method() === "POST") posts += 1;
      return r.fallback();
    });
    await page.goto("/");
    for (const bad of ["https://gitlab.com/o/r", "https://github.com/onlyowner", "http://github.com/o/r"]) {
      await page.getByLabel("GitHub repository URL").fill(bad);
      await page.getByRole("button", { name: "Index Repository" }).click();
      await expect(page.locator("p[role=alert]")).toContainText("public GitHub repository URL");
    }
    expect(posts).toBe(0);
  });

  test("indexing shows its stages, becomes ready, and questions go to that repository", async ({ page }) => {
    const sequence = ["queued", "cloning", "indexing", "ready"];
    let polls = 0;
    let listed: unknown[] = [];
    await page.route("**/api/v1/repositories", async (r) => {
      if (r.request().method() === "POST") {
        const body = r.request().postDataJSON();
        expect(body.url).toBe("https://github.com/acme/widgets");
        listed = [repo("acme/widgets", { id: "gh__acme__widgets", status: "queued", files: null, chunks: null })];
        return r.fulfill({ status: 202, json: listed[0] });
      }
      const status = sequence[Math.min(polls++, sequence.length - 1)];
      const ready = status === "ready";
      listed = [
        repo("acme/widgets", {
          id: "gh__acme__widgets",
          status,
          files_total: 3,
          files: ready ? 3 : null,
          chunks: ready ? 9 : null,
        }),
      ];
      return r.fulfill({ json: listed });
    });
    let asked: Record<string, unknown> = {};
    await page.route("**/api/v1/answer", (r) => {
      asked = r.request().postDataJSON();
      return r.fulfill({ json: answer({ status: "insufficient_evidence", missing: "none" }) });
    });
    await page.goto("/");
    await page.getByLabel("GitHub repository URL").fill("https://github.com/acme/widgets");
    await page.getByRole("button", { name: "Index Repository" }).click();
    const stages = page.getByRole("list", { name: "Indexing progress" });
    await expect(stages).toBeVisible();
    await expect(stages.locator("li[data-state=active]")).toHaveText(/Cloning|Indexing|Queued/);
    await expect(page.getByRole("status")).toContainText("Indexing…");
    await expect(page.getByLabel("Your question")).toBeVisible();
    await expect(page.getByRole("button", { name: "Ask", exact: true })).toBeDisabled();
    await expect(page.getByRole("status")).toContainText("Index ready", { timeout: 20_000 });
    await expect(page.getByText("Indexed repository:")).toContainText("acme/widgets");
    await expect(stages).toBeHidden();
    await ask(page, "Where is it?");
    await expect(page.getByText("Not enough evidence")).toBeVisible();
    expect(asked.repository).toBe("gh__acme__widgets");
  });

  test("an indexing job the server loses track of is reported instead of spinning forever", async ({ page }) => {
    let polls = 0;
    let posts = 0;
    const cloning = repo("acme/widgets", { id: "gh__acme__widgets", status: "cloning", files: null, chunks: null });
    await page.route("**/api/v1/repositories", (r) => {
      if (r.request().method() === "POST") {
        posts += 1;
        return r.fulfill({ status: 202, json: cloning });
      }
      polls += 1;
      // the server restarted: it knows nothing about the job any more
      return r.fulfill({ json: polls <= 2 ? [cloning] : [] });
    });
    await page.goto("/");
    await page.getByLabel("GitHub repository URL").fill("https://github.com/acme/widgets");
    await page.getByRole("button", { name: "Index Repository" }).click();
    await expect(page.getByRole("list", { name: "Indexing progress" })).toBeVisible();
    const notice = page.locator("div[role=alert]").filter({ hasText: "lost track" });
    await expect(notice).toBeVisible({ timeout: 15_000 });
    await expect(notice).toContainText("acme/widgets");
    await expect(page.getByRole("list", { name: "Indexing progress" })).toBeHidden();
    await notice.getByRole("button", { name: "Try again" }).click();
    await expect.poll(() => posts).toBe(2);
  });

  test("losing contact with the server while indexing is reported", async ({ page }) => {
    let polls = 0;
    const cloning = repo("acme/widgets", { id: "gh__acme__widgets", status: "cloning", files: null, chunks: null });
    await page.route("**/api/v1/repositories", (r) => {
      if (r.request().method() === "POST") return r.fulfill({ status: 202, json: cloning });
      polls += 1;
      return polls <= 1 ? r.fulfill({ json: [cloning] }) : r.fulfill({ status: 502, json: { detail: "down" } });
    });
    await page.goto("/");
    await page.getByLabel("GitHub repository URL").fill("https://github.com/acme/widgets");
    await page.getByRole("button", { name: "Index Repository" }).click();
    await expect(page.locator("p[role=alert]")).toContainText("Lost contact with the server", { timeout: 20_000 });
  });

  test("a failed job shows its error and Try again starts it again", async ({ page }) => {
    let posts = 0;
    const failed = repo("acme/private", {
      id: "gh__acme__private",
      status: "failed",
      error: "git clone failed: repository not found",
      files: null,
      chunks: null,
    });
    await page.route("**/api/v1/repositories", (r) => {
      if (r.request().method() === "POST") {
        posts += 1;
        return r.fulfill({ status: 202, json: posts === 1 ? failed : { ...failed, status: "queued", error: null } });
      }
      return r.fulfill({ json: posts === 1 ? [failed] : [{ ...failed, status: "queued", error: null }] });
    });
    await page.goto("/");
    await page.getByLabel("GitHub repository URL").fill("https://github.com/acme/private");
    await page.getByRole("button", { name: "Index Repository" }).click();
    await expect(page.locator("p[role=alert], div[role=alert]").filter({ hasText: "repository not found" })).toBeVisible();
    await page.getByRole("button", { name: "Try again" }).click();
    await expect.poll(() => posts).toBe(2);
    await expect(page.getByRole("list", { name: "Indexing progress" })).toBeVisible();
  });

  for (const [status, detail] of [
    [409, "This server already holds 10 repositories (the limit); none is removed"],
    [429, "Too many repositories are being indexed; try again shortly"],
    [422, "Only github.com repositories are supported"],
    [403, "Indexing repositories from the API is disabled on this server"],
  ] as const) {
    test(`a ${status} from the server is shown as written`, async ({ page }) => {
      await page.route("**/api/v1/repositories", (r) =>
        r.request().method() === "POST" ? r.fulfill({ status, json: { detail } }) : r.fulfill({ json: [] }),
      );
      await page.goto("/");
      await page.getByLabel("GitHub repository URL").fill("https://github.com/acme/widgets");
      await page.getByRole("button", { name: "Index Repository" }).click();
      await expect(page.locator("p[role=alert]")).toContainText(detail);
    });
  }

  test("when the server has indexing switched off the form says so and is disabled", async ({ page }) => {
    await page.unroute("**/api/health");
    await page.route("**/api/health", (r) => r.fulfill({ json: { ...HEALTH_OK, indexing_enabled: false } }));
    await repos(page, [repo("itsdangerous", { default: true })]);
    await page.goto("/");
    await expect(page.getByText(/Connecting repositories is turned off/)).toBeVisible();
    await expect(page.getByLabel("GitHub repository URL")).toBeDisabled();
    await expect(page.getByRole("button", { name: "Index Repository" })).toBeDisabled();
  });

  test("with two indexed repositories the user chooses which one to ask about", async ({ page }) => {
    await repos(page, [
      repo("itsdangerous", { default: true }),
      repo("acme/widgets", { id: "gh__acme__widgets" }),
    ]);
    const asked: unknown[] = [];
    await page.route("**/api/v1/answer", (r) => {
      asked.push(r.request().postDataJSON().repository);
      return r.fulfill({ json: answer({ status: "insufficient_evidence", missing: "none" }) });
    });
    await page.goto("/");
    const group = page.getByRole("group", { name: "Indexed repositories" });
    await expect(group.getByRole("button", { name: "itsdangerous" })).toHaveAttribute("aria-pressed", "true");
    await ask(page, "first");
    await expect(page.getByText("Not enough evidence")).toBeVisible();
    await group.getByRole("button", { name: "acme/widgets" }).click();
    await expect(page.getByText("Not enough evidence")).toHaveCount(0); // the old answer is cleared
    await expect(page.getByText("Indexed repository:")).toContainText("acme/widgets");
    await ask(page, "second");
    await expect(page.getByText("Not enough evidence")).toBeVisible();
    expect(asked).toEqual(["gh__itsdangerous", "gh__acme__widgets"]);
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

  test("a real language question is answered from the files shown", async ({ page }) => {
    await page.goto("/");
    await ask(page, "What programming language is this project written in?");
    await expect(page.locator(".badge")).toBeVisible({ timeout: 90_000 });
    await expect(page.locator(".badge")).toHaveText("Answered");
    await expect(page.locator(".claim").first()).toContainText("Python");
    const chip = page.locator("a.cite").first();
    await expect(chip).toContainText(".py");
    await chip.click();
    await expect(page.locator("li:target")).toContainText(".py");
  });

  test("a real false-premise question abstains", async ({ page }) => {
    await page.goto("/");
    await ask(page, "How does itsdangerous send analytics events?");
    await expect(page.locator(".badge")).toBeVisible({ timeout: 90_000 });
    await expect(page.locator(".badge")).toHaveText("Not enough evidence");
  });
});

test.describe("on a phone-sized screen", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.beforeEach(async ({ page }) => {
    await repos(page, [repo("itsdangerous", { default: true })]);
  });

  async function noSideScroll(page: Page) {
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  }

  test("the first screen, an answer and an abstention fit the width and stack cleanly", async ({ page }) => {
    const longName = "an-extremely-long-repository-name-that-could-overflow-the-status-bar";
    await page.route("**/api/health", (r) => r.fulfill({ json: HEALTH_OK }));
    await repos(page, [repo(longName, { files: 1234, chunks: 56789, default: true })]);
    const code = [
      "def verify_signature(self, value, signature, extra_argument_that_is_long):",
      "    return True",
    ].join("\n");
    const evidence = EVIDENCE.map((e) => ({ ...e, text: code }));
    const claim = { text: "Signatures are checked here.", citations: [CITATION] };
    let reply = answer({ claims: [claim], evidence: { evidence } });
    await page.route("**/api/v1/answer", (r) => r.fulfill({ json: reply }));
    await page.goto("/");
    await expect(page.getByText("Indexed repository:")).toContainText(longName);
    await noSideScroll(page);

    await ask(page, "Where is it checked?");
    await expect(page.getByText("Answered", { exact: true })).toBeVisible();
    await page.getByRole("link", { name: /\[E1\]/ }).click();
    await expect(page.locator("#ev-1 pre")).toBeVisible();
    await noSideScroll(page);
    const answerBox = await page.locator(".answer-col .card").boundingBox();
    const evidenceBox = await page.locator(".evidence-col .card").boundingBox();
    expect(evidenceBox!.y).toBeGreaterThan(answerBox!.y + answerBox!.height - 1); // stacked, not side by side
    expect(answerBox!.x + answerBox!.width).toBeLessThanOrEqual(390);
    expect(evidenceBox!.x + evidenceBox!.width).toBeLessThanOrEqual(390);

    reply = answer({ status: "insufficient_evidence", missing: "Nothing about it.", evidence: { evidence } });
    await ask(page, "How does it send analytics?");
    await expect(page.getByText(/does not guess/)).toBeVisible();
    await noSideScroll(page);
  });
});

test.describe("when the API sends something unexpected", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/health", (r) => r.fulfill({ json: HEALTH_OK }));
  });

  test("a repository list that is not a list does not crash the page", async ({ page }) => {
    await page.route("**/api/v1/repositories", (r) => r.fulfill({ json: { detail: "oops" } }));
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Connect a repository" })).toBeVisible();
    await expect(page.getByText("Application error")).toHaveCount(0);
  });

  test("an answer without the expected fields shows an error instead of crashing", async ({ page }) => {
    await repos(page, [repo("itsdangerous", { default: true })]);
    await page.route("**/api/v1/answer", (r) => r.fulfill({ json: {} }));
    await page.goto("/");
    await ask(page, "anything");
    await expect(page.locator("p[role=alert]")).toContainText("unexpected");
    await expect(page.getByText("Application error")).toHaveCount(0);
    await expect(page.getByLabel("Your question")).toBeVisible();
  });

  test("an evidence response without a list shows an error instead of crashing", async ({ page }) => {
    await page.unroute("**/api/health");
    await page.route("**/api/health", (r) => r.fulfill({ json: { ...HEALTH_OK, answer_enabled: false } }));
    await repos(page, [repo("itsdangerous", { default: true })]);
    await page.route("**/api/v1/evidence?*", (r) => r.fulfill({ json: { nothing: true } }));
    await page.goto("/");
    await ask(page, "signature", "Find evidence");
    await expect(page.locator("p[role=alert]")).toContainText("unexpected");
    await expect(page.getByText("Application error")).toHaveCount(0);
  });

  test("a connect response without an id is reported", async ({ page }) => {
    await page.route("**/api/v1/repositories", (r) =>
      r.request().method() === "POST" ? r.fulfill({ status: 202, json: {} }) : r.fulfill({ json: [] }),
    );
    await page.goto("/");
    await page.getByLabel("GitHub repository URL").fill("https://github.com/acme/widgets");
    await page.getByRole("button", { name: "Index Repository" }).click();
    await expect(page.locator("p[role=alert]")).toContainText("unexpected");
    await expect(page.getByText("Application error")).toHaveCount(0);
  });
});
