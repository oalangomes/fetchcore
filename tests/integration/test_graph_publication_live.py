"""Cross-collection graph publication on an explicitly disposable SolrCloud."""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from fetchcore.adapters.retrieval.solr.backend import (
    SolrSettings, UrllibSolrTransport,
)
from fetchcore.adapters.retrieval.solr.cloud_lifecycle import (
    SolrCloudGenerationManager, UrllibSolrCloudCollectionsApi,
)
from fetchcore.adapters.retrieval.solr.index_contract import IndexContractSolrIndexStore
from fetchcore.application.graph_publication import GraphPublicationService
from fetchcore.application.graph_navigation import NavigateGraphRequest
from fetchcore.application.service import RepositoryBinding, RetrievalApplicationService
from fetchcore.application.index_operations import prepare_repository_index_runtime
from fetchcore.knowledge.graph_projection import project_graph
from fetchcore.retrieval.contracts import KnowledgeSearchError


@pytest.mark.skipif(
    "FETCHCORE_GRAPH_TEST_SOLR_URL" not in os.environ,
    reason="requires disposable SolrCloud with graph and knowledge configsets",
)
def test_graph_publication_pins_physical_knowledge_generation(tmp_path: Path) -> None:
    base = os.environ["FETCHCORE_GRAPH_TEST_SOLR_URL"].rstrip("/")
    suffix = uuid4().hex[:10]
    knowledge_alias = "fc30_knowledge_" + suffix
    graph_alias = knowledge_alias + "_graph"
    knowledge_collection = "fc30_knowledge_physical_" + suffix
    alternate_collection = "fc30_knowledge_alternate_" + suffix
    (tmp_path / "a.py").write_text(
        "from b import beta\ndef alpha():\n    beta()\n", encoding="utf-8",
    )
    (tmp_path / "b.py").write_text(
        "from c import gamma\ndef beta():\n    gamma()\n", encoding="utf-8",
    )
    (tmp_path / "c.py").write_text(
        "from d import delta\ndef gamma():\n    delta()\n", encoding="utf-8",
    )
    (tmp_path / "d.py").write_text("def delta():\n    return 1\n", encoding="utf-8")
    for args in (("init",), ("config", "user.name", "Graph Test"),
                 ("config", "user.email", "graph@example.test"),
                 ("add", "."), ("commit", "-m", "fixture")):
        subprocess.run(["git", *args], cwd=tmp_path, check=True,
                       capture_output=True, text=True)
    runtime = prepare_repository_index_runtime(
        tmp_path, repository="owner/graph-test", branch=None, revision=None,
    )
    indexed = runtime.indexer.index(tmp_path)
    settings = SolrSettings(
        url=base, collection=knowledge_alias, deployment_mode="solrcloud",
        configset="fetchcore-projection-v3",
    )
    api = UrllibSolrCloudCollectionsApi(settings)

    def store_factory(collection: str) -> IndexContractSolrIndexStore:
        physical_settings = replace(settings, collection=collection)
        store = IndexContractSolrIndexStore(
            UrllibSolrTransport(physical_settings), physical_settings,
        )
        store.indexing_fingerprint = runtime.fingerprint
        return store

    created: list[str] = []
    graph_collection: str | None = None
    try:
        for collection in (knowledge_collection, alternate_collection):
            api.create_collection(collection, settings.configset)
            created.append(collection)
            store_factory(collection).full_rebuild(indexed)
        api.set_alias(knowledge_alias, knowledge_collection)
        manager = SolrCloudGenerationManager(settings, api=api, store_factory=store_factory)
        graph_settings = replace(settings, collection=graph_alias,
                                 configset="fetchcore-graph-v1")
        publisher = GraphPublicationService(manager, graph_settings, api=api)
        projection = project_graph(indexed.documents, generation="graph-" + suffix,
                                   indexing_fingerprint=runtime.fingerprint)
        published = publisher.publish(projection)
        graph_collection = published.active_collection
        assert publisher.inspect(indexed.repository)["ready"] is True
        snapshot = store_factory(knowledge_collection).load_snapshot(indexed.repository)
        with publisher.read_active(indexed.repository, indexed.revision, snapshot) as session:
            assert session.knowledge_collection == knowledge_collection
            assert session.graph_collection == graph_collection
            assert session.store.load_all_edges(
                graph_collection, indexed.repository, indexed.revision, projection.generation,
            ) == projection.edges

        seed = next(edge.source_identity for edge in projection.edges
                    if edge.relation_kind == "calls")
        application = RetrievalApplicationService(
            {indexed.repository: RepositoryBinding(
                indexed.repository, tmp_path, "solr", revision=indexed.revision,
                settings={"url": base, "collection": knowledge_alias,
                          "deployment_mode": "solrcloud",
                          "configset": "fetchcore-projection-v3"},
            )},
            navigation_store=lambda _binding: store_factory(knowledge_collection),
            graph_publication=lambda _binding: publisher,
        )
        map_result = application.navigate_graph(NavigateGraphRequest(
            seed, repository=indexed.repository, revision=indexed.revision,
            relation_types=("calls",), depth=3,
        ))
        assert map_result.status in {"success", "partial"}
        assert len(map_result.edges) == 3
        assert len(map_result.entities) == 4
        assert any(len(path.edge_ids) == 3 for path in map_result.paths)
        assert map_result.backend_queries >= 1

        # A knowledge-only alias switch, even with identical content, revokes
        # the old graph binding. It does not affect lexical knowledge serving.
        api.set_alias(knowledge_alias, alternate_collection)
        with pytest.raises(KnowledgeSearchError, match="knowledge alias"), publisher.read_active(
            indexed.repository, indexed.revision, snapshot,
        ):
            pass
        assert publisher.inspect(indexed.repository)["ready"] is False
        api.set_alias(knowledge_alias, knowledge_collection)
        with pytest.raises(KnowledgeSearchError, match="knowledge alias"), publisher.read_active(
            indexed.repository, indexed.revision, snapshot,
        ):
            api.set_alias(knowledge_alias, alternate_collection)
        api.set_alias(knowledge_alias, knowledge_collection)
        assert publisher.inspect(indexed.repository)["ready"] is True
    finally:
        aliases = api.list_aliases()
        for alias in (graph_alias, knowledge_alias):
            if alias in aliases:
                api.delete_alias(alias)
        if graph_collection is not None:
            created.append(graph_collection)
        for collection in created:
            if collection in api.list_collections():
                api.delete_collection(collection)
