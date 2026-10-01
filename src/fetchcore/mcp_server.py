"""Compatibility entry point for the FetchCore MCP transport."""

from fetchcore.serving.mcp import create_mcp_server, main, mcp, service_from_environment

__all__ = ["create_mcp_server", "main", "mcp", "service_from_environment"]

if __name__ == "__main__":
    main()
