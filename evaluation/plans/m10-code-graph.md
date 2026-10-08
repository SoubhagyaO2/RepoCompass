# Experiment plan: Milestone 10, the code graph

**Status: frozen before any graph code or graph result existed.**
Written 2026-10-08 on top of commit `ea195da` (Milestone 9). Two parts, kept separate on purpose:
**M10A** builds and validates the graph without using the benchmark; **M10B** tests whether graph
expansion improves retrieval. Deviations, if any, are recorded as deviations in the results.

## Why

The hybrid retriever's remaining failures are structural. Of its 9 Top-5 misses, 5-6 are
dependency questions ("which files import X"), where the answer is a relationship, not text
similar to the question. Reranking (Milestone 9) did not touch them. A graph of imports, calls,
definitions and inheritance is a different kind of information.

## A circularity that shapes how results are read

The dependency questions' ground truth was derived by parsing imports with the project's
Tree-sitter parser and resolving relative imports. The graph's import edges are built the same
way, so a graph-based retriever will largely reproduce those answers *by construction*. A gain on
dependency questions shows that the mechanism works; it is **not independent evidence that the
graph generalises**. The caller questions are only partly independent (their truth came from a
text search of identifiers; graph call edges come from AST analysis). Therefore:

- the graph is validated against independent sources (below), not against the benchmark;
- results are reported overall **and excluding the dependency category**;
- the headline claim about generalisation rests on the non-dependency questions.

## M10A: graph construction and validation (the benchmark is not used)

**Representation:** a NetworkX `MultiDiGraph` over every Python file in the repository (tests and
examples included, like the indexes).

| Nodes | Edges (each keeps its evidence: file, line, rule) |
|---|---|
| `file` (id = repo-relative path) | `imports`: file -> file (internal imports only, `TYPE_CHECKING` imports included) |
| `class`, `function`, `method` (id = `<path>::<qualified name>`; overloads of one name share a node) | `defines`: file -> top-level symbol, class -> member |
| | `inherits`: class -> direct base class |
| | `calls`: symbol (or file, for module-level code) -> symbol |

**Call resolution rules (fixed now):** only call expressions and decorator applications are edges.
Names are resolved statically, never by guessing among alternatives:

1. `foo()`: a module-level function/class `foo` in the same file; else a name bound by an import in
   the file (following re-exports up to 5 levels); else unresolved.
2. `mod.foo()` where `mod` is an imported module: `foo` defined in that module.
3. `self.foo()` / `cls.foo()`: a method `foo` on the enclosing class, else on its base classes
   (depth-first, left to right); `super().foo()`: base classes only.
4. `obj.foo()` for any other receiver: the method `foo` **only if exactly one class in the
   repository defines a method of that name**; if several do, the call is skipped and counted as
   ambiguous; if none, unresolved.

**Deliberately not modelled** (recorded as known limitations): dynamic dispatch to subclass
overrides, methods passed as values (callbacks), `getattr`/string-based access, and calls whose
method name is shared by several classes.

**Validation (fixed before the builder exists):**

- **V1, independent parser.** The graph's import edges inside `src/flask` must equal the same
  relation computed with Python's own `ast` module (set equality).
- **V2, hand-verified facts.** [`evaluation/graph_facts/flask.json`](../graph_facts/flask.json)
  was written from reading the source *before* any graph code: expected-present edges,
  expected-absent edges (a precision check) and two documented known gaps. Every present edge must
  exist, every absent and known-gap edge must not. A failure is investigated as a bug; any rule
  change it causes is recorded as a deviation.
- **V3, statistics:** node and edge counts per type, and how many calls were resolved by each rule,
  skipped as ambiguous, or unresolved.

The benchmark's answers are not used to build or fix the graph. After the graph is frozen, its
precision/recall against the benchmark's caller and dependency answers is reported once as
descriptive information, with no rule changes afterwards.

## M10B: graph-enhanced retrieval

| Name | First stage (unchanged from Milestone 8) | Then |
|---|---|---|
| H1 / H2 (references, already saved) | fixed / AST chunks, BM25 + embeddings, RRF k = 60 | nothing |
| **G1** | H1 | graph expansion |
| **G2** | H2 | graph expansion |

The first stage must reproduce the saved Milestone 8 rankings exactly (integrity check).

**Expansion, fixed now and not tuned:**

