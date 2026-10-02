"""OpenSearch query translation and result validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from fetchcore.projection.semantics import (
    QUERY_FIELDS_BY_SIGNAL_TYPE, SIGNAL_FIELD_FACTORS, float_value,
    query_field_boost, typed_query_entries,
)
from fetchcore.retrieval.contracts import (
    KnowledgeSearchError, SearchIntent, SearchScope as KnowledgeSearchScope,
)

if TYPE_CHECKING:
    from fetchcore.adapters.retrieval.opensearch.backend import OpenSearchIndexSnapshot, OpenSearchSettings

_EXACT_FIELDS = frozenset(
    {
        "identifiers_exact",
        "content_identifiers_exact",
        "symbols",
        "filename_exact",
        "technical_role",
        "path_segments",
        "route_methods",
        "route_paths",
        "route_mechanisms",
        "import_targets",
        "dependency_targets",
        "relation_targets",
    }
)
OPENSEARCH_VECTOR_FIELD = "embedding_vector"


def _search_body(
    intent: SearchIntent,
    scope: KnowledgeSearchScope,
    snapshot: OpenSearchIndexSnapshot,
    settings: OpenSearchSettings,
) -> dict[str, object]:
    clauses: list[dict[str, object]] = []
    for signal_type, value, multiplier in typed_query_entries(intent):
        field_names = (
            *tuple(SIGNAL_FIELD_FACTORS[signal_type]),
            *QUERY_FIELDS_BY_SIGNAL_TYPE[signal_type],
        )
        field_clauses: list[dict[str, object]] = []
        for field_name in field_names:
            boost = query_field_boost(
                signal_type,
                field_name,
                multiplier,
                settings,
            )
            query_value = value.casefold()
            if field_name in _EXACT_FIELDS:
                field_clauses.append(
                    {"term": {field_name: {"value": query_value, "boost": boost}}}
                )
            else:
                field_clauses.append(
                    {
                        "match_phrase": {
                            field_name: {
                                "query": query_value,
                                "boost": boost,
                            }
                        }
                    }
                )
        if field_clauses:
            clauses.append(
                {
                    "bool": {
                        "should": field_clauses,
                        "minimum_should_match": 1,
                    }
                }
            )

    filters: list[dict[str, object]] = [
        {"term": {"record_kind": "knowledge"}},
        {"term": {"repository": snapshot.repository}},
    ]
    if scope.repository_paths:
        filters.append(
            {
                "bool": {
                    "minimum_should_match": 1,
                    "should": [
                        {
                            "match_phrase": {
                                "path_text": {
                                    "query": item.casefold(),
                                }
                            }
                        }
                        for item in scope.repository_paths
                    ],
                }
            }
        )
    if scope.knowledge_types:
        filters.append({"terms": {"knowledge_type": list(scope.knowledge_types)}})
    for field_name in ("language", "source_type"):
        raw = scope.settings.get(field_name)
        if raw is None:
            continue
        if isinstance(raw, str):
            values = [raw]
        elif isinstance(raw, (list, tuple)):
            values = [str(item) for item in raw if str(item).strip()]
        else:
            raise KnowledgeSearchError(
                f"OpenSearch filter {field_name!r} must be a string or list",
                category="schema_mismatch",
            )
        if values:
            filters.append({"terms": {field_name: values}})

    query: dict[str, object]
    if scope.repository_paths:
        query = {"bool": {"filter": filters}}
    else:
        query = {
            "bool": {
                "filter": filters,
                "should": clauses or [{"match_all": {}}],
                "minimum_should_match": 1,
            }
        }
    return {
        "size": max(
            scope.max_results,
            settings.diagnostic_candidate_window or scope.max_results,
        ),
        "query": query,
        "sort": [{"_score": {"order": "desc"}}, {"id": {"order": "asc"}}],
        "track_total_hits": True,
        "_source": {"excludes": [OPENSEARCH_VECTOR_FIELD]},
    }


def _search_documents(
    payload: Mapping[str, object],
) -> tuple[list[dict[str, object]], int]:
    raw_hits = payload.get("hits")
    if not isinstance(raw_hits, Mapping):
        raise KnowledgeSearchError(
            "OpenSearch response is missing hits",
            category="response_parsing_failure",
        )
    raw_items = raw_hits.get("hits", [])
    if not isinstance(raw_items, list):
        raise KnowledgeSearchError(
            "OpenSearch hits have an invalid shape",
            category="response_parsing_failure",
        )
    docs: list[dict[str, object]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            raise KnowledgeSearchError(
                "OpenSearch hit is not an object",
                category="response_parsing_failure",
            )
        raw_source = raw_item.get("_source")
        if not isinstance(raw_source, Mapping):
            raise KnowledgeSearchError(
                "OpenSearch hit has no _source",
                category="response_parsing_failure",
            )
        document = {str(key): value for key, value in raw_source.items()}
        if document.get("record_kind") == "snapshot":
            # Snapshot queries intentionally use this parser too; the caller
            # determines whether control records are expected.
            pass
        document["score"] = float_value(raw_item.get("_score"))
        docs.append(document)

    raw_total = raw_hits.get("total", len(docs))
    if isinstance(raw_total, Mapping):
        raw_total = raw_total.get("value", len(docs))
    try:
        total = int(cast(float | int | str, raw_total))
    except (TypeError, ValueError) as exc:
        raise KnowledgeSearchError(
            "OpenSearch total hit count is invalid",
            category="response_parsing_failure",
        ) from exc
    return docs, total
