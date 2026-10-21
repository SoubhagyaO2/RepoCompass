import type { NextRequest } from "next/server";

// Forwards /api/* to the RepoCompass API, so the browser only ever talks to this server (no CORS).
// The API address is read from REPOCOMPASS_API_URL when each request arrives. A build-time rewrite
// would bake the address into the build, and a deployment that did not set the variable while
// building would then try to reach 127.0.0.1 and answer every request with 502.

export const dynamic = "force-dynamic";

const DEFAULT_API_URL = "http://127.0.0.1:8000";
const ALLOWED_FIRST_SEGMENTS = new Set(["health", "v1", "v2"]); // the API's public routes
const TIMEOUT_MS = 180_000;
const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "te",
  "trailer",
  "proxy-authorization",
  "proxy-authenticate",
  "host",
  "content-length",
  "content-encoding",
]);

function apiBase(): { url: string; configured: boolean } {
  const value = (process.env.REPOCOMPASS_API_URL ?? "").trim();
  return { url: (value || DEFAULT_API_URL).replace(/\/+$/, ""), configured: value !== "" };
}

function failure(status: number, detail: string) {
  return Response.json({ detail }, { status });
}

async function forward(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const segments = path.filter((s) => s !== "");
  const unsafe = segments.some((s) => s === "." || s === ".." || /[\\@:]/.test(s));
  if (segments.length === 0 || !ALLOWED_FIRST_SEGMENTS.has(segments[0]) || unsafe) {
    return failure(404, "Not found");
  }

  const base = apiBase();
  const target = `${base.url}/${segments.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) headers.set(key, value);
  });
  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      redirect: "manual",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    const responseHeaders = new Headers();
    upstream.headers.forEach((value, key) => {
      if (!HOP_BY_HOP.has(key.toLowerCase())) responseHeaders.set(key, value);
    });
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    const hint = base.configured
      ? "Check that the API is running and that REPOCOMPASS_API_URL points at it."
      : "REPOCOMPASS_API_URL is not set on the web server, so it tried the default http://127.0.0.1:8000. Set it to the API's address.";
    return failure(502, `The web server could not reach the RepoCompass API. ${hint}`);
  }
}

export {
  forward as GET,
  forward as POST,
  forward as PUT,
  forward as PATCH,
  forward as DELETE,
  forward as HEAD,
};
