"""Solr-specific query translation, response validation and safe diagnostics."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from fetchcore.projection.semantics import (
    QUERY_FIELDS_BY_SIGNAL_TYPE, SIGNAL_FIELD_FACTORS, dedupe,
    query_field_boost, typed_query_entries,
)
from fetchcore.retrieval.contracts import (
    KnowledgeSearchError, SearchIntent, SearchScope as KnowledgeSearchScope,
)

if TYPE_CHECKING:
    from fetchcore.backends.solr import SolrIndexSnapshot, SolrSettings

_SIGNAL_FIELD_FACTORS = SIGNAL_FIELD_FACTORS
_V3_QUERY_FIELDS_BY_SIGNAL_TYPE = QUERY_FIELDS_BY_SIGNAL_TYPE
_dedupe = dedupe
_query_field_boost = query_field_boost
_typed_query_entries = typed_query_entries


def _search_params(
    intent: SearchIntent,
    scope: KnowledgeSearchScope,
    snapshot: SolrIndexSnapshot,
    settings: SolrSettings,
) -> dict[str, object]:
    typed_entries = _typed_query_entries(intent)
    typed_clauses = [
        _typed_query_clause(signal_type, value, multiplier, settings)
        for signal_type, value, multiplier in typed_entries
    ]
    q = (
        "*:*"
        if scope.repository_paths
        else " OR ".join(item for item in typed_clauses if item) or "*:*"
    )
    filters = ["record_kind:knowledge", _term_filter("repository", snapshot.repository)]
    if scope.repository_paths:
        filters.append(
            "path_text:("
            + " OR ".join(_quote_query(item.casefold()) for item in scope.repository_paths)
            + ")"
        )
    if scope.knowledge_types:
        filters.append(
            "knowledge_type:("
            + " OR ".join(_quote_query(item) for item in scope.knowledge_types)
            + ")"
        )
    for field_name in ("language", "source_type"):
        values = _configured_filter_values(scope.settings.get(field_name), field_name)
        if values:
            filters.append(
                f"{field_name}:(" + " OR ".join(_quote_query(item) for item in values) + ")"
            )
    params: dict[str, object] = {
        "defType": "edismax",
        "q": q,
        "q.op": "OR",
        # Every query term is already fielded by signal type. Keeping qf lexical-only
        # avoids granting structural boosts to semantic or hint values.
        "qf": f'content^{settings.field_boosts["content"]:g}',
        "fq": filters,
        "rows": max(
            scope.max_results,
            settings.diagnostic_candidate_window or scope.max_results,
        ),
        "fl": (
            "id,source_type,source_ref,chunk_ref,repository,branch,document_revision,"
            "path,filename,language,knowledge_type,technical_role,symbols,symbol_types,"
            "content,line_start,line_end,checksum,source_checksum,identifiers_exact,"
            "content_identifiers_exact,filename_exact,path_text,path_segments,route_methods,"
            "route_paths,route_mechanisms,import_targets,dependency_targets,relation_types,"
            "relation_targets,relation_tokens,documentation_text,projection_version,score"
        ),
        "sort": "score desc,id asc",
        "wt": "json",
    }
    return params






def _typed_query_clause(
    signal_type: str,
    value: str,
    multiplier: float,
    settings: SolrSettings,
) -> str:
    field_factors = _SIGNAL_FIELD_FACTORS[signal_type]
    escaped = _escape_query(value.casefold())
    clauses = []
    for field_name in field_factors:
        boost = _query_field_boost(
            signal_type,
            field_name,
            multiplier,
            settings,
        )
        clauses.append(f'{field_name}:"{escaped}"^{boost:g}')
    for field_name in _V3_QUERY_FIELDS_BY_SIGNAL_TYPE[signal_type]:
        boost = _query_field_boost(
            signal_type,
            field_name,
            multiplier,
            settings,
        )
        clauses.append(f'{field_name}:"{escaped}"^{boost:g}')
    return "(" + " OR ".join(clauses) + ")"






def _search_documents(payload: Mapping[str, object]) -> tuple[list[dict[str, object]], int]:
    response = payload.get("response")
    if not isinstance(response, dict):
        raise KnowledgeSearchError(
            "Solr search response is missing response metadata",
            category="response_parsing_failure",
        )
    raw_docs = response.get("docs", [])
    if not isinstance(raw_docs, list):
        raise KnowledgeSearchError(
            "Solr search documents have an invalid shape",
            category="response_parsing_failure",
        )
    docs: list[dict[str, object]] = []
    for item in raw_docs:
        if not isinstance(item, dict):
            raise KnowledgeSearchError(
                "Solr search returned a non-object document",
                category="response_parsing_failure",
            )
        if item.get("record_kind") == "snapshot":
            raise KnowledgeSearchError(
                "Solr search leaked a snapshot control record",
                category="schema_mismatch",
            )
        docs.append(cast(dict[str, object], item))
    try:
        return docs, int(response.get("numFound", len(docs)))
    except (TypeError, ValueError) as exc:
        raise KnowledgeSearchError(
            "Solr search numFound is invalid",
            category="response_parsing_failure",
        ) from exc




























def _safe_query_telemetry(params: Mapping[str, object]) -> dict[str, object]:
    q = str(params.get("q", params.get("query", "")))
    filters = params.get("fq", params.get("filter", []))
    if isinstance(filters, str):
        filters = [filters]
    filter_names = [
        _filter_field_name(item) for item in cast(list[str], filters)
    ]
    return {
        "q": "<redacted>",
        "query_sha256": hashlib.sha256(q.encode("utf-8")).hexdigest(),
        "qf": params.get("qf"),
        "filter_fields": filter_names,
        "rows": params.get("rows", params.get("limit")),
        "sort": params.get("sort"),
        "transport": "json-post" if "query" in params else "query-params",
    }

def _filter_field_name(value: str) -> str:
    if value.startswith("{!term f=") and "}" in value:
        return value.removeprefix("{!term f=").split("}", 1)[0]
    return value.split(":", 1)[0]




def _configured_filter_values(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return _dedupe((value,))
    if isinstance(value, list):
        if not all(isinstance(item, str) for item in value):
            raise KnowledgeSearchError(
                f"Solr {field_name} filter values must be strings",
                category="schema_mismatch",
            )
        return _dedupe(cast(list[str], value))
    raise KnowledgeSearchError(
        f"Solr {field_name} filter must be a string or list",
        category="schema_mismatch",
    )








def _term_filter(field_name: str, value: str) -> str:
    return f"{{!term f={field_name}}}{value}"


def _repository_query(repository: str) -> str:
    return f'repository:"{_escape_query(repository)}"'


def _quote_query(value: str) -> str:
    return f'"{_escape_query(value)}"'


def _escape_query(value: str) -> str:
    return re.sub(r'([+\-!(){}\[\]^"~*?:\\/])', r"\\\1", value)
