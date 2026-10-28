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


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "src" / "fetchcore"
_RETRIEVAL_OWNED_IMPORT_ROOTS = frozenset(
    {
        "fetchcore.retrieval",
        "fetchcore.search",
        "fetchcore.signals",
        "fetchcore.evidence",
        "fetchcore.fusion",
        "fetchcore.embedding",
    }
)


def _is_retrieval_owned_import(module: str) -> bool:
    return any(
        module == root or module.startswith(f"{root}.")
        for root in _RETRIEVAL_OWNED_IMPORT_ROOTS
    )


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


def test_canonical_projection_modules_do_not_import_retrieval() -> None:
    for path in (SOURCE / "projection").glob("*.py"):
        if path.name == "semantics.py":  # Historical compatibility alias.
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not any(
            _is_retrieval_owned_import(name) for name in imported_modules
        ), path


def test_historical_retrieval_aliases_are_classified_as_retrieval_owned() -> None:
    for module in (
        "fetchcore.retrieval",
        "fetchcore.retrieval.contracts",
        "fetchcore.search",
        "fetchcore.signals",
        "fetchcore.evidence",
        "fetchcore.fusion",
        "fetchcore.embedding",
    ):
        assert _is_retrieval_owned_import(module)

    assert not _is_retrieval_owned_import("fetchcore.projection")
    assert not _is_retrieval_owned_import("fetchcore.knowledge")
    assert not _is_retrieval_owned_import("fetchcore.repository")


def test_projection_semantics_is_only_a_compatibility_alias() -> None:
    path = SOURCE / "projection" / "semantics.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for node in ast.walk(tree))
    assert not any(isinstance(node, (ast.Dict, ast.List, ast.Set)) for node in ast.walk(tree))
    owner = importlib.import_module("fetchcore.retrieval.projection_matching")
    facade = importlib.import_module("fetchcore.projection.semantics")
    historical = importlib.import_module("fetchcore.projection_semantics")
    assert facade is historical is owner
    assert facade.match_projection_record is owner.match_projection_record


def test_projection_and_retrieval_matching_do_not_import_agentsorch() -> None:
    files = (
        *(SOURCE / "projection").glob("*.py"),
        SOURCE / "retrieval" / "projection_matching.py",
        SOURCE / "retrieval" / "embedding.py",
        SOURCE / "retrieval" / "fusion.py",
        SOURCE / "retrieval" / "reranking.py",
        SOURCE / "retrieval" / "reranking_query.py",
        SOURCE / "retrieval" / "reranking_runtime.py",
    )
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not any(
            name == "agentsorch" or name.startswith("agentsorch.")
            for name in imported_modules
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


def test_persisted_identities_version_projection_route_semantics() -> None:
    assert projection_contract_fingerprint() == (
        "sha256:4390897bae3636a7d79fb5318e80d79358053c8e4de0cfb6f295b884fd4c7a20"
    )
    assert vector_embedding_serializer_fingerprint(ProjectionEmbeddingMode.PROJECTION_V3) == (
        "sha256:5f859289b8b3a594e924545edb1c3ae6178b77ea22cf3ff224d43c5ec7aeb43a"
    )
    indexer = RepositoryIndexer(
        RepositoryIndexConfig(),
        symbol_extractors=default_structural_symbol_extractors(),
    )
    # Persisting canonical declaration identities changes indexing; rebuild explicitly.
    assert indexing_contract_fingerprint(indexer) == (
        "sha256:94145db7c427d5fb38849bac97570cd2cef2d4b82e631a4017f8aa5ae678ff21"
    )


def test_mcp_entry_point_retains_its_historical_module() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["scripts"]["fetchcore-mcp"] == "fetchcore.mcp_server:main"
    from fetchcore.mcp_server import main as historical_main
    from fetchcore.serving.mcp import main as serving_main

    assert historical_main is serving_main
