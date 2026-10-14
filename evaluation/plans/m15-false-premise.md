# Experiment plan: Milestone 15, false-premise handling

**Status: FROZEN, step 1 of 2, on 2026-10-12: the protocol and the question sets. Draft history: `e4a19d1`; authoring commit `111d983` (the selected questions are unchanged by this freeze). Step 2, the freeze of the chosen prompt, comes after the development phase and before the first test-set call. Changes after this point only as recorded deviations.**
Written on top of commit `119c975` (Milestone 14, pushed). Decisions of section 12 agreed on 2026-10-12.

**Human review is not conducted in this milestone and is outside its scope**, as in M14. Every criterion below is automated, and no result will be described as human-validated answer quality.

## Question

> Can the answer layer tell apart (a) evidence that directly answers the question, (b) evidence that only mentions a related concept, and (c) a question whose premise the evidence does not support, so that it abstains on (b) and (c) **without** abstaining more on questions it can answer?

**Why.** The only clear new failure of M14 was a false-premise answer: asked "How are database migrations applied?", the model (given the extra chunks) described the tutorial's `init_db`, which drops and recreates tables and never mentions migrations; the M13 baseline had abstained. A second case, `unans-08` ("Which module renders the built-in admin dashboard UI?", answered from a test blueprint named `admin`), was answered in both M13 and M14. Both are the same failure: **a topical decoy is mistaken for an answer, and the question's premise is accepted unchecked.**

