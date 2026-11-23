"""Two-shard GraphProjection round trip on an explicitly disposable SolrCloud."""

from __future__ import annotations

import os
import subprocess
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import uuid4

import pytest

from fetchcore.adapters.retrieval.solr.backend import SolrSettings
from fetchcore.adapters.retrieval.solr.graph_store import GraphStore, edge_checksum
from fetchcore.adapters.retrieval.solr.graph_schema_validation import validate_graph_schema
from fetchcore.application.index_operations import prepare_repository_index_runtime
from fetchcore.knowledge.graph_projection import project_graph
from fetchcore.retrieval.contracts import KnowledgeSearchError


def _collection_action(base: str, action: str, name: str) -> None:
    params = {"action": action, "name": name, "wt": "json"}
    if action == "CREATE":
        params.update({"numShards": "2", "replicationFactor": "1",
                       "collection.configName": "fetchcore-graph-v1"})
    with urlopen(base + "/admin/collections?" + urlencode(params), timeout=30) as response:
        assert response.status == 200


@pytest.mark.skipif(
    "FETCHCORE_GRAPH_TEST_SOLR_URL" not in os.environ,
    reason="requires a disposable SolrCloud with fetchcore-graph-v1 configset",
)
def test_two_shard_graph_projection_round_trip(tmp_path: Path) -> None:
    base = os.environ["FETCHCORE_GRAPH_TEST_SOLR_URL"].rstrip("/")
    collection = "fc_graph_test_" + uuid4().hex[:12]
    (tmp_path / "a.py").write_text(
        "from b import beta\ndef alpha():\n    beta()\n    beta()\n", encoding="utf-8",
    )
    (tmp_path / "b.py").write_text("def beta():\n    return 1\n", encoding="utf-8")
    for args in (("init",), ("config", "user.name", "Graph Test"),
                 ("config", "user.email", "graph@example.test"),
                 ("add", "."), ("commit", "-m", "fixture")):
        subprocess.run(["git", *args], cwd=tmp_path, check=True,
                       capture_output=True, text=True)
    runtime = prepare_repository_index_runtime(
        tmp_path, repository="owner/graph-test", branch=None, revision=None,
    )
    result = runtime.indexer.index(tmp_path)
    projection = project_graph(result.documents, generation="test-generation",
                               indexing_fingerprint=runtime.fingerprint)
    assert len(projection.edges) == 3  # one file import and two distinct call sites
    manifest: dict[str, object] = {
        "repository": projection.repository, "revision": projection.revision,
        "generation": projection.generation, "graph_collection": collection,
        "graph_alias": "fc_graph_test_alias", "knowledge_collection": "knowledge-physical",
        "knowledge_alias": "knowledge-alias", "corpus_checksum": projection.corpus_checksum,
        "indexing_fingerprint": projection.indexing_fingerprint,
        "knowledge_projection_version": "3", "knowledge_projection_fingerprint": "test-v3",
        "projection_version": projection.version, "projection_fingerprint": projection.fingerprint,
        "edge_count": len(projection.edges), "edges_checksum": edge_checksum(projection.edges),
        "publication_state": "eligible", "manifest_version": 1,
    }
    settings = SolrSettings(
        url=base, collection=collection, deployment_mode="solrcloud",
        configset="fetchcore-graph-v1",
    )
    _collection_action(base, "CREATE", collection)
    try:
        store = GraphStore(settings)
        schema = store._transport(collection).get("schema", {"wt": "json"})["schema"]
        altered = deepcopy(schema)
        assert isinstance(altered, dict)
        altered["fields"][0]["type"] = "pint"
        with pytest.raises(KnowledgeSearchError, match="incompatible"):
            validate_graph_schema(altered)
        store.write_projection(collection, projection, manifest)
        assert store.load_manifest(collection, projection.repository) == manifest
        assert store.load_all_edges(collection, projection.repository, projection.revision,
                                    projection.generation) == projection.edges
        seed = next(edge.source_identity for edge in projection.edges
                    if edge.relation_kind == "calls")
        page = store.list_edges(
            collection, projection.repository, projection.revision, projection.generation,
            source_identity=seed, relation_types=("calls",), limit=1,
        )
        assert len(page.edges) == 1 and page.has_more
        assert store.count_records(collection) == 4
    finally:
        _collection_action(base, "DELETE", collection)
