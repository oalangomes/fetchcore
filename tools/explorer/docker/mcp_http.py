"""Single-Lab MCP HTTP adapter over the API's authorized target catalog."""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from fetchcore.application import SearchRequest, SourceReadRequest
from fetchcore.retrieval.contracts import KnowledgeSearchError, SearchIntent
from tools.explorer.api.app import _available_targets, _runtime, _selected_target


def create_lab_mcp_server() -> MCPServer:
    """Select only server-owned DEV/BENCH bindings; keep stdio unchanged."""
    context = _runtime()
    server = MCPServer("FetchCore Search Lab")

    def selected(target_id: str | None):
        try:
            return _selected_target(context, target_id)
        except KnowledgeSearchError as exc:
            raise ToolError(f"{exc.category}: {exc}") from exc

    @server.tool(name="list_targets")
    def list_targets() -> dict[str, object]:
        return {"targets": [target.public() for target in _available_targets(context).values()]}

    @server.tool(name="search")
    def search(
        query: str = "",
        repository: str | None = None,
        limit: int = 10,
        knowledge_types: list[str] | None = None,
        intent: dict[str, object] | None = None,
        target_id: str | None = None,
    ) -> dict[str, object]:
        target = selected(target_id)
        return target.service.search(SearchRequest(
            query=query, repository=repository or target.repository, limit=limit,
            knowledge_types=tuple(knowledge_types or ()),
            intent=SearchIntent.from_mapping(intent) if intent is not None else None,
        )).to_mapping()

    @server.tool(name="get_source")
    def get_source(
        source_ref: str,
        repository: str | None = None,
        revision: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
        context_lines: int = 0,
        target_id: str | None = None,
    ) -> dict[str, object]:
        target = selected(target_id)
        return target.service.get_source(SourceReadRequest(
            source_ref=source_ref, repository=repository or target.repository,
            revision=revision, line_start=line_start, line_end=line_end,
            context_lines=context_lines,
        )).to_mapping()

    return server


def main() -> None:
    server = create_lab_mcp_server()
    server.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8766,
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
