from __future__ import annotations

import asyncio
from pathlib import Path

from fetchcore.application import RepositoryBinding, RetrievalApplicationService
from fetchcore.mcp_server import create_mcp_server


def test_mcp_server_registers_p0_tools(tmp_path: Path) -> None:
    service = RetrievalApplicationService(
        {
            "owner/repo": RepositoryBinding(
                repository="owner/repo",
                workspace=tmp_path,
                backend="local-semantic",
            )
        }
    )

    server = create_mcp_server(service)
    tools = asyncio.run(server.list_tools())

    assert {tool.name for tool in tools} == {"search", "get_source"}
