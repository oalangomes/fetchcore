"""S3 Postman 2.1 safe extraction and canonical indexing contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fetchcore.application.service import (
    RepositoryBinding, RetrievalApplicationService, SourceReadRequest,
)
from fetchcore.projection import ProjectionV3Compiler
from fetchcore.repository.api_contracts import ApiEvidenceV1
from fetchcore.repository.index_fingerprint import indexing_contract_fingerprint
from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
from fetchcore.repository.openapi_indexing import OpenApiRepositoryIndexer
from fetchcore.repository.postman_extraction import sanitize_postman_collection
from fetchcore.repository.postman_indexing import (
    ApiContractRepositoryIndexer, PostmanRepositoryIndexer,
)
from fetchcore.repository.safe_scan import scan_repository_text
from fetchcore.retrieval.contracts import KnowledgeSearchError
from fetchcore.retrieval.embedding import ProjectionV3EmbeddingSerializer

SENTINEL = "S0_FAKE_SECRET_MUST_NEVER_APPEAR_IN_EVIDENCE"
SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"


def test_large_safe_collection_is_sanitized_but_oversize_fails_closed() -> None:
    collection = {
        "info": {"name": "Large safe collection", "schema": SCHEMA,
                 "description": SENTINEL + "x" * 300_000},
        "item": [{"name": "Get customer", "request": {
            "method": "GET", "url": {"path": ["customers", "42"]},
        }}],
    }
    payload = json.dumps(collection)
    result = sanitize_postman_collection("example.postman_collection.json", payload)
    assert result is not None
    assert len(result.units) == 1
    assert result.units[0].route == "/customers/42"
    assert SENTINEL not in json.dumps([unit.content for unit in result.units])
    assert sanitize_postman_collection(
        "example.postman_collection.json", payload + " " * 700_000,
    ) is None


def _collection() -> dict[str, object]:
    return {
        "info": {"name": "Safe collection", "schema": SCHEMA},
        "auth": {"type": "bearer", "bearer": [{"key": "token", "value": SENTINEL}]},
        "variable": [{"key": "baseUrl", "value": SENTINEL}],
        "item": [
            {"name": "Customers", "auth": {"type": "basic", "basic": [{"value": SENTINEL}]},
             "item": [
                {"name": "Create customer", "request": {
                    "method": "POST", "url": {"raw": "{{baseUrl}}/customers",
                                              "path": ["customers"],
                                              "query": [{"key": "limit", "value": SENTINEL},
                                                        {"key": "api_key", "value": SENTINEL}]},
                    "header": [
                        {"key": "X-Trace-Id", "value": SENTINEL},
                        {"key": "Authorization", "value": SENTINEL},
                        {"key": "X-Api-Key", "value": SENTINEL},
                    ],
                    "auth": {"type": "oauth2", "oauth2": [{"value": SENTINEL}]},
                    "body": {"mode": "raw", "raw": SENTINEL},
                    "description": SENTINEL,
                    "event": [{"script": {"exec": ["throw " + SENTINEL]}}],
                }, "response": [
                    {"code": 201, "name": SENTINEL, "body": SENTINEL,
                     "header": [{"key": "Content-Type", "value": "application/json"},
                                {"key": "Authorization", "value": SENTINEL}]},
                ]},
                {"name": "Get customer", "request": {
                    "method": "GET", "url": "{{baseUrl}}/customers/42?limit=1&api_key=" + SENTINEL,
                }},
                {"name": "Subfolder", "item": [
                    {"name": "Get customer", "disabled": True, "request": {
                        "method": "GET", "url": {"path": ["customers", "{{id}}"]},
                    }},
                ]},
            ]},
            {"name": "Other", "item": [
                {"name": "Get customer", "request": {
                    "method": "GET", "url": {"path": ["customers", "{{id}}"]},
                }},
            ]},
        ],
        "event": [{"script": {"exec": ["console.log('" + SENTINEL + "')"]}}],
    }


def _write(root: Path, *, filename: str = "source.json") -> None:
    (root / filename).write_text(json.dumps(_collection()), encoding="utf-8")
    (root / "private.postman_environment.json").write_text(
        json.dumps({"_postman_variable_scope": "environment", "values": [
            {"key": "token", "value": SENTINEL},
        ]}), encoding="utf-8",
    )
    (root / "README.md").write_text("# Safe readme\n", encoding="utf-8")


def test_collection_request_units_are_recursive_stable_and_sanitized() -> None:
    result = sanitize_postman_collection("source.json", json.dumps(_collection()))
    assert result is not None
    units = result.units
    assert len(units) == 4
    assert [unit.pointer for unit in units] == [
        "#/item/0/item/0", "#/item/0/item/1", "#/item/0/item/2/item/0", "#/item/1/item/0",
    ]
    assert [unit.method for unit in units] == ["POST", "GET", "GET", "GET"]
    assert [unit.route for unit in units] == [
        "/customers", "/customers/42", "/customers/{id}", "/customers/{id}",
    ]
    assert len({unit.pointer for unit in units}) == 4
    assert "folders Customers" in units[0].content
    assert "folders Customers Subfolder" in units[2].content
    assert "collection_auth bearer" in units[0].content
    assert "folder_auth basic" in units[0].content
    assert "request_auth oauth2" in units[0].content
    assert "query limit" in units[0].content
    assert "headers X-Trace-Id" in units[0].content
    assert "response_status 201" in units[0].content
    assert "response_mime application/json" in units[0].content
    assert "disabled" in units[2].content
    assert "description_present" in units[0].content
    serialized = json.dumps([unit.__dict__ for unit in units])
    assert SENTINEL not in serialized
    assert "api_key" not in serialized
    assert "Authorization" not in serialized
    assert "X-Api-Key" not in serialized


def test_postman_canonical_enrichment_projection_and_source_boundary(
    tmp_path: Path,
) -> None:
    _write(tmp_path)
    indexer = PostmanRepositoryIndexer(RepositoryIndexConfig(repository="s3/corpus"))
    result = indexer.index(tmp_path)
    assert result.changes.removal_detection_complete
    assert "source.json" in {doc.path for doc in result.documents}
    assert all(
        not doc.path.endswith("postman_environment.json")
        for doc in result.documents
    )
    facts = [
        ApiEvidenceV1.from_envelope(doc.evidence.semantic[0])
        for doc in result.documents if doc.evidence.semantic
    ]
    assert len(facts) == 4
    assert all(f.kind == "api_request" and f.format_version == "2.1.0" for f in facts)
    assert len({doc.id for doc in result.documents}) == len(result.documents)
    for doc in result.documents:
        assert doc.path == doc.provenance.path
        assert doc.provenance.source_checksum.startswith("sha256:")
        assert SENTINEL not in json.dumps(doc.to_mapping())
        projection = ProjectionV3Compiler().compile(doc)
        assert SENTINEL not in json.dumps(projection.to_mapping())
        assert SENTINEL not in ProjectionV3EmbeddingSerializer().serialize_record(projection)
    service = RetrievalApplicationService({
        "s3/corpus": RepositoryBinding(
            repository="s3/corpus", workspace=tmp_path,
            backend="local-semantic",
        )
    })
    with pytest.raises(KnowledgeSearchError) as caught:
        service.get_source(SourceReadRequest(
            repository="s3/corpus", source_ref="repository://s3/corpus/source.json",
        ))
    assert caught.value.category == "unsafe_source"


def test_default_and_s2_opt_in_do_not_admit_postman(tmp_path: Path) -> None:
    _write(tmp_path, filename="example.postman_collection.json")
    config = RepositoryIndexConfig(repository="s3/corpus")
    default = RepositoryIndexer(config)
    s2 = OpenApiRepositoryIndexer(config)
    s3 = PostmanRepositoryIndexer(config)
    baseline = default.index(tmp_path)
    prior = s2.index(tmp_path)
    enabled = s3.index(tmp_path)
    assert {doc.path for doc in baseline.documents} == {"README.md"}
    assert {doc.path for doc in prior.documents} == {"README.md"}
    assert "example.postman_collection.json" in {doc.path for doc in enabled.documents}
    assert len({
        indexing_contract_fingerprint(i) for i in (default, s2, s3)
    }) == 3


@pytest.mark.parametrize(
    ("filename", "payload"),
    [
        ("sample.postman_collection.json", '{"info":'),
        ("sample.postman_collection.json", '{"info":{"schema":"' + SCHEMA + '"},"item":{}}'),
        ("sample.postman_collection.json", '{"info":{"schema":"' + SCHEMA + '"},"item":[]}'),
        ("sample.postman_collection.json", '{"info":{"schema":"' + SCHEMA + '"}, "item":[], "item":[]}'),
        ("sample.postman_collection.json", '{"info":{"schema":"https://schema.getpostman.com/json/collection/v2.0.0/collection.json"},"item":[{"request":{"method":"GET","url":"/x"}}]}'),
        ("sample.postman_collection.json", '{"info":{"schema":"' + SCHEMA + '"},"item":[{"name":"folder","item":[],"request":{}}]}'),
        ("sample.postman_collection.json", '{"info":{"schema":"' + SCHEMA + '"},"item":[{"request":{"method":"GET","url":"/' + SENTINEL + '"}}]}'),
        ("sample.postman_collection.json", '{"info":{"schema":"' + SCHEMA + '"},"item":[{"request":{"method":"GET","url":"https://some.where/' + SENTINEL + '"}}]}'),
        ("sample.postman_environment.json", '{"_postman_variable_scope":"environment","values":[{"value":"' + SENTINEL + '"}]}'),
        ("unsafe.request.yaml", 'request:\n  method: GET\n  value: ' + SENTINEL),
    ],
)
def test_bad_or_unsupported_postman_is_quarantined_without_fallback(
    tmp_path: Path, filename: str, payload: str,
) -> None:
    (tmp_path / filename).write_text(payload, encoding="utf-8")
    result = PostmanRepositoryIndexer(
        RepositoryIndexConfig(repository="s3/corpus")
    ).index(tmp_path)
    assert not result.documents
    assert result.changes.removal_detection_complete
    assert SENTINEL not in json.dumps(result.to_mapping(), default=str)


def test_opt_in_safe_scan_never_admits_environment(tmp_path: Path) -> None:
    _write(tmp_path)
    from fetchcore.repository.postman_extraction import sanitize_postman_collection

    params = {
        "include_patterns": ("*.json",),
        "exclude_patterns": (),
        "max_files": 20,
        "max_file_bytes": 500_000,
        "max_total_bytes": 1_000_000,
    }
    normal = scan_repository_text(tmp_path, **params)
    assert not normal.sources
    selected = scan_repository_text(
        tmp_path, api_sanitizer=sanitize_postman_collection,
        api_sanitizer_kinds=frozenset({"postman_collection"}), **params,
    )
    assert [item.relative for item in selected.sources] == ["source.json"]
    assert len(selected.sources[0].api_units) == 4
    assert all(SENTINEL not in item.content for item in selected.sources)
    assert selected.skipped["api_artifact_blocked_postman_environment"] == 1


def test_stable_identity_and_combined_postman_openapi(tmp_path: Path) -> None:
    _write(tmp_path)
    (tmp_path / "openapi.json").write_text(
        '{"openapi":"3.1.0","paths":{"/orders":{"get":{"responses":{"200":{"description":"OK"}}}}}}',
        encoding="utf-8",
    )
    config = RepositoryIndexConfig(repository="s3/corpus", revision="fixture-rev")
    indexer = ApiContractRepositoryIndexer(config)
    first = indexer.index(tmp_path)
    second = indexer.index(tmp_path, previous_documents=first.documents)
    assert [doc.id for doc in first.documents] == [doc.id for doc in second.documents]
    assert len(second.changes.unchanged) == len(first.documents)
    facts = [
        ApiEvidenceV1.from_envelope(doc.evidence.semantic[0])
        for doc in first.documents if doc.evidence.semantic
    ]
    assert sum(f.kind == "api_request" for f in facts) == 4
    assert sum(f.kind == "api_operation" for f in facts) == 1
    assert "private.postman_environment.json" not in {doc.path for doc in first.documents}
    assert SENTINEL not in json.dumps(first.to_mapping(), default=str)
