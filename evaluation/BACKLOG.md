# Experiment backlog

Ideas and open confounds that are **deliberately not tested yet**. Any of these that involves
tuning must be evaluated on a separate dev set, not on the frozen benchmark (see `README.md`).

## Open confounds

- **AST chunks over the embedder's context window.** 42 of 1,027 AST chunks (0 of 792 fixed
  chunks) exceed the embedding model's 512 tokens and are silently truncated. It may affect the
  AST + embeddings and AST hybrid results. Test on a dev set (e.g. split large chunks) only if
  reranking or embedding results suggest it matters.
- **Caller questions reward finding the definition.** For 7 of 13 caller questions the
  definition file is also a counted call-site file. Fixing it means a new dataset version
  (`flask-v2.json`), not an edit.
- **Test and example files crowd the top results** (the #1 file is outside `src/flask/` for 16-27
  of 73 questions depending on the method). The `category` tag on every chunk could be used to
  down-weight them, but that design choice was suggested by looking at these results.

## Experiments to run later

- A separate **dev set** of questions for tuning (RRF `k`, BM25 `k1`/`b`, chunk size).
- A **second benchmark repository** (FastAPI) to test whether results generalise.
- **Metadata-enriched indexing** (file path and symbol name added to the indexed text).
- **Multi-chunk reranking:** score every retrieved chunk of a candidate file and take the max,
  instead of one representative chunk per file.
- A **stronger reranker** (`bge-reranker-base` or larger) where compute allows (GPU, or fewer
  candidates).
- **Splitting very large AST chunks** at statement boundaries.
- **AST + embeddings with truncation handled** (a clean test of the chunking effect under
  embeddings).

## Added after Milestone 9 (reranking did not help)

Ideas for why, all untested; run them on a dev set, never on the frozen benchmark:

- **Domain mismatch:** `ms-marco-MiniLM-L-6-v2` is trained on natural-language passages. Try a
  stronger or code-aware reranker where compute allows (`bge-reranker-base` or larger).
- **Give the reranker the context retrieval uses:** prepend the file path and symbol name to the
  chunk text it scores (the first stage never sees them either).
- **Score more than one chunk per file** and take the max, instead of one representative chunk.
- **Candidate depth** (20 files was fixed in advance; 10 or 50 are untested).
- **Truncation:** 133 of R2's 1,460 scored pairs exceed the reranker's 512 tokens.
- **Interpolate** the reranker score with the first-stage rank instead of replacing the order.
