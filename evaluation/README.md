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
