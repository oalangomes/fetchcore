"""Real Compose smoke: nginx -> API -> Solr -> source plus MCP over HTTP.

Use after explicitly indexing the fixture:
docker compose exec -T api python -m tools.explorer.docker.smoke
"""

from __future__ import annotations

import asyncio
import json
from urllib.request import Request, urlopen

WEB = "http://web:8080"
MCP = "http://mcp:8766/mcp"


def fetch(path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    request = Request(
        WEB + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def web_smoke() -> tuple[str, str]:
    with urlopen(WEB, timeout=10) as response:
        assert "FetchCore" in response.read().decode()
    config = fetch("/api/config")
    assert config["backend"] == "solr", config
    repository = str(config["repository"])
    result = fetch(
        "/api/search",
        {
            "repository": repository,
            "limit": 10,
            "intent": {"schema_version": 2, "canonical_query": "eligibility_validator"},
        },
    )
    assert result["backend"] == "solr", result
    assert result["results"], "Solr has no indexed fixture results"
    hit = next(hit for hit in result["results"] if "eligibility.py" in hit["source_ref"])
    assert "eligibility_validator" in hit["content"]
    assert hit.get("revision"), "index evidence must retain its revision"
    source = fetch("/api/source", {"repository": repository, "source_ref": hit["source_ref"]})
    assert "eligibility_validator" in source["content"], source
    print("PASS: browser gateway -> API -> Solr -> source", flush=True)
    return repository, hit["source_ref"]


async def mcp_smoke(repository: str, source_ref: str) -> None:
    from mcp.client import Client

    async with Client(MCP) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert {"search", "get_source"} <= names, names
        search = await client.call_tool(
            "search", {"query": "eligibility_validator", "repository": repository, "limit": 10}
        )
        assert not search.is_error, search
        assert search.structured_content and search.structured_content.get("results"), search
        source = await client.call_tool(
            "get_source", {"repository": repository, "source_ref": source_ref}
        )
        assert not source.is_error, source
        assert source.structured_content and "eligibility_validator" in source.structured_content.get(
            "content", ""
        ), source
    print("PASS: MCP HTTP -> Solr search -> authorized source", flush=True)


if __name__ == "__main__":
    repo, ref = web_smoke()
    asyncio.run(mcp_smoke(repo, ref))
