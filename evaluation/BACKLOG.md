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

## Added after Milestone 10 (graph expansion hurt non-structural questions)

All untested; design and tune on a dev set, never on the frozen benchmark:

- **Intent-gated graph use:** route "what calls / imports / depends on X" questions to the graph
  (callers, importers) and leave the rest to the hybrid. Needs a question classifier and questions
  whose ground truth was **not** derived from the same parser as the graph.
- **Direction-aware expansion** (callers only vs callees only vs importers only) instead of the
  undirected union.
- **Scale the vote to the score range** (normalise the graph vote against the spread of the
  hybrid's scores) and **dilute hubs** (divide an anchor's vote by its degree).
- **Fewer or smarter anchors** than the top 3, and filtering neighbours by file category (tests).
- **Callbacks as references:** add non-call references (`_async_wrapper=app.ensure_sync`) to the
  graph to raise caller recall (the graph alone reaches recall 0.66 on the caller questions).
- **A second repository and independently derived structural questions** (for example ground truth
  from a different tool) to test whether graph answers generalise beyond the circular case.

## From Milestone 11 (incremental indexing)

- **Targeted graph re-resolution:** the graph is re-assembled globally on every update (about 40 ms
  on Flask); for repositories 100x larger, re-resolve only names affected by the changed files.
- **A command-line entry point for the live index** (build, update, status); at the moment it is
  driven by the experiment runner and tests.
- **Edit-stable fixed windows** (content-defined boundaries) if the fixed chunking is kept.
- **Measure on a larger repository** (second repo) to see where the global parts start to matter.

## From Milestone 12 (Evidence API)

- **Deterministic tie-breaking for the embedding component** (for example by chunk id after
  quantising scores). It changes retrieval, so evaluate it on a dev set and re-run M8 first.
- **`additional_chunks` per file** (schema v2) if M13 needs several chunks of one file; evaluate
  as a separate extension, never silently.
