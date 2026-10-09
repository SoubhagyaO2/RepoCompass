# Experiment plan: Milestone 14, additional chunks per file

**Status: DRAFT for review. Nothing is implemented. To be frozen (and committed) before any code.**
Written 2026-10-09 on top of commit `524ec53` (Milestone 13, pushed).

## Question

> Does supplying additional chunks from already-relevant files reduce unnecessary abstention and keep answers evidence-grounded,
> without increasing unsupported claims, and at what cost in tokens and latency?

**Why this experiment.** In M13, 11 of 73 set-A answers were `insufficient_evidence`; in 8 of them a ground-truth file was in the
context, but the single chunk shown for that file (Evidence API v1: one primary chunk per file) was not the one holding the answer.
M13 recorded the remedy as a backlog item, `additional_chunks`, "evaluated as its own experiment, never silently". This is that
experiment. It is an intervention on the *evidence*; the model, the prompt and the validator do not change.

**Hypothesis (one, stated before any result).** H1: with up to two additional chunks per retrieved file, the number of set-A
questions answered `insufficient_evidence` is lower than in the M13 baseline, beyond the run-to-run noise measured in this
milestone, while the safety guards of section 8 still hold. Nothing here claims an improvement until the experiment shows one.

## 1. What changes and what is frozen

| | |
|---|---|
| **Changes (the intervention)** | Evidence API **schema v2**: each item may carry `additional_chunks` (section 2). The context given to the model includes them (section 3). The context budget is raised accordingly (section 3) |
| **Frozen (unchanged, checked)** | the model request (`gemini-3.5-flash-lite`, temperature 0, 8,192 max output tokens), the system prompt (SHA-256 `26988fdf9914f8635435bd76a09853b131629c3e92622bcc6f717242e6914820`), the output validator (`backend/app/answer/validate.py`, blob SHA-256 `909ab2133d33cfe5c6d676df5fe173f1b48b003f4ae60365956955d5241a5322` at `524ec53`), retrieval and its ordering (hybrid RRF k=60 over BM25 + embeddings, depth 50, AST chunks), `top_k = 8` files, the 113 cases and their seeds, the live index, the graph |
| **Evidence API v1** | untouched. `GET /v1/evidence` stays byte-for-byte what M12 validated; v2 is a new endpoint |

## 2. Evidence API schema v2

New endpoint `GET /v2/evidence?q=...&top_k=...&include_text=...&extra_chunks=N` (default `extra_chunks=2`; allowed 0-4; 0 returns v1
content with `schema_version: 2`). Response `schema_version: 2`. Every v1 field of every item is unchanged; each item gains

```json
"additional_chunks": [
  {"rank_in_file": 1, "line_start": 120, "line_end": 150,
   "symbol": {...} | null, "symbol_status": "exact|module_level|unparsed",
   "scores": {"rrf_chunk": 0.0325, "bm25": {"rank": 4, "score": 7.1} | null, "embedding": {"rank": 9, "score": 0.61} | null},
   "text": "..." | null}
]
```

**Selection policy (fixed now, not tuned).**
1. *Candidates* for file F: the chunks of F that appear in the BM25 chunk list or the embedding chunk list **already retrieved**
   (the same depth-50 chunk lists the ranking uses), except F's primary chunk. No new retrieval, no extra search, nothing outside
   what retrieval returned; a chunk that retrieval did not return is never added.
2. *Chunk score:* `rrf_chunk = sum over the two components of 1 / (60 + position of the chunk in that component's chunk list)`
   (position among chunks, not files; a component that did not retrieve the chunk contributes 0). The constant 60 is the existing RRF constant.
3. *Choice:* the `N` candidates with the highest `rrf_chunk` (default `N = 2`); ties by the better single-component position, then by
   `line_start`. Fewer candidates -> fewer extra chunks (possibly none).
4. *Presentation:* `rank_in_file` follows the choice order (1 = best). Files keep exactly their v1 order and scores; the primary chunk, its
   scores, lines, symbol and graph annotation are exactly the v1 ones. Graph annotations are **not** produced for additional chunks (out of scope).
5. Line numbers, symbol mapping and `text` follow the M12 rules (1-based inclusive lines, symbols by `(file, start line)`, never guessed).
   AST chunks of one file do not overlap, so no de-duplication of text is needed.

**M12-style invariants, tested before any live run** (on the synthetic repository and, as in M12, over the 73 questions on Flask):
(a) the v2 response with `extra_chunks=0` and the v1 response are identical in every v1 field; (b) for any `N`, file order, primary chunk, scores,
symbols and graph facts equal v1; (c) additional chunks are distinct from the primary and from each other, belong to the same file, lie inside it, and
every one appears in a component's retrieved chunk list; (d) at most `N` per file; (e) ordering and tie-breaking as above, deterministic (within the M12 tolerance for
embedding scores); (f) `text` equals the file's lines; (g) an incrementally updated index gives the same v2 evidence as a fresh one (excluding exact-tie
cases, as in M12); (h) the v1 endpoint's M12 validation still passes unchanged.

