"""MCP v2 adapter for the FetchCore retrieval application service."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fetchcore.application import RetrievalApplicationService, SearchRequest, SourceReadRequest
from fetchcore.application.navigation import FindSymbolRequest, GetRelatedRequest
from fetchcore.application.runtime_config import load_runtime_config, service_from_runtime_config
from fetchcore.retrieval.contracts import KnowledgeSearchError, SearchIntent

if TYPE_CHECKING:
    from mcp.server import MCPServer


def service_from_environment() -> RetrievalApplicationService:
    """Compatibility entry point for the application runtime bootstrap."""
    return service_from_runtime_config(load_runtime_config())


def create_mcp_server(service: RetrievalApplicationService) -> MCPServer:
    """Create the thin MCP transport adapter around a retrieval service."""
    try:
        from mcp.server import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
    except ModuleNotFoundError as exc:  # pragma: no cover - packaging guard
        if exc.name == "mcp":
            raise RuntimeError(
                "FetchCore MCP serving requires the optional dependency: "
                "pip install 'fetchcore[mcp]'"
            ) from exc
        raise

    server = MCPServer(
        "FetchCore",
        instructions=(
            "Search authorized software repositories through FetchCore and read "
            "bounded source ranges by source_ref. Retrieval results remain evidence; "
            "the MCP server does not make lifecycle or approval decisions."
        ),
    )

    @server.tool(name="search")
    def search_tool(
        query: str = "",
        repository: str | None = None,
        limit: int = 10,
        knowledge_types: list[str] | None = None,
        intent: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Search technical repository knowledge inside the authorized scope."""
        try:
            response = service.search(
                SearchRequest(
                    query=query,
                    repository=repository,
                    limit=limit,
                    knowledge_types=tuple(knowledge_types or ()),
                    intent=SearchIntent.from_mapping(intent) if intent is not None else None,
                )
            )
        except KnowledgeSearchError as exc:
            raise ToolError(f"FetchCore retrieval failed: {exc.category}") from exc
        return response.to_mapping()

    @server.tool(name="find_symbol")
    def find_symbol_tool(
        name: str,
        repository: str | None = None,
        revision: str | None = None,
        language: str | None = None,
        kind: str | None = None,
        path: str | None = None,
        limit: int = 20,
    ) -> dict[str, object]:
        """Find exact names, qualified names or canonical identities; report coverage."""
        try:
            return service.find_symbol(FindSymbolRequest(
                name=name, repository=repository, revision=revision,
                language=language, kind=kind, path=path, limit=limit,
            )).to_mapping()
        except KnowledgeSearchError as exc:
            raise ToolError(f"FetchCore symbol lookup failed: {exc.category}") from exc

    @server.tool(name="get_related")
    def get_related_tool(
        source: str,
        repository: str | None = None,
        revision: str | None = None,
        relation_types: list[str] | None = None,
        direction: str = "outgoing",
        limit: int = 20,
        depth: int = 1,
        require_resolution: bool = False,
    ) -> dict[str, object]:
        """Read observed relations by symbol identity or source_ref, without graph inference."""
        try:
            return service.get_related(GetRelatedRequest(
                source=source, repository=repository, revision=revision,
                relation_types=tuple(relation_types or ()), direction=direction,
                limit=limit, depth=depth, require_resolution=require_resolution,
            )).to_mapping()
        except KnowledgeSearchError as exc:
            raise ToolError(f"FetchCore related lookup failed: {exc.category}") from exc

    @server.tool(name="get_source")
    def get_source_tool(
        source_ref: str,
        repository: str | None = None,
        revision: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
        context_lines: int = 0,
    ) -> dict[str, object]:
        """Read bounded source; optional revision is an exact materialized precondition."""
        try:
            result = service.get_source(
                SourceReadRequest(
                    source_ref=source_ref,
                    repository=repository,
                    revision=revision,
                    line_start=line_start,
                    line_end=line_end,
                    context_lines=context_lines,
                )
            )
        except KnowledgeSearchError as exc:
            raise ToolError(f"FetchCore source read failed: {exc.category}") from exc
        return result.to_mapping()

    return server


def main() -> None:
    """Run the FetchCore MCP server over stdio."""
    mcp.run()


mcp = create_mcp_server(service_from_environment())


if __name__ == "__main__":
    main()
