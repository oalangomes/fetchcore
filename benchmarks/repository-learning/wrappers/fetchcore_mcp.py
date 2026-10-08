#!/usr/bin/env python3
"""Budget-aware benchmark MCP adapter over FetchCore's application service.

This adapter owns only experiment-facing output shaping and acquisition budgets.
It delegates search/source semantics to RetrievalApplicationService and never
talks to a backend directly.
"""

from __future__ import annotations

import os

from fetchcore.application import SearchRequest, SourceReadRequest
from fetchcore.retrieval.contracts import SearchIntent
from fetchcore.serving.mcp import service_from_environment
from mcp.server import MCPServer


def _optional_budget(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    value = int(raw)
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


class AcquisitionBudget:
    def __init__(self, max_calls: int | None, max_chars: int | None):
        self.max_calls = max_calls
        self.max_chars = max_chars
        self.calls = 0
        self.chars = 0

    def start_call(self) -> bool:
        if self.max_calls is not None and self.calls >= self.max_calls:
            return False
        self.calls += 1
        return True

    @property
    def remaining_chars(self) -> int | None:
        if self.max_chars is None:
            return None
        return max(0, self.max_chars - self.chars)

    def admit(self, chars: int) -> bool:
        remaining = self.remaining_chars
        if remaining is not None and chars > remaining:
            return False
        self.chars += chars
        return True

    def mapping(self, *, exhausted: bool = False) -> dict[str, object]:
        return {
            "max_tool_calls": self.max_calls,
            "max_returned_chars": self.max_chars,
            "tool_calls_used": self.calls,
            "returned_chars_used": self.chars,
            "remaining_tool_calls": (
                None if self.max_calls is None else max(0, self.max_calls - self.calls)
            ),
            "remaining_returned_chars": self.remaining_chars,
            "exhausted": exhausted,
        }


def _budget_exhausted(budget: AcquisitionBudget, reason: str) -> dict[str, object]:
    return {
        "status": "budget_exhausted",
        "reason": reason,
        "results": [],
        "telemetry": {"returned_chars": 0},
        "budget": budget.mapping(exhausted=True),
    }


def _result_view(raw: dict[str, object]) -> dict[str, object]:
    fields = (
        "id",
        "source",
        "source_ref",
        "knowledge_type",
        "content",
        "score",
        "query_alignment_score",
        "line_start",
        "line_end",
        "revision",
        "checksum",
        "trust_level",
        "source_type",
    )
    return {field: raw[field] for field in fields if field in raw}


service = service_from_environment()
budget = AcquisitionBudget(
    _optional_budget("FETCHCORE_RLB_MAX_TOOL_CALLS"),
    _optional_budget("FETCHCORE_RLB_MAX_RETURNED_CHARS"),
)
mcp = MCPServer(
    "FetchCore",
    instructions=(
        "Search one authorized repository and read bounded source through FetchCore. "
        "Budget metadata is experimental acquisition policy, not retrieval authority."
    ),
)


@mcp.tool(name="search")
def search_tool(
    query: str = "",
    repository: str | None = None,
    limit: int = 10,
    knowledge_types: list[str] | None = None,
    intent: dict[str, object] | None = None,
) -> dict[str, object]:
    """Search repository knowledge and return a provenance-preserving evidence view."""
    if not budget.start_call():
        return _budget_exhausted(budget, "max_tool_calls")
    response = service.search(
        SearchRequest(
            query=query,
            repository=repository,
            limit=limit,
            knowledge_types=tuple(knowledge_types or ()),
            intent=SearchIntent.from_mapping(intent) if intent is not None else None,
        )
    ).to_mapping()
    selected: list[dict[str, object]] = []
    returned_chars = 0
    for item in response.get("results", []):
        if not isinstance(item, dict):
            continue
        view = _result_view({str(key): value for key, value in item.items()})
        content = view.get("content")
        chars = len(content) if isinstance(content, str) else 0
        if not budget.admit(chars):
            break
        selected.append(view)
        returned_chars += chars
    exhausted = len(selected) < len(response.get("results", []))
    return {
        "backend": response.get("backend"),
        "status": response.get("status"),
        "results": selected,
        "metadata": {
            "repository": response.get("metadata", {}).get("repository")
            if isinstance(response.get("metadata"), dict)
            else None,
            "snapshot_revision": response.get("metadata", {}).get("snapshot_revision")
            if isinstance(response.get("metadata"), dict)
            else None,
        },
        "telemetry": {"returned_chars": returned_chars},
        "budget": budget.mapping(exhausted=exhausted),
    }


@mcp.tool(name="get_source")
def get_source_tool(
    source_ref: str,
    repository: str | None = None,
    revision: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    context_lines: int = 0,
) -> dict[str, object]:
    """Read bounded source while preserving the public revision precondition."""
    if not budget.start_call():
        return _budget_exhausted(budget, "max_tool_calls")
    result = service.get_source(
        SourceReadRequest(
            source_ref=source_ref,
            repository=repository,
            revision=revision,
            line_start=line_start,
            line_end=line_end,
            context_lines=context_lines,
        )
    ).to_mapping()
    content = result.get("content")
    chars = len(content) if isinstance(content, str) else 0
    if not budget.admit(chars):
        return _budget_exhausted(budget, "max_returned_chars")
    return {
        **result,
        "telemetry": {"returned_chars": chars},
        "budget": budget.mapping(),
    }


if __name__ == "__main__":
    mcp.run()
