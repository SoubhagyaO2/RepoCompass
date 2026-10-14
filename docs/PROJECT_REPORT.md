# RepoCompass: project report (Milestones 0-15)

RepoCompass indexes a Python repository and answers questions about it with exact files, symbols
and line ranges. It was built as a series of pre-registered experiments on one frozen benchmark
(73 questions on Flask, commit `d73fa1c`), so every claim below points to a result file. Where a
component did not help, it is listed as rejected rather than removed from the record.

**Scope of every number here:** one repository (Flask), one embedding model
(`BAAI/bge-small-en-v1.5`), one answer model (`gemini-3.5-flash-lite`, free tier), 73 questions.
Human review was not conducted and is outside the scope of the answer-layer experiments (M13's
readers were AI models, M14 and M15 used none). Nothing here is a claim of human-validated quality.

## 1. Validated results

| Milestone | What was shown | Evidence |
|---|---|---|
| M2-M4 | Tree-sitter parsing, AST-aware chunking and a naive fixed-size RAG baseline exist and are tested | `backend/app/{parsing,chunking,ingestion}`, tests |
| M5-M6 | A frozen 73-question benchmark with a locked scoring method; the naive baseline scores Top-1 32.9%, Top-5 80.8%, MRR 0.541 | `evaluation/README.md` |
| M7-M8 | BM25 with a code-aware tokenizer is strong on exact identifiers and callers; **hybrid retrieval (BM25 + embeddings, RRF k=60)** has the best Top-5 (87.7%) and MRR, clearly better than embeddings alone (p = 0.0003) but not clearly better than the best single method | `evaluation/results/`, `docs/decisions.md` |
| M10A | The code graph is correct: import edges identical to Python's `ast` module, 51 of 51 hand-verified facts hold, name-matching precision 93% after a recorded fix | `evaluation/graph_facts/` |
| M11 | The live index equals a full rebuild in 11 of 11 scenarios; a one-file edit updates in 0.23 s against a 133 s cold build | `evaluation/results/`, plan `m11` |
| M12 | The Evidence API reproduces the saved hybrid ranking for 73 of 73 questions, with 0 invariant violations over 730 items | plan `m12` |
| M13 | The grounded answer layer: 110 of 113 outputs valid, 0 fabricated references in the 33 answers an AI reader checked, 0 planted instructions followed, 13 of 15 correct abstentions on unanswerable questions and 19 of 20 on mismatched evidence | plan `m13`, results |
| M14 | Up to two extra chunks per file (Evidence schema v2) cut set-A abstentions from 11 to 5, exact McNemar p = 0.016 (vs M13) and 0.031 (vs a same-period repeat); the frozen rule says **supported** | plan `m14`, results |

## 2. Rejected or not supported

| Component | Result | Status |
|---|---|---|
| Cross-encoder reranking (M9) | MRR -0.096 (fixed chunks) and -0.046 (AST), about 50x the latency; rescued fewer questions than it pushed out | Rejected for the default pipeline; code and results kept |
| Always-on graph expansion of the ranking (M10B) | Dependency Top-1 8% -> 58% (circular: same parser as the ground truth) but MRR -0.18 / -0.24 on the other 61 questions | Rejected as a re-ranker; kept as annotation only. Run before the M10 scope fix and not re-run |
| Premise-assessment contract (M15) | 23 of 24 matched pairs correct against 19 of 24 for the baseline; 4 improvements, 0 reversals, exact McNemar p = 0.125; the regression run then failed the over-abstention guard (4 new abstentions on answerable questions, limit 2) and added 4 invalid outputs | **Not supported** by the frozen rule. The direction favours the treatment but 24 pairs cannot confirm it |

Why M15 is inconclusive in practice: the baseline already scored 19 of 24, leaving little room,
and the question pairs were written by the same party who wrote the prompt candidates. The
authoring checks establish structural quality, not freedom from author bias.

## 3. Limitations

- **One repository and one model.** Nothing is known about other codebases, other languages or
  other models. Python only (Tree-sitter Python).
- **Small benchmark.** 73 questions: a category moves by several points per question.
- **Citation validity is not truth.** The validator proves that cited ids, paths, lines and
  symbols exist in the retrieved evidence. It cannot tell whether the evidence supports the
  claim.
- **No human review.** The M13 reviewers were AI models; no person has labelled any answer.
- **Nondeterminism.** Embedding scores in local Qdrant vary by about 3e-8 and tied chunks follow
  store order; the model's output varies between calls even at temperature 0 (a same-period
  repeat changed 1 abstention outcome).
- **Free-tier model.** Requests are rate-limited, and the service returned transient HTTP 503s
  during M15 (14 calls were re-run, all kept in the raw files).
- **The API and MCP server use provider-enforced output shape (a response schema).** This is
  not what the published answer-layer numbers were measured with (those used prompt-only JSON
  and show format failures such as plain-string claims). A handful of live checks after the
  change returned valid output, which is not a measurement.
- **Single-process index.** The local Qdrant store allows one process at a time, so the API and
  the MCP server cannot serve the same data directory simultaneously.

## 4. Unfinished work

- A second benchmark repository and a stronger answer model, as separate experiments.
- Fixing false-premise handling without raising over-abstention (M15 left it open).
- Intent-gated graph use for structural questions; targeted graph re-resolution for large
  repositories; deterministic tie-breaking for embeddings (see `evaluation/BACKLOG.md`).
- A measured comparison of provider-enforced output against prompt-only JSON.
- A human reader for the answer-layer results, if human-validated quality is wanted.
- Automatic ingestion from a GitHub URL into the served index (today the index is built with
  `python -m app.evidence --build`, see the README).

## 5. Where everything is

- Pre-registered plans (frozen before each run): `evaluation/plans/`.
- Raw model outputs (append-only, retries kept) and summaries: `evaluation/results/`.
- Decisions per component: `docs/decisions.md`. Open ideas: `evaluation/BACKLOG.md`.
- Methodology, datasets and scoring: `evaluation/README.md`.
