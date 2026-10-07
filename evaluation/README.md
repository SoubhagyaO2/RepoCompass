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
