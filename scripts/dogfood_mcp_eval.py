"""End-to-end FetchCore dogfood evaluation through the public MCP stdio boundary."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeGuard

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


@dataclass(frozen=True)
class ExpectedSymbol:
    """Exact structural identity a find_symbol scenario must observe."""

    name: str
    qualified_name: str
    kind: str
    path: str
    line_start: int
    line_end: int

    def to_mapping(self) -> dict[str, object]:
        return {
            "name": self.name,
            "qualified_name": self.qualified_name,
            "kind": self.kind,
            "path": self.path,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


@dataclass(frozen=True)
class SymbolScenario:
    """Exact symbol lookup scenario evaluated over the MCP find_symbol tool."""

    id: str
    action: str
    name: str
    kind: str | None
    path: str | None
    revision: str | None
    limit: int
    expected_symbols: tuple[ExpectedSymbol, ...]
    verify_source: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path("."))
    parser.add_argument("--repository", required=True)
    parser.add_argument("--revision")
    parser.add_argument("--solr-url", required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--mode", choices=("lexical", "dense", "hybrid"), default="lexical")
    parser.add_argument("--embedding-provider", default="ollama")
    parser.add_argument("--embedding-model", default="embeddinggemma")
    parser.add_argument("--embedding-dimensions", type=int, default=768)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
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


def load_scenarios(path: Path) -> tuple[Scenario, ...] | tuple[SymbolScenario, ...]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("dogfood scenarios require a mapping document")
    version = payload.get("version")
    if version == 1:
        return _load_search_scenarios(payload)
    if version == 2:
        return _load_symbol_scenarios(payload)
    raise ValueError("dogfood scenarios require version: 1 or version: 2")


def _load_search_scenarios(payload: dict[object, object]) -> tuple[Scenario, ...]:
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


def _load_symbol_scenarios(payload: dict[object, object]) -> tuple[SymbolScenario, ...]:
    defaults = payload.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ValueError("dogfood scenario defaults must be a mapping")
    default_limit = _strict_limit(
        defaults.get("limit", 20), "dogfood scenario defaults"
    )
    default_revision = _document_revision(payload)
    raw_scenarios = payload.get("scenarios")
    if not isinstance(raw_scenarios, list) or not raw_scenarios:
        raise ValueError("dogfood scenarios must be a non-empty list")

    scenarios: list[SymbolScenario] = []
    ids: set[str] = set()
    for index, raw in enumerate(raw_scenarios):
        if not isinstance(raw, dict):
            raise ValueError(f"scenario {index} must be a mapping")
        scenario_id = _required_text(raw, "id", index)
        if scenario_id in ids:
            raise ValueError(f"duplicate dogfood scenario id {scenario_id!r}")
        ids.add(scenario_id)
        action = _required_text(raw, "action", index)
        if action != "find_symbol":
            raise ValueError(
                f"scenario {scenario_id!r} declares unsupported action {action!r}"
            )
        expected_raw = raw.get("expected_symbols")
        if not isinstance(expected_raw, list) or not expected_raw:
            raise ValueError(f"scenario {scenario_id!r} requires expected_symbols")
        expected_symbols = tuple(
            _expected_symbol(item, scenario_id, position)
            for position, item in enumerate(expected_raw)
        )
        seen: set[tuple[object, ...]] = set()
        for expected in expected_symbols:
            key = (
                expected.name,
                expected.qualified_name,
                expected.kind,
                expected.path,
                expected.line_start,
                expected.line_end,
            )
            if key in seen:
                raise ValueError(
                    f"scenario {scenario_id!r} contains duplicate expected symbols"
                )
            seen.add(key)
        revision = raw.get("revision", default_revision)
        if revision is not None and (
            not isinstance(revision, str) or not revision.strip()
        ):
            raise ValueError(
                f"scenario {scenario_id!r} requires a non-empty revision when present"
            )
        limit = _strict_limit(raw.get("limit", default_limit), f"scenario {scenario_id!r}")
        verify_source = raw.get("verify_source", False)
        if not isinstance(verify_source, bool):
            raise ValueError(
                f"scenario {scenario_id!r} requires a boolean verify_source"
            )
        scenarios.append(
            SymbolScenario(
                id=scenario_id,
                action=action,
                name=_required_text(raw, "name", index),
                kind=_optional_scenario_text(raw, "kind", scenario_id),
                path=_optional_scenario_text(raw, "path", scenario_id),
                revision=(
                    revision.strip() if isinstance(revision, str) else None
                ),
                limit=limit,
                expected_symbols=expected_symbols,
                verify_source=verify_source,
            )
        )
    return tuple(scenarios)


def _strict_limit(value: object, context: str) -> int:
    if not _is_integer(value):
        raise ValueError(f"{context} requires an integer limit")
    limit = int(value)
    if not 1 <= limit <= 100:
        raise ValueError(f"{context} limit must be between 1 and 100")
    return limit


def _document_revision(payload: dict[object, object]) -> str | None:
    revision = payload.get("revision")
    if revision is None:
        return None
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError("dogfood scenario revision must be a non-empty string")
    return revision.strip()


def _expected_symbol(
    raw: object,
    scenario_id: str,
    position: int,
) -> ExpectedSymbol:
    if not isinstance(raw, dict):
        raise ValueError(
            f"scenario {scenario_id!r} expected symbol {position} must be a mapping"
        )
    fields: dict[str, str] = {}
    for label in ("name", "qualified_name", "kind", "path"):
        value = raw.get(label)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"scenario {scenario_id!r} expected symbol {position} "
                f"requires non-empty {label!r}"
            )
        fields[label] = value.strip()
    line_start = raw.get("line_start")
    line_end = raw.get("line_end")
    if not _is_integer(line_start) or int(line_start) < 1:
        raise ValueError(
            f"scenario {scenario_id!r} expected symbol {position} "
            "requires a positive integer line_start"
        )
    if not _is_integer(line_end) or int(line_end) < int(line_start):
        raise ValueError(
            f"scenario {scenario_id!r} expected symbol {position} "
            "requires line_end >= line_start"
        )
    return ExpectedSymbol(
        name=fields["name"],
        qualified_name=fields["qualified_name"],
        kind=fields["kind"],
        path=fields["path"],
        line_start=int(line_start),
        line_end=int(line_end),
    )


def _optional_scenario_text(
    raw: dict[object, object],
    key: str,
    scenario_id: str,
) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"scenario {scenario_id!r} requires a non-empty {key!r} when present"
        )
    return value.strip()


def _loader_environment() -> dict[str, str]:
    """Pass the Python runtime loader path to the MCP child when required."""

    return {
        name: value
        for name in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH")
        if (value := os.environ.get(name))
    }


def server_environment(args: argparse.Namespace) -> dict[str, str]:
    """Build the MCP child process environment shared by every scenario version."""

    workspace = args.workspace.expanduser().resolve(strict=True)
    server_env = {
        **_loader_environment(),
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
    if args.mode in {"dense", "hybrid"}:
        server_env["FETCHCORE_MCP_RETRIEVAL"] = json.dumps(
            {
                "dense": {
                    "enabled": True,
                    "embedding": {
                        "provider": args.embedding_provider,
                        "model": args.embedding_model,
                        "dimensions": args.embedding_dimensions,
                    },
                },
                "hybrid": {"enabled": args.mode == "hybrid"},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        server_env["FETCHCORE_OLLAMA_URL"] = args.ollama_url
    if args.revision:
        server_env["FETCHCORE_MCP_REVISION"] = args.revision
    return server_env


async def evaluate(
    args: argparse.Namespace,
    scenarios: tuple[Scenario, ...] | tuple[SymbolScenario, ...],
) -> dict[str, object]:
    if scenarios and isinstance(scenarios[0], SymbolScenario):
        return await evaluate_symbols(args, scenarios)
    search_scenarios = tuple(
        scenario for scenario in scenarios if isinstance(scenario, Scenario)
    )
    workspace = args.workspace.expanduser().resolve(strict=True)
    server_env = server_environment(args)

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

        smoke_path = search_scenarios[0].expected_paths[0]
        smoke_source_ref = (
            f"repository://{args.repository}/"
            + smoke_path.strip().lstrip("./")
        )
        smoke_result = await session.call_tool(
            "get_source",
            {
                "source_ref": smoke_source_ref,
                "repository": args.repository,
                **({"revision": args.revision} if args.revision else {}),
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

        for scenario in search_scenarios:
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
                    source_arguments: dict[str, object] = {
                        "source_ref": source_ref,
                        "repository": args.repository,
                    }
                    source_revision = matched.get("revision")
                    if isinstance(source_revision, str) and source_revision:
                        source_arguments["revision"] = source_revision
                    source_result = await session.call_tool(
                        "get_source",
                        source_arguments,
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
                    "retrieval_telemetry": payload.get("telemetry"),
                    "top_source_refs": [
                        item.get("source_ref") or item.get("source")
                        for item in results[:5]
                    ],
                }
            )

    summary = search_summary(reports)
    return {
        "schema_version": 1,
        "status": "passed" if summary["passed"] == len(reports) else "failed",
        "repository": args.repository,
        "revision": args.revision,
        "backend": "solr",
        "mode": args.mode,
        "collection": args.collection,
        "protocol_smoke": protocol_smoke,
        "summary": summary,
        "scenarios": reports,
    }


def search_summary(reports: list[dict[str, object]]) -> dict[str, object]:
    """Search-only v1 metrics; structural find_symbol metrics stay separate."""

    passed_count = sum(1 for item in reports if item["passed"])
    reciprocal_ranks = [
        float(value)
        for item in reports
        if isinstance(
            (value := item.get("reciprocal_rank")), (int, float)
        )
        and not isinstance(value, bool)
    ]
    duration_values = [
        int(value)
        for item in reports
        if isinstance((value := item.get("search_duration_ms")), int)
    ]
    time_to_first_values = [
        int(value)
        for item in reports
        if isinstance((telemetry := item.get("retrieval_telemetry")), dict)
        and isinstance((value := telemetry.get("time_to_first_candidate_ms")), int)
    ]
    return {
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
        "search_duration_ms_p50": _percentile(duration_values, 50),
        "search_duration_ms_p95": _percentile(duration_values, 95),
        "time_to_first_candidate_ms_p50": _percentile(time_to_first_values, 50),
        "time_to_first_candidate_ms_p95": _percentile(time_to_first_values, 95),
    }


NAVIGATION_STATUSES = frozenset(
    {"success", "not_found", "partial", "unsupported", "unknown"}
)


def symbol_required_tools(
    scenarios: tuple[SymbolScenario, ...],
) -> frozenset[str]:
    """Require get_source only when a scenario verifies returned source."""

    if any(scenario.verify_source for scenario in scenarios):
        return frozenset({"find_symbol", "get_source"})
    return frozenset({"find_symbol"})


async def _symbol_smoke(
    session: ClientSession,
    args: argparse.Namespace,
    scenarios: tuple[SymbolScenario, ...],
    required: frozenset[str],
) -> tuple[dict[str, object], bool]:
    """Smoke get_source when required; a failed smoke never blocks the report."""

    smoke_symbol = scenarios[0].expected_symbols[0]
    smoke_source_ref = (
        f"repository://{args.repository}/"
        + smoke_symbol.path.strip().lstrip("./")
    )
    smoke: dict[str, object] = {
        "initialized": True,
        "required_tools": sorted(required),
        "get_source_required": "get_source" in required,
        "get_source_verified": None,
        "source_ref": smoke_source_ref,
        "error_category": None,
    }
    if "get_source" not in required:
        return smoke, True
    smoke_revision = getattr(args, "revision", None) or scenarios[0].revision
    arguments: dict[str, object] = {
        "source_ref": smoke_source_ref,
        "repository": args.repository,
    }
    if smoke_revision:
        arguments["revision"] = smoke_revision
    result = await session.call_tool("get_source", arguments)
    error_text = call_result_error(result)
    if error_text is not None:
        smoke["error_category"] = parse_error_category(error_text)
        smoke["get_source_verified"] = False
        return smoke, False
    payload = tool_payload(result)
    if not str(payload.get("content", "")).strip():
        smoke["error_category"] = "empty_content"
        smoke["get_source_verified"] = False
        return smoke, False
    smoke["get_source_verified"] = True
    return smoke, True


async def evaluate_symbols(
    args: argparse.Namespace,
    scenarios: tuple[SymbolScenario, ...],
) -> dict[str, object]:
    """Evaluate exact find_symbol identity, provenance and source evidence."""

    workspace = args.workspace.expanduser().resolve(strict=True)
    server_env = server_environment(args)
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
        required = symbol_required_tools(scenarios)
        missing_tools = sorted(required - tool_names)
        if missing_tools:
            raise RuntimeError(
                "FetchCore MCP server is missing required tools: "
                + ", ".join(missing_tools)
            )

        protocol_smoke, smoke_ok = await _symbol_smoke(
            session, args, scenarios, required
        )
        protocol_smoke["advertised_tools"] = sorted(tool_names)

        for scenario in scenarios:
            reports.append(
                await _evaluate_symbol_scenario(session, args, scenario)
            )

    summary = symbol_summary(reports)
    return {
        "schema_version": 2,
        "status": (
            "passed"
            if summary["passed"] == summary["total"] and smoke_ok
            else "failed"
        ),
        "repository": args.repository,
        "revision": args.revision,
        "backend": "solr",
        "mode": args.mode,
        "collection": args.collection,
        "protocol_smoke": protocol_smoke,
        "summary": summary,
        "scenarios": reports,
    }


async def _evaluate_symbol_scenario(
    session: ClientSession,
    args: argparse.Namespace,
    scenario: SymbolScenario,
) -> dict[str, object]:
    arguments: dict[str, object] = {
        "name": scenario.name,
        "repository": args.repository,
        "limit": scenario.limit,
    }
    if scenario.kind is not None:
        arguments["kind"] = scenario.kind
    if scenario.path is not None:
        arguments["path"] = scenario.path
    if scenario.revision is not None:
        arguments["revision"] = scenario.revision

    started = time.monotonic_ns()
    result = await session.call_tool("find_symbol", arguments)
    duration_ms = int((time.monotonic_ns() - started) // 1_000_000)

    report: dict[str, object] = {
        "id": scenario.id,
        "action": scenario.action,
        "name": scenario.name,
        "kind_filter": scenario.kind,
        "path_filter": scenario.path,
        "revision_filter": scenario.revision,
        "expected_symbols": [
            expected.to_mapping() for expected in scenario.expected_symbols
        ],
        "matched_symbols": [],
        "missing_symbols": [
            expected.to_mapping() for expected in scenario.expected_symbols
        ],
        "contract_errors": [],
        "coverage_status": None,
        "result_count": 0,
        "limit_reached": None,
        "scan_diagnostics": [],
        "outcome": "failed",
        "passed": False,
        "find_symbol_duration_ms": duration_ms,
        "verify_source": {
            "requested": scenario.verify_source,
            "verified": False,
            "source_ref": None,
            "category": None,
            "reasons": [],
            "get_source_checksum_declared": None,
        },
    }

    error_text = call_result_error(result)
    if error_text is not None:
        report["outcome"] = "tool_error"
        verification = _verify_block(report)
        verification["reasons"] = ["find_symbol returned a tool error"]
        verification["category"] = parse_error_category(error_text)
        return report

    payload = tool_payload(result)
    contract_errors = list(symbol_payload_errors(payload))
    raw_results = payload.get("results")
    raw_list = raw_results if isinstance(raw_results, list) else []
    results = [item for item in raw_list if isinstance(item, dict)]
    effective_revision = scenario.revision or getattr(args, "revision", None)
    for index, item in enumerate(raw_list):
        if not isinstance(item, dict):
            contract_errors.append(f"result {index}: not an object")
            continue
        for message in symbol_provenance_errors(item):
            contract_errors.append(f"result {index}: {message}")
        for message in symbol_scope_errors(
            item, str(args.repository), effective_revision
        ):
            contract_errors.append(f"result {index}: {message}")
    report["contract_errors"] = contract_errors
    report["coverage_status"] = payload.get("status")
    report["result_count"] = len(raw_list)
    limit_reached = payload.get("limit_reached")
    report["limit_reached"] = bool(limit_reached) if isinstance(limit_reached, bool) else None
    raw_diagnostics = payload.get("scan_diagnostics")
    report["scan_diagnostics"] = [
        item for item in raw_diagnostics if isinstance(item, str)
    ] if isinstance(raw_diagnostics, list) else []

    if contract_errors:
        report["outcome"] = "contract_error"
        return report

    matched, missing = match_expected_symbols(results, scenario.expected_symbols)
    report["matched_symbols"] = [
        {"expected": expected.to_mapping(), "result": hit}
        for expected, hit in matched
    ]
    report["missing_symbols"] = [
        expected.to_mapping() for expected in missing
    ]

    if payload.get("status") == "unsupported":
        report["outcome"] = "unsupported"
        _verify_block(report)["reasons"] = [
            "declaration extraction is unsupported in this scope"
        ]
        return report
    if not results and payload.get("status") in {"not_found", "unknown"}:
        report["outcome"] = "not_found"
        return report
    if len(matched) != len(scenario.expected_symbols):
        return report

    report["outcome"] = "passed"
    report["passed"] = True
    if scenario.verify_source:
        verifications = [
            await _verify_symbol_source(session, args, hit)
            for _, hit in matched
        ]
        verified_symbols = sum(
            1 for entry in verifications if entry["verified"]
        )
        report["verify_source"] = {
            "requested": True,
            "verified": verified_symbols == len(verifications),
            "requested_symbols": len(verifications),
            "verified_symbols": verified_symbols,
            "per_symbol": verifications,
        }
        if verified_symbols != len(verifications):
            report["outcome"] = "failed"
            report["passed"] = False
    return report


def _verify_block(report: dict[str, object]) -> dict[str, object]:
    verification = report["verify_source"]
    assert isinstance(verification, dict)
    return verification


async def _verify_symbol_source(
    session: ClientSession,
    args: argparse.Namespace,
    symbol: dict[str, object],
) -> dict[str, object]:
    """Read bounded source through get_source without presuming a checksum field."""

    verification: dict[str, object] = {
        "requested": True,
        "verified": False,
        "symbol": {
            key: symbol.get(key)
            for key in ("name", "qualified_name", "kind", "path", "line_start", "line_end")
        },
        "source_ref": symbol.get("source_ref"),
        "category": None,
        "reasons": [],
        "get_source_checksum_declared": None,
    }
    arguments: dict[str, object] = {
        "source_ref": symbol.get("source_ref"),
        "repository": args.repository,
        "line_start": symbol.get("line_start"),
        "line_end": symbol.get("line_end"),
    }
    revision = symbol.get("revision")
    if isinstance(revision, str) and revision.strip():
        arguments["revision"] = revision
    checksum = symbol.get("checksum")
    if isinstance(checksum, str) and checksum.strip():
        arguments["checksum"] = checksum
    verification["get_source_arguments"] = arguments

    result = await session.call_tool("get_source", arguments)
    error_text = call_result_error(result)
    if error_text is not None:
        verification["reasons"] = ["get_source returned a tool error"]
        verification["category"] = parse_error_category(error_text)
        return verification
    payload = tool_payload(result)
    verification["get_source_checksum_declared"] = "checksum" in payload
    reasons = list(source_read_errors(payload, symbol, str(args.repository)))
    verification["reasons"] = reasons
    verification["verified"] = not reasons
    return verification


def call_result_error(result: Any) -> str | None:
    """Return the error text of a failed MCP tool result, else None."""

    is_error = getattr(result, "is_error", None)
    if is_error is None:
        is_error = getattr(result, "isError", False)
    if not is_error:
        return None
    texts: list[str] = []
    for item in getattr(result, "content", ()) or ():
        if getattr(item, "type", None) == "text":
            text = getattr(item, "text", None)
            if isinstance(text, str):
                texts.append(text)
    return "\n".join(texts) if texts else "tool error without text content"


def parse_error_category(message: str) -> str:
    """Read the FetchCore error category from a tool error message."""

    return message.rsplit(":", 1)[-1].strip() or "unknown"


def symbol_matches_expected(
    result: dict[str, object],
    expected: ExpectedSymbol,
) -> bool:
    """Exact field equality; no scores, similarity or ranking involvement."""

    line_start = result.get("line_start")
    line_end = result.get("line_end")
    return (
        result.get("name") == expected.name
        and result.get("qualified_name") == expected.qualified_name
        and result.get("kind") == expected.kind
        and _normalize_path(result.get("path")) == _normalize_path(expected.path)
        and _is_integer(line_start)
        and int(line_start) == expected.line_start
        and _is_integer(line_end)
        and int(line_end) == expected.line_end
    )


def match_expected_symbols(
    results: list[dict[str, object]],
    expected_symbols: tuple[ExpectedSymbol, ...],
) -> tuple[list[tuple[ExpectedSymbol, dict[str, object]]], list[ExpectedSymbol]]:
    """Pair every expectation with its exact result; unmatched stay explicit."""

    matched: list[tuple[ExpectedSymbol, dict[str, object]]] = []
    missing: list[ExpectedSymbol] = []
    for expected in expected_symbols:
        hit = next(
            (item for item in results if symbol_matches_expected(item, expected)),
            None,
        )
        if hit is None:
            missing.append(expected)
        else:
            matched.append((expected, hit))
    return matched, missing


def symbol_provenance_errors(result: dict[str, object]) -> tuple[str, ...]:
    """Require repository, revision, source_ref, checksum and a valid span."""

    errors: list[str] = []
    for field in ("repository", "revision", "source_ref", "checksum"):
        value = result.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"missing {field}")
    line_start = result.get("line_start")
    line_end = result.get("line_end")
    start_valid = _is_integer(line_start) and int(line_start) >= 1
    if not start_valid:
        errors.append("invalid line_start")
    if (
        not _is_integer(line_end)
        or not start_valid
        or not _is_integer(line_start)
        or int(line_end) < int(line_start)
    ):
        errors.append("invalid line_end")
    return tuple(errors)


def symbol_scope_errors(
    result: dict[str, object],
    repository: str,
    revision: str | None,
) -> tuple[str, ...]:
    """Validate each result against the binding and the requested revision."""

    errors: list[str] = []
    if result.get("repository") != repository:
        errors.append("unexpected repository")
    if revision is not None and result.get("revision") != revision:
        errors.append("unexpected revision")
    return tuple(errors)


def symbol_payload_errors(payload: dict[str, object]) -> tuple[str, ...]:
    """Contract errors of a find_symbol response envelope."""

    errors: list[str] = []
    if payload.get("status") not in NAVIGATION_STATUSES:
        errors.append("invalid navigation status")
    if not isinstance(payload.get("results"), list):
        errors.append("invalid results collection")
    return tuple(errors)


def source_read_errors(
    payload: dict[str, object],
    symbol: dict[str, object],
    repository: str,
) -> tuple[str, ...]:
    """Validate get_source metadata, line range and content against the symbol."""

    errors: list[str] = []
    if payload.get("repository") != repository:
        errors.append("get_source repository mismatch")
    if payload.get("source_ref") != symbol.get("source_ref"):
        errors.append("get_source source_ref mismatch")
    returned_revision = payload.get("revision")
    if returned_revision is not None and returned_revision != symbol.get("revision"):
        errors.append("get_source revision mismatch")
    path = payload.get("path")
    if not isinstance(path, str) or _normalize_path(path) != _normalize_path(
        symbol.get("path")
    ):
        errors.append("get_source path mismatch")
    line_start = symbol.get("line_start")
    expected_end = symbol.get("line_end")
    if payload.get("line_start") != line_start:
        errors.append("get_source line_start mismatch")
    returned_end = payload.get("line_end")
    if (
        not _is_integer(returned_end)
        or not _is_integer(line_start)
        or not _is_integer(expected_end)
        or int(returned_end) != int(expected_end)
    ):
        errors.append("get_source line_end mismatch")
    if payload.get("truncated"):
        errors.append("get_source returned a truncated window")
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        errors.append("get_source returned empty content")
        return tuple(errors)
    returned_start = payload.get("line_start")
    if (
        not payload.get("truncated")
        and _is_integer(returned_start)
        and _is_integer(returned_end)
    ):
        observed_lines = len(content.splitlines())
        expected_lines = int(returned_end) - int(returned_start) + 1
        if observed_lines != expected_lines:
            errors.append("get_source content does not cover the returned line range")
    name = symbol.get("name")
    if isinstance(name, str) and name and name not in content:
        errors.append("get_source content does not contain the symbol name")
    return tuple(errors)


def symbol_summary(reports: list[dict[str, object]]) -> dict[str, object]:
    """Structural find_symbol metrics; never mixed with search MRR."""

    total = len(reports)
    passed = sum(1 for item in reports if item.get("outcome") == "passed")
    unsupported = sum(1 for item in reports if item.get("outcome") == "unsupported")
    not_found = sum(1 for item in reports if item.get("outcome") == "not_found")
    tool_errors = sum(1 for item in reports if item.get("outcome") == "tool_error")
    contract_errors = sum(
        1 for item in reports if item.get("outcome") == "contract_error"
    )
    failed = total - passed - unsupported
    expected_symbols = sum(
        len(paths)
        for item in reports
        if isinstance((paths := item.get("expected_symbols")), list)
    )
    matched_symbols = sum(
        len(entries)
        for item in reports
        if isinstance((entries := item.get("matched_symbols")), list)
    )
    provenance_valid = 0
    for item in reports:
        entries = item.get("matched_symbols")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            assert isinstance(entry, dict)
            result = entry.get("result")
            if isinstance(result, dict) and not symbol_provenance_errors(result):
                provenance_valid += 1
    durations = [
        int(value)
        for item in reports
        if _is_integer(value := item.get("find_symbol_duration_ms"))
    ]
    source_requested = 0
    source_verified = 0
    source_symbols_requested = 0
    source_symbols_passed = 0
    for item in reports:
        verification = item.get("verify_source")
        if not isinstance(verification, dict) or not verification.get("requested"):
            continue
        source_requested += 1
        if verification.get("verified"):
            source_verified += 1
        symbols = verification.get("per_symbol")
        if isinstance(symbols, list):
            source_symbols_requested += len(symbols)
            source_symbols_passed += sum(
                1 for entry in symbols
                if isinstance(entry, dict) and entry.get("verified")
            )
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "unsupported": unsupported,
        "not_found": not_found,
        "tool_errors": tool_errors,
        "contract_errors": contract_errors,
        "expected_symbols": expected_symbols,
        "matched_symbols": matched_symbols,
        "symbol_match_rate": (
            0.0 if not expected_symbols else matched_symbols / expected_symbols
        ),
        "provenance_valid_rate": (
            0.0 if not matched_symbols else provenance_valid / matched_symbols
        ),
        "verify_source_requested": source_requested,
        "source_verified": source_verified,
        "verify_source_symbols_requested": source_symbols_requested,
        "verify_source_symbols_passed": source_symbols_passed,
        "find_symbol_duration_ms_total": sum(durations),
        "find_symbol_duration_ms_p50": _percentile(durations, 50),
        "find_symbol_duration_ms_p95": _percentile(durations, 95),
    }


def _is_integer(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _normalize_path(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("\\", "/").strip().lstrip("./")


def _percentile(values: list[int], percentile: int) -> float | None:
    if not values:
        return None
    if percentile == 50:
        return float(statistics.median(values))
    ordered = sorted(values)
    rank = max(1, (percentile * len(ordered) + 99) // 100)
    return float(ordered[rank - 1])

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
        f"- Retrieval mode: `{payload['mode']}`",
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


def markdown_symbol_report(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    assert isinstance(summary, dict)
    lines = [
        "# FetchCore find_symbol structural evaluation",
        "",
        f"- Status: **{payload['status']}**",
        f"- Repository: `{payload['repository']}`",
        f"- Revision: `{payload.get('revision') or 'unknown'}`",
        f"- Backend: `{payload['backend']}`",
        f"- Retrieval mode: `{payload['mode']}`",
        f"- Collection: `{payload['collection']}`",
        "- MCP protocol smoke: **PASS**",
        (
            f"- Scenarios: {summary['passed']}/{summary['total']} passed "
            f"({summary['unsupported']} unsupported)"
        ),
        f"- Symbol match rate: {float(summary['symbol_match_rate']):.4f}",
        f"- Provenance validity: {float(summary['provenance_valid_rate']):.4f}",
        "",
        "| Scenario | Outcome | Coverage status | Matched | Source verified |",
        "| --- | --- | --- | ---: | --- |",
    ]
    scenarios = payload["scenarios"]
    assert isinstance(scenarios, list)
    for item in scenarios:
        assert isinstance(item, dict)
        verification = item.get("verify_source")
        assert isinstance(verification, dict)
        if not verification.get("requested"):
            source = "-"
        elif verification.get("verified"):
            source = "yes"
        else:
            failed = next(
                (
                    entry
                    for entry in verification.get("per_symbol", [])
                    if isinstance(entry, dict) and not entry.get("verified")
                ),
                None,
            )
            if failed is not None:
                reason = failed.get("category") or "; ".join(
                    str(item) for item in failed.get("reasons", [])
                )
            else:
                reason = verification.get("category") or "; ".join(
                    str(item) for item in verification.get("reasons", [])
                )
            source = f"no ({reason})"
        matched = item.get("matched_symbols")
        expected = item.get("expected_symbols")
        matched_count = len(matched) if isinstance(matched, list) else 0
        expected_count = len(expected) if isinstance(expected, list) else 0
        lines.append(
            "| {id} | {outcome} | {status} | {matched} | {source} |".format(
                id=item["id"],
                outcome=item.get("outcome"),
                status=item.get("coverage_status") or "-",
                matched=f"{matched_count}/{expected_count}",
                source=source,
            )
        )
    lines.append("")
    return "\n".join(lines)


def _required_text(raw: dict[object, object], key: str, index: int) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"scenario {index} requires non-empty {key!r}")
    return value.strip()


def _evaluation_exit_code(payload: dict[str, object], enforce_scenario_gate: bool) -> int:
    if enforce_scenario_gate and payload.get("status") != "passed":
        return 1
    return 0


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
    report = (
        markdown_symbol_report(payload)
        if payload.get("schema_version") == 2
        else markdown_report(payload)
    )
    args.output_markdown.write_text(report, encoding="utf-8")

    print(json.dumps(payload["summary"], sort_keys=True))
    if args.report_only:
        return 0
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
