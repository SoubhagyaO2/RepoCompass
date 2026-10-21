"use client";

import hljs from "highlight.js/lib/core";
import python from "highlight.js/lib/languages/python";
import { useCallback, useEffect, useState } from "react";
import {
  Answer,
  EvidenceItem,
  Health,
  RepoStatus,
  Repository,
  ask,
  connectRepository,
  getHealth,
  listRepositories,
  searchEvidence,
} from "@/lib/api";

hljs.registerLanguage("python", python);

const STATUS_TEXT: Record<Answer["status"], string> = {
  answered: "Answered",
  partial: "Partly answered",
  insufficient_evidence: "Not enough evidence",
  invalid_output: "The model's output failed validation",
};

// Example questions per indexed repository (matched on the repository name). A repository without
// examples shows none rather than wrong ones.
const EXAMPLES: Record<string, string[]> = {
  flask: [
    "Where is the session cookie saved?",
    "What calls url_for?",
    "How does Flask handle errors raised in a view?",
  ],
  itsdangerous: [
    "Where is the signature verified?",
    "How are timestamps checked for expiry?",
    "How does itsdangerous send analytics events?",
  ],
};

function examplesFor(repository: string | undefined): string[] {
  const name = (repository ?? "").toLowerCase();
  const key = Object.keys(EXAMPLES).find((k) => name.includes(k));
  return key ? EXAMPLES[key] : [];
}

const GITHUB_URL = /^https:\/\/github\.com\/[A-Za-z0-9-]+\/[A-Za-z0-9._-]+?(\.git)?\/?$/;
const POLL_MS = 1500;
const SNIPPET_LINES = 40;
const STAGES: { status: RepoStatus; label: string }[] = [
  { status: "queued", label: "Queued" },
  { status: "cloning", label: "Cloning" },
  { status: "indexing", label: "Indexing" },
  { status: "ready", label: "Ready" },
];

// "E3" and "E3b" both belong to evidence item 3 (the b/c ids are extra chunks of the same file).
function evidenceRank(id: string) {
  return /^E(\d+)/.exec(id)?.[1] ?? "";
}

function lines(item: { line_start: number; line_end: number }) {
  return `${item.line_start}-${item.line_end}`;
}

function highlight(code: string): string | null {
  try {
    return hljs.highlight(code, { language: "python" }).value;
  } catch {
    return null;
  }
}

function snippet(text: string) {
  const all = text.split("\n");
  return all.length > SNIPPET_LINES
    ? `${all.slice(0, SNIPPET_LINES).join("\n")}\n… (${all.length - SNIPPET_LINES} more lines)`
    : text;
}

