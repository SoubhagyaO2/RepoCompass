# Experiment plan: Milestone 12, the Evidence API

**Status: FROZEN 2026-10-08 before any implementation code. Approved with the four decisions in section 12 and the three contract rules in section 13. Changes after this point only as recorded deviations.**
Written 2026-10-08 on top of commit `04bda56` (Milestone 11).

This is an **interface and grounding milestone, not a retrieval-quality experiment.** It adds no
ranking logic, tunes nothing, and reports no Top-K/MRR. The claim under test is narrower:

> For any query, RepoCompass can return a deterministic, auditable list of evidence items, each
> traceable to exact lines of a specific file version, with the scores and graph facts that
> produced it, and **returning evidence never changes which files are retrieved or in what order**.

The later LLM layer (M13) may only cite what this layer returns. No prose is generated here.

## 1. Where it sits

```
LiveIndex (M11) -> HybridRetriever (M8, AST chunks, RRF k=60) -> EvidenceBuilder -> Evidence API
                                                    graph (M10) annotates, never re-ranks
```

- Default retrieval is the **AST-chunk hybrid** (best MRR in M8). The fixed-window view is not
  exposed by the API (its chunks have no symbols).
- The unit of evidence is **one item per retrieved file**, the same unit the benchmark scores and
  RRF fuses. Items are ordered exactly as `HybridRetriever` orders files.
- `HybridRetriever.search` is refactored to build on a new `search_detailed` that also returns each
  component's rank, raw score and best chunk per file; `search` keeps its exact behaviour (proved
  by test 1 below). No retrieval logic is duplicated in the API, CLI or MCP.
- The graph (M10B showed always-on expansion hurts) is used **only to annotate**, never to add,
  remove or reorder items.

## 2. Evidence schema (version `1`)

Response:

```json
{
  "schema_version": 1,
  "query": "where is the session cookie saved",
  "index": {"repository": "flask", "revision": "<commit or null>", "view": "ast",
            "config_sha256": "...", "manifest_sha256": "..."},
  "retrieval": {"method": "hybrid-rrf", "rrf_k": 60, "depth": 50, "components": ["bm25","embedding"]},
  "evidence": [ ...items... ]
}
```

Item (all fields always present; `null` when not applicable, never omitted):

```json
{
  "rank": 1,
  "file": "src/flask/sessions.py",
  "file_sha256": "…",
  "category": "source",
  "line_start": 330, "line_end": 372,
  "symbol": {"qualified_name": "SecureCookieSessionInterface.save_session",
             "name": "save_session", "kind": "method", "node_id": "src/flask/sessions.py::SecureCookieSessionInterface.save_session",
             "line_start": 330, "line_end": 372},
  "symbol_status": "exact",
  "scores": {"rrf": 0.0325,
             "bm25": {"rank": 1, "score": 8.42, "line_start": 330, "line_end": 372},
             "embedding": {"rank": 3, "score": 0.71, "line_start": 301, "line_end": 329}},
  "graph": {"degree": {"calls": 4, "called_by": 6, "imports": 0, "imported_by": 0},
            "relationships": [ {"relationship": "called_by", "other": "src/flask/app.py::Flask.process_response",
                                "other_file": "src/flask/app.py", "line": 1250, "rule": "self"} ],
            "truncated": false,
            "links_to_other_results": [ {"relationship": "called_by", "other_rank": 2, "line": 1250, "rule": "self"} ]},
  "text": null
}
```

Field rules:
- `rank`: 1-based position in the final ordering.
- `file`: repo-relative POSIX path. `file_sha256`: SHA-256 of the file bytes the index was built
  from (the M11 manifest value), so a consumer can verify the lines refer to the version indexed.
- `scores.rrf`: the fused score. `scores.bm25` / `scores.embedding`: that component's rank (among
  unique files), raw score, and the line range of **its** best chunk for the file; `null` if the
  file was not in that component's top-`depth`. Raw scores are shown but are **not comparable
  across components** (documented in the schema).
- `line_start`/`line_end`/`symbol`: those of the **displayed chunk**, chosen by the existing
  rule (the chunk from the component that ranks the file best; BM25 wins a tie).
- `text`: `null` unless `include_text=true` (see 6).
- No timestamps, durations, hostnames or random ids in an evidence body (determinism, see 5).
  Latency goes in a separate `timing` block outside `evidence`, excluded from determinism tests.

## 3. Line-number semantics

