# Experiment plan: Milestone 9, cross-encoder reranking

**Status: frozen before any reranking result existed.**
Written 2026-10-07 on top of commit `fada3e1` (Milestone 8). Nothing below is changed after
seeing results; deviations, if any, are recorded in the results README as deviations.

## Question

Does re-scoring the hybrid retriever's top candidates with a cross-encoder put relevant files
higher than the hybrid's own ranking does, and does it rescue relevant files that retrieval found
but ranked poorly?

## Systems

| Name | First stage (unchanged from Milestone 8) | Then |
|---|---|---|
| H1 / H2 (references, already saved) | fixed / AST chunks, BM25 + embeddings fused with RRF (k = 60) | nothing |
| **R1** | H1 | cross-encoder rerank |
| **R2** | H2 | cross-encoder rerank |

The first stage is exactly the Milestone 8 hybrid. The run must reproduce the saved Milestone 8
rankings for every question before reranking is applied (integrity check; the run aborts if not).

## Pre-registered method

| Decision | Value |
|---|---|
| Reranker model | `cross-encoder/ms-marco-MiniLM-L-6-v2` (22M parameters) |
| Why this model | Fast enough to evaluate on this CPU: measured 45 ms per (query, passage) pair on a synthetic smoke test that did not use benchmark questions. `bge-reranker-base` has roughly a dozen times the compute (estimated, not measured), tens of seconds per query, too slow. |
| Candidate depth | the **top 20 unique files** of the hybrid ranking |
| Candidate unit | one candidate per unique file, represented by **its best-ranked retrieved chunk** (the chunk from the component that ranks that file highest; the earlier component, BM25, wins a tie). No chunk is scored twice. |
| Reranker input | `(question, raw chunk content)`: no file path, no symbol name (same naive input as every other method) |
| Scoring | raw cross-encoder logit (identity activation) of the representative chunk. File score = that chunk's logit |
| Truncation | the model reads at most 512 tokens; longer chunks are truncated. The number of candidate chunks over the limit is recorded. Not tuned |
| Batch size / device | 16 / CPU |
| Final ranking | the 20 candidates sorted by reranker score, best first (ties by first-stage rank), followed by the files ranked 21 and below in first-stage order |
| Final Top-K | 5 (as in every locked evaluation) |
| Evaluation | the locked scoring, unchanged: each component retrieves 50 chunks, deduplicated to unique files; Top-1 / Top-5 / MRR of the first counted file; primary and primary + supporting scopes; by category and by difficulty |
| Latency | end-to-end per query (first stage + reranking), plus the two parts separately; model already loaded; one query at a time on CPU; one untimed warm-up query through the full pipeline |

The candidate pool is **preserved**: for every question and every candidate the results file
records `retrieval_rank`, `retrieval_score`, `reranker_score`, `reranker_rank` and the chunk's
line range, so the reranker never replaces the retrieval evidence.

## Pre-registered comparisons (3, paired, B vs A)

1. R1 vs H1 (fixed hybrid -> reranker)
2. R2 vs H2 (AST hybrid -> reranker)
3. R2 vs R1

Primary outcome: change in MRR (primary scope) with its 95% paired-bootstrap interval. Secondary:
Top-1, Top-5, and the sign test on per-question rank wins/losses. Reranking is reported as an
improvement only if the MRR interval excludes zero; the sign test is reported alongside. With three
comparisons the Bonferroni threshold for the sign test is p < 0.0167.

## Pre-registered descriptive statistics (primary scope, per system and by category)

- **Pool ceiling:** the fraction of questions with a primary file among the 20 candidates (the
  most any reranker could achieve).
- **Rescued:** questions whose first primary file was at retrieval rank 6-20 and is at reranker
  rank 1-5. **Pushed out:** retrieval rank 1-5 and reranker rank 6 or lower.
- Questions where the first primary file's rank improved / worsened / stayed the same.
- Latency breakdown (first stage vs reranker).

## Not changed

Dataset (frozen), scoring, chunking, RRF `k`, BM25 parameters, the embedding model, and the
512-token truncation of the embedder (an open confound, see `BACKLOG.md`). Candidate depth, model
and batch size are not tuned on the 73 questions.

## Known risks and limits

- The model was trained on natural-language passage ranking (MS MARCO); source code is out of
  domain, so a null or negative result may reflect the model rather than reranking in general.
- One representative chunk per file: a file whose answer lies in a different chunk than the one
  retrieval surfaced cannot be fully judged.
- One repository, 73 questions: small categories are noisy.
- Candidates beyond the model's 512-token limit are truncated.

## Integrity

- Dataset fingerprint: `6e78d06ad988b637eb3f77f047232238667e0cf9b325abd971f1eab3c024a311`
  (`flask-eval-v1`, frozen 2026-10-06), pinned Flask commit
  `d73fa1cdcbd8b1465c151db8924ba58b1dd14e35`.
- Reproduce: `python -m app.evaluation.run_rerank` (planned entry point).
