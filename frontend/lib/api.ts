// Types and calls for the parts of the RepoCompass API the UI uses (see backend/app/answer/schema.py
// and backend/app/repositories.py).

export type Health = {
  status: string;
  version: string;
  index_loaded: boolean;
  answer_enabled: boolean;
  indexing_enabled?: boolean;
};

export type Citation = {
  evidence_id: string;
  file: string;
  line_start: number;
  line_end: number;
  symbol: string | null;
};

export type Claim = { text: string; citations: Citation[] };

export type EvidenceItem = {
  rank: number;
  file: string;
  line_start: number;
  line_end: number;
  category: string;
  symbol: { qualified_name: string } | null;
  text?: string | null;
};

export type Answer = {
  question: string;
  status: "answered" | "partial" | "insufficient_evidence" | "invalid_output";
  claims: Claim[];
  missing: string;
  validation: { valid: boolean; reasons: string[] };
  evidence: { evidence: EvidenceItem[] };
  model: { returned_model_version: string | null; latency_ms: number };
};

export type RepoStatus = "queued" | "cloning" | "indexing" | "ready" | "failed";

export type Repository = {
  id: string;
  name: string;
  status: RepoStatus;
  error: string | null;
  files: number | null;
  chunks: number | null;
  files_total: number | null;
  default: boolean;
};

export class ApiError extends Error {}

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail) && body.detail.length > 0) {
      return body.detail.map((d: { msg?: string }) => d.msg ?? "invalid request").join("; ");
    }
    if (body.error) return body.quota_exhausted ? `Model quota exhausted: ${body.error}` : body.error;
  } catch {
    // not JSON: fall through
  }
  return `The server answered ${response.status}.`;
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) throw new ApiError(await readError(response));
  return response.json();
}

// A proxy hiccup or a different server version can send a 200 with the wrong shape; render an
// error message instead of letting the page crash on it.
const UNEXPECTED = "The server sent an unexpected response. Reload the page and try again.";

function expect<T>(value: unknown, ok: (v: any) => boolean): T {
  if (!ok(value)) throw new ApiError(UNEXPECTED);
  return value as T;
}

const isObject = (v: unknown) => typeof v === "object" && v !== null && !Array.isArray(v);

export const getHealth = () =>
  fetch("/api/health")
    .then((r) => json<unknown>(r))
    .then((v) => expect<Health>(v, isObject));

export const listRepositories = () =>
  fetch("/api/v1/repositories")
    .then((r) => json<unknown>(r))
    .then((v) =>
      expect<Repository[]>(v, (x) => Array.isArray(x) && x.every((r) => isObject(r) && typeof r.id === "string")),
    );

export const connectRepository = (url: string) =>
  fetch("/api/v1/repositories", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  })
    .then((r) => json<unknown>(r))
    .then((v) => expect<Repository>(v, (x) => isObject(x) && typeof x.id === "string"));

export const ask = (question: string, repository?: string) =>
  fetch("/api/v1/answer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, repository }),
  })
    .then((r) => json<unknown>(r))
    .then((v) =>
      expect<Answer>(
        v,
        (a) =>
          isObject(a) &&
          typeof a.status === "string" &&
          Array.isArray(a.claims) &&
          isObject(a.validation) &&
          isObject(a.evidence) &&
          Array.isArray(a.evidence.evidence) &&
          isObject(a.model),
      ),
    );

export async function searchEvidence(question: string, repository?: string): Promise<EvidenceItem[]> {
  const repo = repository ? `&repository=${encodeURIComponent(repository)}` : "";
  const q = encodeURIComponent(question);
  const response = await fetch(`/api/v1/evidence?q=${q}&top_k=8&include_text=true${repo}`);
  const body = await json<unknown>(response);
  return expect<{ evidence: EvidenceItem[] }>(body, (b) => isObject(b) && Array.isArray(b.evidence)).evidence;
}
