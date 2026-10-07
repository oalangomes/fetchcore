"""MCP v2 adapter for the FetchCore retrieval application service."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fetchcore.application import RetrievalApplicationService, SearchRequest, SourceReadRequest
from fetchcore.application.runtime_config import load_runtime_config, service_from_runtime_config
from fetchcore.retrieval.contracts import SearchIntent

if TYPE_CHECKING:
    from mcp.server import MCPServer


def service_from_environment() -> RetrievalApplicationService:
    """Compatibility entry point for the application runtime bootstrap."""
    return service_from_runtime_config(load_runtime_config())


def create_mcp_server(service: RetrievalApplicationService) -> MCPServer:
    """Create the thin MCP transport adapter around a retrieval service."""
    try:
        from mcp.server import MCPServer
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
        response = service.search(
            SearchRequest(
                query=query,
                repository=repository,
                limit=limit,
                knowledge_types=tuple(knowledge_types or ()),
                intent=SearchIntent.from_mapping(intent) if intent is not None else None,
            )
        )
        return response.to_mapping()

    @server.tool(name="get_source")
    def get_source_tool(
        source_ref: str,
        repository: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
        context_lines: int = 0,
    ) -> dict[str, object]:
        """Read a bounded source range referenced by a FetchCore search result."""
        result = service.get_source(
            SourceReadRequest(
                source_ref=source_ref,
                repository=repository,
                line_start=line_start,
                line_end=line_end,
                context_lines=context_lines,
            )
        )
        return result.to_mapping()

    return server


def main() -> None:
    """Run the FetchCore MCP server over stdio."""
    mcp.run()


mcp = create_mcp_server(service_from_environment())


if __name__ == "__main__":
    main()
