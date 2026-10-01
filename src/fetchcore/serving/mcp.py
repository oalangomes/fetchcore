"""MCP v2 adapter for the FetchCore retrieval application service."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from fetchcore.application import (
    RepositoryBinding,
    RetrievalApplicationService,
    SearchRequest,
    SourceReadRequest,
)
from fetchcore.retrieval import RetrievalConfig

if TYPE_CHECKING:
    from mcp.server import MCPServer


def service_from_environment() -> RetrievalApplicationService:
    """Build one explicitly scoped serving service from environment variables."""
    workspace = Path(os.environ.get("FETCHCORE_MCP_WORKSPACE", os.getcwd())).expanduser()
    repository = os.environ.get("FETCHCORE_MCP_REPOSITORY", workspace.name).strip()
    backend = os.environ.get("FETCHCORE_MCP_BACKEND", "local-semantic").strip()
    revision = os.environ.get("FETCHCORE_MCP_REVISION")
    settings = _json_mapping("FETCHCORE_MCP_BACKEND_SETTINGS", default={})
    retrieval_raw = _optional_json_mapping("FETCHCORE_MCP_RETRIEVAL")
    retrieval = (
        None
        if retrieval_raw is None
        else RetrievalConfig.from_mapping(retrieval_raw)
    )
    knowledge_types = _csv("FETCHCORE_MCP_KNOWLEDGE_TYPES")
    repository_paths = _csv("FETCHCORE_MCP_REPOSITORY_PATHS")
    binding = RepositoryBinding(
        repository=repository,
        workspace=workspace,
        backend=backend,
        revision=None if revision is None else revision.strip() or None,
        settings=settings,
        retrieval=retrieval,
        knowledge_types=knowledge_types,
        repository_paths=repository_paths,
    )
    return RetrievalApplicationService({repository: binding})


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
        query: str,
        repository: str | None = None,
        limit: int = 10,
        knowledge_types: list[str] | None = None,
    ) -> dict[str, object]:
        """Search technical repository knowledge inside the authorized scope."""
        response = service.search(
            SearchRequest(
                query=query,
                repository=repository,
                limit=limit,
                knowledge_types=tuple(knowledge_types or ()),
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


def _json_mapping(name: str, *, default: dict[str, object]) -> dict[str, object]:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return dict(default)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{name} must contain a JSON object")
    return {str(key): item for key, item in value.items()}


def _optional_json_mapping(name: str) -> dict[str, object] | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return _json_mapping(name, default={})


def _csv(name: str) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in os.environ.get(name, "").split(",")
        if item.strip()
    )


mcp = create_mcp_server(service_from_environment())


if __name__ == "__main__":
    main()