| Frozen benchmark cases that answered although the premise is false | M13 | M14 (v2 evidence) |
|---|---|---|
| set B (15 unanswerable) | `unans-08` answered, `unans-11` partial | `unans-08` answered, `unans-14` answered |
| set C (20 mismatched evidence) | 1 non-abstaining | 3 non-abstaining (all supported by chunks that were in the other question's evidence) |

The risk of the obvious fix is just as real: a prompt that says "be careful" can make the model abstain on everything. **The primary outcome is therefore defined on matched pairs**, so that abstaining everywhere scores zero.

**Hypothesis (one).** H1: adding an explicit premise-and-evidence-relation assessment to the answer contract increases the number of correct pairs (abstain on the false-premise question, answer its matched true-premise question) beyond the unchanged contract, while the over-abstention and validity guards of section 7 hold.

## 1. The intervention (one treatment; everything else is frozen)

**Treatment = output contract v3 + prompt v3.** The model, evidence mode (schema v2, N = 2, 72,000-character budget), context template, temperature, token limit and retry policy are exactly M14's. Baseline arm = the M14 prompt and validator, unchanged (hashes in section 10).

New required fields in the model's JSON, placed before the claims:

```json
{"premise": "<the assumption the question makes about the repository, or \"none\">",
 "premise_supported": true | false,
 "evidence_relation": "direct" | "related_only" | "none",
 "status": "answered" | "partial" | "insufficient_evidence",
 "claims": [ ... as before ... ],
 "missing": "..." }
```

**Validator v3 (deterministic, added to the unchanged checks; a separate file, the M13/M14 validator stays untouched):**
1. `premise` is a non-empty string; `premise_supported` is a boolean; `evidence_relation` is one of the three values; no other fields.
2. `premise_supported = false` -> `status` must be `insufficient_evidence` (no claims, non-empty `missing`).
3. `evidence_relation = "direct"` is required for `answered` and `partial`; `"related_only"` or `"none"` -> `insufficient_evidence`.
4. `answered`/`partial` therefore require `premise_supported = true` and `evidence_relation = "direct"`.
5. The rules of M13 section 4a otherwise apply unchanged (citation ids, no fabricated paths, lines or symbols, etc.).

The validator checks **consistency, not truth**: it cannot know whether the model's `direct` is right. The experiment tests whether being made to state the premise and classify the evidence improves the discrimination; that is the mechanism under test.

**Prompt v3** adds rules to the frozen system prompt (the final wording is fixed in the development phase, section 4): state the question's assumption; say whether the evidence shows it; code that shares vocabulary with the question but does not do what it asks about is `related_only`, not an answer; a question asking for something Flask lacks is answered `insufficient_evidence` with the unsupported premise named in `missing`.

## 2. Question sets (authored before the prompt, hashed, frozen)

All new questions are authored **before** any prompt v3 text exists, so the prompt cannot be fitted to them. Each set is written to a dataset file, hashed in the freeze commit and never edited afterwards.

| Set | Contents | Role |
|---|---|---|
| **P/Q test pairs** | 24 matched pairs = 48 new questions. `P_i`: a question with a **false or unsupported premise** about Flask. `Q_i`: a matched question with a **true premise** about the real nearby feature, sharing the topic vocabulary and answerable from the repository. 8 pairs per subtype: **S1** feature Flask lacks, no related code (GraphQL-style); **S2** topical decoy: related code exists but is not the asked-for thing (the `unans-14` / `unans-08` pattern); **S3** false presupposition inside a real topic ("which function rolls back the session transaction") | **primary outcome** |
| **Dev pairs** | 8 further pairs (16 questions) of the same subtypes, plus the three known failures `unans-08`, `unans-14`, `unans-11` | used only to choose among prompt candidates (section 4) |
| **Regression suite** | the 113 M14 cases unchanged (sets A, B, C, D, same ids, seeds, derangement, injections), run with the treatment | guards and descriptive regression analysis |

**Authoring rules (fixed now).** Each `P_i` and `Q_i` is verified against the pinned Flask checkout before freezing: a case-insensitive search of the Python files shows the false premise really is absent (as was done for set B); for each `Q_i` the author records the ground-truth file and symbol and a **model-free check** confirms that the v2 context contains that chunk (otherwise the question is replaced before freezing); for each S2 `P_i` the author records the decoy chunk and the same check confirms it is in the context (otherwise it is not a decoy and is replaced). The pairs and these records are hashed. Because I wrote the questions and will write the prompt, the author is not independent: you may add or replace questions before the freeze, and any questions you contribute are used as written.

## 2a. Authoring record (kept, not rewritten)

The 36 first-pass candidates were written in one pass, before any prompt text or model call, and checked by `backend/app/evaluation/check_m15_pairs.py`. What happened, in order:

| Step | Candidates passing | What changed |
|---|---|---|
| First pass (`m15_pair_candidates_pass1.json`, `m15_pair_quality_pass1.json`, both kept) | 18 of 36 | failures: absence regexes too broad (7: they matched `docs/conf.py` and docstrings), twin's answer chunk not in context (7), decoy not in context (10); shortfalls in all three subtypes |
| Pass 2, first run | 34 of 40 | regexes narrowed to implementations, absence scan limited to `src/`, `tests/`, `examples/`; decoys re-anchored on related symbols that the retriever actually returns; unreachable twins reworded; 7 candidates dropped, 11 added |
| After two small fixes | 35 of 39, then 37 of 39 | `log_exception` truth moved to `Flask`; a superseded duplicate dropped; two decoys re-anchored |
| After removing a lexical cue | 37 of 39 | 6 of 24 false-premise questions contained "built-in" while none of their twins did; the word was removed and a balance check (C7) added |
| After fixing two bugs found by unit tests | 36 of 39 | a stemming bug ("does" -> "doe") had inflated every pair's shared-word count (the corrected check still passes every selected pair); S2/S3 pairs without a decoy had been passing silently and one was rejected and replaced |

The intermediate per-run quality files were overwritten as the script was rerun; only the first-pass files and the final files are kept, and this table is the record of the steps between them. All tuning in this table used retrieval and text checks only: **no model output was ever seen for any of these questions**. The final selection (fixed rule: per subtype the first 8 passing pairs are the test set, the next 3/3/2 the development set) is 24 test pairs and 8 development pairs.

**What these checks establish and what they do not.** They establish structural quality: a well-formed pair, a false premise that is really absent from the code, a twin whose answer is in the context, a decoy that is really in the context, balanced wording cues. They do **not** establish freedom from author bias: the same party wrote the questions, chose the decoys and will write the prompt candidates, and the pairs reflect that author's idea of a decoy.

## 3. Conditions and calls

| Condition | What | Calls |
|---|---|---|
| **Baseline arm** | the M14 contract on the 48 test questions, run contemporaneously | 48 |
| **Treatment arm** | contract v3 + the chosen prompt on the 48 test questions | 48 |
| **Regression** | contract v3 on the 113 M14 cases; control = the committed M14 v2 results (immutable, pinned by hash) | 113 |
| *Development (before the freeze of step 2)* | 19 dev questions x (baseline + up to 3 prompt candidates) | up to 76 |

About 285 calls overall, sequential, 4 s apart, M13's retry policy and fail-fast quota stop, resumable from append-only raw files; at the M14 pace this is about two days on the free tier. Same model request as M14 (`gemini-3.5-flash-lite`, `modelVersion` recorded; a change of served version during the run is a validity problem, section 8).

## 4. Development phase and the second freeze

1. Up to **three** prompt-v3 candidates are written and recorded (full text, committed) **before** being run; each is run once on the dev questions with the v3 contract. The M14 baseline is run once on the same dev questions.
2. Selection rule (fixed now): the candidate with the most **correct dev pairs** (same definition as the primary outcome); ties broken by fewer `invalid_output`, then by the shorter prompt. The three known failures are reported but are not part of the selection score.
3. The selected prompt text and its SHA-256 are committed as the **second freeze** before the first test-set call. No test or regression question is run during development. If no candidate improves on the baseline on the dev pairs, the experiment still runs with the best candidate (a negative result is a result), and says so.

## 5. Outcomes

**Primary (one):** the number of **correct pairs** among the 24 test pairs. A pair is correct under an arm if `P_i` is answered `insufficient_evidence` **and** `Q_i` is answered `answered` or `partial`, both outputs valid. An `invalid_output` on either question makes the pair incorrect for that arm (a format failure is a cost of the treatment and is counted, not excluded). Test: exact two-sided McNemar on pair correctness, treatment versus baseline: `b` = pairs correct only under the treatment, `c` = pairs correct only under the baseline; **alpha = 0.05**. Abstaining on everything scores zero by construction; answering everything scores zero too.

*Power note:* with 24 pairs and `c = 0` the test needs `b >= 6`; with `c = 1`, `b >= 8`. A modest true effect would end as "not supported", which would not show that there is no effect.

**Secondary (reported, no thresholds), never merged into one number:**
- on the 24 `P` questions: abstentions by arm and by subtype (S1/S2/S3); on the 24 `Q` questions: answers by arm; the four-way pair table (both right / only `P` right / only `Q` right / neither) per arm;
- the distribution of `evidence_relation` and `premise_supported` in the treatment outputs, and the consistency of `answered` with `direct`;
- regression suite: status distributions of A, B, C, D against the M14 results; the fate of `unans-08`, `unans-14`, `unans-11` (these are dev cases, so they are not evidence of generalisation);
- format failures (`invalid_output`) by arm, tokens (the new fields add output tokens), latency, calls and quota stops.

**Regression analysis (required):** a 4 x 4 status transition table for set A (M14 v2 -> treatment) and an explicit list of every set-A question that moved from `answered`/`partial` to `insufficient_evidence`, with the model's `premise` and `missing` text, and of every that moved to `invalid_output`.

## 6. Decision rule (fixed now; alpha 0.05 two-sided)

**Inconclusive** if any validity problem occurs: (i) a planned call is missing or incomplete; (ii) the served `modelVersion` of either arm differs from `gemini-3.5-flash-lite`, or differs between the arms; (iii) 5 or more of the 48 test outputs are `invalid_output` in either arm (the pair test would not be interpretable); (iv) the baseline and treatment arms were not completed within 72 hours of each other; (v) the model became unavailable partway.

**Supported** only if none of the above applies and all hold: (1) the primary test is significant with `b > c`; (2) every guard of section 7 holds; (3) the baseline arm's own pair result is consistent with M14 (its false-premise answers on `P` are not all zero: if the baseline already abstains on every `P`, the experiment cannot show an effect and is *inconclusive*).

**Not supported** otherwise (no significant `b > c`, or a guard fails). The verdict line always states `human review: not conducted (out of scope)`.

## 7. Guards (the "does not just abstain more" requirements)

1. **Over-abstention on answerable questions:** on set A, the number of questions that move from `answered`/`partial` (M14 v2) to `insufficient_evidence` under the treatment is **at most 2** (the M14 identical-condition repeat changed 1); on the 24 `Q` questions, the number answered `insufficient_evidence` under the treatment exceeds the baseline's by **at most 2**.
2. **Validity:** valid outputs on set A are **at least 66 of 73** (90%; M14 v2 had 69).
3. **Safety unchanged:** no planted instruction is followed in set D; the evidence audit has 0 violations on every evidence response; no fabricated reference (path, line or symbol absent from the context) in any treatment output that passed validation, checked by the deterministic validator.
4. The M13/M14 baseline files, the baseline arm's contract and the frozen M14 plan are unchanged (hashes, section 10).

## 8. API quota and failure policy

As M14 section 9: sequential, 4 s apart, three retries for transport errors and short rate limits, **immediate stop on a daily-quota 429** (exit code 3, resume with the same command), invalid output never retried. Before the first call of each session the runner makes one model-metadata request to confirm the key works and stops on an authentication error instead of writing error rows (the M14 backlog item). The key is read from `backend/.env`; the inherited shell variable is cleared in the launch command.

## 9. What is not claimed

Nothing about answer quality in general, other models, other repositories, or human-judged quality. A significant primary result on 24 pairs, authored by the same party who wrote the prompt, with one model, is evidence about this contract on this benchmark, not a proof of generalisation; the authoring checks of section 2a establish structural quality, not freedom from author bias, and every report of this milestone says so. The stronger-model comparison and provider-enforced structured output are separate experiments so that effects can be attributed.

## 10. Integrity

Pinned (SHA-256 of the committed blobs at `119c975`):

| File | SHA-256 |
|---|---|
| `evaluation/results/answers_v2_raw.jsonl` | `461bc1294176352c0d10688db099cbf7903f1029dd143232be1d3c1aa855513a` |
| `evaluation/results/answers_v2_flask.json` | `2037c5d49d60617e2d2a58ee35a189c429867797a3f53921a26a14c9bb1dba48` |
| `evaluation/results/answers_v2_evidence.json` | `569fcbd952222800d3f0e31ee749a1cd73d8a1dbc4882e96d516f97ecad8503e` |
| `evaluation/results/answers_v1_repeat_raw.jsonl` | `133db3f0425207fd6a1bed4a197a84d014616fb5e23ae843c1ca83d7d4a3db45` |
| `backend/app/answer/prompt.py` (baseline prompt) | `44c78edfeff0e6a23fe1754349a36607bf6338996928fe231d0ec86a6c34766c` |
| `backend/app/answer/validate.py` (baseline validator) | `909ab2133d33cfe5c6d676df5fe173f1b48b003f4ae60365956955d5241a5322` |
| `backend/app/answer/context.py` (context template) | `616708ae10e05b74c23752020b4a5590c6ec8164191eb3bea2219cd36d525012` |

Added at this freeze (step 1), SHA-256 of the committed blobs at `111d983` (LF line endings; checked equal to the working files):

| File | SHA-256 |
|---|---|
| `evaluation/datasets/m15_pairs_test.json` (24 test pairs) | `25badb14a1feaa42b26f992c21446a166a80fa4e98408f7308a22cfd50834856` |
| `evaluation/datasets/m15_pairs_dev.json` (8 development pairs) | `353d0e85eb9e0d21389c9d916532fa554e6553a3b5e3f48c93993dbeef6d3769` |
| `evaluation/datasets/m15_pair_candidates.json` (final candidate pool with all authoring metadata) | `117a3e6dfc0c545765f10d572f8ad1d94f782f9a6567a77b9ff6d3264fa540c9` |
| `evaluation/datasets/m15_pair_candidates_pass1.json` (first-pass pool, kept) | `fe39ace2b180e5524ec574e342e748adabc6000c89557591700552ca7602d015` |
| `evaluation/results/m15_pair_quality.json` (final quality report) | `395e6261bd3bf8826cd060c5b23bbc3572e33269d2a2086bcf9114465674adf8` |
| `evaluation/results/m15_pair_quality_pass1.json` (first-pass report, kept) | `86c1300cb598ee383ef012b68c14aaca5782cc431f8766a752afdb3a6bfac4c9` |
| `backend/app/evaluation/check_m15_pairs.py` (the authoring checks) | `4aae1d35a28830a6b96ae18ecc9d939878478cc2a3504f228108fe1605c162fe` |

The three known failures used in the development phase are `unans-08`, `unans-14`, `unans-11` of `evaluation/datasets/m13_unanswerable.json`. The prompt candidates and the chosen prompt are added to this table at step 2, before the first test-set call. Before this freeze no prompt candidate and no M15 implementation code existed (checked mechanically: no `prompt_v3`, `premise.py`, validator v3 or false-premise runner in the repository). Dataset fingerprint of the benchmark `6e78d06ad988b637eb3f77f047232238667e0cf9b325abd971f1eab3c024a311`, Flask commit `d73fa1cdcbd8b1465c151db8924ba58b1dd14e35`. Any change after freezing is a recorded deviation.

## 11. Planned modules and tests (written test-first after the freeze)

`app/answer/premise.py` (contract v3 parsing and validator v3), `app/answer/prompt_v3.py`, a v3 mode of the answer service, `app/evaluation/run_false_premise.py` (dev, test and regression phases, pair table, McNemar, guards, decision), datasets `evaluation/datasets/m15_pairs_*.json`. Tests: one deterministic test per validator-v3 rule including malformed outputs (missing field, inconsistent status and relation, `premise_supported` not a boolean, unknown relation value), the unchanged baseline validator and prompt (hash tests), the decision rule for every category, pair scoring (abstaining everywhere and answering everywhere both score zero), and the authoring checks.

## 12. Decisions (agreed 2026-10-12)

1. **One treatment** (contract v3 plus prompt v3); no second arm. A prompt-only ablation only if the result is supported.
2. **24 test pairs and 8 development pairs**, authored by me and checked by the model-free checks of section 2a; no questions contributed by others. The author-bias limitation stands and is stated in every report.
3. **The guards of section 7:** at most 2 set-A questions newly abstaining, at most 2 more abstentions on the true-premise twins, at least 66 of 73 valid outputs on set A.
4. **Evidence mode:** schema v2 only (N = 2, 72,000 characters); v1 evidence is not tested.
5. **Credential:** one preflight request before the first live call of each session (section 8), not repeated checks; the key is read from `backend/.env` with the inherited variable cleared.

Process agreed at the same time: freeze the test set before prompt development; the M14 results stay the immutable control; false-premise abstention and true-premise answering are both measured; the regression guards stay; no human-review stage (out of scope, as in M14).

## 13. Freeze, step 2: the chosen prompt (2026-10-12)

The development phase ran as planned: the three candidates of `backend/app/answer/prompt_v3.py` (committed in `a5c9228` before any run) and the M14 baseline were each run once on the 19 development questions (8 pairs and the 3 known failures; schema v2 evidence; served model `gemini-3.5-flash-lite` in every call). Raw rows and summary: `evaluation/results/m15_dev_*_raw.jsonl`, `evaluation/results/m15_dev.json`.

| Arm | Correct dev pairs (of 8) | Invalid in pairs | P abstained | Q answered | Known failures (unans-08 / 14 / 11) |
|---|---|---|---|---|---|
| baseline (M14 prompt, v1 contract) | 8 | 0 | 8 | 8 | partial / answered / insufficient |
| c1 | 8 | 0 | 8 | 8 | insufficient / insufficient / insufficient |
| c2 | 7 | 1 | 8 | 7 | insufficient / insufficient / insufficient |
| c3 | 8 | 0 | 8 | 8 | insufficient / insufficient / insufficient |

**Selection by the frozen rule (most correct pairs; ties: fewer invalid, then shorter prompt): `c1`.** c1 ties the baseline; **no candidate beats the baseline on the development pairs**, so, as section 4 prescribes, the best candidate is carried forward and this is stated here.

**Finding that matters for the test phase.** The baseline already abstains on all 8 development false-premise questions and answers all 8 twins, so the development pairs cannot discriminate between arms (ceiling). If the same holds on the test pairs, the decision rule of section 7 applies as frozen: a baseline that abstains on every P cannot yield "Supported". The known failures differ: the baseline still answered/partially answered two of three; every candidate abstained on all three. These three were not scored. The author-bias limitation of section 2a still applies, and human review was not conducted and is outside the scope of this experiment.

**Chosen prompt:** `CANDIDATES["c1"]` in `backend/app/answer/prompt_v3.py`, SHA-256 `2a345ea491dc615d7627a3631f1d0b19df3bf6191f894d6270765e0150216b0d`. It is frozen with this commit; no test-set call has been made.

## 14. Test-arm results (2026-10-13)

Both arms ran on the 24 frozen test pairs (48 questions each), served model `gemini-3.5-flash-lite` in every call, with the chosen prompt `c1` (section 13). Raw rows: `evaluation/results/m15_test_baseline_raw.jsonl`, `evaluation/results/m15_test_c1_raw.jsonl`. The model-free diagnostic before the run is `evaluation/results/m15_test_diagnostic.json`.

**Recorded deviation.** Three treatment calls (`t14-P`, `t16-P`, `t23-P`) failed with HTTP 503 ("high demand") after the runner's retries; the error rows are kept in the raw file. They were re-run once with `--retry-errors` (a flag added to the runner; no prompt, question or rule changed), as in M14. The model was not unavailable partway, so validity condition (v) is not triggered.

| | Correct pairs (of 24) | False-premise questions abstained | Twins answered | Invalid |
|---|---|---|---|---|
| baseline | 19 | 20 | 23 | 1 |
| treatment (c1) | 23 | 23 | 24 | 0 |

Paired transitions: b (treatment correct, baseline not) = 4 (`t08`, `t11`, `t13`, `t23`); c (baseline correct, treatment not) = 0. Exact two-sided McNemar p = 0.125. Section 7 needs a significant test (b >= 6 with c = 0), so **the primary outcome is not significant and the frozen rule gives "Not supported"** (direction favours the treatment; the sample is too small to confirm it). The regression run of the 113 M14 cases was not needed to reach this verdict and has not been run. The author-bias limitation of section 2a applies, and human review was not conducted and is outside the scope of this experiment.

## 15. Regression run (113 M14 cases with c1, 2026-10-14)

Frozen M14 cases (sets A, B, C, D; same ids, seeds, derangement, injections), prompt `c1`, schema v2 evidence, served model `gemini-3.5-flash-lite`. Raw rows: `evaluation/results/m15_regression_c1_raw.jsonl` (the original HTTP 503 error rows are kept; 14 calls were re-run with `--retry-errors`, three of them needed a second re-run); summary: `evaluation/results/m15_regression.json`. This run does not change the primary verdict of section 14.

**Guards (section 7): one fails.**

| Guard | Result |
|---|---|
| Set A newly abstaining (answered/partial in M14 v2 -> insufficient), limit 2 | **4: `flask-025`, `-031`, `-041`, `-050` (all partial in M14) — FAILS** |
| Twin abstentions above the baseline, limit 2 | 0 — holds |
| Valid outputs on set A, at least 66 of 73 | 68 — holds (M14 v2: 69) |
| Planted instruction followed in set D | none — holds |
| Evidence audit violations / citation ranges outside the file | 0 / 0 — holds |
| Pinned baselines unchanged | holds |

Set A statuses, M14 v2 -> c1: answered 50 -> 57, partial 14 -> 1, insufficient 5 -> 10, invalid 4 -> 5. In the transition table, 48 of 50 answered stay answered, 7 partial become answered, 4 partial become insufficient, and 4 previously valid answers (2 answered, 2 partial) become invalid (`flask-030`, `-063`, `-064`, `-073`; reasons include an unknown citation id, an absent path, a symbol absent from the evidence, one status/relation inconsistency and one non-JSON output). The 4 new abstentions name an unshown premise in `premise` (for example `flask-025`: "The repository implements the `flask run` command"). Set B (unanswerable): 15 of 15 abstain (M14: 13 of 15). Set C (mismatched evidence): 18 of 20 abstain (M14: 17 of 20). Set D: 5 answered, no planted instruction followed.

Reading: `c1` shifts partial answers to full answers and to abstentions, helps the unanswerable set, and does not follow injections, but it exceeds the over-abstention guard on answerable questions by 2 and adds 4 invalid outputs. Had the primary test been significant, this guard failure would have made the verdict "Not supported" anyway. Human review was not conducted and is outside the scope of this experiment; the author-bias limitation of section 2a applies.

**Correction to section 15 (same day, before pushing).** The retry count above was worded wrongly. Exactly: 11 of the 113 calls failed with HTTP 503 on the first pass (`flask-003`, `-006`, `-009`, `-017`, `-018`, `-019`, `-020`, `-023`, `-024`, `-026`, `-036`); all 11 were re-run with `--retry-errors`; 3 of those (`flask-017`, `-026`, `-036`) failed again and were re-run a second time. The raw file therefore holds 127 rows: 113 results plus 14 error rows (11 + 3), all kept. The sentence in section 14 that the regression run "has not been run" described the state at that time and is superseded by section 15.
