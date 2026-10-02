from __future__ import annotations

from collections.abc import Mapping

import pytest

from fetchcore.backends.solr import SolrIndexStore, SolrSettings
from fetchcore.backends.solr_schema import provision_lexical_solr_schema
from fetchcore.retrieval.contracts import KnowledgeSearchError


class _SchemaTransport:
    def __init__(self, *, incompatible_content: bool = False) -> None:
        self.fields: list[dict[str, object]] = [
            {
                "name": "id",
                "type": "string",
                "indexed": True,
                "stored": True,
                "multiValued": False,
            }
        ]
        if incompatible_content:
            self.fields.append(
                {
                    "name": "content",
                    "type": "string",
                    "indexed": True,
                    "stored": True,
                    "multiValued": False,
                }
            )
        self.field_types: list[dict[str, object]] = []
        self.posts: list[dict[str, object]] = []

    def get(
        self,
        path: str,
        params: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        assert path == "schema"
        return {
            "schema": {
                "uniqueKey": "id",
                "fields": [dict(item) for item in self.fields],
                "fieldTypes": [dict(item) for item in self.field_types],
            }
        }

    def post(
        self,
        path: str,
        payload: object,
        params: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        assert path == "schema"
        assert isinstance(payload, dict)
        self.posts.append(payload)
        raw_field_type = payload.get("add-field-type")
        if isinstance(raw_field_type, dict):
            self.field_types.append(dict(raw_field_type))
        raw_field = payload.get("add-field")
        if isinstance(raw_field, dict):
            self.fields.append(dict(raw_field))
        return {}


def _store(transport: _SchemaTransport) -> SolrIndexStore:
    settings = SolrSettings(
        url="http://127.0.0.1:8983/solr",
        collection="fetchcore-test",
    )
    return SolrIndexStore(transport, settings)  # type: ignore[arg-type]


def test_provision_lexical_schema_is_explicit_and_idempotent() -> None:
    transport = _SchemaTransport()
    store = _store(transport)

    first = provision_lexical_solr_schema(store)
    second = provision_lexical_solr_schema(store)

    assert first["validated"] is True
    assert first["created_field_count"] > 0
    assert first["created_field_types"] == ["identifier_ci"]
    assert second["created_field_count"] == 0
    assert second["created_field_types"] == []


def test_provision_schema_does_not_rewrite_incompatible_existing_fields() -> None:
    transport = _SchemaTransport(incompatible_content=True)
    store = _store(transport)

    with pytest.raises(KnowledgeSearchError) as captured:
        provision_lexical_solr_schema(store)

    assert captured.value.category == "schema_mismatch"
    assert "content" in str(captured.value.metadata)
