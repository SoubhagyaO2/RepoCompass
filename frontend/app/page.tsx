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

const SNIPPET_LINES = 40;

// "E3" and "E3b" both belong to evidence item 3 (the b/c ids are extra chunks of the same file).
function evidenceRank(id: string) {
  return /^E(\d+)/.exec(id)?.[1] ?? "";
}

function lines(item: { line_start: number; line_end: number }) {
  return `${item.line_start}-${item.line_end}`;
}

function snippet(text: string) {
  const all = text.split("\n");
  return all.length > SNIPPET_LINES
    ? `${all.slice(0, SNIPPET_LINES).join("\n")}\n… (${all.length - SNIPPET_LINES} more lines)`
    : text;
}

type Phase = "idle" | "retrieving" | "done";

function Workflow({ phase }: { phase: Phase }) {
  const steps = [
    { name: "Question", state: phase === "idle" ? "active" : "done" },
    {
      name: "Retrieval",
      state: phase === "retrieving" ? "active" : phase === "done" ? "done" : "todo",
    },
    { name: "Evidence-backed answer", state: phase === "done" ? "active" : "todo" },
  ];
  return (
    <ol className="flow" aria-label="How RepoCompass works">
      {steps.map((s, i) => (
        <li key={s.name} data-state={s.state}>
          <span className="n">{i + 1}</span>
          {s.name}
        </li>
      ))}
    </ol>
  );
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
  const [opened, setOpened] = useState<Set<number>>(new Set());

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
    setOpened(new Set());
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

  function showCode(rank: number) {
    setOpened((prev) => new Set(prev).add(rank));
  }

  const ready = health?.index_loaded ?? false;
  const examples = index?.repository.includes("flask") ? FLASK_EXAMPLES : [];
  const phase: Phase = busy ? "retrieving" : answer || evidence ? "done" : "idle";
  const canAsk = ready && !busy && question.trim().length > 0;

  return (
    <main>
      <header>
        <h1>RepoCompass</h1>
        <p className="lede">
          Ask a code repository a question. RepoCompass retrieves the relevant source code, answers
          only from it, cites the exact files and lines, and says so when the evidence does not
          support an answer.
        </p>
        <Workflow phase={phase} />
      </header>

      <div className="status" role="status">
        <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="6" cy="5" r="2.2" />
          <circle cx="6" cy="19" r="2.2" />
          <circle cx="18" cy="7" r="2.2" />
          <path d="M6 7.2v9.6M18 9.2c0 4-6 3-12 7" />
        </svg>
        <span className="repo">
          {index ? (
            <>
              Indexed repository: <strong>{index.repository}</strong> ({index.files} files,{" "}
              {index.chunks} chunks)
            </>
          ) : (
            "No repository loaded yet"
          )}
        </span>
        <span className="pills">
          <span className={`pill ${ready ? "ok" : "bad"}`}>
            <span aria-hidden="true">●</span>{" "}
            {health ? (ready ? "Index ready" : "No index") : "Connecting…"}
          </span>
          {health && (
            <span className={`pill ${health.answer_enabled ? "ok" : "warn"}`}>
              {health.answer_enabled ? "Written answers on" : "Evidence-only"}
            </span>
          )}
        </span>
      </div>

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
          placeholder="e.g. Where is the signature verified?"
        />
        <div className="row">
          <button type="submit" className="primary" disabled={!canAsk} aria-busy={busy}>
            {busy && <span className="spinner" aria-hidden="true" />}
            {busy ? "Working…" : health?.answer_enabled ? "Ask" : "Find evidence"}
          </button>
          {examples.length > 0 && (
            <span className="examples">
              Try:{" "}
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
          )}
        </div>
      </form>

      {error && (
        <p className="notice bad" role="alert">
          {error}
        </p>
      )}

      <div className="workspace">
        <section className="answer-col" aria-live="polite">
          {!answer && !busy && !error && (
            <div className="card placeholder">
              <h2>Answer</h2>
              <p>
                Your answer appears here as a short list of claims. Each claim carries citations
                that link to the code that supports it.
              </p>
            </div>
          )}
          {busy && (
            <div className="card placeholder">
              <h2>Retrieving code…</h2>
              <p>Searching the index, then writing an answer from what was found.</p>
            </div>
          )}
          {answer && (
            <div className={`card answer ${answer.status}`}>
              <h2>
                <span className={`badge ${answer.status}`}>{STATUS_TEXT[answer.status]}</span>
              </h2>
              {answer.status === "insufficient_evidence" && (
                <p className="abstain">
                  RepoCompass found nothing in the retrieved code that answers this, so it does not
                  guess.
                </p>
              )}
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
                        onClick={() => showCode(Number(evidenceRank(c.evidence_id)))}
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
                  Validation failed: {answer.validation.reasons.join("; ")}. The answer is
                  withheld.
                </p>
              )}
              <p className="fine">
                Citations are checked to exist in the retrieved evidence; they are not proof that
                the evidence supports each claim.
                {answer.model.returned_model_version &&
                  ` Model: ${answer.model.returned_model_version}.`}
              </p>
            </div>
          )}
        </section>

        <aside className="evidence-col">
          <div className="card">
            {evidence ? <h2>Evidence ({evidence.length} files)</h2> : <h2>Evidence</h2>}
            {!evidence && (
              <p className="muted">
                {busy
                  ? "Retrieving…"
                  : "Retrieved source code appears here. Every claim in an answer cites one of these files and line ranges."}
              </p>
            )}
            {evidence && (
              <ol className="evidence">
                {evidence.map((item) => (
                  <li key={item.rank} id={`ev-${item.rank}`}>
                    <div className="ev-head">
                      <span className="eid">E{item.rank}</span>
                      <code>
                        {item.file}:{lines(item)}
                      </code>
                    </div>
                    <div className="ev-meta">
                      {item.symbol && <span className="sym">{item.symbol.qualified_name}</span>}
                      <span className="cat">{item.category}</span>
                    </div>
                    {item.text && (
                      <details
                        open={opened.has(item.rank)}
                        onToggle={(e) => {
                          const isOpen = (e.currentTarget as HTMLDetailsElement).open;
                          setOpened((prev) => {
                            const next = new Set(prev);
                            if (isOpen) next.add(item.rank);
                            else next.delete(item.rank);
                            return next;
                          });
                        }}
                      >
                        <summary>View code</summary>
                        <pre>{snippet(item.text)}</pre>
                      </details>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </div>
        </aside>
      </div>
    </main>
  );
}
