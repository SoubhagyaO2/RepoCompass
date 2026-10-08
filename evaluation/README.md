# evaluation

Benchmark datasets and (from Milestone 6) the scripts that score retrieval strategies on them.

## Datasets

| File | Repository | Pinned commit | Questions | Status |
|---|---|---|---|---|
| [`datasets/flask.json`](datasets/flask.json) | `pallets/flask` (3.2.0.dev) | `d73fa1cdcbd8b1465c151db8924ba58b1dd14e35` | 73 | **frozen** 2026-10-06 |

| Category | What it asks | Count |
|---|---|---|
| `definition` | where a named symbol is defined | 16 |
| `caller` | what calls or uses a named symbol | 13 |
| `dependency` | which modules import / are imported by a named module | 12 |
| `concept` | where a described behaviour is implemented | 21 |
| `flow` | how a described multi-step process works | 11 |

| Difficulty | Count |
|---|---|
| easy | 20 |
| medium | 38 |
| hard | 15 |

Each question has:

- `category` and `difficulty` (see below).
- `primary`: the file(s) where the behaviour the question asks about is implemented, i.e. the
  one-line answer.
- `supporting`: other files a complete answer must also cite (triggers, hand-offs, base
  implementations). Often empty.
- `relevant_symbols`: qualified names (e.g. `Flask.wsgi_app`) of the functions/classes that
  implement the answer, for chunk-level scoring. Each lives in a primary or supporting file.
- `evidence`: how the answer was verified (lines read at the pinned commit).

The `methodology` block inside the JSON is the authoritative statement of what counts as ground
truth.

### Primary vs supporting

**Primary** is where the answer *lives*. **Supporting** is code a complete explanation must also
mention but where the behaviour itself is not implemented, and it is listed only when the
question's wording asks for that step ("when", "how it is reached"). The test for every candidate
file is: *could a correct, complete answer leave this file out?* If yes, it is not listed at all.
A file that merely calls the primary code is never ground truth outside `caller` questions.
For `definition`, `caller` and `dependency` questions the answer *is* a set of files, so all of
them are primary.

Example: "Where are teardown functions called when a context is popped?" has primary
`app.py` (`do_teardown_request` actually calls them) and supporting `ctx.py` (`AppContext.pop`
triggers them, and the question asks about pop time).

### Difficulty

Assigned by a fixed rubric from the *shape of the ground truth* (the validator enforces it):

| Question type | 1 file | 2-3 files | 4+ files |
|---|---|---|---|
| named (`definition`, `caller`, `dependency`) | easy | medium | hard |
| described (`concept`, `flow`) | medium | hard | hard |

This is *structural* difficulty: how much must be found and combined. It is not measured
difficulty. Milestone 6 will report whether the two agree, rather than assuming they do.

### How the ground truth was made

Every answer was checked against the source at the pinned commit, not written from memory:
symbols were listed with the project's own parser, call sites were found by searching `src/flask`,
import edges were extracted from the parsed imports with relative imports resolved, and the
request / session / error-handling flows were read line by line. A script then re-checked that
the cited line numbers point at the code they claim. The draft was reviewed once for the
primary/supporting split; that review applied the omit test above and was made without any
retrieval results.

## Freeze policy

> The dataset is frozen. **Never change it based on retrieval results.**

Editing the questions after seeing how a retriever scores would tune the test set to the system
and weaken every number reported later. The file records a SHA-256 fingerprint of its questions,
and the validator (and the test suite) fail if they no longer match.

If a *factual* error is found (the code does not do what an entry says), do not edit
`flask.json` in place. Add `flask-v2.json` with a changelog explaining each correction and why,
re-run the benchmarks on it, and report which version each number comes from.

## Validating

```bash
cd backend
python -m app.evaluation.validate ../evaluation/datasets/flask.json
```

This checks: no duplicate ids or questions; symbols belong to primary/supporting files; evidence
present; difficulty matches the rubric; the fingerprint matches; the checkout is at the pinned
commit; and every listed file and symbol exists.

If your clone of Flask is at a different commit, pin it to the dataset's commit:

```bash
git -C data/repos/pallets/flask fetch --depth 1 origin d73fa1cdcbd8b1465c151db8924ba58b1dd14e35
git -C data/repos/pallets/flask checkout d73fa1cdcbd8b1465c151db8924ba58b1dd14e35
```

## Scoring methodology (locked)

Decided before any results existed; never changed after seeing them. Implemented in
[`scoring.py`](../backend/app/evaluation/scoring.py) and [`runner.py`](../backend/app/evaluation/runner.py).

For each question:

1. Retrieve a ranked list of **50 chunks** (retrieval depth).
2. Map each chunk to its file path.
3. Remove duplicate paths, keeping each file's **first occurrence**.
4. Score the ranked list of **unique files** against the ground truth.

- **Top-1 / Top-5:** a hit if at least one counted file is among the first 1 / 5 unique files.
- **MRR:** mean of 1 / (rank of the *first* counted file in the unique-file ranking), 0 if none
  of the 50 chunks is in a counted file. It measures the first relevant file; it does **not**
  require every required file to be found.
- Two scopes are always reported: **primary** (the headline) and **primary + supporting**
  (broader coverage). Files outside the counted set never score, even if plausible (e.g. a test
  that exercises the function).
- Every metric is reported overall and by category and difficulty.
- Latency is per query (embed the question + vector search, model already loaded, after one
  untimed warm-up query); indexing time is a fresh full index.

Each run saves its metadata (dataset fingerprint, method, chunking, embedding model, depth, top-k,
deduplication rule, timestamp, indexing time, package versions, per-question results) to
[`results/`](results/) so it can be reproduced:

```bash
cd backend
python -m app.evaluation.run_baseline        # about 2 minutes on CPU
```

## Results

### Fixed-chunk baseline (Milestone 6)

