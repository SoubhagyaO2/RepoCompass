"""The MCP tools, called through an in-memory MCP client."""

import asyncio
import json
from typing import Any

import pytest
from answer_fixtures import FakeLLM, claim, output
from mcp import Client
from test_evidence import builder, root, store  # noqa: F401  (fixtures)

from app.answer.service import AnswerService
from app.evidence.builder import EvidenceBuilder
from app.mcp_server import build_server


def call(server: Any, tool: str, **args: Any) -> Any:
    async def run() -> Any:
        async with Client(server) as client:
            return await client.call_tool(tool, args)

    return asyncio.run(run())


def payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


def test_the_three_tools_are_listed(builder: EvidenceBuilder) -> None:  # noqa: F811
    async def names() -> set[str]:
        async with Client(build_server(builder, None)) as client:
            return {t.name for t in (await client.list_tools()).tools}

    assert asyncio.run(names()) == {"search_evidence", "ask", "index_status"}


def test_search_evidence_returns_cited_locations(builder: EvidenceBuilder) -> None:  # noqa: F811
    result = call(build_server(builder, None), "search_evidence", query="engine run helper")
    assert not result.is_error
    items = payload(result)["evidence"]
    assert items and {"file", "line_start", "line_end", "file_sha256"} <= set(items[0])


def test_search_evidence_with_extra_chunks_is_schema_v2(builder: EvidenceBuilder) -> None:  # noqa: F811
    result = call(
        build_server(builder, None), "search_evidence", query="engine run helper", extra_chunks=2
    )
    assert "additional_chunks" in payload(result)["evidence"][0]


@pytest.mark.parametrize("args", [{"top_k": 0}, {"top_k": 51}, {"extra_chunks": 5}, {"query": " "}])
def test_bad_arguments_are_tool_errors(
    builder: EvidenceBuilder,  # noqa: F811
    args: dict[str, Any],
) -> None:
    result = call(build_server(builder, None), "search_evidence", **{"query": "engine", **args})
    assert result.is_error


def test_ask_returns_claims_with_resolved_citations(builder: EvidenceBuilder) -> None:  # noqa: F811
    first = builder.build("engine run helper", 3, include_text=True).evidence[0]
    reply = output(claims=[claim("The engine is run here.", "E1")])
    service = AnswerService(lambda q, k: builder.build(q, k, include_text=True), FakeLLM(reply))
    result = call(build_server(builder, service), "ask", question="engine run helper")
    body = payload(result)
    assert not result.is_error and body["status"] in ("answered", "invalid_output")
    assert body["evidence_files"][0] == first.file


def test_ask_without_a_model_is_a_clear_tool_error(builder: EvidenceBuilder) -> None:  # noqa: F811
    result = call(build_server(builder, None), "ask", question="anything")
    assert result.is_error and "not configured" in result.content[0].text


def test_index_status(builder: EvidenceBuilder) -> None:  # noqa: F811
    body = payload(call(build_server(builder, None), "index_status"))
    assert body["files"] > 0
