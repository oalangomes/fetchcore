"""Live structural lifecycle using the repository's existing backend Compose files."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from fetchcore.adapters.retrieval.opensearch.backend import (
    OpenSearchIndexStore, OpenSearchSettings, UrllibOpenSearchTransport,
)
from fetchcore.adapters.retrieval.qdrant.backend import (
    QdrantVectorIndexStore, QdrantVectorSettings, UrllibQdrantTransport,
)
from fetchcore.application import (
    FindSymbolRequest, GetRelatedRequest, RepositoryBinding,
    RetrievalApplicationService, SourceReadRequest,
)
from fetchcore.application.index_operations import prepare_repository_index_runtime


pytestmark = pytest.mark.skipif(
    "FETCHCORE_LIVE_STRUCTURAL_BACKEND" not in os.environ,
    reason="requires a live OpenSearch or Qdrant service",
)


class _DeterministicEmbedder:
    provider_id = "structural-test"
    model_id = "sha256-4"

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.calls += len(texts)
        return tuple(tuple((hashlib.sha256(text.encode()).digest()[i] + 1) / 256
                           for i in range(4)) for text in texts)


def _store(backend: str):
    token = uuid4().hex[:10]
    clock = lambda: datetime.now(UTC).isoformat()
    if backend == "opensearch":
        settings = OpenSearchSettings.from_mapping({
            "url": os.environ.get("FETCHCORE_OPENSEARCH_URL", "http://127.0.0.1:9200"),
            "index": f"fetchcore-structural-{token}",
        })
        return OpenSearchIndexStore(UrllibOpenSearchTransport(settings), settings, clock=clock), None
    if backend == "qdrant":
        settings = QdrantVectorSettings.from_mapping({
            "url": os.environ.get("FETCHCORE_QDRANT_URL", "http://127.0.0.1:6333"),
            "collection": f"fetchcore-structural-{token}",
            "embedding_model": "sha256-4", "vector_dimensions": 4,
        })
        embedder = _DeterministicEmbedder()
        return QdrantVectorIndexStore(
            UrllibQdrantTransport(settings), settings, embedder, clock=clock,
        ), embedder
    raise ValueError("unknown live structural backend")


def test_live_structural_index_navigation_and_incremental_removal(tmp_path: Path) -> None:
    backend = os.environ["FETCHCORE_LIVE_STRUCTURAL_BACKEND"]
    repository = "integration/structural"
    target = tmp_path / "target.py"
    caller = tmp_path / "caller.py"
    target.write_text("def run():\n    return 1\n", encoding="utf-8")
    caller.write_text(
        "from target import run as execute\ndef invoke():\n    execute()\n",
        encoding="utf-8",
    )
    runtime = prepare_repository_index_runtime(
        tmp_path, repository=repository, branch=None, revision=None,
    )
    store, embedder = _store(backend)
    store.indexing_fingerprint = runtime.fingerprint
    initial = runtime.indexer.index(tmp_path)
    store.full_rebuild(initial)
    application = RetrievalApplicationService(
        {repository: RepositoryBinding(repository, tmp_path,
                                       "qdrant-vector" if backend == "qdrant" else backend)},
        navigation_store=lambda _binding: store,
    )
    original_load_documents = store.load_documents
    store.load_documents = lambda _repository: (_ for _ in ()).throw(AssertionError(
        "navigation loaded the full corpus",
    ))  # type: ignore[method-assign]
    definition = application.find_symbol(FindSymbolRequest("target.run", repository)).results[0]
    source = application.find_symbol(FindSymbolRequest("caller.invoke", repository)).results[0]
    outgoing = application.get_related(GetRelatedRequest(
        source.identity, repository, relation_types=("calls",), require_resolution=True,
    ))
    assert len(outgoing.results) == 1
    assert outgoing.results[0].target_identity == definition.identity
    incoming = application.get_related(GetRelatedRequest(
        definition.identity, repository, relation_types=("calls",), direction="incoming",
    ))
    assert len(incoming.results) == 1
    assert incoming.results[0].source_identity == source.identity
    assert "def run" in application.get_source(SourceReadRequest(
        definition.source_ref, repository=repository, revision=definition.revision,
        checksum=definition.checksum,
    )).content
    previous_calls = embedder.calls if embedder is not None else 0
    assert application.find_symbol(FindSymbolRequest("target.run", repository)).results
    assert embedder is None or embedder.calls == previous_calls

    # The target changes while the caller is retained. Its old edge must vanish.
    target.write_text("def replacement():\n    return 2\n", encoding="utf-8")
    runtime = prepare_repository_index_runtime(
        tmp_path, repository=repository, branch=None, revision=None,
    )
    updated = runtime.indexer.index(tmp_path, previous_documents=initial.documents)
    assert updated.changes.updated or updated.changes.added
    store.load_documents = original_load_documents  # type: ignore[method-assign]
    store.incremental_sync(updated)
    assert application.find_symbol(FindSymbolRequest("target.run", repository)).results == ()
    assert application.find_symbol(FindSymbolRequest("replacement", repository)).results
    assert application.get_related(GetRelatedRequest(
        source.identity, repository, relation_types=("calls",), require_resolution=True,
    )).results == ()