[`results/flask_fixed_chunk_baseline.json`](results/flask_fixed_chunk_baseline.json), 2026-10-06:
1,000-character chunks with 200 overlap, `BAAI/bge-small-en-v1.5` (CPU), `flask-eval-v1`
(73 questions, fingerprint `6e78d06a…`), depth 50 chunks, top-5.

|  | n | Top-1 | Top-5 | MRR | Top-1 (+supp.) | Top-5 (+supp.) | MRR (+supp.) |
|---|---|---|---|---|---|---|---|
| **Overall** | 73 | **32.9%** | **80.8%** | **0.541** | 35.6% | 82.2% | 0.569 |
| definition | 16 | 31.2% | 93.8% | 0.595 | 31.2% | 93.8% | 0.595 |
| caller | 13 | 53.8% | 100.0% | 0.756 | 53.8% | 100.0% | 0.756 |
| dependency | 12 | 0.0% | 33.3% | 0.215 | 0.0% | 33.3% | 0.215 |
| concept | 21 | 38.1% | 90.5% | 0.565 | 42.9% | 90.5% | 0.601 |
| flow | 11 | 36.4% | 72.7% | 0.515 | 45.5% | 81.8% | 0.634 |
| easy | 20 | 45.0% | 95.0% | 0.667 | 45.0% | 95.0% | 0.667 |
| medium | 38 | 31.6% | 73.7% | 0.506 | 31.6% | 73.7% | 0.506 |
| hard | 15 | 20.0% | 80.0% | 0.458 | 33.3% | 86.7% | 0.595 |

Query latency: mean 26 ms, median 24 ms, p95 35 ms. Indexing: 71.6 s (83 files, 792 chunks).

For reference, ranking the 80 indexed files at random would score about Top-1 2.0%, Top-5 9.5%,
MRR 0.082 on this dataset (computed exactly from the number of primary files per question).

**How to read these numbers**

- The sample is small: categories have 11-21 questions, so one question moves a category's Top-1
  by 5-9 points. Treat differences of a few points between categories as noise.
- **Dependency is the clear weakness** (0% Top-1, 33% Top-5): "which files import X" is a
  structural question that text similarity answers poorly.
- **Test and example files crowd the results.** The #1 file is outside `src/flask/` for 21 of 73
  questions, and 166 of the 365 top-5 slots are non-library files; they can never score.
- **The caller score is inflated by dataset construction.** For 7 of 13 caller questions the
  function's definition is in the same file as a call site, so finding the definition counts as a
  hit. On the other 6 (exploratory, not part of the locked scoring) the baseline hit Top-5 6/6 but
  Top-1 only 1/6, with the first counted file at rank 2 for five of them.
- **Structural difficulty tracks Top-1 and MRR** (easy 45% / 0.667, medium 31.6% / 0.506,
  hard 20% / 0.458) **but not Top-5.** The medium bucket is dragged down because 7 of its 38
  questions are dependency questions.
- The index contains all 83 Python files in the repository (including tests, docs and examples),
  while only `src/flask/` files can score. This is deliberate: the baseline is the naive approach.

### BM25 on fixed and AST chunks (Milestone 7)

Same frozen dataset, scoring, breakdowns and latency protocol as the baseline.
[`results/flask_fixed_chunk_bm25.json`](results/flask_fixed_chunk_bm25.json) and
[`results/flask_ast_chunk_bm25.json`](results/flask_ast_chunk_bm25.json), 2026-10-07; reproduce with
`python -m app.evaluation.run_bm25`.

**Configuration, fixed before the run and not tuned on the benchmark:** BM25 with the textbook
defaults k1 = 1.5, b = 0.75; lowercase tokens, identifiers indexed whole *and* split on `_` and
camelCase, no stemming; question-word stopwords removed from queries only; **chunk content only is
indexed** (no file path or symbol name) for both chunkings, so they differ in chunk boundaries and
nothing else. Fixed chunks are 1,000/200 as in the baseline (792 chunks); AST chunks are the
Milestone 3 chunks (1,027 chunks).

Primary scope (the headline), Top-1 / Top-5 / MRR:

| | n | Fixed + embeddings | Fixed + BM25 | AST + BM25 |
|---|---|---|---|---|
| **Overall** | 73 | 32.9% / 80.8% / 0.541 | **41.1% / 84.9% / 0.598** | 31.5% / 83.6% / 0.543 |
| definition | 16 | 31.2% / **93.8%** / 0.595 | 37.5% / 75.0% / 0.534 | 31.2% / 81.2% / 0.515 |
| caller | 13 | 53.8% / 100% / 0.756 | **76.9%** / 100% / **0.885** | 53.8% / 100% / 0.718 |
| dependency | 12 | 0.0% / 33.3% / 0.215 | **16.7% / 58.3% / 0.392** | 8.3% / 50.0% / 0.331 |
| concept | 21 | 38.1% / 90.5% / 0.565 | 38.1% / **100%** / 0.602 | **42.9% / 100% / 0.675** |
| flow | 11 | 36.4% / 72.7% / 0.515 | 36.4% / **81.8% / 0.571** | 9.1% / 72.7% / 0.359 |
| easy | 20 | **45.0% / 95.0% / 0.667** | 45.0% / 85.0% / 0.619 | 35.0% / 90.0% / 0.567 |
| medium | 38 | 31.6% / 73.7% / 0.506 | **39.5% / 84.2% / 0.570** | 31.6% / 78.9% / 0.544 |
| hard | 15 | 20.0% / 80.0% / 0.458 | 40.0% / 86.7% / **0.642** | 26.7% / 86.7% / 0.509 |
| median query latency | | 23.9 ms | 0.37 ms | 0.36 ms |
| indexing time | | 71.6 s | 0.11 s | 0.26 s |

Paired comparison of the overall results (`python -m app.evaluation.compare A.json B.json`; per
question, B vs A; exact sign test; 95% bootstrap interval for the MRR difference):

