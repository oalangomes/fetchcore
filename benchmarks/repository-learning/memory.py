"""Minimal ai-memory MCP client for the Repository Learning Benchmark."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from protocol import ProtocolError


class AiMemoryClient:
    def __init__(self, server_url: str, auth_token: str | None, timeout_seconds: int = 30):
        if not server_url.strip():
            raise ProtocolError("ai-memory server URL is required")
        self.url = server_url.rstrip("/") + "/mcp"
        self.auth_token = auth_token
        self.timeout_seconds = timeout_seconds
        self.request_id = 0
        self.server_info: dict[str, object] = {}

    def _post(self, method: str, params: dict[str, object]) -> dict[str, object]:
        self.request_id += 1
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params}
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        request = Request(self.url, data=payload, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise ProtocolError(f"ai-memory MCP request failed: {exc}") from exc
        message = self._decode_response(raw)
        if "error" in message:
            raise ProtocolError(f"ai-memory MCP error: {message['error']}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise ProtocolError("ai-memory MCP response missing result object")
        return {str(k): v for k, v in result.items()}

    @staticmethod
    def _decode_response(raw: str) -> dict[str, object]:
        raw = raw.strip()
        if raw.startswith("{"):
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ProtocolError("ai-memory MCP response is not an object")
            return {str(k): v for k, v in value.items()}
        data_lines = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")]
        for candidate in reversed(data_lines):
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return {str(k): v for k, v in value.items()}
        raise ProtocolError("ai-memory MCP response is neither JSON nor supported SSE")

    def initialize(self) -> dict[str, object]:
        result = self._post(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "fetchcore-repository-learning", "version": "1"},
            },
        )
        server_info = result.get("serverInfo")
        self.server_info = server_info if isinstance(server_info, dict) else {}
        return result

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        result = self._post("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError") is True:
            raise ProtocolError(f"ai-memory tool {name} returned isError=true")
        for item in result.get("content", []):
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = item.get("text")
            if not isinstance(text, str):
                continue
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict):
                return {str(k): v for k, v in decoded.items()}
        raise ProtocolError(f"ai-memory tool {name} did not return a JSON object payload")

    def write_page(self, workspace: str, project: str, path: str, body: str) -> dict[str, object]:
        return self.call_tool(
            "memory_write_page",
            {"workspace": workspace, "project": project, "path": path, "body": body},
        )

    def query(self, workspace: str, project: str, query: str, limit: int) -> dict[str, object]:
        return self.call_tool(
            "memory_query",
            {"workspace": workspace, "project": project, "query": query, "limit": limit},
        )
