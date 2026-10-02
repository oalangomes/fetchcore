"""Explicit built-in implementation registries and application composition."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from fetchcore.retrieval.config import EmbeddingConfig, RetrievalConfig
from fetchcore.retrieval.contracts import KnowledgeSearchBackend, KnowledgeSearchError
from fetchcore.retrieval.embedding import EmbeddingProvider

if TYPE_CHECKING:
    from fetchcore.adapters.retrieval.qdrant.backend import QdrantVectorSettings

BackendFactory = Callable[[], KnowledgeSearchBackend]
EmbeddingFactory = Callable[[EmbeddingConfig, Mapping[str, object]], EmbeddingProvider]


def _ollama_factory(
    embedding: EmbeddingConfig, settings: Mapping[str, object]
) -> EmbeddingProvider:
    from fetchcore.adapters.embedding.ollama.provider import (
        OllamaEmbeddingProvider,
        OllamaEmbeddingSettings,
    )
    from .runtime_config import OllamaRuntimeSettings

    runtime = OllamaRuntimeSettings.from_mapping(settings)
    return OllamaEmbeddingProvider(
        OllamaEmbeddingSettings(
            url=runtime.url,
            model=embedding.model,
            timeout_seconds=runtime.timeout_seconds,
            request_batch_size=runtime.request_batch_size,
        ),
        timeout_seconds=embedding.timeout_seconds,
        request_batch_size=embedding.request_batch_size,
    )


def builtin_embedding_provider_factories() -> dict[str, EmbeddingFactory]:
    return {"ollama": _ollama_factory}


class EmbeddingProviderRegistry:
    """Resolve provider identity through explicit, injectable factories."""

    def __init__(
        self,
        factories: Mapping[str, EmbeddingFactory] | None = None,
        *,
        settings: Mapping[str, Mapping[str, object]] | None = None,
    ) -> None:
        self._factories = dict(
            builtin_embedding_provider_factories() if factories is None else factories
        )
        self._settings = dict(settings or {})

    def create(
        self, embedding: EmbeddingConfig, *, backend_id: str, backend_name: str
    ) -> EmbeddingProvider:
        factory = self._factories.get(embedding.provider)
        if factory is None:
            raise KnowledgeSearchError(
                f"configured embedding provider is not supported by {backend_name} dense retrieval",
                category="unsupported_capability",
                metadata={"backend": backend_id, "capability": f"embedding_provider:{embedding.provider}"},
            )
        provider = factory(embedding, self._settings.get(embedding.provider, {}))
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


def create_embedding_provider(
    embedding: EmbeddingConfig,
    *,
    backend_id: str,
    backend_name: str,
    url: str | None = None,
) -> EmbeddingProvider:
    """Keep the historical helper while routing selection through the registry."""
    from .runtime_config import ollama_settings_from_environment

    settings: dict[str, Mapping[str, object]] = {}
    if embedding.provider == "ollama":
        resolved_url = ollama_settings_from_environment().url if url is None else url
        settings["ollama"] = {"url": resolved_url}
    return EmbeddingProviderRegistry(settings=settings).create(
        embedding, backend_id=backend_id, backend_name=backend_name
    )


def configured_ollama_provider(
    retrieval: RetrievalConfig, *, backend_id: str, backend_name: str,
) -> EmbeddingProvider:
    return create_embedding_provider(
        retrieval.dense.embedding, backend_id=backend_id, backend_name=backend_name
    )


def solr_embedding_provider(retrieval: RetrievalConfig) -> EmbeddingProvider:
    return configured_ollama_provider(retrieval, backend_id="solr", backend_name="Solr")


def opensearch_embedding_provider(retrieval: RetrievalConfig) -> EmbeddingProvider:
    return configured_ollama_provider(
        retrieval, backend_id="opensearch", backend_name="OpenSearch"
    )


def _qdrant_embedding_provider(
    settings: QdrantVectorSettings, embedding: EmbeddingConfig,
) -> EmbeddingProvider:
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


def builtin_backend_factories(
    providers: EmbeddingProviderRegistry | None = None,
) -> dict[str, BackendFactory]:
    """Return the code-owned backend catalog without constructing adapters eagerly."""
    from fetchcore.adapters.retrieval.local.backend import LocalSemanticBackend
    from fetchcore.adapters.retrieval.opensearch.backend import OpenSearchBackend
    from fetchcore.adapters.retrieval.qdrant.backend import QdrantVectorBackend
    from fetchcore.adapters.retrieval.solr.backend import SolrBackend

    registry = providers or EmbeddingProviderRegistry()

    def qdrant_provider(
        settings: QdrantVectorSettings, embedding: EmbeddingConfig
    ) -> EmbeddingProvider:
        if providers is None:
            return _qdrant_embedding_provider(settings, embedding)
        return registry.create(
            EmbeddingConfig(
                provider=embedding.provider,
                model=settings.embedding_model,
                timeout_seconds=settings.timeout_seconds,
            ),
            backend_id="qdrant-vector",
            backend_name="Qdrant vector",
        )

    return {
        "local-semantic": LocalSemanticBackend,
        "solr": lambda: SolrBackend(
            embedding_factory=lambda retrieval: registry.create(
                retrieval.dense.embedding, backend_id="solr", backend_name="Solr"
            )
        ),
        "opensearch": lambda: OpenSearchBackend(
            embedding_factory=lambda retrieval: registry.create(
                retrieval.dense.embedding, backend_id="opensearch", backend_name="OpenSearch"
            )
        ),
        "qdrant-vector": lambda: QdrantVectorBackend(embedding_factory=qdrant_provider),
    }


class BackendRegistry:
    """Resolve backend ids behind an application-level factory boundary."""

    def __init__(
        self,
        factories: Mapping[str, BackendFactory] | None = None,
        *,
        providers: EmbeddingProviderRegistry | None = None,
    ) -> None:
        self._factories = dict(
            builtin_backend_factories(providers) if factories is None else factories
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