| B vs A | B better / worse / equal | Top-1 | Top-5 | MRR | sign test p |
|---|---|---|---|---|---|
| fixed+BM25 vs fixed+embeddings | 28 / 18 / 27 | +8.2% | +4.1% | +0.058 (-0.021 to +0.135) | 0.184 |
| AST+BM25 vs fixed+BM25 (chunking) | 15 / 25 / 33 | -9.6% | -1.4% | -0.055 (-0.119 to +0.010) | 0.154 |
| AST+BM25 vs fixed+embeddings | 26 / 21 / 26 | -1.4% | +2.7% | +0.003 (-0.071 to +0.077) | 0.560 |

**How to read these numbers**

- **No overall difference is statistically distinguishable from noise.** Every interval includes
  zero and every sign test has p > 0.15. BM25 looks better than embeddings on this sample
  (+8 points Top-1), but 73 questions cannot confirm it; the same goes for AST chunks looking
  worse than fixed chunks. Do not claim an overall winner from this table.
- **Per-category patterns are suggestive, not established** (11-21 questions each). BM25 is
  better on `caller`, `dependency` and `concept` questions (exact identifiers and rare words), and
  embeddings are better on `definition` Top-5 (94% vs 75%): BM25 ranked `render_template`,
  `jsonify`, `url_for` and `FlaskClient` below 5 files because tests and examples *use* those names
  constantly, and BM25 cannot tell "defines" from "uses".
- **Test/example files still crowd the results.** Files outside `src/flask/` are ranked #1 for 19
  of 73 questions with fixed+BM25, 27 with AST+BM25 and 21 with the embedding baseline; they never
  score.
- **Why AST chunks did not help under BM25 is untested.** One hypothesis: AST chunks vary from a
  few lines to thousands of characters, and BM25's length normalisation favours short, keyword-dense
  chunks such as small test functions over the long function that actually implements the answer.
  Confirming it would mean varying `b` or the chunk sizes, which is tuning on the benchmark; it
  needs a separate dev set or a new dataset.
- **The chunking effect is only measured under BM25.** AST chunks + embeddings has not been run
  (it is part of the Milestone 19 ablation), so we cannot yet say whether AST chunking helps
  embeddings.
- The latency and indexing columns compare an in-memory keyword index with a neural model on CPU;
  BM25 is about 65 times faster per query and indexes in a fraction of a second.

### Hybrid retrieval and the AST + embeddings control (Milestone 8)

Reproduce with `python -m app.evaluation.run_hybrid` (about 4 minutes on CPU; builds all indexes
fresh). `python -m app.evaluation.run_hybrid --report-only` re-prints the tables and paired
comparisons below from the saved files without re-running anything. Results:
[`flask_ast_chunk_embeddings.json`](results/flask_ast_chunk_embeddings.json) (control),
[`flask_fixed_chunk_hybrid.json`](results/flask_fixed_chunk_hybrid.json) (H1) and
[`flask_ast_chunk_hybrid.json`](results/flask_ast_chunk_hybrid.json) (H2), 2026-10-07.

**Method, fixed before any hybrid result existed and not tuned on the benchmark:** reciprocal rank
fusion with equal weights and `k = 60` (the value from the original RRF paper). Each component
retrieves 50 chunks, which are deduplicated to unique files (first occurrence) *before* fusing, so a
file with many similar chunks gets one vote per component. A file's score is
`sum over components of 1 / (60 + its rank in that component)`; ties are broken by best individual
rank, then path. Components are BM25 (the Milestone 7 configuration) and `bge-small` embeddings (the
Milestone 6 configuration) over the same chunking, each indexing raw chunk content only. Six paired
comparisons were fixed in advance (listed below). AST + embeddings is the control that lets the AST
hybrid be interpreted.

Overall, primary scope, Top-1 / Top-5 / MRR (73 questions):

| Chunking | Embeddings | BM25 | Hybrid (RRF) |
|---|---|---|---|
| **Fixed** | 32.9% / 80.8% / 0.541 | 41.1% / 84.9% / 0.598 | 39.7% / **87.7%** / 0.614 |
| **AST** | 43.8% / 79.5% / 0.583 | 31.5% / 83.6% / 0.543 | **43.8% / 87.7% / 0.626** |

MRR by category:

| | Fixed: emb | BM25 | **Hybrid** | AST: emb | BM25 | **Hybrid** |
|---|---|---|---|---|---|---|
| definition (16) | 0.595 | 0.534 | **0.611** | 0.825 | 0.515 | **0.738** |
| caller (13) | 0.756 | 0.885 | **0.872** | 0.635 | 0.718 | **0.718** |
| dependency (12) | 0.215 | 0.392 | **0.334** | 0.071 | 0.331 | **0.274** |
| concept (21) | 0.565 | 0.602 | **0.630** | 0.683 | 0.675 | **0.742** |
| flow (11) | 0.515 | 0.571 | **0.587** | 0.539 | 0.359 | **0.519** |

Median query latency: hybrid about 21 ms (almost all of it embedding the question); indexing
about 68 s (fixed) and 51 s (AST), almost all of it computing embeddings.

Pre-registered paired comparisons (B vs A; exact sign test on per-question rank wins/losses;
95% paired-bootstrap interval for the MRR difference; uncorrected, so the Bonferroni threshold
for six comparisons is p < 0.0083):

| B vs A | B better / worse / equal | Top-1 | Top-5 | MRR (95% CI) | sign test p |
|---|---|---|---|---|---|
| 1. H1 (fixed hybrid) vs fixed+embeddings | 29 / 7 / 37 | +6.8% | +6.8% | **+0.073 (+0.030 to +0.119)** | **0.0003** |
| 2. H1 vs fixed+BM25 | 25 / 14 / 34 | -1.4% | +2.7% | +0.016 (-0.046 to +0.077) | 0.108 |
| 3. H2 (AST hybrid) vs AST+embeddings | 25 / 13 / 35 | +0.0% | +8.2% | +0.043 (-0.015 to +0.103) | 0.073 |
| 4. H2 vs AST+BM25 | 21 / 12 / 40 | +12.3% | +4.1% | +0.083 (+0.034 to +0.137) | 0.163 |
| 5. AST+embeddings vs fixed+embeddings | 26 / 25 / 22 | +11.0% | -1.4% | +0.043 (-0.046 to +0.130) | 1.000 |
| 6. H2 vs H1 | 21 / 22 / 30 | +4.1% | +0.0% | +0.013 (-0.056 to +0.080) | 1.000 |

