"""MongoDB query syntax and index definitions; no consumer authority rules."""

from __future__ import annotations

import math

from fetchcore.retrieval.contracts import (
    AllOf, Anchor, KnowledgeSearchError, SearchExpression, SearchIntent, SearchScope, Term,
)
from fetchcore.retrieval.projection_matching import (
    QUERY_FIELDS_BY_SIGNAL_TYPE, SIGNAL_FIELD_FACTORS, query_field_boost, typed_query_entries,
)
from fetchcore.retrieval.embedding import validate_vector_dimensions

from .settings import MongoDBSettings


EXACT_FIELDS = frozenset({
    "id", "record_kind", "repository", "projection_fingerprint", "projection_version",
    "knowledge_type", "language", "source_type", "path", "source_ref",
    "identifiers_exact", "content_identifiers_exact", "symbols", "filename_exact",
    "technical_role", "path_segments", "route_methods", "route_paths", "route_mechanisms",
    "import_targets", "dependency_targets", "relation_targets",
})
TEXT_FIELDS = frozenset({
    "content", "path_text", "documentation_text", "structural_tokens", "filename",
    "symbol_tokens", "relation_tokens",
})
FILTER_FIELDS = (
    "record_kind", "repository", "projection_fingerprint", "projection_version",
    "knowledge_type", "language", "source_type", "path",
)
VECTOR_FIELD = "embedding_vector"


def validate_vectors(vectors: tuple[tuple[float, ...], ...], dimensions: int) -> None:
    if validate_vector_dimensions(vectors) != dimensions or any(
        any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            for value in vector)
        or not any(value != 0 for value in vector)
        for vector in vectors
    ):
        raise KnowledgeSearchError(
            "MongoDB cosine vectors require finite nonzero values and matching dimensions",
            category="schema_mismatch",
        )


def index_definitions(settings: MongoDBSettings, *, vectors: bool) -> tuple[dict[str, object], ...]:
    fields: dict[str, object] = {name: {"type": "token"} for name in EXACT_FIELDS}
    fields.update({name: {"type": "string", "analyzer": "lucene.standard"} for name in TEXT_FIELDS})
    definitions: list[dict[str, object]] = [{
        "name": settings.search_index, "type": "search",
        "definition": {"mappings": {"dynamic": False, "fields": fields}},
    }]
    if vectors:
        definitions.append({
            "name": settings.vector_index, "type": "vectorSearch",
            "definition": {"fields": [
                {"type": "vector", "path": VECTOR_FIELD,
                 "numDimensions": settings.vector_dimensions, "similarity": "cosine"},
                *({"type": "filter", "path": name} for name in FILTER_FIELDS),
            ]},
        })
    return tuple(definitions)


def scope_filter(
    scope: SearchScope, repository: str, settings: MongoDBSettings,
) -> dict[str, object]:
    query: dict[str, object] = {
        "record_kind": "knowledge", "repository": repository,
        "projection_version": settings.projection_version,
    }
    if scope.repository_paths:
        query["path"] = {"$in": list(scope.repository_paths)}
    if scope.knowledge_types:
        query["knowledge_type"] = {"$in": list(scope.knowledge_types)}
    for name in ("language", "source_type"):
        raw = scope.settings.get(name)
        if raw is None:
            continue
        values = (raw,) if isinstance(raw, str) else raw
        if (
            not isinstance(values, (list, tuple)) or not values
            or any(not isinstance(value, str) or not value.strip() for value in values)
        ):
            raise KnowledgeSearchError(f"Invalid MongoDB {name} filter", category="schema_mismatch")
        query[name] = {"$in": list(values)}
    return query


def _equals(field: str, value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {"compound": {"should": [
            {"equals": {"path": field, "value": item}} for item in value["$in"]
        ], "minimumShouldMatch": 1}}
    return {"equals": {"path": field, "value": value}}


def _expression(expression: SearchExpression) -> dict[str, object]:
    if isinstance(expression, Term):
        return {"phrase": {"path": "content", "query": expression.value}}
    if isinstance(expression, Anchor):
        fields = {
            "identifier": ("identifiers_exact", "content_identifiers_exact"),
            "qualified_symbol": ("symbols",), "path": ("path",),
            "route": ("route_paths",), "source_ref": ("source_ref",),
        }[expression.kind]
        value = (
            expression.value.casefold()
            if expression.kind in {"identifier", "route"} else expression.value
        )
        return {"compound": {"should": [_equals(name, value) for name in fields],
                             "minimumShouldMatch": 1}}
    children = [_expression(child) for child in expression.children]
    return {"compound": (
        {"must": children} if isinstance(expression, AllOf)
        else {"should": children, "minimumShouldMatch": 1}
    )}


def lexical_pipeline(
    intent: SearchIntent, filters: dict[str, object], settings: MongoDBSettings, limit: int,
) -> list[dict[str, object]]:
    should: list[dict[str, object]] = []
    hints: list[dict[str, object]] = []
    for signal_type, value, multiplier in typed_query_entries(intent):
        names = dict.fromkeys((
            *SIGNAL_FIELD_FACTORS[signal_type], *QUERY_FIELDS_BY_SIGNAL_TYPE[signal_type],
        ))
        clauses: list[dict[str, object]] = []
        for name in names:
            boost = {"boost": {"value": query_field_boost(signal_type, name, multiplier, settings)}}
            if name in EXACT_FIELDS:
                clauses.append({"equals": {"path": name, "value": value.casefold(), "score": boost}})
            elif name in TEXT_FIELDS:
                clauses.append({"phrase": {"path": name, "query": value, "score": boost}})
            else:
                raise KnowledgeSearchError(
                    f"MongoDB has no mapping for query field {name}", category="schema_mismatch"
                )
        target = hints if intent.has_v2_semantics and signal_type == "hint" else should
        target.extend(clauses)
    if intent.canonical_query:
        should.insert(0, {"text": {
            "path": ["content", "path_text", "documentation_text"], "query": intent.canonical_query,
        }})
    should.extend({"phrase": {"path": "content", "query": item.value}} for item in intent.expansions)
    required = [_equals(name, value) for name, value in filters.items()]
    required.extend(_expression(anchor) for anchor in intent.anchors)
    if intent.expression is not None:
        required.append(_expression(intent.expression))
    compound: dict[str, object] = {"filter": required}
    if should:
        compound.update(should=should, minimumShouldMatch=1)
    if hints:
        compound = {"must": [{"compound": compound}], "should": hints, "minimumShouldMatch": 0}
    return [
        {"$search": {"index": settings.search_index, "compound": compound}},
        {"$set": {"score": {"$meta": "searchScore"}}},
        {"$sort": {"score": -1, "id": 1}},
        {"$limit": limit},
        {"$project": {VECTOR_FIELD: 0, "canonical_payload": 0}},
    ]


def vector_pipeline(
    vector: tuple[float, ...], filters: dict[str, object], settings: MongoDBSettings, limit: int,
) -> list[dict[str, object]]:
    return [
        {"$vectorSearch": {
            "index": settings.vector_index, "path": VECTOR_FIELD,
            "queryVector": list(vector), "numCandidates": min(10000, max(limit, 20 * limit)),
            "limit": limit, "filter": filters,
        }},
        {"$set": {"score": {"$meta": "vectorSearchScore"}}},
        {"$sort": {"score": -1, "id": 1}},
        {"$project": {VECTOR_FIELD: 0, "canonical_payload": 0}},
    ]
