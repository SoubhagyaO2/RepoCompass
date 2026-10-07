"""Cross-encoder reranking of a first-stage retriever's top candidates.

A bi-encoder (our embedding model) embeds the question and each chunk *separately* and compares
vectors, which is fast but coarse. A cross-encoder reads the question and one chunk *together*
and outputs a single relevance score, which is slower but more precise. So it is used only on a
short candidate list from the first stage.

Pre-registered design (evaluation/plans/m09-reranking.md): candidates are the first stage's top
20 unique files, each represented by one chunk; the file's score is that chunk's raw logit; the
final ranking is the candidates by score followed by the remaining files in first-stage order.
Every candidate's first-stage rank and reranker rank is kept in a trace, so reranking never
replaces the retrieval evidence.
"""

import time
from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel

from app.models.search import SearchHit

SearchFn = Callable[[str, int], list[SearchHit]]

DEFAULT_CANDIDATES = 20


class Reranker(Protocol):
    model_name: str
    max_length: int

    def score(self, query: str, passages: list[str]) -> list[float]:
        """Relevance of each passage to the query; higher is more relevant."""
        ...


class CrossEncoderReranker:
    def __init__(
        self,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        max_length: int = 512,
        batch_size: int = 16,
    ) -> None:
        # Imported here: sentence-transformers pulls in torch, which is slow to import.
        import torch
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model_name, max_length=max_length, device="cpu")
        self._identity = torch.nn.Identity()
        self._batch_size = batch_size
        self.model_name = model_name
        self.max_length = max_length
        self.batch_size = batch_size
        self.device = str(self._model.model.device)

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        scores = self._model.predict(
            [(query, passage) for passage in passages],
            batch_size=self._batch_size,
            show_progress_bar=False,
            activation_fn=self._identity,  # raw logits, no squashing
        )
        return [float(s) for s in scores]

    def count_pair_tokens(self, query: str, passages: list[str]) -> list[int]:
        """Tokens in each (query, passage) pair before truncation."""
        encoded = self._model.tokenizer(
            [query] * len(passages), passages, truncation=False, verbose=False
        )
        return [len(ids) for ids in encoded["input_ids"]]


class Candidate(BaseModel):
    """One candidate file with its rank before and after reranking."""

    file_path: str
    retrieval_rank: int  # position in the first-stage file ranking (1 = best)
    retrieval_score: float  # first-stage score (RRF)
    reranker_score: float  # cross-encoder logit of the representative chunk
    reranker_rank: int  # position among the candidates after reranking (1 = best)
    start_line: int  # the representative chunk that was scored
    end_line: int


class RerankTrace(BaseModel):
    candidates: list[Candidate]
    first_stage_ms: float
    rerank_ms: float


class RerankingRetriever:
    def __init__(
        self,
        first_stage: SearchFn,
        reranker: Reranker,
        candidates: int = DEFAULT_CANDIDATES,
    ) -> None:
        self._first_stage = first_stage
        self._reranker = reranker
        self._n = candidates
        self.last_trace: RerankTrace | None = None

    def _pool(self, query: str, depth: int) -> list[SearchHit]:
        """The first stage's ranking as one hit per unique file, best first."""
        seen: set[str] = set()
        files: list[SearchHit] = []
        for hit in self._first_stage(query, depth):
            if hit.file_path not in seen:
                seen.add(hit.file_path)
                files.append(hit)
        return files

    def search(self, query: str, depth: int) -> list[SearchHit]:
        """`depth` is passed to the first stage (chunks per component, before deduplication)."""
        t0 = time.perf_counter()
        files = self._pool(query, depth)
        t1 = time.perf_counter()

        pool, rest = files[: self._n], files[self._n :]
        scores = self._reranker.score(query, [hit.content for hit in pool])
        t2 = time.perf_counter()

        order = sorted(range(len(pool)), key=lambda i: (-scores[i], i))  # ties: first-stage order
        reranker_rank = {i: rank for rank, i in enumerate(order, 1)}
        self.last_trace = RerankTrace(
            candidates=[
                Candidate(
                    file_path=hit.file_path,
                    retrieval_rank=i + 1,
                    retrieval_score=hit.score,
                    reranker_score=scores[i],
                    reranker_rank=reranker_rank[i],
                    start_line=hit.start_line,
                    end_line=hit.end_line,
                )
                for i, hit in enumerate(pool)
            ],
            first_stage_ms=(t1 - t0) * 1000,
            rerank_ms=(t2 - t1) * 1000,
        )
        return [pool[i].model_copy(update={"score": scores[i]}) for i in order] + rest

    def count_truncated(self, query: str, depth: int) -> int:
        """Diagnostic (not timed): candidates whose (query, chunk) pair exceeds the token limit."""
        counter = getattr(self._reranker, "count_pair_tokens", None)
        if counter is None:
            return 0
        pool = self._pool(query, depth)[: self._n]
        limit = self._reranker.max_length
        return sum(1 for n in counter(query, [h.content for h in pool]) if n > limit)