**How to read these numbers**

- **Hybrid is the best configuration overall, but its advantage over the better single method is
  not established.** Both hybrids reach 87.7% Top-5 (the best single method is 84.9%) and the
  highest MRR in their row. Only comparison 1 is clearly real (p = 0.0003, survives the correction):
  fixed-chunk hybrid beats the fixed-chunk embedding baseline. Against the stronger component the
  gains (+0.016 and +0.043 MRR) have intervals that include zero. Comparison 4's interval excludes
  zero but its sign test does not (p = 0.16); treat it as suggestive.
- **Fusion partly, not fully, fixes each retriever's weakness.** On fixed chunks it recovers the
  definition Top-5 that BM25 lost (75.0% to 93.8%, matching embeddings). Across the ten
  category x chunking cells, hybrid MRR is above both components in 4, equal to the best in 1 and
  below the better component in 5 (for example AST definition: 0.738 hybrid vs 0.825 embeddings).
  Fusion without weights tends to land near the better component rather than beyond it.
- **Dependency questions are still unsolved.** Hybrid misses Top-5 on 5 (fixed) and 6 (AST) of the
  12, and those are 5-6 of its 9 total Top-5 misses; Top-1 is 8%. Structural questions need
  structure, which is the job of the code graph (Milestones 10-11).
- **Test and example files still crowd the top.** The #1 result lies outside `src/flask/` for 16
  (fixed hybrid) and 20 (AST hybrid) of 73 questions, down from 19-27 for the single methods but
  far from gone.
- **AST chunking helps embeddings and hurts BM25 here, and the two roughly cancel in the hybrid.**
  The control (comparison 5) shows AST + embeddings at 43.8% Top-1 vs 32.9% for fixed chunks, driven
  by definition questions (Top-1 31% to 69%, 6 questions) and concept questions, but with Top-5 on
  dependency questions collapsing (33% to 8%); overall it is not significant (p = 1.0, MRR interval
  -0.046 to +0.130). Under BM25 AST chunks were worse (Milestone 7). In the hybrid the two effects
  net to nothing (comparison 6: p = 1.0, MRR +0.013). This reading is consistent with the data but
  was not tested directly.
- **A confound for the AST embeddings:** the embedding model reads at most 512 tokens, and 42 of
  the 1,027 AST chunks are longer (0 of 792 fixed chunks), so their tails are silently cut off.
  Whether that matters was not tested.
- Six comparisons on one repository with 73 questions: a result that does not hold on a second
  repository (planned) or a held-out question set should not be trusted. Tuning any parameter
  (RRF `k`, BM25 `b`, chunk size) on these 73 questions would need a separate dev set.

### Cross-encoder reranking (Milestone 9)

The method, the three comparisons and the statistics were frozen in
[`plans/m09-reranking.md`](plans/m09-reranking.md) and committed (`fe76816`) before any reranking
result existed. Reproduce with `python -m app.evaluation.run_rerank` (about 4 minutes; needs the
Milestone 8 indexes); `--report-only` re-prints everything below from the saved files. Results:
[`flask_fixed_chunk_hybrid_rerank.json`](results/flask_fixed_chunk_hybrid_rerank.json) (R1) and
[`flask_ast_chunk_hybrid_rerank.json`](results/flask_ast_chunk_hybrid_rerank.json) (R2), 2026-10-07.

**Method (pre-registered, not tuned):** the first stage is exactly the Milestone 8 hybrid (checked
question by question before reranking). The top 20 unique files are the candidates, each
represented by its best-ranked retrieved chunk. `cross-encoder/ms-marco-MiniLM-L-6-v2` scores
`(question, raw chunk content)` and the file's score is that raw logit. The final ranking is the
20 candidates by score followed by the remaining files in first-stage order. Every candidate's
`retrieval_rank` and `reranker_rank` is saved in the results (`candidate_pools`).

Overall, primary scope, Top-1 / Top-5 / MRR:

| Chunking | Hybrid (M8) | Hybrid + rerank |
|---|---|---|
| Fixed | 39.7% / 87.7% / 0.614 | 32.9% / 82.2% / 0.517 |
| AST | 43.8% / 87.7% / 0.626 | 41.1% / 82.2% / 0.580 |

MRR by category (hybrid -> hybrid + rerank):

| | Fixed | AST |
|---|---|---|
| definition (16) | 0.611 -> 0.565 | 0.738 -> 0.661 |
| caller (13) | 0.872 -> 0.782 | 0.718 -> 0.639 |
| dependency (12) | 0.334 -> 0.324 | 0.274 -> 0.227 |
| concept (21) | 0.630 -> 0.454 | 0.742 -> 0.696 |
| flow (11) | 0.587 -> 0.468 | 0.519 -> 0.559 |

Pre-registered paired comparisons (B vs A; Bonferroni sign-test threshold for three comparisons
p < 0.0167):

| B vs A | B better / worse / equal | Top-1 | Top-5 | MRR (95% CI) | sign test p |
|---|---|---|---|---|---|
| 1. R1 vs H1 (fixed) | 18 / 34 / 21 | -6.8% | -5.5% | -0.096 (-0.189 to -0.005) | 0.036 |
| 2. R2 vs H2 (AST) | 18 / 24 / 31 | -2.7% | -5.5% | -0.046 (-0.127 to +0.034) | 0.441 |
| 3. R2 vs R1 | 33 / 21 / 19 | +8.2% | +0.0% | +0.063 (-0.038 to +0.160) | 0.134 |

Pre-registered descriptive statistics (first primary file, rank before vs after reranking):

