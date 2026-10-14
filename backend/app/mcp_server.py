"""MCP server: RepoCompass evidence and grounded answers as tools for coding assistants.

    python -m app.mcp_server        # stdio; configure the same REPOCOMPASS_* variables as the API

The tools call the same services as the HTTP API (EvidenceBuilder, AnswerService) and add no
retrieval or answer logic of their own. Tool errors are returned to the client as tool errors.
"""

import sys
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from app.answer.service import AnswerService, AnswerUnavailable
from app.config import get_settings
from app.evidence.builder import DEPTH, MAX_EXTRA_CHUNKS, EvidenceBuilder, EvidenceQueryError
from app.evidence.service import load_builder
from app.indexing.live_index import IndexStateError
from app.llm import get_llm_client
from app.llm.base import LLMConfigError

INSTRUCTIONS = (
    "RepoCompass answers questions about one indexed code repository. Use search_evidence to "
    "find files, symbols and line ranges for a question (no prose is generated). Use ask for a "
    "grounded answer whose claims cite evidence ids; status insufficient_evidence means the "
    "evidence does not support an answer, and a valid output is checked for citation validity, "
    "not for truth."
)


def build_server(builder: EvidenceBuilder, service: AnswerService | None) -> MCPServer:
    server = MCPServer("repocompass", instructions=INSTRUCTIONS)

    @server.tool()
    def search_evidence(query: str, top_k: int = 5, extra_chunks: int = 0) -> dict[str, Any]:
        """Hybrid retrieval as auditable evidence: files, hashes, line ranges, symbols, scores.

        extra_chunks (0-4; 2 is the evaluated setting) adds more chunks per file (schema v2)."""
        if not 1 <= top_k <= DEPTH:
            raise ToolError(f"top_k must be between 1 and {DEPTH}")
        if not 0 <= extra_chunks <= MAX_EXTRA_CHUNKS:
            raise ToolError(f"extra_chunks must be between 0 and {MAX_EXTRA_CHUNKS}")
        try:
            if extra_chunks:
                response = builder.build_v2(query, top_k, True, extra_chunks)
            else:
                response = builder.build(query, top_k, True)
        except EvidenceQueryError as error:
            raise ToolError(str(error)) from error
        return response.model_dump(mode="json")

    @server.tool()
    def ask(question: str, top_k: int | None = None) -> dict[str, Any]:
        """A grounded answer: claims with resolved citations, or an abstention."""
        if service is None:
            raise ToolError("the answer layer is not configured (set GEMINI_API_KEY)")
        try:
            answer = service.answer(question, top_k)
        except AnswerUnavailable as error:
            kind = "quota exhausted" if error.quota_exhausted else "model unavailable"
            raise ToolError(f"{kind}: {error}") from error
        return {
            "status": answer.status,
            "answer": answer.answer_text,
            "claims": [c.model_dump(mode="json") for c in answer.claims],
            "missing": answer.missing,
            "validation": answer.validation.model_dump(mode="json"),
            "evidence_files": [item.file for item in answer.evidence.evidence],
            "model": answer.model.returned_model_version,
        }

    @server.tool()
    def index_status() -> dict[str, Any]:
        """What is indexed: repository, revision, file and chunk counts, configuration."""
        return builder.index_info().model_dump(mode="json")

    return server


def main() -> int:
    settings = get_settings()
    if not (settings.live_repository and settings.live_root):
        print(
            "error: set REPOCOMPASS_LIVE_REPOSITORY and REPOCOMPASS_LIVE_ROOT "
            "(see backend/.env.example)",
            file=sys.stderr,
        )
        return 2
    try:
        builder, store = load_builder(settings, settings.live_repository, settings.live_root)
    except IndexStateError as error:
        print(f"error: cannot load the live index: {error}", file=sys.stderr)
        return 2
    try:
        try:
            service: AnswerService | None = AnswerService(
                lambda q, k: builder.build(q, k, include_text=True),
                get_llm_client(settings),
                structured_output=settings.structured_output,
            )
        except LLMConfigError as error:
            print(f"note: answers disabled ({error}); search_evidence still works", file=sys.stderr)
            service = None
        build_server(builder, service).run("stdio")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
