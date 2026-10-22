"""Operator recovery contract against the existing in-memory backend fixtures.

No service, model, provisioning command or operational resource is accessed.
"""
from __future__ import annotations

import copy
import json
import socket

import pytest

from fetchcore.application import indexing
from fetchcore.application.composition import EmbeddingProviderRegistry
from fetchcore.application.repositories import RepositoryApplicationService
from fetchcore.adapters.retrieval.solr.backend import SolrIndexStore
from fetchcore.adapters.retrieval.opensearch.backend import OpenSearchIndexStore
from fetchcore.adapters.retrieval.qdrant.backend import QdrantVectorIndexStore
from fetchcore.retrieval.config import RetrievalConfig
from fetchcore.retrieval.contracts import KnowledgeSearchError
from test_opensearch_operational import MemoryOpenSearch
from test_qdrant_operational import DeterministicEmbedder, MemoryQdrant
from test_repository_index_lifecycle import (
    LifecycleTransport,
    tree_bytes,
    workspace,  # noqa: F401 - shared pytest fixture
)


@pytest.fixture(params=["solr", "opensearch", "qdrant-vector"])
def recovery(request, workspace, tmp_path, monkeypatch):  # noqa: F811 - pytest fixture dependency
    def no_network(*args, **kwargs):
        pytest.fail("snapshot recovery contracts must not access a real service")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    repositories = RepositoryApplicationService(
        cache_root=tmp_path / "cache", allowed_roots=(tmp_path,),
    )
    repositories.add(str(workspace), provider="local-workspace")
    backend = request.param
    embedder = DeterministicEmbedder()
    retrieval = None
    providers = None
    settings = {}
    if backend == "solr":
        transport = LifecycleTransport()
        monkeypatch.setattr(indexing, "UrllibSolrTransport", lambda _: transport)
        # Solr schema requests are covered separately by the transport contracts.
        monkeypatch.setattr(SolrIndexStore, "validate_schema", lambda *a, **k: None)
        store_class = SolrIndexStore
    elif backend == "opensearch":
        transport = MemoryOpenSearch()
        monkeypatch.setattr(indexing, "UrllibOpenSearchTransport", lambda _: transport)
        settings = {"index": "fetchcore-test"}
        store_class = OpenSearchIndexStore
    else:
        transport = MemoryQdrant()
        monkeypatch.setattr(indexing, "UrllibQdrantTransport", lambda _: transport)
        settings = {"collection": "fetchcore-test", "vector_dimensions": 4}
        providers = EmbeddingProviderRegistry(factories={"ollama": lambda *a: embedder})
        retrieval = RetrievalConfig.from_mapping({
            "dense": {"enabled": True, "embedding": {"dimensions": 4}},
        })
        store_class = QdrantVectorIndexStore
    service = indexing.RepositoryIndexApplicationService(
        repositories, backend=backend, backend_settings=settings,
        retrieval=retrieval, providers=providers,
    )
    writes = transport.post_calls if backend == "solr" else transport.writes
    return service, transport, writes, embedder, store_class


def _records(transport):
    if isinstance(transport, LifecycleTransport):
        return transport.documents
    if isinstance(transport, MemoryOpenSearch):
        return transport.indices[next(iter(transport.aliases.values()))]
    return transport.collections[next(iter(transport.aliases.values()))]["points"]


def _snapshot(transport):
    return next(
        payload for record in _records(transport).values()
        if (payload := record.get("payload", record))["record_kind"] == "snapshot"
    )


def test_absent_snapshot_requires_explicit_rebuild(recovery, tmp_path):
    service, _transport, writes, embedder, _ = recovery
    before = tree_bytes(tmp_path)
    status = service.run("status", "acme/widgets")
    assert status["currentness"] == "absent"
    assert status["snapshot_indexing_fingerprint"] is None
    assert status["snapshot_projection_fingerprint"] is None
    plan = service.run("plan", "acme/widgets")
    assert plan["mode"] == "initial"
    assert plan["planned_mutations"]["upsert_documents"] == 2
    assert plan["planned_mutations"]["deleted"] is None
    with pytest.raises(KnowledgeSearchError) as error:
        service.run("sync", "acme/widgets")
    assert error.value.category == "snapshot_missing"
    assert writes == []
    assert embedder.texts == []
    assert tree_bytes(tmp_path) == before

    execution = service.run("rebuild", "acme/widgets")["execution"]
    assert writes
    status = service.run("status", "acme/widgets")
    assert status["currentness"] == "current"
    assert status["target_revision"] == status["indexed_revision"]
    assert status["indexed_revision"] == execution["snapshot"]["revision"]
    assert status["indexing_fingerprint"] == status["snapshot_indexing_fingerprint"]
    assert status["projection_fingerprint"] == status["snapshot_projection_fingerprint"]


