"use client";

import { useEffect, useState } from "react";
import {
  Answer,
  EvidenceItem,
  Health,
  IndexStatus,
  ask,
  getHealth,
  getIndexStatus,
  searchEvidence,
} from "@/lib/api";

const STATUS_TEXT: Record<Answer["status"], string> = {
  answered: "Answered",
  partial: "Partly answered",
  insufficient_evidence: "Not enough evidence",
  invalid_output: "The model's output failed validation",
};

// Example questions for the Flask index only; other indexes get none rather than wrong ones.
const FLASK_EXAMPLES = [
  "Where is the session cookie saved?",
  "What calls url_for?",
  "How does Flask handle errors raised in a view?",
];

// "E3" and "E3b" both belong to evidence item 3 (the b/c ids are extra chunks of the same file).
function evidenceRank(id: string) {
  return /^E(\d+)/.exec(id)?.[1] ?? "";
}

function lines(item: { line_start: number; line_end: number }) {
  return `${item.line_start}-${item.line_end}`;
}

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [index, setIndex] = useState<IndexStatus | null>(null);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [evidence, setEvidence] = useState<EvidenceItem[] | null>(null);

  useEffect(() => {
    getHealth()
      .then((h) => {
        setHealth(h);
        if (h.index_loaded) getIndexStatus().then(setIndex).catch(() => setIndex(null));
      })
      .catch((e: Error) => setHealthError(e.message));
  }, []);

  async function submit(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    setBusy(true);
    setError(null);
    setAnswer(null);
    setEvidence(null);
    try {
      if (health?.answer_enabled) {
        const result = await ask(q);
        setAnswer(result);
        setEvidence(result.evidence.evidence);
      } else {
        setEvidence(await searchEvidence(q));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  const ready = health?.index_loaded ?? false;
  const examples = index?.repository.includes("flask") ? FLASK_EXAMPLES : [];

  return (
    <main>
      <h1>RepoCompass</h1>
      <p className="lede">
        Ask a code repository a question. Answers cite exact files and line ranges; when the
        evidence does not support an answer, RepoCompass says so.
      </p>

      {index && (
        <p className="fine">
          Indexed repository: <strong>{index.repository}</strong> ({index.files} files,{" "}
          {index.chunks} chunks)
        </p>
      )}

      {healthError && (
        <p className="notice bad" role="alert">
          Cannot reach the API ({healthError}). Start it with <code>uvicorn app.main:app</code>.
        </p>
      )}
      {health && !health.index_loaded && (
        <p className="notice bad" role="alert">
          No index is loaded. Set <code>REPOCOMPASS_LIVE_REPOSITORY</code> and{" "}
          <code>REPOCOMPASS_LIVE_ROOT</code> and restart the API.
        </p>
      )}
      {health && health.index_loaded && !health.answer_enabled && (
        <p className="notice">
          Evidence-only mode: no model key is configured, so you get the retrieved files and lines
          but no written answer.
        </p>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit(question);
        }}
      >
        <label htmlFor="q">Your question</label>
        <textarea
          id="q"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          rows={3}
          maxLength={2000}
          placeholder="e.g. Where is the session cookie saved?"
        />
        <div className="row">
          <button type="submit" disabled={busy || !ready || !question.trim()}>
            {busy ? "Working…" : health?.answer_enabled ? "Ask" : "Find evidence"}
          </button>
          <span className="examples">
            {examples.length > 0 && "Try: "}
            {examples.map((ex) => (
              <button
                key={ex}
                type="button"
                className="link"
                disabled={busy || !ready}
                onClick={() => {
                  setQuestion(ex);
                  submit(ex);
                }}
              >
                {ex}
              </button>
            ))}
          </span>
        </div>
      </form>

      {error && (
        <p className="notice bad" role="alert">
          {error}
        </p>
      )}

      {answer && (
        <section aria-live="polite">
          <h2>
            <span className={`badge ${answer.status}`}>{STATUS_TEXT[answer.status]}</span>
          </h2>
          {answer.claims.map((claim, i) => (
            <div className="claim" key={i}>
              <p>{claim.text}</p>
              <p className="cites">
                {claim.citations.map((c) => (
                  <a
                    className="cite"
                    key={c.evidence_id}
                    href={`#ev-${evidenceRank(c.evidence_id)}`}
                    title={c.symbol ?? undefined}
                  >
                    [{c.evidence_id}] {c.file}:{lines(c)}
                    {c.symbol ? ` · ${c.symbol}` : ""}
                  </a>
                ))}
              </p>
            </div>
          ))}
          {answer.missing && (
            <p className="missing">
              <strong>Not covered by the evidence:</strong> {answer.missing}
            </p>
          )}
          {!answer.validation.valid && (
            <p className="notice bad">
              Validation failed: {answer.validation.reasons.join("; ")}. The answer is withheld.
            </p>
          )}
          <p className="fine">
            Citations are checked to exist in the retrieved evidence; they are not proof that the
            evidence supports each claim.
            {answer.model.returned_model_version && ` Model: ${answer.model.returned_model_version}.`}
          </p>
        </section>
      )}

      {evidence && (
        <section>
          <h2>Evidence ({evidence.length} files)</h2>
          <ol className="evidence">
            {evidence.map((item) => (
              <li key={item.rank} id={`ev-${item.rank}`}>
                <code>
                  {item.file}:{lines(item)}
                </code>
                {item.symbol && <span className="sym"> {item.symbol.qualified_name}</span>}
                <span className="cat"> {item.category}</span>
              </li>
            ))}
          </ol>
        </section>
      )}
    </main>
  );
}