| Decision | Value |
|---|---|
| Anchors | the top **3** unique files of the hybrid ranking |
| Neighbours of an anchor | files connected to it by `imports` (either direction) or by `calls` (either direction, file-level: some symbol in one file calls some symbol in the other); all four relations, equal weight |
| Vote | every (anchor, neighbour) pair gives the neighbour a vote of `1 / (60 + anchor rank)`, once per pair however many relations connect them |
| Fused score | the file's hybrid (RRF) score, or 0 if the hybrid did not return it, plus its graph votes |
| Final ranking | by fused score; ties by hybrid rank, then path. A neighbour the hybrid did not return enters with an empty representative chunk |
| Graph scope | the whole repository including tests, with no filtering by file category |

**Preserved evidence:** for every question, every expansion as
`{source, relationship, target, evidence:{file, line, rule}, anchor_rank, vote}` and each
neighbour's rank before and after expansion.

**Evaluation:** the locked scoring, unchanged (primary and primary + supporting, by category and
difficulty); latency reported as first stage + expansion, with the graph build time separate.

**Pre-registered comparisons (B vs A):** 1. G1 vs H1; 2. G2 vs H2; 3. G2 vs G1. Primary outcome:
change in MRR (primary scope) with its 95% paired-bootstrap interval; secondary Top-1, Top-5 and
the sign test (Bonferroni threshold for three comparisons p < 0.0167). An improvement is claimed
only if the MRR interval excludes zero. Every comparison is also reported **excluding the
dependency category** (the 61 other questions).

**Pre-registered descriptive statistics:** per category, questions whose first primary file moved
up / down / stayed; primary files **brought in** (not in the hybrid's top 20, in the top 5 after
expansion); primary files **pushed out** (top 5 before, not after); the share of expansion
neighbours that are test or example files.

## Not changed

The dataset (frozen), scoring, chunking, RRF `k`, BM25 and embedding settings, and the hybrid
first stage. The number of anchors, the vote rule and the relation set are not tuned on the 73
questions.

## Known risks and limits

- The circularity above: dependency gains are mechanical.
- Test and example files import and call library code, so they are neighbours of nearly every
  library file and may crowd the ranking. They are deliberately **not** filtered out, because
  that choice would be informed by the benchmark's scope; a category filter is a backlog item
  for a dev set.
- Undirected expansion ignores question intent: "what calls X" and "what does X import" are
  treated alike. Intent-aware expansion is a backlog item.
- Name-based call resolution is imprecise by construction (rule 4) and misses callbacks; recall
  against the benchmark's caller answers will be below 100% (the text search that produced them
  also counts non-call references).
- One repository, 73 questions.

## Integrity

- Dataset fingerprint `6e78d06ad988b637eb3f77f047232238667e0cf9b325abd971f1eab3c024a311`;
  pinned Flask commit `d73fa1cdcbd8b1465c151db8924ba58b1dd14e35`.
- Planned entry points: `python -m app.graph.validate` (M10A) and
  `python -m app.evaluation.run_graph` (M10B).

## Addendum (2026-10-08, before any M10B result): deviation 1, rule 4 precision fix

**What happened.** After the builder passed V1 (import edges identical to Python's `ast`) and V2
(51 of 51 facts), I audited the weakest rule, `unique-name`, which the facts barely exercise, by
sampling 30 of its 76 edges inside `src/flask` and reading the code line behind each. Only 16
were correct (53%; 37% if the typing-only `ProxyMixin._get_current_object` stub is counted as
wrong). Of the 14 wrong edges, 10 were builtin container methods (`kwargs.setdefault(...)` linked
to the repo's only `setdefault`), 2 were calls into external libraries (`click.command`,
werkzeug's `EnvironBuilder.from_environ`), 1 was self-recursion by name, and 1 (`ep.load()`)
cannot be fixed without type inference. These false edges would have made files such as `ctx.py`
neighbours of nearly every file and polluted graph expansion for reasons unrelated to the idea
being tested.

**The change** (no benchmark data was used; no M10B result existed). The rule's principle, never
guess among alternatives, now also covers:

1. a receiver bound by an import that leaves the repository is external, never name-matched;
2. a receiver that is a statically known class but lacks the method is unresolved, never
   name-matched;
3. name matching is used only for receivers of unknown type, and not for method names that exist
   on builtin types (`dict`, `list`, `set`, `str`, `bytes`, ...) nor when it would link a method
   to itself.

**Re-audit on fresh samples** (single reviewer, judged by reading the call line;
`evaluation/results/graph_audit_flask.json`): 30 of the 48 remaining `unique-name` edges, 28
correct (93%; 23 or 77% if the typing-only stub counts as wrong), the 2 errors being
`loader.get_source(...)` on a Jinja loader; and 30 of the 235 edges from all other rules, 30
correct. V1 is still identical and V2 still 51 of 51. `calls` edges went from 1,223 to 1,127.
