from __future__ import annotations

import ast
from pathlib import Path

from fetchcore.backends.local_semantic import LocalSemanticBackend
from fetchcore.backends.opensearch import OpenSearchBackend, OpenSearchSettings
from fetchcore.backends.qdrant_vector import QdrantVectorBackend, QdrantVectorSettings
from fetchcore.backends.solr import SolrBackend, SolrSettings
from fetchcore.backends.solr_projection import (
    SOLR_PROJECTION_VERSION,
    SolrCanonicalDocumentReader,
    SolrProjectionCompiler,
)
from fetchcore.projection import (
    PROJECTION_VERSION,
    ProjectionV3CanonicalDocumentReader,
    ProjectionV3Compiler,
)
from fetchcore.backends.qdrant_sparse import (
    SPARSE_ANALYZER_FINGERPRINT,
    intent_sparse_terms,
    sparse_vector_from_terms,
)
from fetchcore.search import SearchIntent, SearchScope


def test_backend_capabilities_are_explicit() -> None:
    assert LocalSemanticBackend.retrieval_capabilities.lexical is True

    assert SolrBackend.retrieval_capabilities.lexical is True
    assert SolrBackend.retrieval_capabilities.dense is True
    assert SolrBackend.retrieval_capabilities.hybrid is True

    assert OpenSearchBackend.retrieval_capabilities.lexical is True
    assert OpenSearchBackend.retrieval_capabilities.dense is True
    assert OpenSearchBackend.retrieval_capabilities.hybrid is True

    assert QdrantVectorBackend.retrieval_capabilities.lexical is False
    assert QdrantVectorBackend.retrieval_capabilities.dense is True
    assert QdrantVectorBackend.retrieval_capabilities.hybrid is True


def test_solr_projection_remains_compatibility_facade_only() -> None:
    assert SOLR_PROJECTION_VERSION == PROJECTION_VERSION
    assert SolrProjectionCompiler is ProjectionV3Compiler
    assert SolrCanonicalDocumentReader is ProjectionV3CanonicalDocumentReader


def test_backend_settings_parse_without_network_access() -> None:
    assert SolrSettings.from_mapping({}).url
    assert OpenSearchSettings.from_mapping({}).url
    assert QdrantVectorSettings.from_mapping({}).url


def test_qdrant_sparse_analyzer_is_deterministic() -> None:
    intent = SearchIntent(
        exact_terms=("MedicationHistoryService",),
        semantic_terms=("medication history",),
    )
    first = intent_sparse_terms(intent)
    second = intent_sparse_terms(intent)
    assert first == second
    assert first
    assert len(SPARSE_ANALYZER_FINGERPRINT) == 64

    vector = sparse_vector_from_terms(first)
    assert len(vector.indices) == len(vector.values) == len(vector.terms)
    assert tuple(sorted(vector.indices)) == vector.indices


def test_backend_modules_have_no_agentsorch_imports() -> None:
    root = Path(__file__).resolve().parents[3] / "src" / "fetchcore"
    relatives = (
        "adapters/retrieval/local/backend.py",
        "adapters/retrieval/solr/backend.py",
        "adapters/retrieval/opensearch/backend.py",
        "adapters/retrieval/qdrant/backend.py",
        "adapters/retrieval/solr/projection.py",
        "adapters/retrieval/qdrant/sparse.py",
    )
    for relative in relatives:
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
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
        )



def test_local_backend_executes_standalone(tmp_path: Path) -> None:
    (tmp_path / "needle.py").write_text(
        "class MedicationHistoryService:\n"
        "    def get_history(self):\n"
        "        return True\n",
        encoding="utf-8",
    )
    response = LocalSemanticBackend().search(
        SearchIntent(exact_terms=("MedicationHistoryService",)),
        SearchScope(
            workspace=tmp_path,
            revision="rev-a",
            repository="example/repo",
            knowledge_types=("code",),
            max_results=10,
            settings={"file_patterns": ["*.py"]},
        ),
        (),
    )

    assert response.backend == "local-semantic"
    assert response.status == "success"
    assert response.results
    assert response.results[0].source_ref == "repository://example/repo/needle.py"
