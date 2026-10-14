# RepoCompass

Ask a Python repository a question and get an answer that cites exact files, symbols and line
ranges, or an honest "the evidence does not show this". RepoCompass retrieves code with hybrid
search (BM25 + embeddings over AST chunks), keeps the index current as files change, exposes the
retrieved evidence through an API, and writes answers whose every claim is checked against that
evidence.

It was built as a series of pre-registered experiments, so the project also records what did
**not** work (reranking, always-on graph expansion, a premise-assessment prompt). Read
[`docs/PROJECT_REPORT.md`](docs/PROJECT_REPORT.md) for the evidence, the rejected approaches, the
limitations and the unfinished work before relying on any claim here.

> **What the checks mean.** The validator proves that every citation points at something that
> exists in the retrieved evidence. It does not prove the evidence supports the claim. Human
> review was not conducted and is outside the scope of the experiments.

## What is in the box

| Part | Where | What it does |
|---|---|---|
| Engine and API | `backend/` | Ingest, parse, chunk, index, retrieve, evidence (`/v1/evidence`, `/v2/evidence`), grounded answers (`/v1/answer`) |
| MCP server | `backend/app/mcp_server.py` | Tools `search_evidence`, `ask`, `index_status` for coding assistants |
| Web UI | `frontend/` | Ask a question, see claims with citations, or evidence-only when no model key is set |
| Experiments | `evaluation/` | Frozen benchmark (73 Flask questions), pre-registered plans M9-M15, raw results |

## Quick start

Requires Python 3.12+, Git, and Node 20+ for the UI.

```bash
# 1. Install
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows;  source .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"
cp ../.env.example .env           # then edit; see "Configuration"

# 2. Get a repository and index it (any public Python repository)
python -m app.ingestion https://github.com/pallets/flask        # prints the local path
python -m app.evidence --repository flask --root data/repos/pallets/flask --build
python -m app.evidence --repository flask --root data/repos/pallets/flask "where is the session saved"

# 3. Serve it
export REPOCOMPASS_LIVE_REPOSITORY=flask            # PowerShell: $env:REPOCOMPASS_LIVE_REPOSITORY='flask'
export REPOCOMPASS_LIVE_ROOT=data/repos/pallets/flask
uvicorn app.main:app                                 # http://127.0.0.1:8000  (GET /health, /docs)

# 4. The UI (second terminal)
cd frontend && npm install && npm run build && npm run start      # http://127.0.0.1:3000
```

`--build` also updates an existing index incrementally (only changed files are re-embedded), so
re-run it after pulling new commits. `GET /health` reports whether an index is loaded and whether
the answer layer is enabled.

Only one process can open a local index at a time: stop the API before running the MCP server or
`python -m app.evidence` against the same data directory.

## Configuration

Settings come from environment variables or `backend/.env` (never commit it). See `.env.example`.

| Variable | Meaning |
|---|---|
| `GEMINI_API_KEY` | Enables written answers. Leave empty for evidence-only mode |
| `REPOCOMPASS_GEMINI_MODEL` | Default `gemini-3.5-flash-lite`, the model all results were measured with |
| `REPOCOMPASS_LIVE_REPOSITORY`, `REPOCOMPASS_LIVE_ROOT` | The index name and working tree to serve |
| `REPOCOMPASS_DATA_DIR` | Where clones and indexes live (default `data`) |
| `REPOCOMPASS_STRUCTURED_OUTPUT` | Provider-enforced answer shape for the API and MCP server (default on; **off for every experiment**) |

A shell variable named `GEMINI_API_KEY` overrides `backend/.env`; clear it if the wrong key is used.

## MCP server

```bash
python -m app.mcp_server        # stdio; same REPOCOMPASS_* variables
```

Example client configuration (adjust paths):

```json
{ "mcpServers": { "repocompass": {
    "command": "C:/path/to/backend/.venv/Scripts/python.exe",
    "args": ["-m", "app.mcp_server"],
    "cwd": "C:/path/to/backend",
    "env": { "REPOCOMPASS_LIVE_REPOSITORY": "flask", "REPOCOMPASS_LIVE_ROOT": "data/repos/pallets/flask" } } } }
```

Tools: `search_evidence(query, top_k=5, extra_chunks=0)`, `ask(question, top_k)`, `index_status()`.
Errors (bad arguments, no model key, quota) come back as tool errors with a readable message.

## Docker

```bash
# 1. Put GEMINI_API_KEY in backend/.env (optional: without it the stack runs evidence-only)
# 2. One-time: clone and index a repository into the shared volume
docker compose build
docker compose run --rm backend python -m app.ingestion https://github.com/pallets/itsdangerous
docker compose run --rm backend python -m app.evidence --repository itsdangerous     --root /data/repos/pallets/itsdangerous --build
# 3. Serve (the API on :8000, the UI on :3000)
REPOCOMPASS_LIVE_REPOSITORY=itsdangerous REPOCOMPASS_LIVE_ROOT=/data/repos/pallets/itsdangerous     docker compose up -d
```

In Git Bash on Windows, set `MSYS_NO_PATHCONV=1` first, or `/data/...` arguments are rewritten into
Windows paths (the index command now refuses a root that does not exist or has no Python files).
The backend image uses CPU-only torch and bakes the embedding model in; it is large (several GB).

## Tests

```bash
cd backend && pytest && ruff check .        # 592 passed, 1 skipped; no network or API key needed
cd frontend && npm run typecheck && npm run build
cd frontend && npx playwright test          # 12 mocked-API browser tests, in the installed Chrome
```

Against a running stack: `REAL_STACK=1 npx playwright test` (real question, citation navigation,
real abstention; needs the itsdangerous index and a key), `EVIDENCE_ONLY=1 npx playwright test
evidence-only` (stack started without a key), `BACKEND_DOWN=1 npx playwright test backend-down`
(backend container stopped). The backend suite uses fake models and embedders.

## Demo

`python -m app.demo` runs four fixed questions against a running API; [`docs/DEMO.md`](docs/DEMO.md)
has a captured run with commentary, including a weak answer.

## Verified, and not verified

Release verification, 2026-10-14 (smoke tests of the product; **not experimental evidence**, and the
M0-M15 experiments, results and verdicts are untouched):

- **Docker:** `docker compose build` of both images; on a fresh volume, ingest and index of
  `pallets/itsdangerous` inside the container; both containers healthy; `/health` through the
  backend and through the UI proxy; a real question returned `answered`, valid, with a citation
  (`signer.py:227-242`, `Signer.verify_signature`) whose line range contains that function.
- **Browser (Chrome via Playwright):** answer rendering, citation links that scroll to and
  highlight the evidence item, partial answers, abstentions, validation-failure display,
  evidence-only fallback, missing index, unreachable API, quota and server errors with recovery,
  all with a mocked API; the real stack for a real answer, a real abstention, a real keyless
  evidence-only stack and a really stopped backend.
- **MCP server:** over real stdio against the Flask index, all three tools.

Not verified: only Windows with Docker Desktop was used; no accessibility audit beyond keyboard
focus styles; one browser (Chrome); only Python repositories; the model's answers vary run to run
and were checked on a handful of questions, which is not a measurement of answer quality.

## Layout

```
backend/    Python package `app` (api, answer, llm, evidence, indexing, graph, retrieval, ...) and tests
frontend/   Next.js UI (proxies /api/* to the backend)
evaluation/ benchmark, pre-registered plans, raw results, backlog
docs/       PROJECT_REPORT.md (start here), DEMO.md (a real run), decisions.md
```
