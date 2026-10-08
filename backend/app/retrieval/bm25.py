"""BM25 keyword retrieval, written from scratch so the tokenizer (the part that matters for
code) is fully visible.

BM25 scores a document D against a query by summing over the distinct query terms t:

    idf(t) * tf(t, D) * (k1 + 1) / (tf(t, D) + k1 * (1 - b + b * |D| / avgdl))

* tf(t, D): occurrences of t in D, with diminishing returns (saturating at k1).
* idf(t): how rare t is across all documents, ln(1 + (N - df + 0.5) / (df + 0.5)).
* |D| / avgdl: document length relative to the average, so long chunks don't win by size alone.

The parameters below are the textbook defaults, fixed before the benchmark was run and never
tuned on it.
"""

import heapq
import math
import re
from collections import Counter

K1 = 1.5
B = 0.75

# Applied to *queries only*: question words and articles. Documents keep every token so that
# idf stays accurate, and code words such as "import" or "class" are never removed.
QUERY_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "of", "to", "is", "are", "does", "do", "where", "what", "which",
        "how", "when", "why", "who", "that", "this", "it", "its", "by",
    }
)  # fmt: skip

_WORD = re.compile(r"[A-Za-z0-9_]+")
# Splits one identifier segment into words: HTTPException -> HTTP, Exception; getUser -> get, User
_SUBWORD = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase tokens, with identifiers indexed both whole and split into their words.

    "ensure_sync" -> ["ensure_sync", "ensure", "sync"]; "FlaskClient" -> ["flaskclient",
    "flask", "client"]; "flask" -> ["flask"]. Whole identifiers give exact-name matches; the
    parts let a plain-English word like "client" find FlaskClient. No stemming.
    """
    tokens: list[str] = []
    for word in _WORD.findall(text):
        parts = [part.lower() for segment in word.split("_") for part in _SUBWORD.findall(segment)]
        if not parts:  # e.g. "___"
            continue
        whole = word.lower()
        if parts != [whole]:
            tokens.append(whole)
        tokens.extend(parts)
    return tokens


def tokenize_query(text: str) -> list[str]:
    """Tokenize a query: same analyzer as documents, minus question-word stopwords, deduplicated."""
    seen: set[str] = set()
    terms: list[str] = []
    for token in tokenize(text):
        if token not in QUERY_STOPWORDS and token not in seen:
            seen.add(token)
            terms.append(token)
    return terms


class BM25Index:
    def __init__(self, documents: list[str], k1: float = K1, b: float = B) -> None:
        self.k1 = k1
        self.b = b
        self._postings: dict[str, list[tuple[int, int]]] = {}  # term -> [(doc id, tf)]
        self._doc_lengths: list[int] = []
        for doc_id, text in enumerate(documents):
            tokens = tokenize(text)
            self._doc_lengths.append(len(tokens))
            for term, tf in Counter(tokens).items():
                self._postings.setdefault(term, []).append((doc_id, tf))

        self.n_docs = len(documents)
        self._avg_len = sum(self._doc_lengths) / self.n_docs if self.n_docs else 0.0
        self._idf = {
            term: math.log(1 + (self.n_docs - len(post) + 0.5) / (len(post) + 0.5))
            for term, post in self._postings.items()
        }

    def search(self, query: str, k: int) -> list[tuple[int, float]]:
        """Return up to k (document id, score) pairs, best first. Only matching documents appear.

        Ties are broken by document id (lower first) so results are deterministic.
        """
        scores: dict[int, float] = {}
        for term in tokenize_query(query):
            idf = self._idf.get(term)
            if idf is None:
                continue
            for doc_id, tf in self._postings[term]:
                norm = 1 - self.b + self.b * self._doc_lengths[doc_id] / self._avg_len
                scores[doc_id] = scores.get(doc_id, 0.0) + idf * tf * (self.k1 + 1) / (
                    tf + self.k1 * norm
                )
        return heapq.nlargest(k, scores.items(), key=lambda item: (item[1], -item[0]))


class LiveBM25Index:
    """BM25 that supports adding and removing documents in place (for incremental indexing).

    Scoring, tokenizer and tie-breaking are those of `BM25Index`: given the same documents in the
    same order, `search` returns identical scores and an identical ranking. Collection statistics
    are derived from counters at query time, so an update costs only the changed documents.
    Ties are broken by the `order` key supplied when a document is added (lower first), which
    callers set to the document's position in the corpus a static index would have used.
    """

    def __init__(self, k1: float = K1, b: float = B) -> None:
        self.k1 = k1
        self.b = b
        self._lengths: dict[str, int] = {}
        self._terms: dict[str, Counter[str]] = {}  # doc id -> term frequencies
        self._order: dict[str, tuple[object, ...]] = {}
        self._postings: dict[str, dict[str, int]] = {}  # term -> {doc id: tf}
        self._total_length = 0

    def __len__(self) -> int:
        return len(self._lengths)

    def __contains__(self, doc_id: str) -> bool:
        return doc_id in self._lengths

    def add(self, doc_id: str, text: str, order: tuple[object, ...]) -> None:
        if doc_id in self._lengths:
            raise ValueError(f"document {doc_id!r} is already indexed")
        tokens = tokenize(text)
        counts = Counter(tokens)
        self._lengths[doc_id] = len(tokens)
        self._terms[doc_id] = counts
        self._order[doc_id] = order
        self._total_length += len(tokens)
        for term, tf in counts.items():
            self._postings.setdefault(term, {})[doc_id] = tf

    def remove(self, doc_id: str) -> None:
        self._total_length -= self._lengths.pop(doc_id)
        del self._order[doc_id]
        for term in self._terms.pop(doc_id):
            postings = self._postings[term]
            del postings[doc_id]
            if not postings:
                del self._postings[term]

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        """Up to k (document id, score) pairs, best first; only matching documents appear."""
        n_docs = len(self._lengths)
        avg_len = self._total_length / n_docs if n_docs else 0.0
        scores: dict[str, float] = {}
        for term in tokenize_query(query):
            postings = self._postings.get(term)
            if postings is None:
                continue
            idf = math.log(1 + (n_docs - len(postings) + 0.5) / (len(postings) + 0.5))
            for doc_id, tf in postings.items():
                norm = 1 - self.b + self.b * self._lengths[doc_id] / avg_len
                scores[doc_id] = scores.get(doc_id, 0.0) + idf * tf * (self.k1 + 1) / (
                    tf + self.k1 * norm
                )
        ranked = sorted(scores.items(), key=lambda item: (-item[1], self._order[item[0]]))
        return ranked[: max(k, 0)]
