# Pipeline decisions

What each retrieval component was, how it was evaluated, and what we decided. All numbers are from
the frozen 73-question Flask benchmark (`evaluation/README.md`); the full results are in
`evaluation/results/`. Decisions are about the **default pipeline**: a component that is not in it
stays in the code as an evaluated, optional component.

| Component | Evidence | Decision |
|---|---|---|
| Fixed-size chunks + embeddings | Baseline: Top-1 32.9%, Top-5 80.8%, MRR 0.541 (M6) | Baseline only |
| AST-aware chunks | Helps embeddings (Top-1 43.8%), hurts BM25; not significant overall (M7-M8) | Kept; both chunkings stay in play |
| BM25 (code-aware tokenizer) | Top-1 41.1% on fixed chunks; strong on callers and exact identifiers (M7) | In the pipeline |
| Hybrid (BM25 + embeddings, RRF k=60, file-level) | Best Top-5 (87.7%) and MRR on both chunkings; clearly better than embeddings alone (p = 0.0003), not clearly better than the stronger single method (M8) | **Default first stage** |
| Cross-encoder reranking (`ms-marco-MiniLM-L-6-v2`) | Made things worse: MRR -0.096 (fixed) and -0.046 (AST), about 50x the latency; rescued 5 and 3 questions, pushed out 9 and 7 (M9) | **Evaluated and rejected for the default pipeline.** The code (`retrieval/reranker.py`, `evaluation/run_rerank.py`) and the results stay, as an optional component to revisit with a better-suited model on a dev set |
| Code graph (construction) | Built from imports, calls, inheritance and definitions; import edges identical to Python's `ast` module, 51 of 51 hand-verified facts hold, name-matching precision audited at 93% after a recorded fix (M10A) | **In the project** as the structural-query engine (`app/graph`) |
| Always-on graph expansion of the hybrid ranking | Dependency Top-1 8% -> 58% (circular: same parser as the ground truth) but MRR -0.18 / -0.24 on the other 61 questions (p = 0.001 / 0.003); hubs such as `app.py` dominate (M10B) | **Evaluated and rejected as an always-on re-ranker.** Kept as an optional component. Next idea: use the graph only for structural questions (intent-gated), tested on a dev set |

Why record rejected components: a result like "reranking degraded retrieval" is as much a finding
as a gain, and keeping the code and the result makes the decision auditable.

## Milestone 11: live (incremental) index

| Component | Evidence | Decision |
|---|---|---|
| Live index: SHA-256 change detection, per-file records, embedding reuse by chunk-text hash, in-place BM25, graph re-assembled from stored parses | Identical to a full rebuild in 11/11 scenarios, both runs; 133 s cold build vs 0.23 s (one-file edit) and 0.57 s (five files); cross-file graph edges correctly removed (M11) | **In the project** (`app/indexing/live_index.py`). Config changes (chunk size, model) require a full rebuild |
| Skip unchanged points on update (deviation 1) | Run 1: writes dominated small edits; run 2: `edit_end` 0.66 -> 0.23 s, `edit_5_files` 1.5 -> 0.57 s | Kept; both runs reported |
| AST vs fixed chunks for a live index | One inserted line re-embedded 84 fixed windows but 1 AST chunk | Favours AST chunks for incremental use |