| | R1 (fixed) | R2 (AST) |
|---|---|---|
| questions with a primary file among the 20 candidates (the ceiling) | 72 of 73 | 71 of 73 |
| rank improved / worsened / unchanged | 18 / 34 / 20 | 18 / 24 / 29 |
| **rescued** (retrieval rank 6-20 -> reranker rank 1-5) | 5 | 3 |
| **pushed out** (retrieval rank 1-5 -> reranker rank 6 or lower) | 9 | 7 |

Latency (median, CPU): hybrid alone about 21 ms; with reranking **1,093 ms (R1) and 1,163 ms (R2)**,
of which the cross-encoder is about 1,050 and 1,138 ms (20 pairs at roughly 45-55 ms each).

**How to read these numbers**

- **Reranking did not help; on fixed chunks it most likely hurt.** By the pre-registered rule
  (improvement only if the MRR interval excludes zero) there is no improvement in either system.
  For fixed chunks the MRR interval excludes zero on the negative side (-0.096), but the sign
  test (p = 0.036) does not survive the correction for three comparisons, so call the harm
  suggestive. For AST chunks the difference is within noise. In neither system is there evidence
  of a benefit, and it costs about 50 times the latency.
- **There was room to improve.** Almost every question (72 and 71 of 73) had a primary file among
  the candidates. The reranker did rescue some relevant files (5 and 3 questions) but pushed out
  more (9 and 7).
- **Exploratory, post hoc (not in the plan):** the reranker is informative but weaker than the
  hybrid it was applied to. Relevant-file candidates get a much higher mean logit than others
  (+0.7 vs -4.6 for R1, +1.0 vs -4.4 for R2), yet within each candidate pool the hybrid's order puts
  the relevant file above an other file in 85.9% (R1) and 85.5% (R2) of pairs, against 78.3% and
  77.7% for the reranker. The reranker did slightly reduce test/example files at #1 (16 to 14 for
  fixed, 20 to 12 for AST).
- **Likely causes, none tested:** the model was trained on natural-language passage ranking
  (MS MARCO) and source code is out of domain; it sees only raw chunk content, while BM25 sees
  exact names and the hybrid fuses two signals; one chunk represents each file. All of these are in
  the backlog.
- **A confound for AST (R2):** 133 of its 1,460 scored (question, chunk) pairs exceed the model's
  512-token limit (62 questions affected) and are truncated; none do for fixed chunks (R1).
- Only one reranker, one candidate depth and one chunk per file were tried, none tuned, so this
  says that *this* off-the-shelf reranker did not help here, not that reranking cannot.

### Code graph: construction and validation (Milestone 10A)

Not a benchmark run: the graph is built and validated without using the benchmark questions. The
plan ([`plans/m10-code-graph.md`](plans/m10-code-graph.md), committed before any graph code) fixes
the node and edge types, the call-resolution rules and the validation. Reproduce with
`python -m app.graph.validate` (writes
[`results/graph_validation_flask.json`](results/graph_validation_flask.json)); inspect a graph with
`python -m app.graph <github-url> --callers <symbol> | --importers <file> | --neighbors <file>`.

**The Flask graph** (NetworkX `MultiDiGraph`, all 83 Python files including tests; graph assembly
takes about 0.01 s on top of roughly 0.2 s of parsing):

| | |
|---|---|
| nodes | 83 files, 70 classes, 519 functions, 313 methods |
| edges | 902 `defines`, 176 `imports`, 26 `inherits`, 1,127 `calls` (every edge keeps its file, lines and resolution rule) |
| call expressions | 4,128 found; 1,452 resolved (`unique-name` 709, `import-module` 300, `import` 214, `self` 128, `local` 93, `super` 4, `class-attr` 4) |
| skipped | 640 external receivers, 729 builtin-type method names, 194 ambiguous, 1,113 unresolved |

**Validation**

- **V1, independent parser:** the import edges inside `src/flask` equal the same relation computed
  with Python's own `ast` module: **identical** (95 edges each).
- **V2, hand-verified facts** ([`graph_facts/flask.json`](graph_facts/flask.json), written from
  reading the source *before* the builder existed): **51 of 51 hold**: 38 expected-present edges
  exist, 11 expected-absent edges (a precision check) do not, and the 2 documented known gaps are
  absent as predicted.
- **Precision audit of the weakest rule** (single reviewer reading the call line,
  [`results/graph_audit_flask.json`](results/graph_audit_flask.json)): see the deviation below.

**Deviation 1 (recorded in the plan).** The first version of the name-matching rule
(`unique-name`) was only 53% precise on a sample of 30 edges (16 correct): 10 of the 14 errors
were builtin container methods (`kwargs.setdefault(...)` linked to the repository's only
`setdefault`), 2 were calls into external libraries, 1 was self-recursion and 1 needs type
inference. It was fixed *before any retrieval result existed and without benchmark data*: external
receivers and known classes are never name-matched, and builtin-type method names and
self-recursion are excluded. On a **fresh** sample the rule is 93% precise (28 of 30; 77% if a
typing-only stub class is counted as wrong), and a sample of 30 edges from all other rules was 30
of 30. V1 and V2 were unchanged.

**Known limitations (by design):** dynamic dispatch to subclass overrides, methods passed as
values (callbacks), `getattr`/string access, and method names defined by several classes are not
modelled, so recall against a text search of identifiers will be below 100%. The `unique-name` rule
can still be wrong when a receiver of unknown type has a method whose name is defined once in the
repository (for example a Jinja `loader.get_source`).

### Graph-enhanced retrieval (Milestone 10B)

