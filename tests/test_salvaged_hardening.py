from __future__ import annotations

from io import BytesIO
from typing import Self
from urllib.error import HTTPError

import pytest

import fetchcore.embedding as embedding_module
from fetchcore.backends.opensearch import opensearch_embedding_provider
from fetchcore.backends.solr import solr_embedding_provider
from fetchcore.embedding import OllamaEmbeddingProvider
from fetchcore.repository.evidence import extract_source_structure
from fetchcore.repository.index_operations import prepare_repository_index_runtime
from fetchcore.repository.indexing import RepositoryIndexConfig
from fetchcore.repository.sap_commerce_pack import SapCommerceKnowledgePack
from fetchcore.repository.structure import SourceStructure, StructuralSymbolSpan
from fetchcore.retrieval import RetrievalConfig, RetrievalConfigurationError


def _retrieval_with_transport_controls() -> RetrievalConfig:
    return RetrievalConfig.from_mapping(
        {
            "dense": {
                "enabled": True,
                "embedding": {
                    "provider": "ollama",
                    "model": "embeddinggemma",
                    "request_batch_size": 7,
                    "timeout_seconds": 45,
                },
            }
        }
    )


def test_embedding_transport_controls_round_trip() -> None:
    config = _retrieval_with_transport_controls()
    embedding = config.dense.embedding

    assert embedding.provider == "ollama"
    assert embedding.model == "embeddinggemma"
    assert embedding.request_batch_size == 7
    assert embedding.timeout_seconds == 45.0
    assert embedding.to_mapping() == {
        "provider": "ollama",
        "model": "embeddinggemma",
        "timeout_seconds": 45.0,
        "request_batch_size": 7,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_batch_size", 0),
        ("timeout_seconds", 0),
        ("timeout_seconds", float("inf")),
    ],
)
def test_embedding_transport_controls_fail_closed(
    field: str,
    value: object,
) -> None:
    with pytest.raises(RetrievalConfigurationError):
        RetrievalConfig.from_mapping(
            {
                "dense": {
                    "enabled": True,
                    "embedding": {
                        "provider": "ollama",
                        "model": "embeddinggemma",
                        field: value,
                    },
                }
            }
        )


def test_ollama_embedding_retries_known_transient_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    class Response:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def read(self) -> bytes:
            return b'{"embeddings":[[1.0,2.0]]}'

    def flaky(*args: object, **kwargs: object) -> object:
        nonlocal calls
        del args, kwargs
        calls += 1
        if calls == 1:
            raise HTTPError(
                "http://localhost:11434/api/embed",
                400,
                "Bad Request",
                hdrs=None,
                fp=BytesIO(
                    b'{"error":"health resp: Get \\"http://127.0.0.1:59055/health\\": connectex"}'
                ),
            )
        return Response()

    monkeypatch.setattr(embedding_module, "urlopen", flaky)
    monkeypatch.setattr(embedding_module, "sleep", sleeps.append)

    assert OllamaEmbeddingProvider(model="embeddinggemma").embed(("hello",)) == (
        (1.0, 2.0),
    )
    assert calls == 2
    assert sleeps == [0.25]


def test_ollama_embedding_does_not_retry_generic_bad_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def fail(*args: object, **kwargs: object) -> object:
        nonlocal calls
        del args, kwargs
        calls += 1
        raise HTTPError(
            "http://localhost:11434/api/embed",
            400,
            "Bad Request",
            hdrs=None,
            fp=BytesIO(b'{"error":"invalid embedding request"}'),
        )

    monkeypatch.setattr(embedding_module, "urlopen", fail)
    monkeypatch.setattr(embedding_module, "sleep", sleeps.append)

    with pytest.raises(Exception) as captured:
        OllamaEmbeddingProvider(model="embeddinggemma").embed(("hello",))

    assert getattr(captured.value, "category", None) == "embedding_failed"
    assert calls == 1
    assert sleeps == []


def test_solr_and_opensearch_honor_embedding_transport_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:11434")
    retrieval = _retrieval_with_transport_controls()

    for provider in (
        solr_embedding_provider(retrieval),
        opensearch_embedding_provider(retrieval),
    ):
        assert isinstance(provider, OllamaEmbeddingProvider)
        assert provider.url == "http://127.0.0.1:11434"
        assert provider.model_id == "embeddinggemma"
        assert provider.request_batch_size == 7
        assert provider.timeout_seconds == 45.0


def test_overlapping_rich_chunk_spans_fall_back_to_safe_chunking() -> None:
    class UnsafeRichExtractor:
        def extract_structure(
            self,
            content: str,
            *,
            path: str | None = None,
        ) -> SourceStructure:
            assert content
            assert path == "unsafe.go"
            symbols = (
                StructuralSymbolSpan("first", "class", 1, 3),
                StructuralSymbolSpan("second", "class", 2, 4),
            )
            return SourceStructure(
                language="go",
                symbols=symbols,
                chunk_symbols=symbols,
            )

    structure = extract_source_structure(
        UnsafeRichExtractor(),
        "one\ntwo\nthree\nfour\n",
        path="unsafe.go",
    )

    assert structure is not None
    assert tuple(item.name for item in structure.symbols) == ("first", "second")
    assert structure.chunk_symbols == ()


def test_base_index_capacity_is_applied_before_knowledge_pack_treatment(
    tmp_path,
) -> None:
    (tmp_path / "hybris" / "bin" / "custom").mkdir(parents=True)
    base_config = RepositoryIndexConfig(
        max_files=65_000,
        max_file_bytes=1_000_000,
        max_total_bytes=512_000_000,
    )

    generic = prepare_repository_index_runtime(
        tmp_path,
        repository="acme/sap-commerce",
        branch="main",
        revision="rev-a",
        knowledge_packs=(),
        base_config=base_config,
    )
    treated = prepare_repository_index_runtime(
        tmp_path,
        repository="acme/sap-commerce",
        branch="main",
        revision="rev-a",
        knowledge_packs=(SapCommerceKnowledgePack(),),
        base_config=base_config,
    )

    expected = (65_000, 1_000_000, 512_000_000)
    for runtime in (generic, treated):
        assert (
            runtime.indexer.config.max_files,
            runtime.indexer.config.max_file_bytes,
            runtime.indexer.config.max_total_bytes,
        ) == expected

    assert generic.active_knowledge_packs == ()
    assert treated.active_knowledge_packs == (("sap.commerce", "8"),)
    assert generic.fingerprint != treated.fingerprint
