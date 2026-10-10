# Experiment plan: Milestone 14, additional chunks per file

**Status: FROZEN 2026-10-10 before any M14 code. Draft history: commit `283c585`. Approved with the decisions and clarifications of section 12. Changes after this point only as recorded deviations.**
Written on top of commit `524ec53` (Milestone 13, pushed).

## Question

> Does supplying additional chunks from already-relevant files reduce unnecessary abstention and keep answers evidence-grounded,
> without increasing unsupported claims, and at what cost in tokens and latency?

**Why this experiment.** In M13, 11 of 73 set-A answers were `insufficient_evidence`; in 8 of them a ground-truth file was in the
context, but the single chunk shown for that file (Evidence API v1: one primary chunk per file) was not the one holding the answer.
M13 recorded the remedy as a backlog item, `additional_chunks`, "evaluated as its own experiment, never silently". This is that
experiment. It is an intervention on the *evidence*; the model, the prompt and the validator do not change.

**Hypothesis (one, stated before any result).** H1: with up to two additional chunks per retrieved file, the number of set-A
questions answered `insufficient_evidence` is lower than in the M13 baseline, and the difference is distinguishable from run-to-run
noise (section 7), while the safety guards of section 8 still hold. Nothing here claims an improvement until the experiment shows one.

## 1. What changes and what is frozen

| | |
|---|---|
| **Changes (the intervention)** | Evidence API **schema v2**: each item may carry `additional_chunks` (section 2). The context given to the model includes them (section 3). The context budget is raised accordingly (section 3) |
| **Frozen (unchanged, checked)** | the model request (`gemini-3.5-flash-lite`, temperature 0, 8,192 max output tokens), the system prompt (SHA-256 `26988fdf9914f8635435bd76a09853b131629c3e92622bcc6f717242e6914820`), the output validator (`backend/app/answer/validate.py`, blob SHA-256 `909ab2133d33cfe5c6d676df5fe173f1b48b003f4ae60365956955d5241a5322` at `524ec53`), retrieval and its ordering (hybrid RRF k=60 over BM25 + embeddings, depth 50, AST chunks), `top_k = 8` files, the 113 cases and their seeds, the live index, the graph |
| **Evidence API v1** | untouched. `GET /v1/evidence` stays byte-for-byte what M12 validated; v2 is a new endpoint |

`N = 2` additional chunks per file and the 72,000-character budget are fixed for the whole experiment. They are not raised, lowered or varied per question, before or after seeing any result.

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
  context, needs no change. This is a check in the tests, not an assumption: **before the experiment starts**, a unit test feeds claims citing `E3b` and `E3c`, with lines inside their ranges and
  fabricated ones outside, through the unchanged validator and checks the accept/reject outcomes.
- **Budget:** 72,000 characters of evidence text (three times the M13 value, matching up to three chunks per file); blocks are added in the order above while they fit;
  a block that does not fit is omitted whole together with everything after it, and the number omitted is told to the model and recorded (the same rule as M13). In M13
  the largest set-A context was 20,850 characters and nothing was ever omitted at 24,000; with two extra chunks per file the budget is meant to be non-binding, and the
  omissions are reported per case so that this can be checked rather than assumed.
- **Reported for every M14 case (not just in aggregate):** the actual context size in characters, the number of blocks, the number of additional chunks supplied per file, and the
  number of blocks omitted for budget. The budget is never increased selectively for difficult questions.
- Repository text appears only inside the `BEGIN/END TEXT` blocks; scores are not shown.

## 4. Conditions and cases

| Condition | What | Calls |
|---|---|---|
| **Baseline (M13)** | the committed M13 results, **immutable**: never regenerated, never edited, never re-labelled. Pinned by hash (section 10) | 0 (already exists) |
| **M14 (v2)** | the same 113 cases (`plan_cases`: sets A, B, C, D with the same ids, seeds, derangement and injections), with v2 evidence. Set C pairs each question with *another question's v2 evidence*; set D plants its instruction at the end of the first item's primary text, as in M13 | 113 |
| **Repeat of the baseline (noise)** | the 73 set-A cases again with **v1** evidence and exactly the M13 code path (frozen prompt, validator, model request, budget 24,000), run in the same period as M14 | 73 |

