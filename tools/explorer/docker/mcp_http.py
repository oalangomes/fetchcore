"""Optional MCP Streamable HTTP adapter for the local Compose stack.

The product's existing fetchcore-mcp entry point remains stdio. This exposes the
same tools and calls the same application service. No direct backend access.
Compose publishes this unauthenticated developer port only to host loopback.
"""

from __future__ import annotations

from fetchcore.serving.mcp import create_mcp_server, service_from_environment


def main() -> None:
    server = create_mcp_server(service_from_environment())
    server.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8766,
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
