"""Observable bounded-map behavior independent of Solr transport."""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import replace
from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace
from collections.abc import Iterator

import pytest
from click.testing import CliRunner

from fetchcore.adapters.retrieval.solr.graph_store import GraphEdgePage
from fetchcore.application.graph_navigation import (
    NavigateGraphRequest, compose_graph_map, walk_graph,
)
from fetchcore.application.index_operations import prepare_repository_index_runtime
from fetchcore.application.service import RepositoryBinding, RetrievalApplicationService
from fetchcore.application.composition import BackendRegistry
from fetchcore.application.index_store import STRUCTURAL_SELECTOR_FIELDS, StructuralDocumentPage
from fetchcore.knowledge.graph_projection import GraphProjection, project_graph
from fetchcore.projection.core import corpus_checksum
from fetchcore.repository.structure_lookup import (
    structural_coverage_summary, structural_lookup_keys,
)
from fetchcore.retrieval.contracts import KnowledgeSearchError


class CanonicalGraphStore:
    def __init__(self, projection: GraphProjection) -> None:
        self.projection = projection
        self.queries = 0

    def list_edges(
        self, collection: str, repository: str, revision: str, generation: str,
        *, source_identity: str | None = None, target_identity: str | None = None,
        relation_types: tuple[str, ...] = (), limit: int,
        timeout_seconds: float | None = None,
    ) -> GraphEdgePage:
        assert collection == "test-physical"
        assert (repository, revision, generation) == (
            self.projection.repository, self.projection.revision, self.projection.generation,
        )
        assert timeout_seconds is not None and timeout_seconds > 0
        self.queries += 1
        edges = tuple(edge for edge in self.projection.edges
                      if (source_identity is None or edge.source_identity == source_identity)
                      and (target_identity is None or edge.target_identity == target_identity)
                      and (not relation_types or edge.relation_kind in relation_types))
        return GraphEdgePage(edges[:limit], len(edges) > limit, 1)


def _fixture(root: Path):
    files = {
        "a.py": "from b import beta\nfrom missing_mod import missing\ndef alpha():\n    beta()\n    beta()\n    missing()\n",
        "b.py": "from c import gamma\ndef beta():\n    gamma()\n",
        "c.py": "from d import delta\ndef gamma():\n    delta()\n",
        "d.py": "def delta():\n    return 1\n",
    }
    for name, content in files.items():
        (root / name).write_text(content, encoding="utf-8")
    for args in (("init",), ("config", "user.name", "Graph Test"),
                 ("config", "user.email", "graph@example.test"),
                 ("add", "."), ("commit", "-m", "fixture")):
        subprocess.run(["git", *args], cwd=root, check=True,
                       capture_output=True, text=True)
    runtime = prepare_repository_index_runtime(
        root, repository="owner/fixture", branch=None, revision=None,
    )
    indexed = runtime.indexer.index(root)
    graph = project_graph(indexed.documents, generation="graph-test",
                          indexing_fingerprint=runtime.fingerprint)
    seed = next(edge.source_identity for edge in graph.edges
                if edge.relation_kind == "calls" and edge.source_ref.endswith("a.py"))
    return indexed, graph, seed


def test_three_hop_map_preserves_paths_parallel_evidence_and_gaps(tmp_path: Path) -> None:
    indexed, graph, seed = _fixture(tmp_path)
    store = CanonicalGraphStore(graph)
    target = next(symbol.identity.key for document in indexed.documents
                  if document.path == "d.py"
                  for symbol in document.evidence.declarations
                  if symbol.identity is not None and symbol.name == "delta")
    request = NavigateGraphRequest(seed, target=target, repository=graph.repository,
                                   relation_types=("calls",), depth=3)
    walk = walk_graph(store, "test-physical", graph.repository, graph.revision,
                      graph.generation, request)
    result = compose_graph_map(
        request, walk, indexed.documents, repository=graph.repository,
        revision=graph.revision, generation=graph.generation, coverage=(),
    )
    assert len(result.entities) == 4
    assert len(result.edges) == 4  # two distinct a->b call sites
    assert len([edge for edge in result.edges if edge.source_ref.endswith("a.py")]) == 2
    assert any(len(path.edge_ids) == 3 for path in result.paths)
    assert result.target_reached is True
    assert any(path.nodes[-1] == target and len(path.edge_ids) == 3
               for path in result.paths)
    assert all(set(path.edge_ids) <= {edge.id for edge in result.edges}
               for path in result.paths)
    assert len(result.inconclusive_observations) >= 1
    assert not any(edge.target_text == "missing" for edge in result.edges)
    assert result.status == "partial"
    assert result.backend_queries == store.queries
    assert result.backend_qtime_ms == store.queries