- **1-based, inclusive on both ends**, counted in lines of the file *as stored* (`\n`, `\r\n` and
  `\r` each end a line; a trailing newline does not create an extra line), identical to the chunker.
- A chunk is whole lines, so `text` (when requested) equals `lines[line_start..line_end]` joined
  with their original line endings. Tested byte-for-byte against the file on disk.
- Line numbers refer to the file version `file_sha256`; if the file changed after indexing, the
  API reports the indexed version (M11 keeps the index current; staleness is surfaced as
  `index.manifest_sha256`, not silently hidden).
- `line_start <= line_end`, both within `[1, number of lines]`. Violation = bug, tested.

## 4. Chunk -> symbol mapping

Source of truth is the **stored parse** of the file at `file_sha256` (the same parse that built the
chunk and the graph), never a re-parse and never a guess from names.

1. The displayed AST chunk is looked up by `(file, line_start)`; the symbol is the parse symbol
   with the same `start_line` and a kind in {function, method, class}. Identification is by
   `(file, start_line)`, not by name (overloads and conditional definitions repeat names).
   -> `symbol_status = "exact"`.
2. Class-header chunk: the symbol is the **class**, `symbol.line_start/line_end` give the class's
   full extent while `line_start/line_end` (top level) give only the header chunk. `exact`.
3. Module chunk (`<module>`: code outside any function or class): `symbol = null`,
   `symbol_status = "module_level"`.
4. Lines outside any chunk (known limitation: code between methods in a class body) cannot be
   returned as an item, because items are chunks. Documented, not worked around.
5. Non-Python / unparsable file or a parse with errors where no symbol covers the chunk:
   `symbol = null`, `symbol_status = "unparsed"`.
6. Docs/test files are Python files here and follow rules 1-3; `category` says `tests`/`docs`.

`symbol_status` is one of `exact | module_level | unparsed`. There is no "guessed" status: if the
mapping is not certain it is `null`, and a consumer (the LLM layer) must treat `null` as "cite the
file and lines only". Nested functions: the innermost symbol that *is* the chunk is used (each
nested def is its own symbol in the parse only if the parser emitted it; the chunk's own symbol is
reported, not its ancestors, whose names appear in `qualified_name`).
`node_id` is the graph node id (`path::qualified_name`); overloaded names share one graph node, so
`node_id` may be shared by two items' symbols, while `(file, line_start)` stays unique.

## 5. Graph evidence

For an item whose symbol is `exact`, the graph node is `node_id`; for `module_level`/`unparsed`
items the node is the file. Representation (plain data, no free text):

- `degree`: counts of `calls`, `called_by`, `imports`, `imported_by` (file node) / `inherits`,
  `defines` where relevant, from the graph as built over the same index revision.
- `relationships`: edges incident to the node, each `{relationship, other (node id), other_file,
  line, rule}` where `line`/`rule` are the original edge evidence (the line of the call/import
  statement in the **source** file and the resolution rule: `self`, `import`, `unique-name`, ...).
  Edges whose rule is `unique-name` are kept but carry that rule so consumers can discount them
  (audited precision 93%; other rules 30/30).
- Ordering of `relationships`: by relationship in the fixed order `calls, called_by, inherits,
  imports, imported_by, defines`, then `other`, then `line`; at most **10 per item** (a constant
  fixed here, not tuned), with `truncated: true` and the full counts still in `degree`.
- `links_to_other_results`: the subset of edges whose other end is the symbol/file of another item
  in this same response (reported as that item's `rank`). This is the graph's contribution to
  "why are these results related", and it is exact (no ranking involved).
- A symbol with no edges has empty lists and zero degrees (not `null`). `graph` is `null` only if
  the index has no graph.

## 6. Raw chunk text

Off by default (`include_text=false`) to keep responses small and to make citations the unit of
exchange. `include_text=true` adds `text` (exactly the chunk lines). Never truncated silently: a
chunk is returned whole or not at all. The M13 layer will request text, but only through the
evidence items, so every sentence of an answer can be mapped back to `(file, lines)`.

## 7. Deterministic ordering and tie-breaking

- Items: descending RRF score; ties by the file's best rank in any component, then by path (the
  existing `reciprocal_rank_fusion` rule, unchanged).
- Within a component, BM25 ties break by `(file_order_key, seq)` (M11); embedding ties by the vector
  store's order, which the API does not claim to be stable across stores (tested as stable for
  this store, version pinned).
