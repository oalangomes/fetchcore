"""Opt-in real MongoDB Search/Vector Search smoke; never a relevance benchmark."""

from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from fetchcore.adapters.retrieval.mongodb.backend import MongoDBSearchBackend
from fetchcore.adapters.retrieval.mongodb.indexing import MongoDBIndexStore
from fetchcore.adapters.retrieval.mongodb.settings import MongoDBSettings
from fetchcore.adapters.retrieval.mongodb.transport import PyMongoTransport
from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
from fetchcore.retrieval.config import RetrievalConfig
from fetchcore.retrieval.contracts import KnowledgeSearchError, SearchIntent, SearchScope


pytestmark = pytest.mark.mongodb_integration


class SmokeEmbeddings:
    provider_id = "smoke"
    model_id = "constant-3"

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0, 0.0, 0.0) for _ in texts)


def test_real_mongodb_lexical_dense_hybrid_and_incremental(tmp_path: Path) -> None:
    uri = os.environ.get("FETCHCORE_MONGODB_TEST_URI")
    if uri is None:
        pytest.skip("Set FETCHCORE_MONGODB_TEST_URI to a Search-enabled test deployment")
    pytest.importorskip("pymongo")
    settings = MongoDBSettings(
        uri=uri, database=os.environ.get("FETCHCORE_MONGODB_TEST_DATABASE", "fetchcore_test"),
        collection="smoke_" + uuid4().hex, vector_dimensions=3,
    )
    provider = SmokeEmbeddings()
    transport = PyMongoTransport(settings)
    store = MongoDBIndexStore(transport, settings, embedding_provider=provider)
    source = tmp_path / "README.md"
    source.write_text("# Traceable\n\nMongoDB search integration sentinel.\n")
    indexer = RepositoryIndexer(RepositoryIndexConfig(repository="mongodb-smoke", revision="rev-1"))
    try:
        store.initialize()
        deadline = time.monotonic() + 120
        while True:
            try:
                store.validate_schema(require_vectors=True)
                break
            except KnowledgeSearchError as exc:
                if exc.category not in {"index_not_ready", "index_missing"} or time.monotonic() >= deadline:
                    raise
                time.sleep(1)
        store.full_rebuild(indexer.index(tmp_path))
        backend = MongoDBSearchBackend(embedding_factory=lambda config: provider)
        for mode in ("lexical", "dense", "hybrid"):
            config = RetrievalConfig.from_mapping({
                "dense": {"enabled": mode != "lexical", "embedding": {
                    "provider": "smoke", "model": "constant-3", "dimensions": 3,
                }},
                "hybrid": {"enabled": mode == "hybrid"}, "targeted": "disabled",
            })
            scope = SearchScope(
                tmp_path, "rev-1", "mongodb-smoke", (), 5,
                settings={
                    "uri": uri, "database": settings.database, "collection": settings.collection,
                    "vector_dimensions": 3,
                }, retrieval=config,
            )
            deadline = time.monotonic() + 60
            while True:
                response = backend.search(SearchIntent(canonical_query="sentinel"), scope, ())
                if response.results:
                    break
                if time.monotonic() >= deadline:
                    pytest.fail(f"MongoDB {mode} never exposed the indexed document")
                time.sleep(1)
            assert response.results[0].revision == "rev-1"
            assert response.results[0].checksum
            if mode == "hybrid":
                assert response.metadata["fusion"]["strategy"] == "rrf"
        previous = store.load_documents("mongodb-smoke")
        assert store.incremental_sync(indexer.index(tmp_path, previous_documents=previous)).mode == "noop"
        source.write_text("# Traceable\n\nChanged MongoDB integration sentinel.\n")
        updated = indexer.index(tmp_path, previous_documents=previous)
        assert store.incremental_sync(replace(updated, revision="rev-2")).updated == 1
        source.unlink()
        deleted = indexer.index(tmp_path, previous_documents=store.load_documents("mongodb-smoke"))
        assert store.incremental_sync(deleted).deleted == 1
    finally:
        transport.client[settings.database][settings.collection].drop()
        transport.close()