The method, comparisons and statistics were frozen in
[`plans/m10-code-graph.md`](plans/m10-code-graph.md) (commit `3a36441`) before any graph code
existed; the graph itself was built and validated separately (Milestone 10A, `bd69b87`). Reproduce
with `python -m app.evaluation.run_graph` (about 1 minute; needs the Milestone 8 indexes);
`--report-only` re-prints everything below. Results:
[`flask_fixed_chunk_hybrid_graph.json`](results/flask_fixed_chunk_hybrid_graph.json) (G1),
[`flask_ast_chunk_hybrid_graph.json`](results/flask_ast_chunk_hybrid_graph.json) (G2) and
[`graph_vs_benchmark_flask.json`](results/graph_vs_benchmark_flask.json), 2026-10-08.

**Method (pre-registered, not tuned):** the first stage is the Milestone 8 hybrid, verified
question by question. The top 3 files are anchors. Every file connected to an anchor by imports or
calls, in either direction, gets a vote of `1 / (60 + anchor rank)` once per (anchor, neighbour)
pair; a file's score is its hybrid RRF score (0 if the hybrid did not return it) plus its votes.
The graph covers the whole repository, tests included. Every expansion is saved with its evidence
(`expansion_traces`: full example edges `{source, relationship, target, evidence}` for pairs whose
neighbour reaches the final top 10, relation counts for the rest).

Overall, primary scope, Top-1 / Top-5 / MRR:

| | Hybrid (M8) | Hybrid + graph |
|---|---|---|
| Fixed, all 73 | 39.7% / 87.7% / 0.614 | 31.5% / 84.9% / 0.522 |
| Fixed, excluding dependency (61) | 45.9% / 93.4% / 0.669 | 26.2% / 83.6% / 0.485 |
| AST, all 73 | 43.8% / 87.7% / 0.626 | 24.7% / 83.6% / 0.494 |
| AST, excluding dependency (61) | 50.8% / 95.1% / 0.696 | 18.0% / 85.2% / 0.459 |

MRR by category (hybrid -> hybrid + graph):

| | Fixed | AST |
|---|---|---|
| definition (16) | 0.611 -> 0.657 | 0.738 -> 0.610 |
| caller (13) | 0.872 -> 0.571 | 0.718 -> 0.511 |
| **dependency (12)** | **0.334 -> 0.707** | **0.274 -> 0.671** |
| concept (21) | 0.630 -> 0.364 | 0.742 -> 0.346 |
| flow (11) | 0.587 -> 0.364 | 0.519 -> 0.395 |

Dependency Top-1 went from 8.3% to 58.3% in both systems, and Top-5 from 58.3% to 91.7% (fixed) and
from 50.0% to 75.0% (AST).

Pre-registered paired comparisons (B vs A; MRR difference with 95% bootstrap interval; sign test,
Bonferroni threshold for three comparisons p < 0.0167):

| B vs A | questions | B better / worse / equal | Top-1 | Top-5 | MRR (95% CI) | sign test p |
|---|---|---|---|---|---|---|
| 1. G1 vs H1 | all 73 | 25 / 41 / 7 | -8.2% | -2.7% | -0.092 (-0.213 to +0.029) | 0.064 |
| | excl. dependency | 15 / 39 / 7 | -19.7% | -9.8% | **-0.184 (-0.305 to -0.060)** | **0.001** |
| 2. G2 vs H2 | all 73 | 24 / 39 / 10 | -19.2% | -4.1% | -0.132 (-0.249 to -0.011) | 0.077 |
| | excl. dependency | 15 / 37 / 9 | -32.8% | -9.8% | **-0.236 (-0.352 to -0.118)** | **0.003** |
| 3. G2 vs G1 | all 73 | 23 / 24 / 26 | -6.8% | -1.4% | -0.027 (-0.103 to +0.046) | 1.000 |
| | excl. dependency | 21 / 19 / 21 | -8.2% | +1.6% | -0.026 (-0.104 to +0.051) | 0.875 |

Pre-registered descriptive statistics (first primary file, rank before vs after): G1 moved up for
25 questions, down for 41, unchanged for 7; G2 up 24, down 39, unchanged 10. By category the
pattern is stark: dependency up 10 / down 2 (G1) and up 9 / down 2 (G2), but concept up 4 / down 17
(G1) and caller up 2 / down 9. Expansion "brought in" a primary file the hybrid had ranked outside
its top 20 for 0 (G1) and 1 (G2) questions, and "pushed out" a top-5 primary file for 10 and 9.
There are 56 (G1) and 48 (G2) expanded (anchor, neighbour) pairs per question; 55% and 50% of the
neighbours are test or example files. Expansion costs about 1 ms (end-to-end median 26 ms vs 21 ms
for the hybrid); building the graph takes 0.6 s including parsing.

What the graph answers by itself (descriptive, computed once after the graph was frozen):
**dependency 12 of 12 exact** (precision 1.00, recall 1.00) and **callers 8 of 13 exact** (mean
precision 0.69, mean recall 0.66).

**How to read these numbers**

- **As designed, graph expansion did not improve retrieval, and it significantly hurt the
  questions that are not about structure.** By the pre-registered rule (improvement only if the MRR
  interval excludes zero) there is no improvement. Excluding dependency, MRR fell by 0.18 (fixed)
  and 0.24 (AST), and both sign tests pass the correction. Over all 73 questions the dependency
  gain offsets part of the loss, which is why the overall comparison is not significant for G1.
- **The dependency result is circular and shows only that the mechanism works.** The benchmark's
  answers were derived with the same parser as the graph's import edges, so the graph answering
  12 of 12 exactly is by construction, not evidence that it generalises. The caller questions are
  the partly independent test: the graph alone gets 8 of 13 exactly (precision 0.69, recall 0.66;
  it cannot see methods passed as callbacks).
- **Why it hurt (exploratory, post hoc; the plan did not include this):** the hybrid's scores are
  tightly packed (mean RRF score 0.032 at rank 1, 0.030 at rank 5, 0.024 at rank 10) while one graph
  vote is 0.016, larger than the whole gap between ranks 1 and 10. So connectivity overrides
  relevance instead of breaking ties. Hub files win: `app.py` becomes the #1 result for 21 of 73
  questions under G1 (up from 13) and 23 under G2 (up from 12), and as an anchor it contributes
  1,643 of the 4,086 expansion pairs. Test files are not the cause: files outside `src/flask/`
  at #1 actually fell (16 to 13, 20 to 8). Expansion mostly reorders: only 8 (G1) and 16 (G2) of the
  365 top-5 slots hold a file retrieval had not returned.
