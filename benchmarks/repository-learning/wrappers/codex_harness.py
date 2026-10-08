#!/usr/bin/env python3
"""Codex CLI wrapper for the Repository Learning Benchmark.

The wrapper keeps agent lifecycle outside FetchCore. It exposes either a
read-only repository shell, FetchCore's public MCP tools, or no tools, then
normalizes Codex JSONL events into the benchmark's single-JSON contract.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from typing import Any


STUDY_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["success"]},
        "memory_pages": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["path", "body"],
                "additionalProperties": False,
            },
        },
        "metrics": {
            "type": "object",
            "properties": {
                key: {"type": "integer", "minimum": 0}
                for key in (
                    "tool_calls",
                    "files_read",
                    "evidence_items",
                    "returned_chars",
                    "input_tokens",
                    "output_tokens",
                )
            },
            "required": [
                "tool_calls",
                "files_read",
                "evidence_items",
                "returned_chars",
                "input_tokens",
                "output_tokens",
            ],
            "additionalProperties": False,
        },
        "tool_events_verified": {"type": "boolean"},
        "tool_events": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "status",
        "memory_pages",
        "metrics",
        "tool_events_verified",
        "tool_events",
    ],
    "additionalProperties": False,
}

EVALUATION_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_id": {"type": "string"},
                    "answer": {"type": "string"},
                },
                "required": ["question_id", "answer"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["answers"],
    "additionalProperties": False,
}

_DIRECT_COMMANDS = {
    "find",
    "git",
    "head",
    "ls",
    "pwd",
    "rg",
    "sed",
    "sort",
    "tail",
    "uniq",
    "wc",
}
_GIT_READ_COMMANDS = {
    "branch",
    "diff",
    "grep",
    "log",
    "ls-files",
    "rev-parse",
    "show",
    "status",
}
_TOOL_ITEM_TYPES = {
    "command_execution",
    "file_change",
    "mcp_tool_call",
    "web_search",
}


class HarnessError(RuntimeError):
    """Raised when Codex or its observed tool trace violates the arm contract."""


def _json_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _events(stdout: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise HarnessError("Codex --json emitted a non-JSON line") from exc
        if not isinstance(value, dict):
            raise HarnessError("Codex --json event must be an object")
        result.append(value)
    return result


def _tool_items(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") in _TOOL_ITEM_TYPES:
            items.append(item)
    return items


def _command_segments(command: str) -> list[list[str]]:
    try:
        outer = shlex.split(command)
    except ValueError as exc:
        raise HarnessError(f"cannot audit DIRECT command: {command!r}") from exc
    if outer and Path(outer[0]).name in {"bash", "sh"} and "-lc" in outer:
        index = outer.index("-lc")
        if len(outer) <= index + 1:
            raise HarnessError(f"cannot audit DIRECT shell command: {command!r}")
        return _command_segments(outer[index + 1])

    lexer = shlex.shlex(command, posix=True, punctuation_chars="|;&")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        shell_tokens = list(lexer)
    except ValueError as exc:
        raise HarnessError(f"cannot audit DIRECT command: {command!r}") from exc
    segments: list[list[str]] = []
    current: list[str] = []
    for token in shell_tokens:
        if token in {"|", "||", "&&", ";"}:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    for tokens in segments:
        while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
            tokens.pop(0)
    segments = [tokens for tokens in segments if tokens]
    return segments


def _audit_direct_commands(items: list[dict[str, Any]]) -> None:
    for item in items:
        if item.get("type") != "command_execution":
            raise HarnessError(f"DIRECT used forbidden tool type {item.get('type')!r}")
        command = str(item.get("command", ""))
        for tokens in _command_segments(command):
            executable = Path(tokens[0]).name
            if executable not in _DIRECT_COMMANDS:
                raise HarnessError(f"DIRECT used forbidden command {executable!r}")
            if executable == "git":
                subcommand = next((token for token in tokens[1:] if not token.startswith("-")), "")
                if subcommand not in _GIT_READ_COMMANDS:
                    raise HarnessError(f"DIRECT used forbidden git subcommand {subcommand!r}")


def _mcp_identity(item: dict[str, Any]) -> tuple[str, str]:
    server = str(item.get("server", item.get("server_name", "")))
    tool = str(item.get("tool", item.get("tool_name", item.get("name", ""))))
    return server, tool


def _audit_fetchcore_tools(items: list[dict[str, Any]]) -> None:
    for item in items:
        if item.get("type") != "mcp_tool_call":
            raise HarnessError(f"FETCHCORE used forbidden tool type {item.get('type')!r}")
        server, tool = _mcp_identity(item)
        if server != "fetchcore" or tool not in {"search", "get_source"}:
            raise HarnessError(f"FETCHCORE used forbidden MCP tool {server}/{tool}")
        if item.get("status") not in {None, "completed"} or item.get("error"):
            raise HarnessError(
                f"FetchCore MCP tool {tool!r} failed: "
                f"status={item.get('status')!r} error={item.get('error')!r} "
                f"result={_json_text(item.get('result'))[:1000]!r}"
            )
        if item.get("result") is None:
            raise HarnessError(f"FetchCore MCP tool {tool!r} returned no result")


def _walk(value: object) -> list[object]:
    values = [value]
    if isinstance(value, dict):
        for item in value.values():
            values.extend(_walk(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(_walk(item))
    elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            pass
        else:
            values.extend(_walk(decoded))
    return values


def _source_refs(item: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for value in _walk(item.get("result", {})):
        if isinstance(value, dict):
            ref = value.get("source_ref")
            if isinstance(ref, str):
                refs.add(ref)
    arguments = item.get("arguments")
    if isinstance(arguments, dict) and isinstance(arguments.get("source_ref"), str):
        refs.add(str(arguments["source_ref"]))
    return refs


def _direct_paths(items: list[dict[str, Any]], workspace: Path) -> set[str]:
    paths: set[str] = set()

    def admit(candidate: str) -> None:
        cleaned = candidate.strip("'\"(),:[]")
        if not cleaned or cleaned.startswith("-") or len(cleaned) > 512:
            return
        path = Path(cleaned)
        resolved = path if path.is_absolute() else workspace / path
        try:
            canonical = resolved.resolve()
            relative = canonical.relative_to(workspace.resolve())
        except (OSError, ValueError):
            return
        try:
            is_file = canonical.is_file()
        except OSError:
            return
        if is_file and ".git" not in relative.parts:
            paths.add(relative.as_posix())

    for item in items:
        command = str(item.get("command", ""))
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        for token in tokens:
            admit(token)
        output = str(item.get("aggregated_output", item.get("output", "")))
        for line in output.splitlines():
            candidate = line.split(":", 1)[0]
            if "/" in candidate or "." in Path(candidate).name:
                admit(candidate)
    return paths


def _returned_chars(items: list[dict[str, Any]]) -> int:
    total = 0
    for item in items:
        if item.get("type") == "command_execution":
            total += len(str(item.get("aggregated_output", item.get("output", ""))))
        elif item.get("type") == "mcp_tool_call":
            total += _mcp_evidence_chars(item)
    return total


def _mcp_evidence_chars(item: dict[str, Any]) -> int:
    """Return FetchCore evidence characters, excluding diagnostic envelope overhead."""
    for value in _walk(item.get("result", {})):
        if not isinstance(value, dict):
            continue
        telemetry = value.get("telemetry")
        if isinstance(telemetry, dict):
            returned = telemetry.get("returned_chars")
            if isinstance(returned, int) and not isinstance(returned, bool):
                return max(0, returned)
        if isinstance(value.get("source_ref"), str) and isinstance(value.get("content"), str):
            return len(str(value["content"]))
    return len(_json_text(item.get("result", {})))


def _usage(events: list[dict[str, Any]]) -> tuple[int, int]:
    input_tokens = 0
    output_tokens = 0
    for event in events:
        if event.get("type") != "turn.completed":
            continue
        usage = event.get("usage")
        if isinstance(usage, dict):
            input_tokens += int(usage.get("input_tokens", 0) or 0)
            output_tokens += int(usage.get("output_tokens", 0) or 0)
    return input_tokens, output_tokens


def _tool_summaries(items: list[dict[str, Any]]) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for item in items:
        item_type = str(item.get("type", ""))
        summary: dict[str, object] = {"type": item_type}
        if item_type == "command_execution":
            summary["command"] = str(item.get("command", ""))
            summary["exit_code"] = item.get("exit_code")
        elif item_type == "mcp_tool_call":
            server, tool = _mcp_identity(item)
            summary.update(
                {
                    "server": server,
                    "tool": tool,
                    "arguments": item.get("arguments", {}),
                    "source_refs": sorted(_source_refs(item)),
                    "status": item.get("status"),
                    "error": item.get("error"),
                }
            )
        summary["returned_chars"] = _returned_chars([item])
        summaries.append(summary)
    return summaries


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _fetchcore_mcp_config(args: argparse.Namespace, workspace: Path) -> str:
    root = Path(__file__).resolve().parents[3]
    python = root / ".venv" / "bin" / "python"
    server = Path(__file__).resolve().with_name("fetchcore_mcp.py")
    if not python.is_file():
        raise HarnessError(f"FetchCore MCP interpreter is missing: {python}")
    settings = json.dumps(
        {"url": args.solr_url, "collection": args.solr_collection}, separators=(",", ":")
    )
    env = {
        "FETCHCORE_WORKSPACE": str(workspace),
        "FETCHCORE_REPOSITORY": args.repository,
        "FETCHCORE_REVISION": args.revision,
        "FETCHCORE_BACKEND": "solr",
        "FETCHCORE_MCP_BACKEND_SETTINGS": settings,
    }
    if args.mode == "fetchcore-bounded":
        env["FETCHCORE_RLB_MAX_TOOL_CALLS"] = str(args.max_tool_calls)
        env["FETCHCORE_RLB_MAX_RETURNED_CHARS"] = str(args.max_returned_chars)
    env_table = ", ".join(f"{key} = {_toml_string(value)}" for key, value in env.items())
    output_token_limit = 4000
    tool_config = (
        "{ search = { approval_mode = \"approve\", "
        f"output_token_limit = {output_token_limit} }}, "
        "get_source = { approval_mode = \"approve\", "
        f"output_token_limit = {output_token_limit} }} }}"
    )
    return (
        "{ command = "
        f"{_toml_string(str(python))}, args = [{_toml_string(str(server))}], "
        f"env = {{ {env_table} }}, required = true, "
        'default_tools_approval_mode = "approve", '
        'enabled_tools = ["search", "get_source"], '
        f"tools = {tool_config} }}"
    )


def _codex_command(
    args: argparse.Namespace,
    *,
    workdir: Path,
    schema_path: Path,
    output_path: Path,
    subject: Path,
) -> list[str]:
    command = [
        args.codex,
        "exec",
        "--model",
        args.model,
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--json",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
        "--sandbox",
        "read-only",
        "--cd",
        str(workdir),
        "--disable",
        "multi_agent",
        "--disable",
        "multi_agent_v2",
        "--disable",
        "apps",
        "--disable",
        "browser_use",
        "--disable",
        "computer_use",
        "--disable",
        "image_generation",
        "--config",
        'web_search="disabled"',
        "--config",
        "project_doc_max_bytes=0" if args.mode != "direct" else "project_doc_max_bytes=32768",
    ]
    if args.mode == "direct":
        command.extend(["--disable", "plugins"])
    elif args.mode in {"fetchcore", "fetchcore-bounded"}:
        command.extend(
            [
                "--disable",
                "shell_tool",
                "--config",
                f"mcp_servers.fetchcore={_fetchcore_mcp_config(args, subject)}",
            ]
        )
    else:
        command.extend(["--disable", "shell_tool", "--disable", "plugins"])
    command.append("-")
    return command


def run(args: argparse.Namespace) -> dict[str, object]:
    prompt = sys.stdin.read()
    if not prompt.strip():
        raise HarnessError("benchmark prompt is empty")
    subject = Path.cwd().resolve()
    schema = EVALUATION_SCHEMA if args.mode == "evaluate" else STUDY_SCHEMA
    with tempfile.TemporaryDirectory(prefix=f"fetchcore-rlb-codex-{args.mode}-") as temp:
        temp_path = Path(temp)
        schema_path = temp_path / "schema.json"
        output_path = temp_path / "last-message.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        workdir = subject if args.mode == "direct" else temp_path / "workspace"
        workdir.mkdir(exist_ok=True)
        command = _codex_command(
            args,
            workdir=workdir,
            schema_path=schema_path,
            output_path=output_path,
            subject=subject,
        )
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=args.timeout_seconds,
            check=False,
            env={**os.environ, "NO_COLOR": "1"},
        )
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, end="")
        if completed.returncode != 0:
            if completed.stdout:
                print(completed.stdout[-4000:], file=sys.stderr, end="")
            raise HarnessError(f"Codex exited {completed.returncode}")
        events = _events(completed.stdout)
        if not output_path.is_file():
            raise HarnessError("Codex did not write its final response")
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HarnessError("Codex final response is not JSON") from exc
        if not isinstance(payload, dict):
            raise HarnessError("Codex final response must be an object")

    items = _tool_items(events)
    if args.mode == "direct":
        _audit_direct_commands(items)
    elif args.mode in {"fetchcore", "fetchcore-bounded"}:
        _audit_fetchcore_tools(items)
    elif items:
        raise HarnessError("evaluator used a tool")

    if args.mode == "evaluate":
        return {str(key): value for key, value in payload.items()}

    returned_chars = _returned_chars(items)
    input_tokens, output_tokens = _usage(events)
    if args.mode == "direct":
        paths = _direct_paths(items, subject)
        files_read = len(paths)
        evidence_items = len(paths)
    else:
        refs = set().union(*(_source_refs(item) for item in items)) if items else set()
        files_read = len(
            {
                ref
                for item in items
                if _mcp_identity(item)[1] == "get_source"
                for ref in _source_refs(item)
            }
        )
        evidence_items = len(refs)

    if args.mode == "fetchcore-bounded":
        if len(items) > args.max_tool_calls:
            raise HarnessError(
                f"bounded arm exceeded max_tool_calls: {len(items)} > {args.max_tool_calls}"
            )
        if returned_chars > args.max_returned_chars:
            raise HarnessError(
                "bounded arm exceeded max_returned_chars: "
                f"{returned_chars} > {args.max_returned_chars}"
            )

    payload["metrics"] = {
        "tool_calls": len(items),
        "files_read": files_read,
        "evidence_items": evidence_items,
        "returned_chars": returned_chars,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    payload["tool_events_verified"] = True
    payload["tool_events"] = _tool_summaries(items)
    return {str(key): value for key, value in payload.items()}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=("direct", "fetchcore", "fetchcore-bounded", "evaluate")
    )
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--model", required=True)
    parser.add_argument("--repository", default="oalangomes/fetchcore")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--solr-url", default="http://127.0.0.1:8983/solr")
    parser.add_argument("--solr-collection", default="fetchcore-knowledge")
    parser.add_argument("--max-tool-calls", type=int, default=24)
    parser.add_argument("--max-returned-chars", type=int, default=20000)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args(argv)
    if args.max_tool_calls < 0 or args.max_returned_chars < 0:
        parser.error("budgets must be non-negative")
    return args


def main(argv: list[str] | None = None) -> int:
    try:
        payload = run(parse_args(argv))
    except (HarnessError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"codex repository-learning wrapper failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
