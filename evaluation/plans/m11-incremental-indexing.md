# Experiment plan: Milestone 11, incremental indexing

**Status: frozen before any incremental-indexing code or result existed.**
Written 2026-10-08 on top of commit `87f7b25` (Milestone 10). This is an engineering experiment,
not a retrieval-quality one: the claim under test is that updating an index after a change costs
far less than rebuilding it, **without changing what the index contains**.

## Question

If one file (or five) of an indexed repository changes, can RepoCompass update only what actually
changed, and is the result *identical* to rebuilding everything from scratch?

## The system: a live index

One persistent index per repository holding, for **both** chunkings used so far (fixed windows and
AST chunks): the chunk records, an embedding collection, a BM25 index, and the code graph.

| Part | Update rule |
|---|---|
| Change detection | SHA-256 of each Python file's bytes against the stored manifest: **added**, **modified**, **deleted**, **unchanged**. Never timestamps; a file rewritten with identical bytes is unchanged. |
| Per-file record | parse result, AST chunks, fixed chunks and their content hashes, stored per file; only changed files are re-parsed and re-chunked. |
| Embeddings | one vector-store point per chunk (stable id derived from the chunk id). Vectors are **reused by chunk-text hash** from any existing chunk (same file, another file, or a previous version of the file), so only chunks with genuinely new text are embedded. Stale points are removed. |
| BM25 | documents are added and removed in place; collection statistics (document count, average length, document frequencies) are recomputed from counters at query time, with scoring formula, tokenizer and tie-break order identical to the static index used in Milestones 7-10. |
| Code graph | changed files are re-parsed (the others come from the stored parse results) and the graph is then re-assembled from the stored parses. Name resolution is global (adding a method named `x` anywhere can make `obj.x()` calls elsewhere ambiguous, renaming a function removes edges from files that did not change), so resolution is rerun for the whole repository; it costs milliseconds, parsing is the cost, and parsing is incremental. |
| Ordering | new points are written first, then the per-file records, then stale points are deleted, and the manifest is written **last**, so an interrupted update is retried safely (writes are idempotent) and never loses data. |

If the configuration changes (chunk size, overlap, embedding model) the manifest records it and a
full rebuild is required.

## Correctness criterion (must hold, or the milestone fails)

After every scenario the incrementally updated index must equal an index built from scratch on the
same files:

1. the set of chunk ids and chunk-text hashes, for both chunkings;
2. the vector-store point ids and payloads, for both chunkings;
3. BM25: the top-50 chunk ids **and scores** for each of the 73 benchmark questions used as
   probe queries (exact equality), for both chunkings;
4. the code graph: node set, and edge set including each edge's evidence lines and rules;
5. vectors: every newly embedded vector, plus a random sample of 30 reused vectors per
   chunking, equals a freshly computed embedding (cosine similarity >= 0.999999);
6. at the start and again after all scenarios (the tree restored to the pinned commit), the live
   index reproduces the saved Milestone 8 hybrid rankings for all 73 questions, for both
   chunkings, exactly.

The from-scratch reference for criteria 1-4 uses a cheap deterministic stand-in embedder (those
criteria do not depend on the embedding model); criterion 5 uses the real model.

## Scenarios (the working tree is a copy of the Flask checkout at the pinned commit)

| Id | Change |
|---|---|
| `noop` | nothing changed |
| `touch` | `src/flask/app.py` rewritten with identical bytes (new modification time) |
| `edit_end` | one comment line appended at the end of `src/flask/app.py` |
| `edit_start` | one comment line inserted at the very top of `src/flask/app.py` (shifts every line and every fixed window) |
| `edit_function` | one comment line appended inside the last line of `Flask.dispatch_request` |
| `edit_5_files` | one comment line appended at the end of each of `ctx.py`, `helpers.py`, `sessions.py`, `cli.py`, `config.py` |
| `add_file` | a new module `src/flask/_extra.py` with a class and two functions |
| `delete_file` | `src/flask/signals.py` deleted (five modules import it) |
| `rename_file` | `src/flask/logging.py` moved to `src/flask/logging_renamed.py`, bytes unchanged |
| `graph_rename_symbol` | `def ensure_sync(` renamed in `src/flask/app.py`: edges from unchanged files that call it must disappear |
| `graph_ambiguity` | a new module defines a second class with a method `make_shell_context`: the edge from the unchanged `cli.py` (resolved because the name was unique) must disappear |

`edit_function`, `graph_rename_symbol` and `graph_ambiguity` are the cross-file tests: the graph
must change in files whose source did not.

## Measurement protocol

- Embedding model loaded and warmed up before timing; one process; this CPU.
- **Full build:** the cold build of the whole live index (both chunkings, BM25, graph), once, with a
  per-component breakdown. It is the reference for every speed-up.
- **Each scenario:** apply the change, run the update (timed), verify (not timed), restore the
  tree, update back (not timed). Three repetitions; report the median and the range.
- Reported per scenario: files added/modified/deleted/unchanged, chunks embedded vs reused per
  chunking, points written and deleted, graph edges added and removed, and the time split into
  scan, parse and chunk, embedding, vector store, BM25, graph and persistence; plus the speed-up
  against the full build.

## What is not claimed

Nothing about retrieval quality (an identical index gives identical benchmark results, which
criterion 6 checks), nothing about repositories larger than Flask, and no speed-up threshold is
set in advance: the numbers are reported as measured. Timing includes hashing every file on every
update. Model loading is excluded, as in all earlier latency numbers.

## Known risks and limits

- Fixed windows are not edit-stable (an insertion early in a file shifts every later window), so
  `edit_start` is expected to re-embed most of a file's fixed chunks; AST chunks are expected to be
  stable. This is a finding to measure, not an assumption.
- Graph re-assembly is global; for repositories much larger than Flask it may need to become a
  targeted re-resolution (backlog).
- Only Python files are indexed, as everywhere else.

## Integrity

Pinned Flask commit `d73fa1cdcbd8b1465c151db8924ba58b1dd14e35`; dataset fingerprint
`6e78d06ad988b637eb3f77f047232238667e0cf9b325abd971f1eab3c024a311`. Planned entry point:
`python -m app.evaluation.run_incremental`.