- `relationships` ordering as in 5. JSON keys are emitted in schema order, floats rounded to 6
  decimals **only at serialization** (internal ranking never uses rounded values).
- Same index + same query + same parameters -> byte-identical `evidence` array, across repeated
  calls and across processes. (The embedding model is deterministic on CPU in our runs; if not
  bit-stable it will be reported, not hidden.)

## 8. API surface

- `GET /v1/evidence?q=...&top_k=10&include_text=false` returning the response above; `top_k`
  limits items after ordering (default 10, max 50 = depth). Validation errors return HTTP 422;
  empty or whitespace-only `q` is a 422, not an empty result.
- `GET /v1/index/status`: repository, revision, file/chunk counts, config and manifest hashes.
- The index is loaded once at startup from the persisted live index (`LiveIndex.load`); a missing or
  inconsistent index is a startup error with a clear message, not a runtime surprise.
- The same `EvidenceBuilder` is called by a CLI (`python -m app.evidence "query"`) and, later, by
  MCP and the LLM layer. Three entry points, one implementation.
- Out of scope: authentication, rate limiting, multi-repository serving, streaming, the LLM.

## 9. Edge cases and their specified behaviour

| Case | Behaviour |
|---|---|
| Module-level chunk | `symbol=null`, `symbol_status="module_level"`, graph at file level |
| Class header chunk | symbol = the class; item lines = header only; symbol lines = class extent |
| Overloaded / repeated name in one file | identified by `(file, line_start)`; shares one graph node |
| Nested function | symbol is the chunk's own definition; ancestors in `qualified_name` |
| File with parse errors | `unparsed` if no parse symbol matches the chunk; never a guessed symbol |
| File missing from the stored parse (index inconsistent) | error 500 with a message; not an item with invented fields |
| Symbol present in parse but absent from graph | zero degrees, empty relationships (not an error) |
| Query returns no chunks (e.g. only stopwords) | `evidence: []` with HTTP 200 |
| Component returned nothing for a file | that component's `scores` entry is `null` |
| `top_k` larger than available | return what exists |
| Query with unicode / very long text | accepted up to 2,000 characters; longer is a 422 |

## 10. Validation (no accuracy numbers; invariants and spot checks)

The tests assert properties, not retrieval quality. The 73 frozen questions are used **only as a
realistic query set** to exercise invariants; no metric is computed, nothing is tuned.

1. **Retrieval is unchanged.** For all 73 questions, the file order of the evidence response
   equals the saved Milestone 8 AST-hybrid ranking (same files, same order), and RRF scores equal
   the saved scores. This is the main guard that evidence is a pure view over retrieval.
2. **Line integrity.** For every item of every question: lines in range; `text` (requested)
   equals the bytes of those lines in the working-tree file; `file_sha256` equals the hash of the
   file.
3. **Symbol integrity.** For every `exact` item: the symbol exists in the stored parse with
   `start_line` equal to the item's (function/method) or header start (class); the item's lines lie
   within the symbol's extent; `node_id` exists in the graph. Counts of each `symbol_status` are
   reported (descriptive only).
4. **Graph integrity.** Every relationship in an item exists in the graph with identical
   `line` and `rule`; `degree` equals the graph's own degree; `links_to_other_results` is exactly
   the edges between items' nodes (checked by brute force); ordering and the 10-edge cap hold.
5. **Determinism.** Each question run twice in-process, and a sample of 10 in a fresh process:
   byte-identical `evidence` JSON.
6. **Schema.** All fields present in every item; response validates against the Pydantic model
   and its exported JSON Schema; unknown `symbol_status` values cannot occur.
7. **Edge-case unit tests** on a small synthetic repository: module-level chunk, class header,
   overloads with a shared name, nested function, file with a syntax error, symbol with no graph
   edges, empty/stopword query, `top_k` bounds, 422 on empty query, ambiguous method name (graph
   edge dropped by the uniqueness rule is *absent*, not invented).
8. **Fresh-index consistency.** After an M11 incremental update (edit a function; rename a symbol)
   the evidence for a query reflects the new lines/symbols and the graph change, and equals the
   evidence from an index rebuilt from scratch (reuses the M11 equivalence harness).