@pytest.mark.parametrize("corruption,reason", [
    ("indexing", "indexing_contract_changed"),
    ("projection", "schema_mismatch"),
    ("invalid", "response_parsing_failure"),
    ("unknown", "index_stale"),
])
def test_incompatible_snapshot_is_inspectable_without_writes(
    recovery, tmp_path, corruption, reason,
):
    service, transport, writes, embedder, _ = recovery
    service.run("rebuild", "acme/widgets")
    snapshot = _snapshot(transport)
    old_fingerprint = "sha256:" + "0" * 64
    if corruption == "indexing":
        if isinstance(transport, LifecycleTransport):
            snapshot["canonical_payload"] = json.dumps({
                "indexing_fingerprint": old_fingerprint, "private": "sensitive-payload",
            })
        else:
            snapshot["indexing_fingerprint"] = old_fingerprint
    elif corruption == "projection":
        snapshot["projection_fingerprint"] = old_fingerprint
    elif corruption == "invalid":
        snapshot["document_count"] = "sensitive-invalid-count"
    else:
        snapshot["snapshot_status"] = "unknown-future-state"
    if isinstance(transport, LifecycleTransport):
        transport.committed = copy.deepcopy(transport.documents)
    writes.clear()
    records_before = copy.deepcopy(_records(transport))
    files_before = tree_bytes(tmp_path)
    texts_before = list(embedder.texts)
    for operation in ("status", "plan"):
        result = service.run(operation, "acme/widgets")
        assert result["mode"] == "rebuild_required"
        assert result["currentness"] == "incompatible"
        assert result["reason"] == reason
        assert "sensitive" not in json.dumps(result)
        if corruption == "indexing":
            assert result["snapshot_indexing_fingerprint"] == old_fingerprint
            assert result["indexing_fingerprint"] != old_fingerprint
        if corruption == "projection":
            assert result["schema_compatible"] is True
            assert result["projection_compatible"] is False
            assert result["snapshot_projection_fingerprint"] == old_fingerprint
            assert result["projection_fingerprint"] != old_fingerprint
    with pytest.raises(KnowledgeSearchError) as error:
        service.run("sync", "acme/widgets")
    assert error.value.category == "rebuild_required"
    assert writes == []
    assert _records(transport) == records_before
    assert tree_bytes(tmp_path) == files_before
    assert embedder.texts == texts_before

    if corruption == "invalid":
        # A corrupt ownership/recovery record may require operator repair first.
        # Explicit rebuild must not bypass those adapter checks.
        return
    service.run("rebuild", "acme/widgets")
    status = service.run("status", "acme/widgets")
    assert status["currentness"] == "current"
    assert status["indexing_fingerprint"] == status["snapshot_indexing_fingerprint"]
    assert status["projection_fingerprint"] == status["snapshot_projection_fingerprint"]


def test_missing_snapshot_does_not_imply_empty_resource(recovery, tmp_path):
    service, transport, writes, embedder, _ = recovery
    service.run("rebuild", "acme/widgets")
    records = _records(transport)
    for key, record in tuple(records.items()):
        if record.get("payload", record)["record_kind"] == "snapshot":
            del records[key]
    writes.clear()
    before = copy.deepcopy(records)
    files_before = tree_bytes(tmp_path)
    texts_before = list(embedder.texts)
    assert service.run("status", "acme/widgets")["reason"] == "snapshot_missing"
    plan = service.run("plan", "acme/widgets")
    assert plan["mode"] == "initial"
    assert plan["planned_mutations"]["deleted"] is None
    assert plan["planned_mutations"]["previous_document_count"] is None
    with pytest.raises(KnowledgeSearchError) as error:
        service.run("sync", "acme/widgets")
    assert error.value.category == "snapshot_missing"
    assert writes == []
    assert records == before
    assert tree_bytes(tmp_path) == files_before
    assert embedder.texts == texts_before


def test_schema_must_be_corrected_and_validated_before_rebuild(
    recovery, tmp_path, monkeypatch,
):
    service, transport, writes, embedder, store_class = recovery
    service.run("rebuild", "acme/widgets")
    validate = store_class.validate_schema

    def incompatible(*args, **kwargs):
        raise KnowledgeSearchError("sensitive schema response", category="schema_mismatch")

    monkeypatch.setattr(store_class, "validate_schema", incompatible)
    writes.clear()
    before = copy.deepcopy(_records(transport))
    files_before = tree_bytes(tmp_path)
    texts_before = list(embedder.texts)
    for operation in ("status", "plan"):
        result = service.run(operation, "acme/widgets")
        assert result["schema_compatible"] is False
        assert result["reason"] == "schema_mismatch"
        assert "planned_mutations" not in result
        assert "sensitive" not in json.dumps(result)
    for operation, category in (("sync", "rebuild_required"), ("rebuild", "schema_mismatch")):
        with pytest.raises(KnowledgeSearchError) as error:
            service.run(operation, "acme/widgets")
        assert error.value.category == category
    assert writes == []
    assert _records(transport) == before
    assert tree_bytes(tmp_path) == files_before
    assert embedder.texts == texts_before

    monkeypatch.setattr(store_class, "validate_schema", validate)
    assert service.run("status", "acme/widgets")["schema_compatible"] is True
    service.run("rebuild", "acme/widgets")
    assert service.run("status", "acme/widgets")["currentness"] == "current"


@pytest.mark.parametrize("operation", ["status", "plan", "sync", "rebuild"])
def test_ambiguous_or_foreign_snapshot_never_authorizes_replacement(recovery, operation):
    service, transport, writes, embedder, _ = recovery
    service.run("rebuild", "acme/widgets")
    if isinstance(transport, MemoryQdrant):
        # Qdrant uses a deterministic snapshot point ID. A foreign repository
        # at that point must also stop before any replacement is authorized.
        _snapshot(transport)["repository"] = "other/repository"
        category = "snapshot_revision_mismatch"
    else:
        snapshot = copy.deepcopy(_snapshot(transport))
        snapshot["id"] = "duplicate-snapshot"
        _records(transport)[snapshot["id"]] = snapshot
        category = "repository_resource_collision"
    writes.clear()
    before = copy.deepcopy(_records(transport))
    texts_before = list(embedder.texts)
    with pytest.raises(KnowledgeSearchError) as error:
        service.run(operation, "acme/widgets")
    assert error.value.category == category
    assert writes == []
    assert _records(transport) == before
    assert embedder.texts == texts_before
