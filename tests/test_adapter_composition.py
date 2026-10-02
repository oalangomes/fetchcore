"""Capability composition and provider injection regression checks."""

from __future__ import annotations

import pytest

from fetchcore.adapters.embedding.ollama.provider import OllamaEmbeddingProvider
from fetchcore.adapters.retrieval.local.backend import LocalSemanticBackend
from fetchcore.adapters.retrieval.opensearch.backend import OpenSearchBackend
from fetchcore.adapters.retrieval.qdrant.backend import (
    QdrantVectorBackend,
    QdrantVectorSettings,
)
from fetchcore.adapters.retrieval.solr.backend import SolrBackend
from fetchcore.application.composition import BackendRegistry, create_embedding_provider
from fetchcore.retrieval.config import EmbeddingConfig, RetrievalConfig
from fetchcore.retrieval.contracts import KnowledgeSearchBackend, KnowledgeSearchError
from fetchcore.retrieval.embedding import EmbeddingProvider


class FixtureEmbeddingProvider:
    provider_id = "fixture"
    model_id = "fixture-model"

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((float(len(text)),) for text in texts)


def test_builtin_backend_registry_preserves_ids_and_implementations() -> None:
    expected = {
        "local-semantic": LocalSemanticBackend,
        "solr": SolrBackend,
        "opensearch": OpenSearchBackend,
        "qdrant-vector": QdrantVectorBackend,
    }
    registry = BackendRegistry()
    for backend_id, implementation in expected.items():
        backend: KnowledgeSearchBackend = registry.create(backend_id)
        assert isinstance(backend, implementation)
        assert backend.backend_id == backend_id


def test_backend_registry_rejects_mismatched_factory() -> None:
    with pytest.raises(KnowledgeSearchError) as captured:
        BackendRegistry({"wrong": LocalSemanticBackend}).create("wrong")
    assert captured.value.category == "schema_mismatch"


def test_embedding_provider_is_structurally_compatible_and_composed() -> None:
    provider: EmbeddingProvider = create_embedding_provider(
        EmbeddingConfig(model="embeddinggemma"), backend_id="solr", backend_name="Solr"
    )
    assert isinstance(provider, OllamaEmbeddingProvider)
    assert provider.provider_id == "ollama"


def test_unknown_embedding_provider_fails_closed() -> None:
    with pytest.raises(KnowledgeSearchError) as captured:
        create_embedding_provider(
            EmbeddingConfig(provider="missing"), backend_id="solr", backend_name="Solr"
        )
    assert captured.value.category == "unsupported_capability"
    assert captured.value.metadata["capability"] == "embedding_provider:missing"


def test_embedding_composition_rejects_provider_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(OllamaEmbeddingProvider, "provider_id", "wrong")
    with pytest.raises(KnowledgeSearchError) as captured:
        create_embedding_provider(
            EmbeddingConfig(), backend_id="solr", backend_name="Solr"
        )
    assert captured.value.category == "schema_mismatch"


def test_retrieval_adapters_accept_the_embedding_port() -> None:
    provider: EmbeddingProvider = FixtureEmbeddingProvider()
    retrieval = RetrievalConfig.from_mapping({"dense": {"enabled": True}})
    solr = SolrBackend(embedding_factory=lambda config: provider)
    opensearch = OpenSearchBackend(embedding_factory=lambda config: provider)
    qdrant = QdrantVectorBackend(embedding_factory=lambda settings, config: provider)

    assert solr.embedding_factory(retrieval).embed(("solr",)) == ((4.0,),)
    assert opensearch.embedding_factory(retrieval).embed(("open",)) == ((4.0,),)
    assert qdrant.embedding_factory(
        QdrantVectorSettings(), EmbeddingConfig(provider="fixture")
    ).embed(("qdrant",)) == ((6.0,),)


def test_qdrant_legacy_embedding_settings_compose_equivalently() -> None:
    backend = BackendRegistry().create("qdrant-vector")
    assert isinstance(backend, QdrantVectorBackend)
    settings = QdrantVectorSettings.from_mapping(
        {"embedding_url": "http://127.0.0.1:11434", "embedding_model": "legacy-model"}
    )
    provider = backend.embedding_factory(settings, EmbeddingConfig(model="legacy-model"))
    assert isinstance(provider, OllamaEmbeddingProvider)
    assert provider.url == "http://127.0.0.1:11434"
    assert provider.model_id == "legacy-model"

    with pytest.raises(KnowledgeSearchError) as captured:
        backend.embedding_factory(settings, EmbeddingConfig(provider="missing"))
    assert captured.value.category == "unsupported_capability"
    assert captured.value.metadata["capability"] == "embedding_provider:missing"


def test_historical_embedding_factory_exports_remain_available() -> None:
    from fetchcore.application.composition import (
        configured_ollama_provider,
        opensearch_embedding_provider,
        solr_embedding_provider,
    )
    from fetchcore.backends.ollama import configured_ollama_provider as old_ollama
    from fetchcore.backends.opensearch import opensearch_embedding_provider as old_opensearch
    from fetchcore.backends.solr import solr_embedding_provider as old_solr

    assert old_ollama is configured_ollama_provider
    assert old_opensearch is opensearch_embedding_provider
    assert old_solr is solr_embedding_provider
