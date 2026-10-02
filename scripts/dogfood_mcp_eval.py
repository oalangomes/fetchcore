"""End-to-end FetchCore dogfood evaluation through the public MCP stdio boundary."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@dataclass(frozen=True)
class Scenario:
    id: str
    query: str
    expected_paths: tuple[str, ...]
    limit: int
    min_expected_path_hits: int
    verify_source: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path("."))
    parser.add_argument("--repository", required=True)
    parser.add_argument("--revision")
    parser.add_argument("--solr-url", required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--server-command", default="fetchcore-mcp")
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Write the evaluation report but do not fail the process on scenario misses.",
    )
    return parser.parse_args()


def load_scenarios(path: Path) -> tuple[Scenario, ...]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("dogfood scenarios require version: 1")
    defaults = payload.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ValueError("dogfood scenario defaults must be a mapping")
    default_limit = int(defaults.get("limit", 10))
    raw_scenarios = payload.get("scenarios")
    if not isinstance(raw_scenarios, list) or not raw_scenarios:
        raise ValueError("dogfood scenarios must be a non-empty list")

    scenarios: list[Scenario] = []
    ids: set[str] = set()
    for index, raw in enumerate(raw_scenarios):
        if not isinstance(raw, dict):
            raise ValueError(f"scenario {index} must be a mapping")
        scenario_id = _required_text(raw, "id", index)
        if scenario_id in ids:
            raise ValueError(f"duplicate dogfood scenario id {scenario_id!r}")
        ids.add(scenario_id)
        expected_raw = raw.get("expected_paths")
        if not isinstance(expected_raw, list) or not expected_raw:
            raise ValueError(f"scenario {scenario_id!r} requires expected_paths")
        expected_paths = tuple(str(item).strip() for item in expected_raw if str(item).strip())
        if len(expected_paths) != len(expected_raw):
            raise ValueError(f"scenario {scenario_id!r} contains an empty expected path")
        limit = int(raw.get("limit", default_limit))
        minimum = int(raw.get("min_expected_path_hits", 1))
        if limit < 1 or minimum < 1 or minimum > len(expected_paths):
            raise ValueError(f"scenario {scenario_id!r} has invalid bounds")
        scenarios.append(
            Scenario(
                id=scenario_id,
                query=_required_text(raw, "query", index),
                expected_paths=expected_paths,
                limit=limit,
                min_expected_path_hits=minimum,
                verify_source=bool(raw.get("verify_source", False)),
            )
        )
    return tuple(scenarios)


async def evaluate(args: argparse.Namespace, scenarios: tuple[Scenario, ...]) -> dict[str, object]:
    workspace = args.workspace.expanduser().resolve(strict=True)
    server_env = {
        "FETCHCORE_MCP_WORKSPACE": str(workspace),
        "FETCHCORE_MCP_REPOSITORY": args.repository,
        "FETCHCORE_MCP_BACKEND": "solr",
        "FETCHCORE_MCP_BACKEND_SETTINGS": json.dumps(
            {
                "url": args.solr_url,
                "collection": args.collection,
                "projection_version": "3",
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    }
    if args.revision:
        server_env["FETCHCORE_MCP_REVISION"] = args.revision

    parameters = StdioServerParameters(
        command=args.server_command,
        env=server_env,
        cwd=workspace,
    )
    reports: list[dict[str, object]] = []

    async with (
        stdio_client(parameters) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        tool_names = {item.name for item in tools.tools}
        missing_tools = sorted({"search", "get_source"} - tool_names)
        if missing_tools:
            raise RuntimeError(
                "FetchCore MCP server is missing required tools: "
                + ", ".join(missing_tools)
            )

        smoke_path = scenarios[0].expected_paths[0]
        smoke_source_ref = (
            f"repository://{args.repository}/"
            + smoke_path.strip().lstrip("./")
        )
        smoke_result = await session.call_tool(
            "get_source",
            {
                "source_ref": smoke_source_ref,
                "repository": args.repository,
            },
        )
        smoke_payload = tool_payload(smoke_result)
        if not str(smoke_payload.get("content", "")).strip():
            raise RuntimeError(
                "FetchCore MCP get_source smoke returned empty content for "
                f"{smoke_source_ref}"
            )
        protocol_smoke = {
            "initialized": True,
            "required_tools": ["get_source", "search"],
            "advertised_tools": sorted(tool_names),
            "get_source_verified": True,
            "source_ref": smoke_source_ref,
        }

        for scenario in scenarios:
            search_result = await session.call_tool(
                "search",
                {
                    "query": scenario.query,
                    "repository": args.repository,
                    "limit": scenario.limit,
                },
            )
            payload = tool_payload(search_result)
            raw_results = payload.get("results", [])
            if not isinstance(raw_results, list):
                raise ValueError(f"scenario {scenario.id!r} returned invalid results")
            results = [item for item in raw_results if isinstance(item, dict)]

            matched_paths = [
                path
                for path in scenario.expected_paths
                if any(result_matches_path(item, path) for item in results)
            ]
            first_rank = first_expected_rank(results, scenario.expected_paths)
            reciprocal_rank = 0.0 if first_rank is None else 1.0 / first_rank

            source_verified = not scenario.verify_source
            source_ref: str | None = None
            if scenario.verify_source and first_rank is not None:
                matched = results[first_rank - 1]
                raw_ref = matched.get("source_ref")
                if isinstance(raw_ref, str) and raw_ref.strip():
                    source_ref = raw_ref
                    source_result = await session.call_tool(
                        "get_source",
                        {
                            "source_ref": source_ref,
                            "repository": args.repository,
                        },
                    )
                    source_payload = tool_payload(source_result)
                    source_verified = bool(
                        str(source_payload.get("content", "")).strip()
                    )

            passed = (
                len(matched_paths) >= scenario.min_expected_path_hits
                and source_verified
            )
            reports.append(
                {
                    "id": scenario.id,
                    "query": scenario.query,
                    "passed": passed,
                    "expected_paths": list(scenario.expected_paths),
                    "matched_paths": matched_paths,
                    "min_expected_path_hits": scenario.min_expected_path_hits,
                    "first_expected_rank": first_rank,
                    "reciprocal_rank": reciprocal_rank,
                    "source_verified": source_verified,
                    "source_ref_verified": source_ref,
                    "backend": payload.get("backend"),
                    "search_duration_ms": payload.get("duration_ms"),
                    "top_source_refs": [
                        item.get("source_ref") or item.get("source")
                        for item in results[:5]
                    ],
                }
            )

    passed_count = sum(1 for item in reports if item["passed"])
    reciprocal_ranks = [float(item["reciprocal_rank"]) for item in reports]
    duration_values = [
        int(value)
        for item in reports
        if isinstance((value := item.get("search_duration_ms")), int)
    ]
    return {
        "schema_version": 1,
        "status": "passed" if passed_count == len(reports) else "failed",
        "repository": args.repository,
        "revision": args.revision,
        "backend": "solr",
        "collection": args.collection,
        "protocol_smoke": protocol_smoke,
        "summary": {
            "total": len(reports),
            "passed": passed_count,
            "failed": len(reports) - passed_count,
            "expected_path_hit_rate": (
                0.0 if not reports else passed_count / len(reports)
            ),
            "mrr": (
                0.0
                if not reciprocal_ranks
                else sum(reciprocal_ranks) / len(reciprocal_ranks)
            ),
            "search_duration_ms_total": sum(duration_values),
        },
        "scenarios": reports,
    }

def tool_payload(result: Any) -> dict[str, object]:
    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return {str(key): value for key, value in structured.items()}

    for item in getattr(result, "content", ()):
        if getattr(item, "type", None) != "text":
            continue
        text = getattr(item, "text", None)
        if not isinstance(text, str):
            continue
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            return {str(key): value for key, value in decoded.items()}
    raise ValueError("MCP tool result did not contain a JSON object")


def result_matches_path(result: dict[str, object], expected_path: str) -> bool:
    expected = expected_path.strip().lstrip("./")
    candidates: list[str] = []
    for key in ("source", "source_ref"):
        value = result.get(key)
        if isinstance(value, str):
            candidates.append(value)
    metadata = result.get("metadata")
    if isinstance(metadata, dict):
        path = metadata.get("path")
        if isinstance(path, str):
            candidates.append(path)
    normalized = [item.replace("\\", "/").lstrip("./") for item in candidates]
    return any(
        candidate == expected
        or candidate.endswith(("/" + expected, ":" + expected))
        for candidate in normalized
    )


def first_expected_rank(
    results: list[dict[str, object]],
    expected_paths: tuple[str, ...],
) -> int | None:
    for rank, result in enumerate(results, start=1):
        if any(result_matches_path(result, path) for path in expected_paths):
            return rank
    return None


def markdown_report(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    assert isinstance(summary, dict)
    lines = [
        "# FetchCore self-dogfood MCP evaluation",
        "",
        f"- Status: **{payload['status']}**",
        f"- Repository: `{payload['repository']}`",
        f"- Revision: `{payload.get('revision') or 'unknown'}`",
        f"- Backend: `{payload['backend']}`",
        f"- Collection: `{payload['collection']}`",
        "- MCP protocol smoke: **PASS**",
        f"- Scenarios: {summary['passed']}/{summary['total']} passed",
        f"- MRR: {float(summary['mrr']):.4f}",
        "",
        "| Scenario | Result | First expected rank | Matched paths | MCP source read |",
        "| --- | --- | ---: | --- | --- |",
    ]
    scenarios = payload["scenarios"]
    assert isinstance(scenarios, list)
    for item in scenarios:
        assert isinstance(item, dict)
        rank = item.get("first_expected_rank")
        matched = item.get("matched_paths")
        lines.append(
            "| {id} | {status} | {rank} | {matched} | {source} |".format(
                id=item["id"],
                status="PASS" if item["passed"] else "FAIL",
                rank="-" if rank is None else rank,
                matched="<br>".join(matched) if isinstance(matched, list) and matched else "-",
                source="yes" if item["source_verified"] else "no",
            )
        )
    lines.append("")
    return "\n".join(lines)


def _required_text(raw: dict[object, object], key: str, index: int) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"scenario {index} requires non-empty {key!r}")
    return value.strip()


def main() -> int:
    args = parse_args()
    scenarios = load_scenarios(args.scenarios)
    payload = asyncio.run(evaluate(args, scenarios))

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.write_text(markdown_report(payload), encoding="utf-8")

    print(json.dumps(payload["summary"], sort_keys=True))
    if args.report_only:
        return 0
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
