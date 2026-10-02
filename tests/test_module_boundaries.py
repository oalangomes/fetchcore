"""Compatibility and import-boundary checks for issue #44's physical moves."""

from __future__ import annotations

import ast
import importlib
import tomllib
from pathlib import Path

from fetchcore.projection import projection_contract_fingerprint
from fetchcore.retrieval.embedding import (
    ProjectionEmbeddingMode,
    vector_embedding_serializer_fingerprint,
)
from fetchcore.repository.index_fingerprint import indexing_contract_fingerprint
from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
from fetchcore.repository.structure import default_structural_symbol_extractors


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "fetchcore"


def test_historical_imports_resolve_to_the_same_implementations() -> None:
    aliases = (
        ("fetchcore.search", "fetchcore.retrieval.contracts"),
        ("fetchcore.signals", "fetchcore.retrieval.signals"),
        ("fetchcore.evidence", "fetchcore.retrieval.evidence"),
        ("fetchcore.fusion", "fetchcore.retrieval.fusion"),
        ("fetchcore.embedding", "fetchcore.retrieval.embedding"),
        ("fetchcore.projection_semantics", "fetchcore.projection.semantics"),
        ("fetchcore.repository.evidence", "fetchcore.knowledge.evidence"),
        ("fetchcore.repository.knowledge_chunking", "fetchcore.knowledge.chunking"),
        ("fetchcore.repository.dart", "fetchcore.repository.extractors.languages.dart"),
        ("fetchcore.repository.document_extractors", "fetchcore.repository.extractors.documents"),
        (
            "fetchcore.repository.sap_commerce_pack",
            "fetchcore.repository.extractors.frameworks.sap_commerce.pack",
        ),
        ("fetchcore.repository.index_operations", "fetchcore.application.index_operations"),
        ("fetchcore.repository.orchestration", "fetchcore.application.orchestration"),
        ("fetchcore.repository.incremental_persistence", "fetchcore.backends.solr_incremental_persistence"),
    )
    for old, new in aliases:
        assert importlib.import_module(old) is importlib.import_module(new)

    old_search = importlib.import_module("fetchcore.search")
    new_search = importlib.import_module("fetchcore.retrieval.contracts")
    assert old_search.SearchIntent is new_search.SearchIntent
    assert importlib.import_module("fetchcore.application").RetrievalApplicationService is (
        importlib.import_module("fetchcore.application.service").RetrievalApplicationService
    )


def test_canonical_and_transport_modules_do_not_import_concrete_backends() -> None:
    files = (
        *SOURCE.joinpath("knowledge").glob("*.py"),
        *SOURCE.joinpath("retrieval").glob("*.py"),
        *SOURCE.joinpath("projection").glob("*.py"),
        SOURCE / "repository" / "indexing.py",
        SOURCE / "repository" / "incremental.py",
        SOURCE / "repository" / "index_fingerprint.py",
        SOURCE / "application" / "service.py",
        SOURCE / "serving" / "mcp.py",
    )
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("fetchcore.backends"), path
            if isinstance(node, ast.Import):
                assert all(
                    not alias.name.startswith("fetchcore.backends") for alias in node.names
                ), path


def test_capability_adapter_dependencies_point_inward() -> None:
    """Concrete selection belongs to composition, not core or another adapter."""
    core_roots = ("knowledge", "projection", "retrieval", "repository", "serving")
    historical_repository_shims = {
        SOURCE / "repository" / "index_contract.py",
        SOURCE / "repository" / "document_incremental.py",
        SOURCE / "repository" / "incremental_persistence.py",
    }
    for root in core_roots:
        for path in (SOURCE / root).rglob("*.py"):
            if path in historical_repository_shims:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("fetchcore.adapters"), path
                elif isinstance(node, ast.Import):
                    assert all(
                        not item.name.startswith("fetchcore.adapters") for item in node.names
                    ), path

    for path in (SOURCE / "adapters" / "retrieval").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("fetchcore.adapters.embedding"), path
            elif isinstance(node, ast.Import):
                assert all(
                    not item.name.startswith("fetchcore.adapters.embedding")
                    for item in node.names
                ), path


