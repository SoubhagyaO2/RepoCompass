# Experiment plan: Milestone 15, false-premise handling

**Status: DRAFT for review. Nothing is implemented and no question set is authored yet. To be frozen (and committed) in two steps before any live test run: (1) this protocol, (2) the chosen prompt, after the development phase.**
Written 2026-10-12 on top of commit `119c975` (Milestone 14, pushed).

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

Nothing about answer quality in general, other models, other repositories, or human-judged quality. A significant primary result on 24 pairs, authored by the same party who wrote the prompt, with one model, is evidence about this contract on this benchmark, not a proof of generalisation. The stronger-model comparison and provider-enforced structured output are separate experiments so that effects can be attributed.

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

The M13 baseline hashes of the M14 plan also still apply. The question files, the pair records and the chosen prompt are added to this table at the two freezes. Dataset fingerprint of the benchmark `6e78d06ad988b637eb3f77f047232238667e0cf9b325abd971f1eab3c024a311`, Flask commit `d73fa1cdcbd8b1465c151db8924ba58b1dd14e35`. Any change after freezing is a recorded deviation.

## 11. Planned modules and tests (written test-first after the freeze)

`app/answer/premise.py` (contract v3 parsing and validator v3), `app/answer/prompt_v3.py`, a v3 mode of the answer service, `app/evaluation/run_false_premise.py` (dev, test and regression phases, pair table, McNemar, guards, decision), datasets `evaluation/datasets/m15_pairs_*.json`. Tests: one deterministic test per validator-v3 rule including malformed outputs (missing field, inconsistent status and relation, `premise_supported` not a boolean, unknown relation value), the unchanged baseline validator and prompt (hash tests), the decision rule for every category, pair scoring (abstaining everywhere and answering everywhere both score zero), and the authoring checks.

## 12. Decisions I need from you before freezing

1. **One treatment** (contract v3 plus prompt v3) as drafted, or two arms (prompt only versus prompt plus contract) to attribute the effect? Two arms add about 100 calls and a harder-to-power comparison; I recommend the single treatment, and a prompt-only ablation only if the result is supported.
2. **Test size and authorship:** 24 pairs, written by me, verified by search and by the model-free context checks. Do you want to contribute or replace questions to reduce author bias?
3. **The guards:** at most 2 set-A questions newly abstaining, at most 2 more abstentions on `Q`, at least 66 of 73 valid. Agree?
4. **Evidence mode:** schema v2 (where the failure appeared). Agree, or also test v1 evidence? (v1 would double the test calls.)
5. **Credential:** the key in `backend/.env` was pasted into a chat. Please rotate it and update `.env` before any live call; the runner will check the key first.
