"""Built-in backend composition for the retrieval application boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from fetchcore.retrieval.contracts import KnowledgeSearchBackend, KnowledgeSearchError


BackendFactory = Callable[[], KnowledgeSearchBackend]


def _local_semantic_backend() -> KnowledgeSearchBackend:
    from fetchcore.backends.local_semantic import LocalSemanticBackend

    return LocalSemanticBackend()


def _solr_backend() -> KnowledgeSearchBackend:
    from fetchcore.backends.solr import SolrBackend

    return SolrBackend()


def _opensearch_backend() -> KnowledgeSearchBackend:
    from fetchcore.backends.opensearch import OpenSearchBackend

    return OpenSearchBackend()


def _qdrant_vector_backend() -> KnowledgeSearchBackend:
    from fetchcore.backends.qdrant_vector import QdrantVectorBackend

    return QdrantVectorBackend()


def builtin_backend_factories() -> dict[str, BackendFactory]:
    """Return the built-in retrieval backends without constructing them eagerly."""
    return {
        "local-semantic": _local_semantic_backend,
        "solr": _solr_backend,
        "opensearch": _opensearch_backend,
        "qdrant-vector": _qdrant_vector_backend,
    }


class BackendRegistry:
    """Resolve backend ids behind an application-level factory boundary."""

    def __init__(
        self,
        factories: Mapping[str, BackendFactory] | None = None,
    ) -> None:
        self._factories = dict(
            builtin_backend_factories() if factories is None else factories
        )

    def create(self, backend_id: str) -> KnowledgeSearchBackend:
        factory = self._factories.get(backend_id)
        if factory is None:
            raise KnowledgeSearchError(
                f"unsupported FetchCore backend {backend_id!r}",
                category="unsupported_backend",
                metadata={"backend": backend_id},
            )
        backend = factory()
        if backend.backend_id != backend_id:
            raise KnowledgeSearchError(
                "backend registry returned a mismatched backend",
                category="schema_mismatch",
                metadata={
                    "requested_backend": backend_id,
                    "actual_backend": backend.backend_id,
                },
            )
        return backend