def test_legacy_backend_paths_reexport_owning_classes() -> None:
    aliases = (
        ("local_semantic", "retrieval.local.backend", "LocalSemanticBackend"),
        ("solr", "retrieval.solr.backend", "SolrBackend"),
        ("opensearch", "retrieval.opensearch.backend", "OpenSearchBackend"),
        ("qdrant_vector", "retrieval.qdrant.backend", "QdrantVectorBackend"),
        ("ollama", "embedding.ollama.provider", "OllamaEmbeddingProvider"),
    )
    for old, new, name in aliases:
        historical = importlib.import_module(f"fetchcore.backends.{old}")
        owning = importlib.import_module(f"fetchcore.adapters.{new}")
        assert getattr(historical, name) is getattr(owning, name)

    module_aliases = (
        ("local_semantic", "retrieval.local.backend"),
        ("ollama", "embedding.ollama.provider"),
        ("opensearch_query", "retrieval.opensearch.query"),
        ("qdrant_vector", "retrieval.qdrant.backend"),
        ("qdrant_query", "retrieval.qdrant.query"),
        ("qdrant_sparse", "retrieval.qdrant.sparse"),
        ("solr_query", "retrieval.solr.query"),
        ("solr_index_contract", "retrieval.solr.index_contract"),
        ("solr_incremental", "retrieval.solr.incremental"),
        ("solr_document_incremental", "retrieval.solr.document_incremental"),
        ("solr_incremental_persistence", "retrieval.solr.incremental_persistence"),
        ("solr_projection", "retrieval.solr.projection"),
    )
    for old, new in module_aliases:
        assert importlib.import_module(f"fetchcore.backends.{old}") is (
            importlib.import_module(f"fetchcore.adapters.{new}")
        )


def test_top_level_module_import_graph_is_acyclic() -> None:
    modules = {
        ".".join(
            path.with_suffix("").relative_to(SOURCE.parent).parts[:-1]
            if path.name == "__init__.py"
            else path.with_suffix("").relative_to(SOURCE.parent).parts
        ): path
        for path in SOURCE.rglob("*.py")
    }
    dependencies: dict[str, set[str]] = {name: set() for name in modules}
    for name, path in modules.items():
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.ImportFrom) and node.module in modules:
                dependencies[name].add(node.module)
            elif isinstance(node, ast.Import):
                dependencies[name].update(
                    alias.name for alias in node.names if alias.name in modules
                )

    finished: set[str] = set()
    active: set[str] = set()

    def visit(module: str) -> None:
        assert module not in active, f"module import cycle at {module}"
        if module in finished:
            return
        active.add(module)
        for dependency in dependencies[module]:
            visit(dependency)
        active.remove(module)
        finished.add(module)

    for module in modules:
        visit(module)


def test_persisted_identities_match_the_pre_migration_baseline() -> None:
    assert projection_contract_fingerprint() == (
        "sha256:cfd4a63f85c8e4034bf17f0f82f08a47c717d52e75a9180ffb1eb6cad719e039"
    )
    assert vector_embedding_serializer_fingerprint(ProjectionEmbeddingMode.PROJECTION_V3) == (
        "sha256:5f859289b8b3a594e924545edb1c3ae6178b77ea22cf3ff224d43c5ec7aeb43a"
    )
    indexer = RepositoryIndexer(
        RepositoryIndexConfig(),
        symbol_extractors=default_structural_symbol_extractors(),
    )
    assert indexing_contract_fingerprint(indexer) == (
        "sha256:41f42d051e2c68b16f220eee761f504e4918a7fb58600d0475d48fe0993121d7"
    )


def test_mcp_entry_point_retains_its_historical_module() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["scripts"]["fetchcore-mcp"] == "fetchcore.mcp_server:main"
    from fetchcore.mcp_server import main as historical_main
    from fetchcore.serving.mcp import main as serving_main

    assert historical_main is serving_main
