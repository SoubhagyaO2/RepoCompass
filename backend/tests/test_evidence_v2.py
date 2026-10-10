# ruff: noqa: F811  (fixtures are imported from test_evidence on purpose)
"""Evidence API schema v2 (Milestone 14): additional chunks per file; plan section 2 (a)-(h)."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_evidence import FILES, QUERIES, builder, make_index, root, store, write  # noqa: F401

from app.evidence.builder import DEPTH, EvidenceBuilder, EvidenceQueryError
from app.evidence.chunks import chunk_rrf, select_additional
from app.evidence.schema import EvidenceResponse, EvidenceResponseV2
from app.evidence.validate import check_response, check_response_v2
from app.main import create_app
from app.models.search import SearchHit
from app.retrieval.embedder import SeededEmbedder
from app.retrieval.hybrid import ChunkCandidate
from app.retrieval.vector_store import VectorStore

PRIMARY = (10, 20)


def candidate(
    start: int, end: int, bm25: int | None = None, emb: int | None = None
) -> ChunkCandidate:
    def hit(pos: int | None) -> SearchHit | None:
        if pos is None:
            return None
        return SearchHit(file_path="f.py", start_line=start, end_line=end, score=1.0 / pos,
                         content="x", category="source")  # fmt: skip

    return ChunkCandidate(start, end, (bm25, emb), (hit(bm25), hit(emb)))


# --- the selection policy as a pure function (plan section 2, steps 1-3) --------------------------


def test_chunk_rrf_is_the_sum_of_one_over_sixty_plus_position() -> None:
    assert chunk_rrf(candidate(1, 2, bm25=1, emb=3)) == pytest.approx(1 / 61 + 1 / 63)
    assert chunk_rrf(candidate(1, 2, bm25=5)) == pytest.approx(1 / 65)  # a missing component adds 0
    assert chunk_rrf(candidate(1, 2)) == 0.0


def test_selection_excludes_the_primary_and_orders_by_chunk_score() -> None:
    cands = [
        candidate(*PRIMARY, bm25=1, emb=1),  # the primary: never an additional chunk
        candidate(30, 40, bm25=2, emb=9),
        candidate(50, 60, bm25=7, emb=2),
        candidate(70, 80, bm25=3, emb=None),
    ]
    chosen = select_additional(cands, PRIMARY, n=2)
    # (50,60): 1/67 + 1/62 = 0.03105 > (30,40): 1/62 + 1/69 = 0.03062 > (70,80): 1/63 = 0.01587
    assert [(c.start_line, c.end_line) for c in chosen] == [(50, 60), (30, 40)]
    assert chunk_rrf(chosen[0]) >= chunk_rrf(chosen[1])


def test_selection_ties_go_to_the_better_single_position_then_to_the_earlier_line() -> None:
    a = candidate(30, 35, bm25=2, emb=4)  # score 1/62 + 1/64
    b = candidate(40, 45, bm25=4, emb=2)  # the same score, the same best position (2)
    c = candidate(20, 25, bm25=3, emb=3)  # lower score (1/63 + 1/63 < 1/62 + 1/64)
    assert chunk_rrf(a) == pytest.approx(chunk_rrf(b))
    chosen = select_additional([c, b, a], PRIMARY, n=3)
    assert [x.start_line for x in chosen] == [30, 40, 20]  # tie on score and best position -> line
    better_position = candidate(60, 65, bm25=1, emb=5)  # 1/61 + 1/65 vs 1/62 + 1/64: higher score
    assert select_additional([a, better_position], PRIMARY, n=1)[0].start_line == 60


def test_selection_respects_n_and_may_return_fewer() -> None:
    cands = [candidate(30, 40, bm25=1), candidate(50, 60, bm25=2), candidate(70, 80, bm25=3)]
    assert len(select_additional(cands, PRIMARY, n=0)) == 0
    assert len(select_additional(cands, PRIMARY, n=2)) == 2
    assert len(select_additional(cands, PRIMARY, n=4)) == 3  # only three candidates exist
    assert select_additional([candidate(*PRIMARY, bm25=1)], PRIMARY, n=2) == []


def test_only_chunks_that_were_retrieved_can_be_candidates() -> None:
    """A chunk with no position in any component's list scores 0 and is never selected."""
    never = candidate(90, 99)  # in neither list
    assert select_additional([never, candidate(30, 40, bm25=1)], PRIMARY, n=2) == [
        candidate(30, 40, bm25=1)
    ]


