"""Real single-Lab smoke: web/API and MCP select DEV and BENCH over Solr.

Use after explicitly indexing both authorized targets:
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


def web_smoke() -> dict[str, tuple[str, str]]:
    with urlopen(WEB, timeout=10) as response:
        assert "FetchCore" in response.read().decode()
    config = fetch("/api/config")
    assert config["backend"] == "solr", config
    targets = {target["id"]: target for target in config["targets"]}
    assert {targets[target]["environment"] for target in ("default", "bench")} == {"dev", "bench"}
    found = {}
    for target_id, query in (("default", "SearchIntent"), ("bench", "eligibility_validator")):
        repository = targets[target_id]["repository"]
        result = fetch("/api/search", {
            "target_id": target_id,
            "repository": repository,
            "limit": 10,
            "intent": {"schema_version": 2, "canonical_query": query},
        })
        assert result["backend"] == "solr" and result["results"], result
        hit = next(hit for hit in result["results"] if query in hit["content"])
        assert hit.get("revision"), "index evidence must retain its revision"
        source = fetch("/api/source", {
            "target_id": target_id, "repository": repository, "source_ref": hit["source_ref"],
        })
        assert query in source["content"], source
        found[target_id] = (repository, hit["source_ref"])
    print("PASS: browser gateway -> API -> DEV/BENCH Solr -> source", flush=True)
    return found


async def mcp_smoke(found: dict[str, tuple[str, str]]) -> None:
    from mcp.client import Client

    async with Client(MCP) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert {"list_targets", "search", "get_source"} <= names, names
        listed = await client.call_tool("list_targets", {})
        assert {target["environment"] for target in listed.structured_content["targets"]} == {"dev", "bench"}
        for target_id, (repository, source_ref) in found.items():
            query = "SearchIntent" if target_id == "default" else "eligibility_validator"
            search = await client.call_tool("search", {
                "query": query, "repository": repository, "target_id": target_id, "limit": 10,
            })
            assert not search.is_error and search.structured_content.get("results"), search
            source = await client.call_tool("get_source", {
                "repository": repository, "source_ref": source_ref, "target_id": target_id,
            })
            assert not source.is_error and query in source.structured_content.get("content", ""), source
        forbidden = await client.call_tool("search", {"query": "x", "target_id": "operational"})
        assert forbidden.is_error, forbidden
    print("PASS: MCP HTTP -> authorized DEV/BENCH search/source", flush=True)


if __name__ == "__main__":
    asyncio.run(mcp_smoke(web_smoke()))
