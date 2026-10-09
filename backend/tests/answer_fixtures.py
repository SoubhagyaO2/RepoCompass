"""Shared fixtures for the answer-layer tests: a small EvidenceResponse and fake models."""

import json
from typing import Any

from app.answer.context import Context, build_context
from app.evidence.schema import (
    ComponentScore,
    EvidenceItem,
    EvidenceResponse,
    GraphDegree,
    GraphEvidence,
    GraphRelationship,
    IndexInfo,
    RetrievalInfo,
    Scores,
    SymbolInfo,
)
from app.llm.base import LLMError, LLMRequest, LLMResponse, UsageRecord

QUESTION = "Where is the session cookie saved?"

SESSION_TEXT = (
    "def save_session(self, app, session, response):\n"
    "    name = self.get_cookie_name(app)\n"
    "    response.set_cookie(name, dump_cookie(session))\n"
)
PROCESS_TEXT = (
    "def process_response(self, response):\n"
    "    ctx = request_ctx\n"
    "    if not self.session_interface.is_null_session(ctx.session):\n"
    "        self.session_interface.save_session(self, ctx.session, response)\n"
    "    return response\n"
)
TEST_TEXT = "def test_save():\n    assert True\n"


def _item(
    rank: int, file: str, start: int, end: int, qualified: str | None, text: str, rel: list[str]
) -> EvidenceItem:
    symbol = (
        SymbolInfo(
            qualified_name=qualified,
            name=qualified.rsplit(".", 1)[-1],
            kind="method" if "." in qualified else "function",
            node_id=f"{file}::{qualified}",
            line_start=start,
            line_end=end,
        )
        if qualified
        else None
    )
    relationships = [
        GraphRelationship(
            relationship="calls",
            other=other,
            other_file=other.split("::")[0],
            line=start,
            rule="import",
        )
        for other in rel
    ]
    return EvidenceItem(
        rank=rank,
        file=file,
        file_sha256=f"{rank:064x}",
        category="test" if file.startswith("tests/") else "source",
        line_start=start,
        line_end=end,
        symbol=symbol,
        symbol_status="exact" if symbol else "module_level",
        scores=Scores(
            rrf=0.03 - rank / 1000,
            bm25=ComponentScore(rank=rank, score=5.0, line_start=start, line_end=end),
            embedding=None,
        ),
        graph=GraphEvidence(
            node=symbol.node_id if symbol else file,
            degree=GraphDegree(calls=len(rel)),
            relationships=relationships,
            truncated=False,
            links_to_other_results=[],
        ),
        text=text,
    )


def make_evidence(question: str = QUESTION) -> EvidenceResponse:
    items = [
        _item(1, "src/pkg/sessions.py", 10, 20, "SessionInterface.save_session", SESSION_TEXT,
              ["src/pkg/helpers.py::dump_cookie"]),
        _item(2, "src/pkg/app.py", 100, 110, "App.process_response", PROCESS_TEXT, []),
        _item(3, "tests/test_sessions.py", 5, 9, "test_save", TEST_TEXT, []),
    ]  # fmt: skip
    return EvidenceResponse(
        query=question,
        index=IndexInfo(
            repository="fixture", revision=None, view="ast", files=3, chunks=3,
            config_sha256="c" * 64, manifest_sha256="m" * 64,
        ),
        retrieval=RetrievalInfo(
            method="hybrid-rrf", rrf_k=60, depth=50, components=["bm25", "embedding"]
        ),
        evidence=items,
    )  # fmt: skip


def make_context(budget: int = 24_000) -> Context:
    return build_context(QUESTION, make_evidence(), budget)


def output(
    status: str = "answered", claims: list[dict[str, Any]] | None = None, missing: str = ""
) -> str:
    if claims is None:
        claims = [
            {"text": "Sessions are saved by SessionInterface.save_session.", "citations": ["E1"]}
        ]
    return json.dumps({"status": status, "claims": claims, "missing": missing})


def claim(text: str, *citations: str) -> dict[str, Any]:
    return {"text": text, "citations": list(citations)}


class FakeLLM:
    """A deterministic model: replies with the given text (or raises) and records every request."""

    provider = "fake"
    model = "fake-model"

    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.requests: list[LLMRequest] = []

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if isinstance(self.reply, Exception):
            raise self.reply
        usage = UsageRecord(
            provider="fake", requested_model="fake-model", returned_model_version="fake-1",
            input_tokens=100, output_tokens=20, thinking_tokens=5, total_tokens=125,
            finish_reason="STOP", latency_ms=1.0, attempts=1,
        )  # fmt: skip
        return LLMResponse(text=self.reply, usage=usage)


def failing_llm(message: str = "boom") -> FakeLLM:
    return FakeLLM(LLMError(message, UsageRecord(provider="fake", requested_model="fake-model")))
