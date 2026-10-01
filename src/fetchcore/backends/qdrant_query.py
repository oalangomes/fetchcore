"""Qdrant-specific filter translation and query response validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from fetchcore.retrieval.contracts import KnowledgeSearchError, SearchScope

if TYPE_CHECKING:
    from fetchcore.backends.qdrant_vector import QdrantVectorSnapshot


def _qdrant_query_points(
    payload: Mapping[str, object],
) -> list[dict[str, object]]:
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise KnowledgeSearchError(
            "Qdrant query response is invalid",
            category="response_parsing_failure",
        )
    raw_points = result.get("points", [])
    if not isinstance(raw_points, list):
        raise KnowledgeSearchError(
            "Qdrant query points are invalid",
            category="response_parsing_failure",
        )
    points: list[dict[str, object]] = []
    for raw_point in raw_points:
        if not isinstance(raw_point, Mapping):
            raise KnowledgeSearchError(
                "Qdrant query returned an invalid point",
                category="response_parsing_failure",
            )
        raw_payload = raw_point.get("payload")
        if not isinstance(raw_payload, Mapping):
            raise KnowledgeSearchError(
                "Qdrant query point is missing payload",
                category="response_parsing_failure",
            )
        if raw_payload.get("record_kind") != "knowledge":
            raise KnowledgeSearchError(
                "Qdrant vector query leaked a control point",
                category="schema_mismatch",
            )
        if "score" not in raw_point:
            raise KnowledgeSearchError(
                "Qdrant query point is missing score",
                category="response_parsing_failure",
            )
        points.append(
            {
                "score": raw_point["score"],
                "payload": {str(key): item for key, item in raw_payload.items()},
            }
        )
    return points


def _qdrant_filter(
    scope: SearchScope,
    snapshot: QdrantVectorSnapshot,
) -> dict[str, object]:
    must: list[dict[str, object]] = [
        {"key": "record_kind", "match": {"value": "knowledge"}},
        {"key": "repository", "match": {"value": snapshot.repository}},
        {
            "key": "document_revision",
            "match": {"value": snapshot.revision},
        },
    ]
    if scope.knowledge_types:
        must.append(
            {
                "key": "knowledge_type",
                "match": {"any": list(scope.knowledge_types)},
            }
        )
    if scope.repository_paths:
        must.append(
            {
                "key": "path",
                "match": {"any": list(scope.repository_paths)},
            }
        )
    for field_name in ("language", "source_type"):
        raw = scope.settings.get(field_name)
        if raw is None:
            continue
        values = [raw] if isinstance(raw, str) else raw
        if not isinstance(values, (list, tuple)):
            raise KnowledgeSearchError(
                f"Qdrant filter {field_name} must be a string or list",
                category="schema_mismatch",
            )
        normalized = [str(item) for item in values if str(item).strip()]
        if normalized:
            must.append(
                {
                    "key": field_name,
                    "match": {"any": normalized},
                }
            )
    return {"must": must}