def test_adjacency_and_expansion_budgets_are_applied_before_extra_queries(tmp_path: Path) -> None:
    _, graph, seed = _fixture(tmp_path)
    store = CanonicalGraphStore(graph)
    request = NavigateGraphRequest(
        seed, repository=graph.repository, relation_types=("calls",),
        max_edges=1, depth=3,
    )
    walk = walk_graph(store, "test-physical", graph.repository, graph.revision,
                      graph.generation, request)
    assert walk.backend_queries == 1
    assert len(walk.edges) == 1
    assert "adjacency_truncated" in walk.diagnostics

    store = CanonicalGraphStore(graph)
    request = replace(request, max_edges=100, expansion_budget=1)
    walk = walk_graph(store, "test-physical", graph.repository, graph.revision,
                      graph.generation, request)
    assert walk.backend_queries == 1
    assert "expansion_budget_reached" in walk.diagnostics

    class OversizedStore(CanonicalGraphStore):
        def list_edges(self, *args, **kwargs):
            page = super().list_edges(*args, **kwargs)
            return GraphEdgePage(page.edges + page.edges, True, 1)

    with pytest.raises(KnowledgeSearchError) as exc:
        walk_graph(OversizedStore(graph), "test-physical", graph.repository,
                   graph.revision, graph.generation,
                   replace(request, max_edges=1, expansion_budget=10))
    assert exc.value.category == "schema_mismatch"


def test_hydration_rejects_stale_edge_checksum_and_output_overflow(tmp_path: Path) -> None:
    indexed, graph, seed = _fixture(tmp_path)
    request = NavigateGraphRequest(seed, repository=graph.repository,
                                   relation_types=("calls",), depth=1)
    walk = walk_graph(CanonicalGraphStore(graph), "test-physical", graph.repository,
                      graph.revision, graph.generation, request)
    tampered = replace(walk, edges=(replace(walk.edges[0], target_checksum="wrong"),
                                    *walk.edges[1:]))
    with pytest.raises(KnowledgeSearchError, match="provenance"):
        compose_graph_map(request, tampered, indexed.documents,
                          repository=graph.repository, revision=graph.revision,
                          generation=graph.generation, coverage=())
    with pytest.raises(KnowledgeSearchError) as exc:
        compose_graph_map(replace(request, max_output_bytes=1), walk, indexed.documents,
                          repository=graph.repository, revision=graph.revision,
                          generation=graph.generation, coverage=())
    assert exc.value.category == "output_budget_exceeded"


def test_sdk_cli_mcp_return_the_same_bounded_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fetchcore import cli
    from fetchcore.mcp_server import create_mcp_server

    indexed, graph, seed = _fixture(tmp_path)
    runtime = prepare_repository_index_runtime(
        tmp_path, repository=graph.repository, branch=None, revision=None,
    )
    lookup: dict[tuple[str, str], list] = {}
    for doc in indexed.documents:
        keys = structural_lookup_keys(doc).to_mapping()
        keys["source_ref"] = [doc.source_ref]
        for field, values in keys.items():
            for value in values:
                lookup.setdefault((field, value), []).append(doc)

    class MaterializedStore:
        def load_snapshot(self, repository: str):
            assert repository == graph.repository
            return SimpleNamespace(
                repository=repository, revision=graph.revision,
                indexing_fingerprint=runtime.fingerprint,
                corpus_checksum=corpus_checksum(indexed.documents),
                structural_coverage=structural_coverage_summary(indexed.documents),
                projection_version="3", projection_fingerprint="test-v3", status="ready",
            )

        def lookup_structural_documents(self, repository, revision, *, selector, value, limit):
            assert repository == graph.repository and revision == graph.revision
            candidates = tuple(lookup.get((STRUCTURAL_SELECTOR_FIELDS[selector], value), ()))
            return StructuralDocumentPage(candidates[:limit], len(candidates) > limit)

        def load_structural_targets(self, repository, revision, identifiers):
            assert repository == graph.repository and revision == graph.revision
            return tuple(doc for doc in indexed.documents if doc.id in identifiers)

    class Publisher:
        @contextmanager
        def read_active(self, repository, revision, snapshot) -> Iterator[SimpleNamespace]:
            assert repository == graph.repository and revision == graph.revision
            assert snapshot.corpus_checksum == graph.corpus_checksum
            yield SimpleNamespace(graph_collection="test-physical",
                                  manifest=SimpleNamespace(generation=graph.generation),
                                  store=CanonicalGraphStore(graph))

    binding = RepositoryBinding(graph.repository, tmp_path, "solr", revision=graph.revision)
    app = RetrievalApplicationService(
        {graph.repository: binding}, backends=BackendRegistry({"solr": lambda: None}),
        navigation_store=lambda _binding: MaterializedStore(),  # type: ignore[arg-type]
        graph_publication=lambda _binding: Publisher(),  # type: ignore[arg-type]
    )
    request = NavigateGraphRequest(seed, repository=graph.repository,
                                   revision=graph.revision, relation_types=("calls",), depth=3)
    sdk = app.navigate_graph(request).to_mapping()
    server = create_mcp_server(app)
    mcp_result = asyncio.run(server.call_tool("navigate_graph", {
        "seed": seed, "repository": graph.repository, "revision": graph.revision,
        "relation_types": ["calls"], "depth": 3,
    }))
    assert not mcp_result.is_error
    assert mcp_result.structured_content == sdk

    monkeypatch.setattr(cli.RepositoryRetrievalApplicationService, "from_sources",
                        lambda **_kwargs: app)
    command = CliRunner().invoke(cli.main, [
        "navigate-graph", seed, "--repo", graph.repository,
        "--revision", graph.revision, "--relation-type", "calls",
        "--depth", "3", "--json",
    ])
    assert command.exit_code == 0, command.output
    import json
    assert json.loads(command.output)["result"] == sdk
