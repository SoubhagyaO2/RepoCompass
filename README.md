# RepoCompass

A repository-scale code retrieval and code intelligence engine. Point it at a GitHub repository and ask questions like *"Where is authentication handled?"* or *"What calls `authenticate_user()`?"*, and get answers grounded in exact files, symbols and line ranges.

The project's focus is measuring whether AST-aware chunking, BM25, hybrid retrieval, reranking and code graphs actually beat a naive fixed-size RAG baseline. Every number reported here will come from a reproducible experiment.

> **Status:** Milestone 0 — project foundation. Nothing beyond a health-check API exists yet.

## Layout

| Path | Purpose |
|------|---------|
| `backend/` | Python package (`app`) and its tests — the core engine and FastAPI API |
| `evaluation/` | Benchmark datasets and evaluation scripts (Milestone 5+) |
| `mcp-server/` | MCP server exposing RepoCompass tools (Milestone 15) |
| `frontend/` | Next.js UI (Milestone 16) |
| `docs/` | Design notes and architecture docs |

## Backend setup

Requires Python 3.11+ and Git.

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"

pytest                          # run tests
ruff check .                    # lint
uvicorn app.main:app --reload   # serve at http://127.0.0.1:8000  (GET /health, /docs)
```