## 3. Context for the model

The context builder (`app/answer/context.py`) gets a v2 mode; **the prompt text, the validator, the citation format and the output schema do not change.**

- Items are written in file rank order. After a file's primary block (`[E3]`) come its additional blocks, ids `E3b`, `E3c` (same header fields: file, lines,
  symbol, category; no scores; no graph lines for them). The model cites these ids exactly like any other; the validator, which accepts any id present in the
  context, needs no change. This is a check in the tests, not an assumption: a unit test feeds a claim citing `E3b` through the unchanged validator.
- **Budget:** 72,000 characters of evidence text (three times the M13 value, matching up to three chunks per file); blocks are added in the order above while they fit;
  a block that does not fit is omitted whole together with everything after it, and the number omitted is told to the model and recorded (the same rule as M13). In M13
  the largest context was 20,850 characters and nothing was ever omitted at 24,000; with two extra chunks per file the budget is meant to be non-binding, and the
  omissions per case are reported so that this can be checked rather than assumed.
- Repository text appears only inside the `BEGIN/END TEXT` blocks; scores are not shown.

## 4. Conditions and cases

| Condition | What | Calls |
|---|---|---|
| **Baseline (M13)** | the committed M13 results, **immutable**: never regenerated, never edited, never re-labelled. Pinned by hash (section 10) | 0 (already exists) |
| **M14 (v2)** | the same 113 cases (`plan_cases`: sets A, B, C, D with the same ids, seeds, derangement and injections), with v2 evidence. Set C pairs each question with *another question's v2 evidence*; set D plants its instruction at the end of the first item's primary text, as in M13 | 113 |
| **Repeat of the baseline (noise)** | the 73 set-A cases again with **v1** evidence and exactly the M13 code path, run in the same period as M14 | 73 |

The repeat exists because temperature 0 is not deterministic and the provider may change the served model: it measures how many set-A answers change status between two runs
of the *same* condition, so that a v2-vs-baseline difference can be judged against it. Total 186 calls, sequential, 4 s apart, the M13 retry policy and fail-fast quota stop,
resumable from append-only raw files. If the returned `modelVersion` of M14 or of the repeat differs from the baseline's (`gemini-3.5-flash-lite`), that is reported as a flag
and interpreted via the repeat.

## 5. Outcomes

**Primary (one):** the number of set-A questions whose output status is `insufficient_evidence`. Baseline: 11 of 73. Compared question by question with the baseline: let
`b` = questions that abstained in the baseline and whose M14 output is `answered` or `partial`, `c` = questions whose baseline output was `answered` or `partial` and whose M14 output is `insufficient_evidence`.
An `invalid_output` (in either run) is neither an abstention nor an answer: such questions are excluded from `b` and `c`, counted separately and listed. Test: exact two-sided McNemar (binomial on `b` versus `c`), alpha = 0.05.
The same statistic is computed for the repeat versus the baseline.

**Secondary (reported, no thresholds):** by set, never merged into one number:
- status distribution of A, B, C, D; validity rate (baseline 110/113); `partial` count on A (baseline 16);
- sets B and C: correct abstentions (baseline 13/15 and 19/20) and every non-abstaining output, listed;
- set D: planted instructions followed (baseline 0/5);
- claims per answer, citations per claim, citations of additional-chunk ids (how often the model uses them);
- cost: input and output tokens per call (baseline mean 4,018 input tokens on set A), wall latency (baseline median 4.0 s on the full run), omitted-for-budget counts, number of calls and any quota stops;
- **context diagnostic, no model involved:** over the 59 set-A questions that have `relevant_symbols`, the share whose context contains a chunk of the named file whose qualified symbol equals
  the named symbol, under v1 and under v2 (a deterministic check of whether the needed chunk is supplied at all; it is not a retrieval score and not used to tune anything).

**Regression analysis (required):** a 4 x 4 transition table of set-A statuses (baseline -> M14, and baseline -> repeat), and an explicit list, with the questions and the reasons, of every set-A answer that
got worse (answered -> insufficient/partial/invalid; valid -> invalid; or a new FAIL in the review below), every one that improved, and every one that did not change.

## 6. Review

