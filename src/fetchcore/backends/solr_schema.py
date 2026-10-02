"""Explicit Solr schema provisioning for the FetchCore Projection v3 adapter."""

from __future__ import annotations

from collections.abc import Mapping

from fetchcore.backends.solr import SolrIndexStore, SolrTransport
from fetchcore.retrieval.contracts import KnowledgeSearchError


_IDENTIFIER_FIELD_TYPE = "identifier_ci"

_SINGLE_STRING_FIELDS = (
    "id",
    "record_kind",
    "source_type",
    "source_ref",
    "chunk_ref",
    "repository",
    "branch",
    "document_revision",
    "path",
    "filename",
    "language",
    "knowledge_type",
    "technical_role",
    "checksum",
    "source_checksum",
    "projection_fingerprint",
    "projection_version",
    "snapshot_revision",
    "corpus_checksum",
    "snapshot_indexed_at",
    "snapshot_status",
)
_SINGLE_IDENTIFIER_FIELDS = ("filename_exact",)
_MULTI_STRING_FIELDS = ("symbol_types",)
_MULTI_IDENTIFIER_FIELDS = (
    "symbols",
    "symbol_tokens",
    "identifiers_exact",
    "content_identifiers_exact",
    "path_segments",
    "route_methods",
    "route_paths",
    "route_mechanisms",
    "import_targets",
    "dependency_targets",
    "relation_types",
    "relation_targets",
    "relation_tokens",
    "structural_tokens",
)
_SINGLE_TEXT_FIELDS = ("content", "path_text")
_MULTI_TEXT_FIELDS = ("documentation_text",)
_INTEGER_FIELDS = ("line_start", "line_end", "document_count")


def provision_lexical_solr_schema(store: SolrIndexStore) -> dict[str, object]:
    """Provision only missing lexical Projection v3 schema elements, then validate.

    Existing incompatible fields are never rewritten. They fail closed through
    validate_schema so schema mutation stays explicit and reviewable.
    """

    response = store.transport.get("schema", {"wt": "json"})
    raw_schema = response.get("schema")
    if not isinstance(raw_schema, dict):
        raise KnowledgeSearchError(
            "Solr schema response is invalid",
            category="schema_mismatch",
        )
    if raw_schema.get("uniqueKey") != "id":
        raise KnowledgeSearchError(
            "Solr schema has an incompatible unique key",
            category="schema_mismatch",
        )

    field_types = _named_items(raw_schema.get("fieldTypes"), "fieldTypes")
    fields = _named_items(raw_schema.get("fields"), "fields")
    created_types: list[str] = []
    created_fields: list[str] = []

    if _IDENTIFIER_FIELD_TYPE not in field_types:
        _add_identifier_field_type(store.transport)
        created_types.append(_IDENTIFIER_FIELD_TYPE)

    for definition in _lexical_field_definitions():
        name = str(definition["name"])
        if name in fields:
            continue
        store.transport.post("schema", {"add-field": definition}, {"wt": "json"})
        created_fields.append(name)

    store.validate_schema(require_vectors=False)
    return {
        "schema": "fetchcore.solr.projection-v3.lexical",
        "projection_version": store.settings.projection_version,
        "created_field_types": created_types,
        "created_fields": created_fields,
        "created_field_count": len(created_fields),
        "validated": True,
    }


def _add_identifier_field_type(transport: SolrTransport) -> None:
    transport.post(
        "schema",
        {
            "add-field-type": {
                "name": _IDENTIFIER_FIELD_TYPE,
                "class": "solr.TextField",
                "sortMissingLast": True,
                "analyzer": {
                    "tokenizer": {"class": "solr.KeywordTokenizerFactory"},
                    "filters": [{"class": "solr.LowerCaseFilterFactory"}],
                },
            }
        },
        {"wt": "json"},
    )


def _lexical_field_definitions() -> tuple[dict[str, object], ...]:
    fields: list[dict[str, object]] = []

    for name in _SINGLE_STRING_FIELDS:
        fields.append(_field(name, "string"))
    for name in _SINGLE_IDENTIFIER_FIELDS:
        fields.append(_field(name, _IDENTIFIER_FIELD_TYPE))
    for name in _MULTI_STRING_FIELDS:
        fields.append(_field(name, "string", multi_valued=True))
    for name in _MULTI_IDENTIFIER_FIELDS:
        fields.append(_field(name, _IDENTIFIER_FIELD_TYPE, multi_valued=True))
    for name in _SINGLE_TEXT_FIELDS:
        fields.append(_field(name, "text_general"))
    for name in _MULTI_TEXT_FIELDS:
        fields.append(_field(name, "text_general", multi_valued=True))
    for name in _INTEGER_FIELDS:
        fields.append(_field(name, "pint"))

    fields.append(
        {
            "name": "canonical_payload",
            "type": "string",
            "indexed": False,
            "stored": True,
            "multiValued": False,
        }
    )
    return tuple(fields)


def _field(
    name: str,
    field_type: str,
    *,
    multi_valued: bool = False,
) -> dict[str, object]:
    return {
        "name": name,
        "type": field_type,
        "indexed": True,
        "stored": True,
        "multiValued": multi_valued,
    }


def _named_items(value: object, context: str) -> dict[str, Mapping[str, object]]:
    if not isinstance(value, list):
        raise KnowledgeSearchError(
            f"Solr schema {context} response is invalid",
            category="schema_mismatch",
        )
    return {
        str(item["name"]): item
        for item in value
        if isinstance(item, Mapping) and item.get("name") is not None
    }
