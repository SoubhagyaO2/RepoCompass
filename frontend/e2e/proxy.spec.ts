import { expect, test } from "@playwright/test";

// The UI server forwards /api/* to the RepoCompass API at the address in REPOCOMPASS_API_URL,
// read when each request arrives (not when the app was built). These run against a real stack.
test.skip(process.env.REAL_STACK !== "1", "set REAL_STACK=1 with a running stack");

test("forwards GET requests and returns the API's JSON and status", async ({ request }) => {
  const response = await request.get("/api/health");
  expect(response.status()).toBe(200);
  expect(await response.json()).toMatchObject({ status: "ok" });
});

test("forwards the request body and passes the API's error status through", async ({ request }) => {
  const response = await request.post("/api/v1/repositories", {
    data: { url: "https://gitlab.com/o/r" },
  });
  expect(response.status()).toBe(422); // validation happened in the API, so the body arrived
  expect((await response.json()).detail).toContain("github.com");
});

test("forwards query strings", async ({ request }) => {
  const response = await request.get("/api/v1/evidence?q=signature&top_k=2&repository=itsdangerous");
  expect(response.status()).toBe(200);
  expect((await response.json()).evidence.length).toBeLessThanOrEqual(2);
});

test("only the API routes are reachable through the proxy", async ({ request }) => {
  for (const path of ["/api/docs", "/api/openapi.json", "/api/", "/api/admin", "/api/%2e%2e/health"]) {
    const response = await request.get(path);
    expect(response.status(), path).toBe(404);
  }
});

test("a path cannot be used to reach another host", async ({ request }) => {
  for (const path of ["/api/v1/@evil.example/x", "/api/v1/..%2f..%2fhealth", "/api/v1//evil.example"]) {
    const response = await request.get(path);
    expect(response.status(), path).toBeLessThan(500);
    expect(await response.text()).not.toContain("evil.example is");
  }
});
