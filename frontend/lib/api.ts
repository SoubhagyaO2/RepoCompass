// Types for the parts of the RepoCompass API the UI uses (see backend/app/answer/schema.py).

export type Health = {
  status: string;
  version: string;
  index_loaded: boolean;
  answer_enabled: boolean;
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

export class ApiError extends Error {}

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    if (body.error) return body.quota_exhausted ? `Model quota exhausted: ${body.error}` : body.error;
  } catch {
    // not JSON: fall through
  }
  return `The server answered ${response.status}.`;
}

export async function getHealth(): Promise<Health> {
  const response = await fetch("/api/health");
  if (!response.ok) throw new ApiError(await readError(response));
  return response.json();
}

export async function ask(question: string): Promise<Answer> {
  const response = await fetch("/api/v1/answer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!response.ok) throw new ApiError(await readError(response));
  return response.json();
}

export async function searchEvidence(question: string): Promise<EvidenceItem[]> {
  const response = await fetch(`/api/v1/evidence?q=${encodeURIComponent(question)}&top_k=8`);
  if (!response.ok) throw new ApiError(await readError(response));
  return (await response.json()).evidence;
}
