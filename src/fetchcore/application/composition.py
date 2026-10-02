"""Built-in backend composition for the retrieval application boundary."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from fetchcore.retrieval.config import EmbeddingConfig, RetrievalConfig
from fetchcore.retrieval.contracts import KnowledgeSearchBackend, KnowledgeSearchError
from fetchcore.retrieval.embedding import EmbeddingProvider

if TYPE_CHECKING:
    from fetchcore.adapters.retrieval.qdrant.backend import QdrantVectorSettings


BackendFactory = Callable[[], KnowledgeSearchBackend]


def _local_semantic_backend() -> KnowledgeSearchBackend:
    from fetchcore.adapters.retrieval.local.backend import LocalSemanticBackend

    return LocalSemanticBackend()


def _solr_backend() -> KnowledgeSearchBackend:
    from fetchcore.adapters.retrieval.solr.backend import SolrBackend

    return SolrBackend(embedding_factory=solr_embedding_provider)


def _opensearch_backend() -> KnowledgeSearchBackend:
    from fetchcore.adapters.retrieval.opensearch.backend import OpenSearchBackend

    return OpenSearchBackend(embedding_factory=opensearch_embedding_provider)


def _qdrant_vector_backend() -> KnowledgeSearchBackend:
    from fetchcore.adapters.retrieval.qdrant.backend import QdrantVectorBackend

    return QdrantVectorBackend(embedding_factory=_qdrant_embedding_provider)


def create_embedding_provider(
    embedding: EmbeddingConfig,
    *,
    backend_id: str,
    backend_name: str,
    url: str | None = None,
) -> EmbeddingProvider:
    """Select an embedding implementation at the application composition boundary."""
    if embedding.provider != "ollama":
        raise KnowledgeSearchError(
            f"configured embedding provider is not supported by {backend_name} dense retrieval",
            category="unsupported_capability",
            metadata={
                "backend": backend_id,
                "capability": f"embedding_provider:{embedding.provider}",
            },
        )
    from fetchcore.adapters.embedding.ollama.provider import (
        OllamaEmbeddingProvider,
        OllamaEmbeddingSettings,
    )

    provider = OllamaEmbeddingProvider(
        OllamaEmbeddingSettings(
            url=url or os.environ.get("OLLAMA_URL", OllamaEmbeddingSettings.url),
            model=embedding.model,
        ),
        timeout_seconds=embedding.timeout_seconds,
        request_batch_size=embedding.request_batch_size,
    )
    if provider.provider_id != embedding.provider:
        raise KnowledgeSearchError(
            "embedding composition returned a mismatched provider",
            category="schema_mismatch",
            metadata={
                "requested_provider": embedding.provider,
                "actual_provider": provider.provider_id,
            },
        )
    return provider


def configured_ollama_provider(
    retrieval: RetrievalConfig, *, backend_id: str, backend_name: str,
) -> EmbeddingProvider:
    """Retain the historical factory signature for existing integrations."""
    return create_embedding_provider(
        retrieval.dense.embedding, backend_id=backend_id, backend_name=backend_name
    )


def solr_embedding_provider(retrieval: RetrievalConfig) -> EmbeddingProvider:
    """Compose the provider used by Solr dense retrieval."""
    return configured_ollama_provider(retrieval, backend_id="solr", backend_name="Solr")


def opensearch_embedding_provider(retrieval: RetrievalConfig) -> EmbeddingProvider:
    """Compose the provider used by OpenSearch dense retrieval."""
    return configured_ollama_provider(
        retrieval, backend_id="opensearch", backend_name="OpenSearch"
    )


def _qdrant_embedding_provider(
    settings: QdrantVectorSettings, embedding: EmbeddingConfig,
) -> EmbeddingProvider:
    if embedding.provider != "ollama":
        raise KnowledgeSearchError(
            "configured embedding provider is not supported by Qdrant vector backend",
            category="unsupported_capability",
            metadata={
                "backend": "qdrant-vector",
                "capability": f"embedding_provider:{embedding.provider}",
            },
        )
    return create_embedding_provider(
        EmbeddingConfig(
            provider=embedding.provider,
            model=settings.embedding_model,
            timeout_seconds=settings.timeout_seconds,
        ),
        backend_id="qdrant-vector",
        backend_name="Qdrant vector",
        url=settings.embedding_url,
    )


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
