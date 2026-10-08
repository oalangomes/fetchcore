"""Native adapter compliance using disposable canonical repository projections."""

import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from fetchcore.application import (
    BackendRegistry,
    RepositoryBinding,
    RetrievalApplicationService,
    SearchRequest,
)
from fetchcore.retrieval import RetrievalConfig, DenseRetrievalConfig
from fetchcore.retrieval.contracts import Anchor, KnowledgeSearchError, SearchIntent
from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
from fetchcore.adapters.retrieval.solr.backend import (
    SolrBackend,
    SolrIndexStore,
    SolrSettings,
    UrllibSolrTransport,
)
from fetchcore.adapters.retrieval.opensearch.backend import (
    OpenSearchBackend,
    OpenSearchIndexStore,
    OpenSearchSettings,
    UrllibOpenSearchTransport,
)


def no_embedding(*args):
    raise AssertionError("exact lookup must not construct an embedder")


@pytest.mark.parametrize("kind", ["path", "source_ref"])
@pytest.mark.parametrize(
    "vendor",
    [
        pytest.param("solr", marks=pytest.mark.solr_integration),
        pytest.param("opensearch", marks=pytest.mark.opensearch_integration),
    ],
)
def test_native_exact_and_fallback(tmp_path: Path, vendor, kind):
    endpoint = os.environ.get("FETCHCORE_TARGETED_" + vendor.upper() + "_URL")
    if not endpoint:
        pytest.skip("set FETCHCORE_TARGETED_" + vendor.upper() + "_URL for disposable test index")
    repository = "targeted/" + uuid4().hex
    (tmp_path / "guide.md").write_text("# Product\nProduct service lookup.\n")
    indexer = RepositoryIndexer(RepositoryIndexConfig(repository=repository, revision="revision-a"))
    corpus = indexer.index(tmp_path)
    if vendor == "solr":
        settings = SolrSettings.from_mapping(
            {
                "url": endpoint,
                "collection": os.environ.get("FETCHCORE_TARGETED_SOLR_COLLECTION", "fetchcore34"),
            }
        )
        store = SolrIndexStore(UrllibSolrTransport(settings), settings)
        backend = SolrBackend(embedding_factory=no_embedding)
        config = {"url": endpoint, "collection": settings.collection}
    else:
        settings = OpenSearchSettings.from_mapping({"url": endpoint})
        store = OpenSearchIndexStore(
            UrllibOpenSearchTransport(settings), settings, clock=lambda: "2026-10-07T00:00:00+00:00"
        )
        backend = OpenSearchBackend(embedding_factory=no_embedding)
        config = {"url": endpoint}
    store.full_rebuild(corpus)
    binding = RepositoryBinding(
        repository,
        tmp_path,
        backend.backend_id,
        revision="revision-a",
        settings=config,
        retrieval=RetrievalConfig(dense=DenseRetrievalConfig(enabled=True)),
    )

    def application(bound):
        return RetrievalApplicationService(
            {repository: bound}, backends=BackendRegistry({backend.backend_id: lambda: backend})
        )

    ref = f"repository://{repository}/guide.md"
    intent = SearchIntent(
        canonical_query="Product", anchors=(Anchor(kind, "guide.md" if kind == "path" else ref),)
    )
    try:
        response = application(binding).search(SearchRequest(intent=intent))
        assert len(response.results) == 1
        hit = response.results[0]
        assert hit.source_ref == ref and hit.revision == "revision-a"
        assert hit.checksum == corpus.documents[0].checksum
        assert hit.query_matches[-1].channels == ("targeted",)
        assert not response.metadata["targeted"]["fallback_used"]
        assert response.telemetry.candidates_examined == 1
        missing = replace(intent, anchors=(Anchor("path", "missing.md"),))
        # Dense translation cannot preserve the required anchor.
        with pytest.raises(KnowledgeSearchError) as error:
            application(binding).search(SearchRequest(intent=missing))
        assert error.value.category == "unsupported_semantics"
        assert error.value.metadata["targeted"]["fallback_reason"] == "not_found"
        lexical = application(replace(binding, retrieval=RetrievalConfig()))
        response = lexical.search(SearchRequest(intent=missing))
        assert not response.results
        assert response.metadata["targeted"]["fallback_reason"] == "not_found"
        assert response.metadata["targeted"]["candidates_examined"] >= 0
        assert lexical.search(SearchRequest(query="Product")).results
        with pytest.raises(KnowledgeSearchError) as error:
            application(replace(binding, revision="revision-b")).search(
                SearchRequest(intent=intent)
            )
        assert error.value.category == "snapshot_revision_mismatch"
    finally:
        store.reset(repository)