9. **Hand-checked spot checks (10 queries, listed in the results file):** for 10 fixed queries
   (chosen now, from the Flask docs, not from the benchmark: e.g. "save_session", "Blueprint
   register", "dispatch_request") I open the cited lines in the source and confirm they show what
   the item claims (symbol name at the start line, call at the cited graph-evidence line). Reported
   as `n/10 confirmed`; any miss is reported with the cause.

Pass/fail: M12 passes only if 1-8 hold with zero violations and 9 is reported honestly.

## 11. What is not claimed

- Nothing about answer quality or retrieval quality (item 1 shows retrieval is *unchanged*).
- Graph annotations are facts about code structure with the audited precision above, not a claim
  that the related symbol is relevant to the question.
- Not tested: other repositories, concurrency, large indexes.

## 12. Decisions (approved)

1. **One item per file.** Matches scoring and RRF; the item points to the best representative chunk.
2. **`include_text=false` by default.**
3. **Raw component scores are exposed**, labelled non-comparable across components.
4. **At most 10 graph relationships per item**; the constant is fixed now and not tuned.

## 13. Contract rules (approved wording)

1. **One primary item per file in v1.** M12 returns exactly one primary evidence item per retrieved
   file. Multiple chunks from the same file are intentionally not exposed in v1. If M13 requires
   additional evidence from the same file (`additional_chunks`), it will be introduced as a
   separately evaluated schema extension rather than silently changing v1 semantics.
2. **Evidence is not relevance.** Graph annotations describe structural relationships only. Their
   presence must never be interpreted as evidence that the related symbol is relevant to the
   user's query. (M10B showed connectivity can overpower relevance.) The schema documents this on
   the `graph` field, and no graph-derived number is ever used for ranking.
3. **M12 -> M13 boundary.** The LLM layer must not have direct access to the repository or the
   index. It receives evidence exclusively through the Evidence API, and may cite only
   `(file, line range)` pairs that appeared in a response it was given. It never searches files,
   inspects the repository, or invents citations.

```
                 +-- BM25
Query -> Hybrid -+-- Embedding
                 |
                 v
          Evidence Builder -> Evidence API -> M13 LLM -> grounded answer with citations
```

## Integrity

Pinned Flask commit `d73fa1cdcbd8b1465c151db8924ba58b1dd14e35`; dataset fingerprint
`6e78d06ad988b637eb3f77f047232238667e0cf9b325abd971f1eab3c024a311`. Planned modules:
`app/evidence/{schema,builder,__main__}.py`, `app/api/evidence.py`, tests under
`tests/test_evidence_*.py`, results in `evaluation/results/evidence_api_flask.json`.

## Addendum (2026-10-08, after the implementation and its unit tests, before any validation run)

Two deviations from the frozen plan, found while implementing. Neither changes the schema, the
rules or the pass/fail checks apart from what is stated here.

**Deviation 1: the saved M8 results contain no RRF scores.** Section 10 check 1 says evidence RRF
scores must equal "the saved scores". The saved M8 result files hold only the ranked file lists
(`retrieved_files`, top 10) per question, no scores. The check is therefore: (a) the evidence file
order equals the saved M8 AST-hybrid ranking (top 10) for all 73 questions, and (b) evidence RRF
scores, lines and order equal those of the unmodified `HybridRetriever.search` run on the same
index (a unit test also checks this on a synthetic repository).

**Deviation 2: "byte-identical" is not achievable for embedding raw scores, and exact embedding
ties are ordered by the vector store.** Found by a unit test: two identical searches in one process
returned embedding scores differing by about 3e-8 (float32 noise in Qdrant's local mode) for some
queries; BM25 and RRF scores and all ranks were identical. Separately, chunks with identical or
bag-of-words-identical text get exactly equal embedding scores, and their order is the vector
store's internal (insertion) order, which an incremental update does not reproduce; this will also
happen with real embeddings for chunks with identical text. Section 7 already says the API does
not claim embedding ties are stable across stores; it is now stated as the determinism contract:

- For a fixed index state, repeated requests return identical `evidence` except that
  `scores.embedding.score` may differ by up to 1.5e-6 (noise plus the 6-decimal rounding).
- Which of several exactly-tied chunks a component returns first, and hence the order of files
  that tie, is the store's order. The retrieval layer is not changed in this milestone (changing
  tie-breaking is a retrieval change and would be evaluated separately; backlog).
- The validation run therefore **measures** how often it matters on the 73 questions instead of
  asserting it never does: repeated in-process and fresh-process responses are compared with
  `compare_evidence` (tolerance as above), and the number of questions with any exactly tied
  embedding scores in their top 10 is reported.
- The incremental-update equivalence test (check 8) uses an embedder without accidental ties so
  that it tests the evidence logic and not the store's tie order.
