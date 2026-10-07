import math

import pytest

from app.retrieval.bm25 import BM25Index, tokenize, tokenize_query

# --- tokenizer ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("flask", ["flask"]),
        ("ensure_sync", ["ensure_sync", "ensure", "sync"]),
        ("FlaskClient", ["flaskclient", "flask", "client"]),
        ("HTTPException", ["httpexception", "http", "exception"]),
        ("getUserById", ["getuserbyid", "get", "user", "by", "id"]),
        ("SECRET_KEY", ["secret_key", "secret", "key"]),
        ("__init__", ["__init__", "init"]),
        ("Flask.process_response", ["flask", "process_response", "process", "response"]),
        ("wsgi_app()", ["wsgi_app", "wsgi", "app"]),
        ("x = 42", ["x", "42"]),
        ("___ --- ", []),
        ("", []),
    ],
)
def test_tokenize(text: str, expected: list[str]) -> None:
    assert tokenize(text) == expected


def test_query_drops_question_words_but_keeps_code_words_and_dedupes() -> None:
    assert tokenize_query("Where is the `Flask` class defined?") == ["flask", "class", "defined"]
    assert tokenize_query("Which files import `flask.ctx`?") == ["files", "import", "flask", "ctx"]
    assert tokenize_query("ensure_sync ensure_sync") == ["ensure_sync", "ensure", "sync"]


# --- BM25 -----------------------------------------------------------------------------


def test_matches_the_bm25_formula_by_hand() -> None:
    docs = ["apple banana", "banana banana cherry", "cherry"]
    index = BM25Index(docs, k1=1.5, b=0.75)

    # N=3, df(banana)=2, avgdl=(2+3+1)/3=2, doc 1 has |D|=3 and tf=2
    idf = math.log(1 + (3 - 2 + 0.5) / (2 + 0.5))
    expected = idf * 2 * (1.5 + 1) / (2 + 1.5 * (1 - 0.75 + 0.75 * 3 / 2))
    ((doc_id, score), *_) = index.search("banana", 5)
    assert doc_id == 1
    assert score == pytest.approx(expected)


def test_rarer_terms_and_better_matches_rank_first() -> None:
    docs = [
        "the cat sat on the mat",
        "the dog sat on the log",
        "quantum entanglement of the cat",
    ]
    index = BM25Index(docs)
    ranked = [doc_id for doc_id, _ in index.search("quantum cat", 3)]
    assert ranked[0] == 2  # "quantum" is rare and only doc 2 has it
    assert set(ranked) == {0, 2}  # doc 1 matches no query term, so it is not returned


def test_only_matching_documents_are_returned() -> None:
    index = BM25Index(["alpha beta", "gamma"])
    assert index.search("delta", 5) == []
    assert index.search("the of", 5) == []  # only stopwords -> no query terms
    assert [d for d, _ in index.search("alpha", 5)] == [0]


def test_length_normalisation_prefers_shorter_documents_for_equal_tf() -> None:
    short = "needle"
    long = "needle " + " ".join(f"filler{i}" for i in range(50))
    ranked = [d for d, _ in BM25Index([long, short]).search("needle", 2)]
    assert ranked == [1, 0]


def test_identifier_whole_and_part_matching() -> None:
    docs = [
        "def ensure_sync(self, func): ...",
        "async handlers need sync wrappers",
        "class FlaskClient: ...",
    ]
    index = BM25Index(docs)
    assert index.search("ensure_sync", 3)[0][0] == 0  # whole identifier
    assert index.search("client", 3)[0][0] == 2  # a part of FlaskClient
    assert index.search("FlaskClient", 3)[0][0] == 2


def test_ties_are_broken_by_document_id_and_k_limits_results() -> None:
    index = BM25Index(["same text", "same text", "same text"])
    assert [d for d, _ in index.search("same", 2)] == [0, 1]
    assert index.search("same", 0) == []


def test_empty_index() -> None:
    assert BM25Index([]).search("anything", 5) == []