The M13 lessons are applied before the run. Review sheets show, for every reviewed answer, **the structure (graph relationship) lines the model was given** and, for `partial` and
`insufficient_evidence`, the whole context. Reviewed answers:
(a) every set-A answer that moved from `insufficient_evidence` or `partial` in the baseline to `answered` or `partial` in M14 (the "newly answered" ones; these are what the hypothesis is about);
(b) a seeded sample (seed 1) of 15 of the other valid set-A M14 answers;
(c) every `answered`/`partial` output of sets B, C and D.
Same five-item rubric, PASS / FAIL / UNCERTAIN, as M13 (frozen). Reviewer 1 (me, an AI and the system's author) labels everything **before** seeing any other label. **A human reader is requested** (you, or someone you name)
for at least the newly answered answers up to 10; M13 had no human reader (plan deviation 5 there). If no human labels are available, the report says that no person labelled anything, in its first paragraph. Any AI second reader
is identified as such, and agreement is always reported as a ceiling when a reader saw another's labels.

## 7. Decision rule for H1 (fixed now)

H1 is called **supported** only if all of these hold:
1. the McNemar test on the primary outcome is significant (p < 0.05) with `b > c`;
2. the same test on the repeat versus the baseline is **not** significant (p >= 0.05), i.e. the run-to-run noise does not by itself produce an apparent effect;
3. every guard of section 8 holds;
4. no newly answered answer (a) is labelled FAIL on rubric items 1 or 2 by reviewer 1.
It is called **not supported** if `b <= c`, or if (1) fails with the guards holding; **inconclusive** if (1) holds but (2) fails (the change is within noise) or (4) fails (an improvement bought with an unsupported claim is not reported as an improvement).
UNCERTAIN labels never fail a guard but are always counted and listed.

## 8. Guards and pass/fail

The milestone's engineering pass criteria (independent of whether H1 is supported):
1. all tests pass, including the v2 invariants of section 2 and the unchanged-validator test for `E3b`-style ids; lint clean;
2. the baseline files are unchanged (hashes, section 10);
3. the evidence audit (M12 `check_response`, extended to additional chunks) has 0 violations on every evidence response used;
4. no fabricated reference (a path, line or symbol not in the context) in any reviewed answer, by reviewer 1;
5. no planted instruction followed in set D;
6. the run is complete and honest: every call has a usage record, errors/retries/quota stops counted, `modelVersion` flagged if it changed, nothing edited after the fact.

Not a pass criterion, but reported with the verdict on H1: the sets B and C false-answer counts and the token/latency ratios. No threshold is set for them in advance, because any number chosen now would be arbitrary;
the acceptability of the cost is your decision.

## 9. What is not claimed

Nothing about other models, other repositories, other values of `N`, or retrieval quality in general (the file ranking is unchanged and is not re-measured). Nothing about answer quality beyond the reviewed answers.
A significant primary result on 73 questions with one model and one noise repeat is evidence, not a proof of generalisation. The stronger-model comparison (`gemini-3.7-flash` or a paid tier) is a separate, later milestone so that gains can be attributed correctly.

## 10. Integrity: what is immutable

Hashes are SHA-256 of the committed blobs at `524ec53` (`git show 524ec53:<path> | sha256sum`; LF line endings):

| File | SHA-256 |
|---|---|
| `evaluation/results/answers_flask_raw.jsonl` | `29439ab46aa6800f47c99cd050112998d6008a4b2eb663afe7beab0ff67785d8` |
| `evaluation/results/answers_flask.json` | `f4a9cb8993c46baf00defd488a48dcf4876d650429aff0b6c00b307baff8f575` |
| `evaluation/results/answers_flask_evidence.json` | `e38e265cbdc8ced75909a646f927aa85cff0186d28745a4e202015f44af32100` |
| `evaluation/results/answers_labels_reviewer1.json` | `e8c93a9715f9fa552f25eb1f81cc72b7dff770c56ef821f932871234fba288b8` |
| `evaluation/results/answers_labels_reviewer2.json` | `69e16d2735c295312a813c961818cfe82674a7a59630eb838660d1ce0d817f12` |

M14 writes only new files (`answers_v2_*`, `answers_v1_repeat_*`, `evidence_v2_*`); the runner refuses to start if a baseline hash differs. Dataset fingerprint
`6e78d06ad988b637eb3f77f047232238667e0cf9b325abd971f1eab3c024a311`, Flask commit `d73fa1cdcbd8b1465c151db8924ba58b1dd14e35`. Any change after freezing is a recorded deviation, not an edit.

## 11. Planned modules and files

`app/retrieval/hybrid.py` (expose each file's retrieved chunk positions; `search`/`search_detailed` behaviour unchanged), `app/evidence/{schema,builder}.py` (v2), `app/api/evidence.py` (`/v2/evidence`),
`app/answer/context.py` (v2 mode), `app/evaluation/run_answers_v2.py` (conditions, hash check, transition tables, diagnostic, McNemar), tests `tests/test_evidence_v2.py`, `tests/test_answer_context_v2.py`;
results `evaluation/results/answers_v2_flask.json` (+ raw JSONL, repeat, labels, comparison).

## 12. Decisions I need from you before freezing

1. **`N = 2` extra chunks and a 72,000-character budget** (three times M13's). Agree, or a different fixed value? (Whatever is chosen is fixed; it is not tuned on the results.)
2. **The noise repeat** (73 extra calls). Without it a difference could be run-to-run noise; I recommend keeping it. Total 186 calls: is the free-tier quota enough in a day, or accept resuming next day?
3. **A human reader** for the newly answered answers (up to 10). Who, and when? If nobody, the plan reports that explicitly.
4. **The decision rule** of section 7, including requiring the repeat not to show an "effect" by itself. Agree?
5. **Evidence v2 as a new `/v2/evidence` endpoint**, with `/v1/evidence` untouched. Agree?