The repeat exists because temperature 0 is not deterministic and the provider may change the served model. It measures how often set-A answers change between two runs of the
*same* condition and, more importantly, gives a **contemporaneous control** for M14 (section 7). Total 186 calls, sequential, 4 s apart, the M13 retry policy and fail-fast quota stop,
resumable from append-only raw files. Order: M14 first, then the repeat. The two must be **completed within 72 hours of each other**, otherwise the "same period" assumption fails (section 7).
If the returned `modelVersion` of M14 or of the repeat differs from the baseline's (`gemini-3.5-flash-lite`), it is flagged and handled by section 7.

## 5. Outcomes

**Primary (one):** the number of set-A questions whose output status is `insufficient_evidence`. Baseline: 11 of 73.

*Paired counts for a comparison X versus Y (both sets of 73):* `b` = questions where Y abstained (`insufficient_evidence`) and X's output is `answered` or `partial`; `c` = questions where Y's output
was `answered` or `partial` and X abstained. A question whose output is `invalid_output` in either run is neither: it is excluded from `b` and `c`, counted separately and listed.
*Test:* exact two-sided McNemar (binomial on `b` versus `c`), **alpha = 0.05**. Three comparisons are computed: **A** = M14 versus the baseline, **B** = M14 versus the repeat (contemporaneous),
**N** = the repeat versus the baseline (reported as the noise measurement). The paired transition tables are always reported, not only the p-values.

*Power note (stated so the result is not over-read):* with 11 abstentions in the baseline, `b` cannot exceed 11. With `c = 0` an exact test needs `b >= 6` for p < 0.05; with `c = 1`, `b >= 8`. The experiment can therefore
detect only a large reduction; a smaller true effect would end as "not supported", which would not show that there is no effect.

**Secondary (reported, no thresholds), by set, never merged into one number:**
- status distribution of A, B, C, D; validity rate (baseline 110/113); `partial` count on A (baseline 16);
- sets B and C: correct abstentions (baseline 13/15 and 19/20) and every non-abstaining output, listed; set D: planted instructions followed (baseline 0/5);
- claims per answer, citations per claim, and how often the additional-chunk ids (`E3b`, ...) are cited;
- cost: input and output tokens per call (baseline mean 4,018 input tokens on set A), wall latency (baseline median 4.0 s over all 113 calls), context sizes and omissions (section 3), number of calls, quota stops;
- **context diagnostic, no model involved:** over the 59 set-A questions that have `relevant_symbols`, the share whose context contains a chunk of the named file whose qualified symbol equals
  the named symbol, under v1 and under v2 (a deterministic check of whether the needed chunk is supplied at all; it is not a retrieval score and is used to tune nothing).

**Regression analysis (required):** a 4 x 4 transition table of set-A statuses (answered / partial / insufficient / invalid) for baseline -> M14 and for baseline -> repeat, and an explicit list, with the questions and the reasons, of every
set-A answer that got worse (answered -> insufficient/partial/invalid; valid -> invalid; or a new FAIL in the review), every one that improved, and every one that did not change.

## 6. Review

The M13 lessons are applied before the run. Review sheets show, for every reviewed answer, **the structure (graph relationship) lines the model was given** and, for `partial` and
`insufficient_evidence`, the whole context.

**Answers reviewed**, with the selection rules fixed now (before any M14 answer exists):
(a) the *newly answered* set-A answers: those that abstained in the baseline and are `answered` or `partial` in M14. If there are 10 or fewer, all are reviewed by the human reader; if more, a reproducible seeded
sample (seed 1, taken from the question ids in sorted order) of 10; all of them are reviewed by the AI reader;
(b) a seeded sample (seed 1) of 15 of the other valid set-A M14 answers;
(c) every `answered`/`partial` output of sets B, C and D.
Rubric: the same five items, PASS / FAIL / UNCERTAIN, as M13 (frozen).

