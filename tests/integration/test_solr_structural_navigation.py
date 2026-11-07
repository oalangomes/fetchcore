"""Live Solr structural smoke; invoked by Search Lab after explicit indexing."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fetchcore.adapters.retrieval.solr.backend import (
    SolrIndexStore, SolrSettings, UrllibSolrTransport,
)
from fetchcore.adapters.retrieval.solr.index_contract import bind_indexing_contract_store
from fetchcore.application import (
    FindSymbolRequest, GetRelatedRequest, RepositoryBinding,
    RetrievalApplicationService, SourceReadRequest,
)
from fetchcore.application.index_operations import prepare_repository_index_runtime

pytestmark = pytest.mark.skipif(
    "FETCHCORE_SOLR_URL" not in os.environ,
    reason="live Solr smoke is run by Search Lab after explicit indexing",
)


def test_live_solr_structural_navigation_after_explicit_index() -> None:
    workspace = Path(os.environ["FETCHCORE_INDEX_WORKSPACE"])
    repository = os.environ["FETCHCORE_INDEX_REPOSITORY"]
    settings = SolrSettings.from_mapping({
        "url": os.environ["FETCHCORE_SOLR_URL"],
        "collection": os.environ["FETCHCORE_SOLR_COLLECTION"],
        "projection_version": "3",
    })
    runtime = prepare_repository_index_runtime(
        workspace, repository=repository, branch=None, revision=None,
    )
    store = bind_indexing_contract_store(
        SolrIndexStore(UrllibSolrTransport(settings), settings), runtime.indexer,
    )
    binding = RepositoryBinding(repository, workspace, "solr", knowledge_types=("code",))
    application = RetrievalApplicationService(
        {repository: binding}, navigation_store=lambda _binding: store,
    )
    store.load_documents = lambda _repository: (_ for _ in ()).throw(AssertionError(
        "structural serving loaded the full corpus",
    ))  # type: ignore[method-assign]
    first = application.find_symbol(FindSymbolRequest("demo.CurrentDirectory"))
    assert len(first.results) == 1
    definition = first.results[0]
    assert definition.path == "navigation.go"
    related = application.get_related(GetRelatedRequest(
        f"repository://{repository}/navigation.go", relation_types=("imports",),
    ))
    assert related.results and related.results[0].kind == "imports"
    source = application.get_source(SourceReadRequest(
        definition.source_ref, checksum=definition.checksum,
    ))
    assert "func CurrentDirectory" in source.content
    assert application.find_symbol(FindSymbolRequest("demo.CurrentDirectory")).results == first.results
