"""Expected serving failures remain actionable through the MCP transport."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from fetchcore.application import RepositoryBinding, RetrievalApplicationService
from fetchcore.serving.mcp import create_mcp_server


def test_unbound_repository_returns_categorized_mcp_tool_error(tmp_path: Path) -> None:
    binding = RepositoryBinding(repository="owner/repo", workspace=tmp_path, backend="solr")
    server = create_mcp_server(RetrievalApplicationService({binding.repository: binding}))

    async def check() -> None:
        with pytest.raises(ToolError, match="unauthorized_repository"):
            await server.call_tool("search", {
                "query": "needle", "repository": "other/repo",
            })
        with pytest.raises(ToolError, match="unauthorized_repository"):
            await server.call_tool("get_source", {
                "source_ref": "repository://other/repo/code.py", "repository": "other/repo",
            })

    asyncio.run(check())