**Readers.**
- *Reviewer 1 (AI: Claude, the system's author)* labels everything **before** seeing any other label, and records labels in a file before the human sheet is handed over.
- *Human reader (required if available):* the user, who is asked to label the answers selected by rule (a) (at most 10) on a sheet that contains no AI labels. The labels file `answers_v2_labels_human.json` records the reader's **name or identity, the date,
  the cases reviewed and the labels**. Nobody else's labels are shown to the human reader.
- *If the human review is not completed*, the results state, in their first paragraph and in the verdict line, that **human review was not completed**. AI labels (reviewer 1, or any AI second reader, identified as such) can supplement the review but
  **do not satisfy the human-review requirement**, and agreement between AI readers is never presented as human agreement. An AI reader who saw another reader's labels first is identified, and their agreement is reported as a ceiling.

## 7. Decision rule (fixed now)

Alpha is 0.05 (two-sided) for each test; the categories are exhaustive and mutually exclusive, and are applied in the order given.

**Inconclusive** if any prespecified validity problem occurs:
(i) any of the 186 planned calls is missing or incomplete at analysis time (a quota pause is not a problem if the run completes, but see (iv));
(ii) the returned `modelVersion` of M14 or of the repeat is not the baseline's `gemini-3.5-flash-lite` (a provider-side change makes the baseline an unreliable control);
(iii) 8 or more set-A questions (about 10%) are excluded from the paired analysis because an output is `invalid_output` in either run being compared;
(iv) M14 and the repeat were not completed within 72 hours of each other, or the model becomes unavailable partway through;
(v) the noise check is not convincing: comparison **N** (repeat versus baseline) has `b_N + c_N >= 6`, i.e. an identical-condition rerun changed at least 6 abstention outcomes, so run-to-run variation is too large for 73 questions to resolve a gain;
(vi) comparison **A** is significant with `b > c` but comparison **B** is not (the apparent gain over the baseline cannot be distinguished from the contemporaneous control);
(vii) comparison **B** is significant with `b > c` but comparison **A** is not (the baseline and the repeat disagree, so the baseline is not a reliable control).
*A non-significant noise test alone is not taken as proof that there is no noise: (v), (vi) and (vii) are the operational definitions.*

**Supported** only if none of the above applies and all of these hold:
1. comparison **A** is significant (p < 0.05) with `b > c`;
2. comparison **B** is significant (p < 0.05) with `b > c`;
3. every guard of section 8 holds;
4. no newly answered answer (rule (a) of section 6) is labelled FAIL on rubric item 1 or 2 by **any** reader who labelled it (the human reader or reviewer 1).
The verdict line states `human review: completed` or `human review: NOT completed` next to the category.

**Not supported** otherwise: there is no statistically significant evidence for the preregistered improvement (neither **A** nor **B** is significant with `b > c`), or the guards hold but item 4 fails (an improvement bought with an unsupported claim is not reported as an improvement), without any of the validity problems above.
UNCERTAIN labels never fail a guard but are always counted and listed. Every transition table is reported in all cases.

## 8. Guards and pass/fail

The milestone's engineering pass criteria (independent of whether H1 is supported):
1. all tests pass, including the v2 invariants of section 2 and the unchanged-validator test for `E3b`-style ids; lint clean;
2. the baseline files are unchanged (hashes, section 10);
3. the evidence audit (M12 `check_response`, extended to additional chunks) has 0 violations on every evidence response used;
4. no fabricated reference (a path, line or symbol not in the context) in any reviewed answer, by reviewer 1 (and by the human reader if their labels exist);
5. no planted instruction followed in set D;
6. the run is complete and honest: every call has a usage record, errors/retries/quota stops counted, `modelVersion` flagged if it changed, nothing edited after the fact;
7. the quota and failure policy of section 9 was followed.

Not a pass criterion, but reported with the verdict: the sets B and C false-answer counts and the token/latency ratios. No threshold is set for them in advance, because any number chosen now would be arbitrary;
the acceptability of the cost is your decision.

## 9. API quota and failure policy

- Provider and model as in M13: Gemini, `gemini-3.5-flash-lite`, free tier. The daily request cap of this model is unknown (113 calls fitted in one day in M13); the v2 contexts are larger (an estimated two to three times the input tokens), so a per-minute or per-day token limit may be hit.
- Requests are sequential, at least 4 s apart. Transport errors, HTTP 429 with a delay of at most 60 s, and 5xx are retried up to 3 times (4, 8, 16 s) and recorded. A 429 that names a per-day quota or asks for more than 60 s **stops the run at once** with exit code 3
  and no row written for the case (M13 deviation 3); the run is resumed from the append-only raw file after the quota resets. Invalid model output is never retried. A case that ended in a transport error may be attempted again in a later pass (M13 deviation 2); every attempt stays in the raw file.
- The same policy applies to the repeat. Quota pauses do not by themselves invalidate the experiment, but section 7 (iv) applies: M14 and the repeat must finish within 72 hours of each other, and an unavailable model is a validity problem, not a reason to switch model mid-experiment.
- No model, prompt, validator, budget or `N` change is made to work around a quota. If the experiment cannot finish, the verdict is *inconclusive* and says why.

## 10. What is not claimed

Nothing about other models, other repositories, other values of `N`, or retrieval quality in general (the file ranking is unchanged and is not re-measured). Nothing about answer quality beyond the reviewed answers.
A significant primary result on 73 questions with one model and one noise repeat is evidence, not a proof of generalisation. The stronger-model comparison (`gemini-3.7-flash` or a paid tier) is a separate, later milestone so that gains can be attributed correctly.

## 11. Integrity: what is immutable

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

## 12. Decisions and clarifications that froze this plan (2026-10-10)

1. `N = 2` additional chunks and a 72,000-character budget: **approved**, fixed for the whole experiment; context sizes, omissions and per-case chunk counts are reported; no selective increase.
2. The 73-call noise repeat: **kept**, with frozen v1 evidence and identical settings; its role in the decision is defined operationally (section 7 (v), (vi), (vii) and condition 2 of "Supported"), not by a non-significant test alone.
3. Human review: **required if available**; one human reader (the user) for up to 10 newly answered cases chosen by the rule of section 6; reader identity, date, cases and labels recorded; if not done, "human review NOT completed" is recorded and AI labels do not substitute.
4. Decision rule: **approved with the clarification** that the categories are Supported / Not supported / Inconclusive with the exact definitions and alpha of section 7; paired transitions always reported.
5. `/v2/evidence` as a new endpoint with `/v1/evidence` unchanged: **approved**; the unchanged validator is tested with `E3b`/`E3c` ids before the run.

## 13. Freeze checklist (verified 2026-10-10 before freezing)

| Item | Status |
|---|---|
| Noise-repeat decision rule operationally defined | section 7 (v), (vi), (vii), "Supported" condition 2 |
| Significance level and outcome categories frozen | alpha 0.05 two-sided; Supported / Not supported / Inconclusive, section 7 |
| Human-review arrangement, or its explicit absence, recorded | section 6 (the user as human reader, with the not-completed clause); whether the user will do it is **to be confirmed** before the human sheet is handed over |
| 113 original cases and pinned hashes verified | verified by script on 2026-10-10: the five baseline files and `validate.py` match their pinned SHA-256 at `524ec53` and at HEAD; the prompt hash matches; 113 cases (A 73, B 15, C 20, D 5) with 113 unique ids equal the baseline's raw case ids; the dataset fingerprint and Flask commit match; baseline set A statuses answered 43, partial 16, insufficient 11, invalid 3 |
| API quota and failure-handling policy documented | section 9 |
| Plan committed as frozen before implementation | this file, committed separately from the draft `283c585` |

## 14. Planned modules and files

`app/retrieval/hybrid.py` (expose each file's retrieved chunk positions; `search`/`search_detailed` behaviour unchanged), `app/evidence/{schema,builder}.py` (v2), `app/api/evidence.py` (`/v2/evidence`),
`app/answer/context.py` (v2 mode), `app/evaluation/run_answers_v2.py` (conditions, hash check, transition tables, diagnostic, McNemar tests, sheets), tests `tests/test_evidence_v2.py`, `tests/test_answer_context_v2.py`;
results `evaluation/results/answers_v2_flask.json` (+ raw JSONL, repeat, labels, comparison).
