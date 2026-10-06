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

## Scoring rules (used from Milestone 6)

Results are scored against **primary** files (the headline) and, separately, against
primary + supporting files ("any required evidence"). Reporting both shows whether a retriever
finds the real answer location or only related code.

- **Hit@k:** at least one of the top-k results is in a counted file.
- **MRR:** 1 / rank of the first result in a counted file (0 if none in the results).
- Results from files outside the counted set never score, even if plausible (for example a test
  that exercises the function).
- Every metric is reported overall **and** broken down by category and by difficulty.