# --- v2 evidence over an index (plan section 2, invariants (a)-(h)) ---------------------------


def v1_view(item: Any) -> dict[str, Any]:
    d = item.model_dump(mode="json")
    d.pop("additional_chunks", None)
    return d


def strip_embedding(d: dict[str, Any]) -> dict[str, Any]:
    if d["scores"]["embedding"]:
        d["scores"]["embedding"]["score"] = None
    return d


@pytest.mark.parametrize("query", QUERIES)
def test_extra_zero_is_identical_to_v1_in_every_v1_field(
    builder: EvidenceBuilder, query: str
) -> None:  # noqa: F811
    v1 = builder.build(query, top_k=DEPTH, include_text=True)
    v2 = builder.build_v2(query, top_k=DEPTH, include_text=True, extra_chunks=0)
    assert v2.schema_version == 2 and v1.schema_version == 1
    assert [strip_embedding(v1_view(i)) for i in v2.evidence] == [
        strip_embedding(i.model_dump(mode="json")) for i in v1.evidence
    ]
    assert all(i.additional_chunks == [] for i in v2.evidence)


@pytest.mark.parametrize("n", [1, 2, 4])
@pytest.mark.parametrize("query", QUERIES)
def test_files_primary_chunks_scores_symbols_and_graph_do_not_change(
    builder: EvidenceBuilder,
    query: str,
    n: int,  # noqa: F811
) -> None:
    v1 = builder.build(query, top_k=DEPTH, include_text=True)
    v2 = builder.build_v2(query, top_k=DEPTH, include_text=True, extra_chunks=n)
    assert [strip_embedding(v1_view(i)) for i in v2.evidence] == [
        strip_embedding(i.model_dump(mode="json")) for i in v1.evidence
    ]


@pytest.mark.parametrize("query", QUERIES)
def test_additional_chunks_are_valid_distinct_bounded_and_retrieved(
    builder: EvidenceBuilder,
    root: Path,
    query: str,  # noqa: F811
) -> None:
    v2 = builder.build_v2(query, top_k=DEPTH, include_text=True, extra_chunks=2)
    assert check_response_v2(v2, builder.index, root, extra_chunks=2) == []
    assert any(i.additional_chunks for i in v2.evidence) or query in ("LIMIT", "hub")
    for item in v2.evidence:
        spans = [(item.line_start, item.line_end)] + [
            (c.line_start, c.line_end) for c in item.additional_chunks
        ]
        assert len(set(spans)) == len(spans)  # distinct from the primary and from each other
        assert len(item.additional_chunks) <= 2
        assert [c.rank_in_file for c in item.additional_chunks] == list(
            range(1, len(item.additional_chunks) + 1)
        )
        scores = [c.scores.rrf_chunk for c in item.additional_chunks]
        assert scores == sorted(scores, reverse=True)
        for c in item.additional_chunks:
            assert c.scores.bm25 or c.scores.embedding  # it was retrieved by a component


@pytest.mark.parametrize("query", QUERIES)
def test_selection_agrees_with_an_independent_derivation(
    builder: EvidenceBuilder,
    query: str,  # noqa: F811
) -> None:
    """Re-derive the extra chunks straight from the two retrievers' chunk lists."""
    index = builder.index
    lists = [index.bm25_search("ast")(query, DEPTH), index.embedding_search("ast")(query, DEPTH)]
    v2 = builder.build_v2(query, top_k=DEPTH, include_text=False, extra_chunks=2)
    for item in v2.evidence:
        candidates: dict[tuple[int, int], list[int | None]] = {}
        for comp, hits in enumerate(lists):
            for pos, hit in enumerate(hits, 1):
                if hit.file_path == item.file:
                    candidates.setdefault((hit.start_line, hit.end_line), [None, None])[comp] = pos
        candidates.pop((item.line_start, item.line_end), None)

        def score(positions: list[int | None]) -> float:
            return sum(1 / (60 + p) for p in positions if p)

        ranked = sorted(
            candidates.items(),
            key=lambda kv: (-score(kv[1]), min(p for p in kv[1] if p), kv[0][0]),
        )
        expected = [span for span, _ in ranked[:2]]
        assert [(c.line_start, c.line_end) for c in item.additional_chunks] == expected


