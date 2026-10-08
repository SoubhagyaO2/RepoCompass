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
