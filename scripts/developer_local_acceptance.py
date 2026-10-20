"""Opt-in installed-artifact Developer Local journey on an operator-owned Solr core.

The script never provisions or removes a backend resource. It refuses resource
names outside its P0.1 namespace and writes only under a new work directory.
Run it once against the baseline artifact, freeze the report, then repeat with
the changed artifact and a different empty owned Solr core.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


REPOSITORY = "acme/fetchcore-p01-fixture"
QUERY = "fetchcore_widget_blueprint"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--mcp", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--solr-url", required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--ack-owned-resource", action="store_true")
    return parser.parse_args()


def _preflight(args: argparse.Namespace) -> None:
    if not args.ack_owned_resource:
        raise ValueError("--ack-owned-resource is required before backend writes")
    if not args.collection.startswith("fetchcore-p01-codex-"):
        raise ValueError("collection must use the isolated fetchcore-p01-codex- namespace")
    parsed = urlparse(args.solr_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("this local acceptance harness requires a loopback Solr URL")
    if args.work_dir.exists():
        raise ValueError("work directory must be new; existing data is never overwritten")
    if not args.cli.is_file() or not args.mcp.is_file():
        raise ValueError("installed CLI and MCP entry points must exist")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _fixture(root: Path) -> tuple[Path, str]:
    workspace = root / "fixture"
    source = workspace / "src" / "widgets.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "def fetchcore_widget_blueprint():\n"
        "    return 'original widget evidence'\n",
        encoding="utf-8",
    )
    (workspace / "README.md").write_text("Original fixture documentation.\n", encoding="utf-8")
    _git(workspace, "init", "-q")
    _git(workspace, "config", "user.name", "FetchCore Acceptance")
    _git(workspace, "config", "user.email", "acceptance@example.test")
    _git(workspace, "remote", "add", "origin", "https://example.test/acme/fetchcore-p01-fixture.git")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "initial fixture")
    return workspace, _git(workspace, "rev-parse", "HEAD")


def _write_config(root: Path, workspace: Path, revision: str, args: argparse.Namespace) -> Path:
    path = root / "runtime.yaml"
    payload = {
        "version": 1,
        "runtime": {
            "backend": "solr", "workspace": str(workspace),
            "repository": REPOSITORY, "revision": revision,
        },
        "backends": {"solr": {"url": args.solr_url, "collection": args.collection}},
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    return path


def _json_command(
    report: dict[str, Any], label: str, argv: list[str], env: dict[str, str],
    *, expected_exit: int = 0,
) -> dict[str, Any]:
    started = time.perf_counter_ns()
    result = subprocess.run(argv, cwd="/tmp", env=env, capture_output=True, text=True,
                            timeout=120, check=False)
    elapsed_ms = round((time.perf_counter_ns() - started) / 1_000_000, 3)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label}: output is not JSON (exit {result.returncode})") from exc
    report["steps"].append({
        "label": label, "argv": argv, "exit_code": result.returncode,
        "wall_ms": elapsed_ms, "result": payload,
    })
    if result.returncode != expected_exit:
        raise RuntimeError(f"{label}: exit {result.returncode}, expected {expected_exit}")
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label}: expected a JSON object")
    return payload


def _result(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("result")
    if payload.get("status") != "succeeded" or not isinstance(value, dict):
        raise RuntimeError(f"operation failed: {payload.get('error')}")
    return value


def _tool_payload(value: Any) -> dict[str, Any]:
    structured = getattr(value, "structuredContent", None)
    if structured is None:
        structured = getattr(value, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    for item in getattr(value, "content", ()):
        if getattr(item, "type", None) == "text":
            try:
                payload = json.loads(item.text)
            except (AttributeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                return payload
    raise RuntimeError("MCP tool returned no JSON object")


async def _mcp_roundtrip(
    report: dict[str, Any], executable: Path, config: Path, source_ref: str,
    revision: str,
) -> None:
    started = time.perf_counter_ns()
    params = StdioServerParameters(
        command=str(executable), cwd="/tmp", env={"FETCHCORE_CONFIG": str(config)},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        startup_ms = round((time.perf_counter_ns() - started) / 1_000_000, 3)
        tools = await session.list_tools()
        if not {"search", "get_source"} <= {tool.name for tool in tools.tools}:
            raise RuntimeError("MCP search/get_source tools are absent")
        search_started = time.perf_counter_ns()
        search = _tool_payload(await session.call_tool(
            "search", {"query": QUERY, "repository": REPOSITORY, "limit": 5},
        ))
        search_ms = round((time.perf_counter_ns() - search_started) / 1_000_000, 3)
        results = search.get("results")
        if not isinstance(results, list) or not results:
            raise RuntimeError("MCP search returned no evidence")
        if not any(isinstance(item, dict) and item.get("source_ref") == source_ref
                   for item in results):
            raise RuntimeError("MCP and CLI evidence references differ")
        source = _tool_payload(await session.call_tool("get_source", {
            "source_ref": source_ref, "repository": REPOSITORY, "revision": revision,
        }))
        if "fetchcore_widget_blueprint" not in str(source.get("content", "")):
            raise RuntimeError("MCP source content disagrees with fixture")
        report["mcp"].append({
            "startup_ms": startup_ms, "search_ms": search_ms,
            "source_ref": source_ref, "revision": source.get("revision"),
        })


def run(args: argparse.Namespace) -> dict[str, Any]:
    _preflight(args)
    root = args.work_dir.resolve()
    root.mkdir(parents=True)
    workspace, revision = _fixture(root)
    config = _write_config(root, workspace, revision, args)
    cache = root / "cache"
    env = dict(os.environ)
    env.update({"FETCHCORE_CONFIG": str(config), "FETCHCORE_REPOSITORY_CACHE": str(cache)})
    cli = str(args.cli.resolve())
    report: dict[str, Any] = {
        "schema_version": 1, "status": "running", "repository": REPOSITORY,
        "revision": revision, "solr_url": args.solr_url, "collection": args.collection,
        "installed_cli": cli, "installed_mcp": str(args.mcp.resolve()),
        "steps": [], "mcp": [], "metrics": {},
    }
    started = time.perf_counter_ns()

    def call(label: str, *words: str) -> dict[str, Any]:
        return _result(_json_command(report, label, [cli, *words], env))

    try:
        call("config", "config", "show", "--json")
        call("doctor_before", "doctor", "--json")
        call("register", "repo", "--allowed-root", str(workspace), "add",
             str(workspace), "--json")
        index = ["index", "--config", str(config), "--cache-root", str(cache),
                 "--allowed-root", str(workspace)]
        plan = call("initial_plan", *index, "plan", REPOSITORY, "--json")
        if plan.get("mode") != "initial":
            raise RuntimeError("test resource is not empty; initial plan was not initial")
        call("rebuild", *index, "rebuild", REPOSITORY, "--json")
        call("status", *index, "status", REPOSITORY, "--json")
        readiness = call("readiness", "readiness", "--json")
        if not readiness.get("ready"):
            raise RuntimeError("snapshot did not become ready")
        search = call("search", "search", QUERY, "--repo", REPOSITORY,
                      "--limit", "5", "--allowed-root", str(workspace), "--json")
        results = search.get("results")
        if not isinstance(results, list) or not results:
            raise RuntimeError("CLI search returned no evidence")
        evidence = next((item for item in results if isinstance(item, dict)
                         and item.get("source") == "src/widgets.py"), None)
        if evidence is None or not isinstance(evidence.get("source_ref"), str):
            raise RuntimeError("fixture source was not returned")
        source_ref = evidence["source_ref"]
        if evidence.get("revision") != revision:
            raise RuntimeError("evidence revision does not match fixture commit")
        report["metrics"]["commands_to_first_evidence"] = len(report["steps"])
        report["metrics"]["time_to_first_evidence_ms"] = round(
            (time.perf_counter_ns() - started) / 1_000_000, 3)
        source = call("source", "source", source_ref, "--repo", REPOSITORY,
                      "--revision", revision, "--allowed-root", str(workspace), "--json")
        if QUERY not in str(source.get("content", "")):
            raise RuntimeError("CLI source does not contain the queried symbol")
        report["metrics"]["time_search_to_source_ms"] = (
            report["steps"][-1]["wall_ms"]
        )
        asyncio.run(_mcp_roundtrip(report, args.mcp, config, source_ref, revision))
        call("search_after_restart", "search", QUERY, "--repo", REPOSITORY,
             "--limit", "5", "--allowed-root", str(workspace), "--json")
        asyncio.run(_mcp_roundtrip(report, args.mcp, config, source_ref, revision))
        (workspace / "src" / "widgets.py").write_text(
            "def fetchcore_widget_blueprint():\n"
            "    return 'updated widget evidence'\n", encoding="utf-8",
        )
        (workspace / "src" / "new_widget.py").write_text(
            "def newly_added_widget():\n    return 2\n", encoding="utf-8",
        )
        (workspace / "README.md").unlink()
        _git(workspace, "add", "-A")
        _git(workspace, "commit", "-qm", "controlled add update delete")
        updated_revision = _git(workspace, "rev-parse", "HEAD")
        _write_config(root, workspace, updated_revision, args)
        updated_plan = call("delta_plan", *index, "plan", REPOSITORY, "--json")
        if updated_plan.get("mode") != "incremental":
            raise RuntimeError("fixture change was not planned as incremental")
        sync = call("sync", *index, "sync", REPOSITORY, "--json")
        if sync.get("mode") != "incremental":
            raise RuntimeError("fixture change was not synchronized incrementally")
        noop = call("noop_sync", *index, "sync", REPOSITORY, "--json")
        if noop.get("mode") != "noop":
            raise RuntimeError("second sync was not a no-op")
        report["updated_revision"] = updated_revision
        report["status"] = "passed"
    except Exception as exc:  # noqa: BLE001 - preserve partial evidence on any E2E failure
        report["status"] = "failed"
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
    return report


def main() -> int:
    args = parse_args()
    try:
        report = run(args)
    except (OSError, ValueError) as exc:
        report = {"schema_version": 1, "status": "blocked", "reason": str(exc)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(args.output)}, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
