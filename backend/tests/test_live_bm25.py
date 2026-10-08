import random

import pytest

from app.retrieval.bm25 import BM25Index, LiveBM25Index

WORDS = (
    "ensure_sync flask session cookie request response blueprint route handler config "
    "FlaskClient get_debug_flag app context teardown signal template render url_for"
).split()


def corpus(n: int, seed: int = 0) -> list[str]:
    rng = random.Random(seed)
    return [" ".join(rng.choice(WORDS) for _ in range(rng.randint(3, 40))) for _ in range(n)]


def live_from(docs: list[str]) -> LiveBM25Index:
    index = LiveBM25Index()
    for i, text in enumerate(docs):
        index.add(f"d{i}", text, (i,))
    return index


QUERIES = [
    "ensure_sync",
    "where is the session cookie saved",
    "FlaskClient request",
    "teardown signal handler app",
    "nothing_matches_this",
    "the of is",
    "url_for render template config",
]


def test_scores_and_ranking_are_bit_identical_to_the_static_index() -> None:
    docs = corpus(300)
    static, live = BM25Index(docs), live_from(docs)
    for query in QUERIES:
        expected = [(f"d{i}", score) for i, score in static.search(query, 50)]
        assert live.search(query, 50) == expected  # exact float equality, not approx


def test_removing_and_readding_documents_matches_a_fresh_index() -> None:
    docs = corpus(120, seed=1)
    live = live_from(docs)
    for i in range(0, 120, 3):  # remove every third document
        live.remove(f"d{i}")
    for i in range(0, 120, 6):  # put half of them back, at their original corpus positions
        live.add(f"d{i}", docs[i], (i,))

    kept = [i for i in range(120) if i % 3 != 0 or i % 6 == 0]
    fresh = BM25Index([docs[i] for i in kept])
    for query in QUERIES:
        expected = [(f"d{kept[i]}", score) for i, score in fresh.search(query, 50)]
        assert live.search(query, 50) == expected
    assert len(live) == len(kept)


def test_replacing_a_document_changes_the_statistics_everywhere() -> None:
    docs = ["alpha beta", "beta gamma", "gamma delta"]
    live = live_from(docs)
    live.remove("d1")
    live.add("d1", "alpha alpha alpha epsilon", (1,))
    fresh = BM25Index(["alpha beta", "alpha alpha alpha epsilon", "gamma delta"])
    for query in ("alpha", "beta", "gamma epsilon"):
        assert live.search(query, 10) == [(f"d{i}", s) for i, s in fresh.search(query, 10)]


def test_ties_follow_the_order_key_not_insertion_order() -> None:
    live = LiveBM25Index()
    live.add("late", "same text", (2,))
    live.add("early", "same text", (1,))
    assert [d for d, _ in live.search("same", 5)] == ["early", "late"]


def test_removed_terms_disappear_and_emptying_the_index_is_safe() -> None:
    live = LiveBM25Index()
    live.add("a", "unique_term here", (0,))
    live.remove("a")
    assert live.search("unique_term", 5) == [] and len(live) == 0
    assert live.search("anything", 5) == []
    live.add("a", "again", (0,))  # ids can be reused after removal
    assert [d for d, _ in live.search("again", 5)] == ["a"]


def test_adding_a_duplicate_id_is_an_error() -> None:
    live = LiveBM25Index()
    live.add("a", "text", (0,))
    with pytest.raises(ValueError, match="already indexed"):
        live.add("a", "other", (1,))
    assert live.search("text", 0) == []