function HighlightedCode({ code }: { code: string }) {
  const html = highlight(code);
  return html === null ? (
    <code>{code}</code>
  ) : (
    <code className="hljs" dangerouslySetInnerHTML={{ __html: html }} />
  );
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

function Progress({ repo }: { repo: Repository }) {
  const current = STAGES.findIndex((s) => s.status === repo.status);
  const detail =
    repo.status === "cloning"
      ? `Fetching ${repo.name} from GitHub…`
      : repo.status === "indexing"
        ? `Parsing and embedding ${repo.files_total ?? "the"} Python files. Large repositories can take a few minutes.`
        : repo.status === "queued"
          ? "Waiting for the indexer…"
          : null;
  return (
    <div className="progress" aria-live="polite">
      <ol className="stages" aria-label="Indexing progress">
        {STAGES.map((s, i) => (
          <li key={s.status} data-state={i < current ? "done" : i === current ? "active" : "todo"}>
            {s.label}
          </li>
        ))}
      </ol>
      {detail && (
        <p className="detail">
          <span className="spinner" aria-hidden="true" /> {detail}
        </p>
      )}
    </div>
  );
}

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [repos, setRepos] = useState<Repository[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [evidence, setEvidence] = useState<EvidenceItem[] | null>(null);
  const [opened, setOpened] = useState<Set<number>>(new Set());

  const refresh = useCallback(async () => {
    try {
      const list = await listRepositories();
      setRepos(list);
      setSelectedId((current) => {
        if (current && list.some((r) => r.id === current)) return current;
        const first = list.find((r) => r.default && r.status === "ready") ?? list.find((r) => r.status === "ready");
        return first?.id ?? null;
      });
    } catch {
      // keep what is shown; the next poll or reload retries
    }
  }, []);

  useEffect(() => {
    getHealth()
      .then((h) => {
        setHealth(h);
        refresh();
      })
      .catch((e: Error) => setHealthError(e.message));
  }, [refresh]);

  const indexing = repos.some((r) => ["queued", "cloning", "indexing"].includes(r.status));
  useEffect(() => {
    if (!indexing) return;
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [indexing, refresh]);

  const selected = repos.find((r) => r.id === selectedId) ?? null;
  const ready = selected?.status === "ready";

  async function connect(text: string) {
    const value = text.trim();
    if (!value || connecting) return;
    if (!GITHUB_URL.test(value)) {
      setConnectError("Enter a public GitHub repository URL like https://github.com/owner/repository");
      return;
    }
    setConnecting(true);
    setConnectError(null);
    try {
      const repo = await connectRepository(value);
      setSelectedId(repo.id);
      setAnswer(null);
      setEvidence(null);
      setRepos((prev) => [...prev.filter((r) => r.id !== repo.id), repo]);
      await refresh();
    } catch (e) {
      setConnectError(e instanceof Error ? e.message : "Could not start indexing.");
    } finally {
      setConnecting(false);
    }
  }

  async function submit(text: string) {
    const q = text.trim();
    if (!q || busy || !selected || !ready) return;
    setBusy(true);
    setError(null);
    setAnswer(null);
    setEvidence(null);
    setOpened(new Set());
    try {
      if (health?.answer_enabled) {
        const result = await ask(q, selected.id);
        setAnswer(result);
        setEvidence(result.evidence.evidence);
      } else {
        setEvidence(await searchEvidence(q, selected.id));
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

  const examples = ready ? examplesFor(selected?.name) : [];
  const phase: Phase = busy ? "retrieving" : answer || evidence ? "done" : "idle";
  const canAsk = ready && !busy && question.trim().length > 0;
  const indexingOff = health !== null && health.indexing_enabled !== true;
  const canConnect = !connecting && url.trim().length > 0 && !indexingOff;
  const working = repos.find((r) => r.id === selectedId && ["queued", "cloning", "indexing"].includes(r.status));
  const failed = repos.find((r) => r.id === selectedId && r.status === "failed");

  return (
    <main>
      <header>
        <h1 className="brand">RepoCompass</h1>
        <Workflow phase={phase} />
      </header>

      <section className="card connect" aria-labelledby="connect-title">
        <h2 id="connect-title">Connect a repository</h2>
        <p className="muted">Paste a public GitHub repository URL to get started.</p>
        <form
          className="connect-form"
          onSubmit={(e) => {
            e.preventDefault();
            connect(url);
          }}
        >
          <label htmlFor="repo-url" className="sr-only">
            GitHub repository URL
          </label>
          <input
            id="repo-url"
            type="url"
            inputMode="url"
            autoComplete="off"
            value={url}
            maxLength={300}
            disabled={indexingOff}
            onChange={(e) => {
              setUrl(e.target.value);
              setConnectError(null);
            }}
            placeholder="https://github.com/owner/repository"
          />
          <button type="submit" className="primary" disabled={!canConnect} aria-busy={connecting}>
            {connecting && <span className="spinner" aria-hidden="true" />}
            {connecting ? "Starting…" : "Index Repository"}
          </button>
        </form>
        {indexingOff && (
          <p className="notice">
            Connecting repositories is turned off on this server. Index one with the command line
            instead (see the README).
          </p>
        )}
        {connectError && (
          <p className="notice bad" role="alert">
            {connectError}
          </p>
        )}
        {working && <Progress repo={working} />}
        {failed && (
          <div className="notice bad failed" role="alert">
            <p>
              Indexing {failed.name} failed: {failed.error ?? "unknown error"}
            </p>
            <button
              type="button"
              className="link"
              onClick={() => connect(`https://github.com/${failed.name}`)}
            >
              Try again
            </button>
          </div>
        )}
        {repos.some((r) => r.status === "ready") && (
          <div className="repos" role="group" aria-label="Indexed repositories">
            <span className="muted">Ask about:</span>
            {repos
              .filter((r) => r.status === "ready")
              .map((r) => (
                <button
                  key={r.id}
                  type="button"
                  className="chip"
                  aria-pressed={r.id === selectedId}
                  onClick={() => {
                    setSelectedId(r.id);
                    setAnswer(null);
                    setEvidence(null);
                    setError(null);
                  }}
                >
                  {r.name}
                </button>
              ))}
          </div>
        )}
      </section>

      <div className="status" role="status">
        <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="6" cy="5" r="2.2" />
          <circle cx="6" cy="19" r="2.2" />
          <circle cx="18" cy="7" r="2.2" />
          <path d="M6 7.2v9.6M18 9.2c0 4-6 3-12 7" />
        </svg>
        <span className="repo">
          {selected && ready ? (
            <>
              Indexed repository: <strong>{selected.name}</strong> ({selected.files} files,{" "}
              {selected.chunks} chunks)
            </>
          ) : selected ? (
            <>
              Repository: <strong>{selected.name}</strong>
            </>
          ) : (
            "No repository loaded yet"
          )}
        </span>
        <span className="pills">
          <span className={`pill ${ready ? "ok" : working ? "warn" : "bad"}`}>
            <span aria-hidden="true">●</span>{" "}
            {health ? (ready ? "Index ready" : working ? "Indexing…" : "No index") : "Connecting…"}
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
          Cannot reach the API ({healthError}). Check that the backend is running (locally:{" "}
          <code>uvicorn app.main:app</code>) and that the web server&apos;s{" "}
          <code>REPOCOMPASS_API_URL</code> points at it.
        </p>
      )}
      {health && !health.index_loaded && !working && indexingOff && (
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
          placeholder={
            ready
              ? "e.g. Where is the signature verified?"
              : "Index a repository first, then ask about it"
          }
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
            {evidence ? (
              <h2>
                Evidence ({evidence.length} {evidence.length === 1 ? "file" : "files"})
              </h2>
            ) : (
              <h2>Evidence</h2>
            )}
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
                        <pre>
                          <HighlightedCode code={snippet(item.text)} />
                        </pre>
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