def test_text_is_off_unless_requested(builder: EvidenceBuilder) -> None:  # noqa: F811
    without = builder.build_v2("engine run helper", top_k=DEPTH, include_text=False)
    assert all(c.text is None for i in without.evidence for c in i.additional_chunks)
    with_text = builder.build_v2("engine run helper", top_k=DEPTH, include_text=True)
    assert all(c.text for i in with_text.evidence for c in i.additional_chunks)


def test_is_deterministic(builder: EvidenceBuilder) -> None:  # noqa: F811
    for q in QUERIES:
        a = builder.build_v2(q, top_k=DEPTH, include_text=True)
        b = builder.build_v2(q, top_k=DEPTH, include_text=True)
        assert [
            (i.file, [(c.line_start, c.line_end) for c in i.additional_chunks]) for i in a.evidence
        ] == [
            (i.file, [(c.line_start, c.line_end) for c in i.additional_chunks]) for i in b.evidence
        ]


@pytest.mark.parametrize("bad", [-1, 5, 100])
def test_extra_chunks_out_of_range_is_rejected(builder: EvidenceBuilder, bad: int) -> None:  # noqa: F811
    with pytest.raises(EvidenceQueryError):
        builder.build_v2("engine", extra_chunks=bad)


def test_v2_response_round_trips_and_is_a_v1_superset(builder: EvidenceBuilder) -> None:  # noqa: F811
    v2 = builder.build_v2("engine run helper", top_k=5, include_text=True)
    again = EvidenceResponseV2.model_validate_json(v2.model_dump_json())
    assert again.model_dump_json() == v2.model_dump_json()
    assert isinstance(v2, EvidenceResponse) and v2.extra_chunks == 2


def test_v1_endpoint_and_validation_are_untouched(builder: EvidenceBuilder, root: Path) -> None:  # noqa: F811
    response = builder.build("engine run helper", top_k=DEPTH, include_text=True)
    assert response.schema_version == 1
    assert not hasattr(response.evidence[0], "additional_chunks")
    assert check_response(response, builder.index, root) == []


def test_http_v2_endpoint(builder: EvidenceBuilder) -> None:  # noqa: F811
    with TestClient(create_app(builder)) as client:
        r = client.get(
            "/v2/evidence", params={"q": "engine run helper", "top_k": 4, "include_text": "true"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["schema_version"] == 2 and body["extra_chunks"] == 2
        assert any(i["additional_chunks"] for i in body["evidence"])
        zero = client.get("/v2/evidence", params={"q": "engine", "extra_chunks": 0}).json()
        assert all(i["additional_chunks"] == [] for i in zero["evidence"])
        assert client.get("/v1/evidence", params={"q": "engine"}).json()["schema_version"] == 1
        for params in (
            {"q": ""},
            {"q": "x", "extra_chunks": 5},
            {"q": "x", "extra_chunks": -1},
            {"q": "x", "top_k": 0},
        ):
            assert client.get("/v2/evidence", params=params).status_code == 422


def test_v2_evidence_after_an_update_equals_a_fresh_build(
    root: Path, tmp_path: Path, store: VectorStore
) -> None:  # noqa: F811
    live = make_index(root, tmp_path / "state-live", store, SeededEmbedder())
    live.build_full()
    builder_live = EvidenceBuilder(live, revision="r")
    write(root, "core.py", FILES["core.py"].replace("def helper(n):", "def helper2(n):"))
    write(root, "extra.py", "def extra():\n    return 1\n")
    live.update()
    with VectorStore(tmp_path / "q-fresh") as fresh_store:
        fresh = make_index(root, tmp_path / "state-fresh", fresh_store, SeededEmbedder())
        fresh.build_full()
        builder_fresh = EvidenceBuilder(fresh, revision="r")
        for q in QUERIES + ["helper2", "extra"]:
            a = builder_live.build_v2(q, top_k=DEPTH, include_text=True)
            b = builder_fresh.build_v2(q, top_k=DEPTH, include_text=True)
            assert check_response_v2(a, live, root, extra_chunks=2) == []
            shape = lambda r: [  # noqa: E731
                (
                    i.file,
                    i.line_start,
                    [(c.line_start, c.line_end, c.text) for c in i.additional_chunks],
                )
                for i in r.evidence
            ]
            assert shape(a) == shape(b)