- **The conclusion is about this design, not about graphs.** Always-on, undirected, equal-weight
  expansion is the wrong way to use a graph that is correct for structural questions. Gating the
  graph on question intent (use it for "what calls / imports / depends on", not for "where is the
  session cookie saved") is the obvious next experiment, but it is suggested by these results, so
  it must be designed and tuned on a dev set, never on this benchmark, and its dependency gains can
  only be believed on questions whose answers were not derived from the same parser.

## Milestone 11: incremental indexing

Pre-registered in `evaluation/plans/m11-incremental-indexing.md`. This is an engineering
experiment, not a retrieval-quality one: the claim is that updating the index after a change costs
far less than rebuilding it **without changing what the index contains**. Changes are detected by
SHA-256 of file bytes (never timestamps); embeddings are reused by chunk-text hash; BM25 is updated
in place with scores bit-identical to the static index; the graph is re-assembled from stored
parses, because name resolution is global.

**Correctness (the criterion that decides pass/fail).** In all 11 scenarios, in both runs, the
updated index was identical to a from-scratch rebuild: chunks, vector-store points and payloads,
BM25 top-50 ids and scores for the 73 questions (exact), graph nodes and edges with evidence, and
sampled real embeddings (cosine 1.000000000). The saved Milestone 8 hybrid rankings were
reproduced at the start and again at the end, for both chunkings.

**Cross-file graph tests (source of the other file unchanged).** After renaming `ensure_sync` in
`app.py`, the edge `views.py::View.as_view -> Flask.ensure_sync` disappeared (+2/-14 edges). After
adding a second class with a `make_shell_context` method, the edge `cli.py::shell_command ->
Flask.make_shell_context` disappeared, because the name stopped being unique (+2/-1).

**Timing** (median of 3, this CPU, model load excluded, includes hashing every file). Cold build of
everything: run 1 136.2 s, run 2 133.0 s (about 95% is embedding; 83 files, 792 fixed + 1,027 AST
chunks).

| Scenario | Run 1 s | Run 2 s | Speed-up (run 2) | Embedded fixed / AST |
|---|---|---|---|---|
| noop | 0.037 | 0.053 | 2,507x | 0 / 0 |
| touch (same bytes) | 0.053 | 0.051 | 2,618x | 0 / 0 |
| edit_end (1 file) | 0.656 | 0.228 | 583x | 1 / 1 |
| edit_start (1 file) | 7.36 | 7.23 | 18x | 84 / 1 |
| edit_function (1 file) | 3.24 | 2.94 | 45x | 34 / 1 |
| edit_5_files | 1.507 | 0.568 | 234x | 5 / 5 |
| add_file | 0.195 | 0.209 | 636x | 1 / 5 |
| delete_file | 0.092 | 0.146 | 914x | 0 / 0 |
| rename_file | 0.137 | 0.163 | 814x | 0 / 0 |
| graph_rename_symbol | 2.82 | 2.94 | 45x | 30 / 1 |
| graph_ambiguity | 0.147 | 0.127 | 1,046x | 1 / 2 |

Full rebuild vs one-file vs five-file edit: 133 s vs 0.23 s (end-of-file edit) vs 0.57 s.

**Findings.**
- **Run 1 revealed a flaw in my implementation, not in the plan:** for small edits the cost was
  rewriting vector-store points, not embedding (0.45 of 0.66 s for `edit_end`), because every chunk
  of a changed file was upserted. Deviation 1 (recorded in the plan before run 2) skips chunks
  identical to what is stored. Run 2 cut `edit_end` from 0.66 to 0.23 s and `edit_5_files` from
  1.5 to 0.57 s. Other scenarios differ by run-to-run noise (single-digit hundredths of a second;
  `delete_file` min-max was 0.097-0.147 s in run 2). The change affects which points are written,
  not the index, and every criterion was checked again.
- **Fixed windows are not edit-stable; AST chunks are.** Inserting one line at the top of a file
  re-embedded all 84 fixed windows but exactly 1 AST chunk (`edit_start`: 7.2 s). Editing inside
  one function re-embedded 34 fixed windows vs 1 AST chunk. This is a point in favour of AST
  chunking for a live index, independent of its retrieval-quality effect.
- **Qdrant local mode does not clear points when a collection is deleted and recreated.** Found by
  the from-scratch reference test; fixed in `VectorStore` with a regression test. I checked the
  M4-M10 collections: they held exactly 792 and 1,027 points, so earlier results are unaffected.
- **Crash safety** was tested, and found a design flaw on the way (a record overwrote the file the
  old manifest pointed to). Records are now versioned by content hash, orphans are cleaned up, and
  `load()` repairs stray points or raises if points are missing.

**Caveats.** Flask-sized repository only (83 files). Graph re-assembly is global; for much larger
repositories it needs targeted re-resolution (backlog). The timings are one machine, three
repetitions; sub-0.1 s differences are noise. Only Python files are indexed. Run-1 numbers are kept
in `results/incremental_indexing_flask_run1.json`.

## Milestone 12: the Evidence API

Pre-registered in `evaluation/plans/m12-evidence-api.md` (four recorded deviations). This is an
interface and grounding milestone: **no retrieval-quality number is reported or was tuned**. The 73
questions are only a realistic query set for invariant checks. The API returns one evidence item
per retrieved file with file, file hash, line range, symbol (`exact` / `module_level` /
`unparsed`, never guessed), RRF score, per-component rank/score/lines, and structural graph facts.
The graph annotates and never reorders (M10B). Full schema: `results/evidence_schema_v1.json`.

**Validation (final run 3, after the graph fix below; runs 1 and 2 are kept in `results/evidence_api_flask_run1.json` and `..._run2_before_graph_fix.json`; run 2 passed every check too, run 1 failed two because of bugs in my validation code, see below).**

| Check | Result |
|---|---|
| File order = saved M8 AST-hybrid top 10, 73 questions | 73/73 identical |
| Files, lines and RRF scores = unmodified `HybridRetriever`, 73 questions | 73/73 identical |
| Line integrity, symbol integrity, graph integrity, schema, text = file bytes (730 items) | 0 violations |
| Repeat in-process, 73 questions | 0 differences |
| Fresh process (command line), 10 questions | 0 differences |
| Incremental update = fresh build at Flask scale (3 changes x 73 questions) | edit inside a function 72/73, rename a symbol 73/73, add a module 71/73; every difference has exactly tied embedding scores (see below) |
| Hand-checked spot checks (10 queries, judged on run 2) | 9/10 fully confirmed, 1 wrong graph edge, fixed afterwards (below) |
| Real server smoke test (uvicorn process, HTTP; `python -m app.evidence.smoke`, `results/evidence_api_smoke_flask.json`) | passes: `/v1/index/status` 200 (83 files, 1,027 chunks); file order equals the saved M8 ranking for 5 of 5 questions through HTTP; `include_text`; six malformed requests -> 422; `/health` 200; 19 s startup, 38-115 ms per request |

Descriptive counts over the 730 items: 604 `exact` symbols, 126 `module_level`, 0 `unparsed`; 652
items have at least one graph relationship, 130 hit the 10-relationship cap, 384 links connect
items of the same response. Latency of a top-10 response without text: median 29.6 ms, max 37.0 ms
in-process (30 questions, one machine). Building the index took 123-180 s across runs.

**Findings.**
- **Run 1 failed on two bugs in my validation code, not in the API:** I compared full-precision
  in-process scores with the 6-decimal scores of a response that had gone through JSON. Fixed
  and recorded as deviation 4; the unaffected checks were identical in both runs.
- **Embedding scores are not bit-stable, and exact ties follow the vector store's order.**
  Qdrant's local mode returned scores differing by about 3e-8 between identical searches, and
  chunks with identical text tie exactly (27 of 73 questions have exact embedding ties within
  their top 50). So the determinism contract is: identical evidence except the embedding raw
  score (within 1.5e-6), and the order among exactly tied chunks is the store's. The three
  differences between an incrementally updated index and a fresh one (questions 037, 045, 059)
  are all such tie cases. This is a property of the existing retrieval layer, exposed by the
  new requirement; changing tie-breaking would change retrieval and needs its own evaluation.
- **The hand check found a wrong graph edge from M10** (details: `results/evidence_spot_checks_flask.md`):
  two function-local imports `... as cls` in one file collided and `Flask.test_client` was linked
  to `FlaskCliRunner`. The evidence layer reported the graph faithfully. It was fixed as an M10
  follow-up (next section) and the whole validation was re-run on the fixed graph: all checks
  pass, and the item for `Flask.test_client` now shows `FlaskClient`. Graph facts still carry their
  `rule` and are documented as structural annotations, not relevance.
- **Scope note:** `python -m app.evidence` can also build or update the index (`--build`) as a
  development convenience. `EvidenceBuilder` itself only reads an existing `LiveIndex`; it does not
  own indexing.

**Caveats.** One repository; one machine. The validation runs were made with an uncommitted
working tree (the reports say so). The Flask-scale equivalence check uses a seeded
random embedder, not the real model, because it tests evidence logic, not embeddings.
M13 contract (frozen in the plan): the LLM layer sees evidence only through this API.

### Milestone 10 follow-up: function-local import scoping (found in Milestone 12)

Recorded as follow-up deviation 2 in [`plans/m10-code-graph.md`](plans/m10-code-graph.md). The
M12 hand check found `Flask.test_client` linked to `FlaskCliRunner`: two methods each had
`from .testing import ... as cls`, and the graph kept one import map per file, so one overwrote the
other. Imports now carry the function or class they sit in, and calls see that function's own
imports first, then the module's; nothing else in the rules changed.

| | Before | After |
|---|---|---|
| Call edges resolved | 1,452 (`unique-name` 709) | 1,525 (`unique-name` 782) |
| Graph edges (all kinds) | 2,231 | 2,283 (+53, -1, 8 edges gained cited lines) |
| V1 import edges vs Python's `ast` | identical (95/95) | identical (95/95) |
| V2 hand-verified facts | 51/51 | 51/51 |

- **The one removed edge is the wrong one**; its replacement `Flask.test_client -> FlaskClient` is
  right (checked against app.py:809-814).
- **52 of the 53 added edges came from a second effect of the same cause:** a test-local
  `from blueprintapp import app` made `app` an "external" name in all of `test_blueprints.py` and
  `test_templating.py`, so calls like `@app.route("/")` and `app.test_client()` were skipped. They
  now resolve (rule `unique-name`) to `Scaffold.route`, `App.template_filter` and so on. I read all
  52 cited lines: in each the receiver is the `app` fixture (`tests/conftest.py` returns a `Flask`)
  and the method is defined once, so all are correct. These are test files, so the count of
  correct-looking additions says little about precision elsewhere.
- **Fresh sample** (seed 1, 30 of the 64 `import`/`import-module` edges in `src/flask`): 30 of 30
  correct. Single reviewer; the sample is dominated by `@setupmethod` decorator edges.
- **Not recomputed:** the M10B retrieval result. It used the earlier graph; the call-edge change
  is 54 of 2,231 edges, in two test files and one method.

The regression tests (`tests/test_graph_builder.py`) cover: two functions with the same alias and
different targets, no leak into other functions, a local import shadowing the module-level one only
inside its function, local module and class receivers, module-level `try`/`if` imports staying
module-level, and the scope recorded by the parser. Four of them fail on the old builder.
The live index format version is now 2 (stored parses carry the scope), so an index built before
this change must be rebuilt.
