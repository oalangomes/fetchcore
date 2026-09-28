from __future__ import annotations

import ast
from io import BytesIO
import json
from pathlib import Path
from typing import Self
from urllib.error import HTTPError
from urllib.request import Request

import pytest

import fetchcore.embedding as embedding_module
from fetchcore.embedding import (
    VECTOR_EMBEDDING_SERIALIZATION_VERSION,
    OllamaEmbeddingProvider,
    OllamaEmbeddingSettings,
    ProjectionEmbeddingMode,
    ProjectionV3EmbeddingSerializer,
    vector_embedding_serializer_fingerprint,
)


def test_embedding_contract_is_backend_neutral() -> None:
    tree = ast.parse(Path(embedding_module.__file__).read_text())
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert not {
        "agentsorch.knowledge.backends.qdrant_vector",
        "agentsorch.knowledge.backends.solr",
        "agentsorch.knowledge.backends.opensearch",
    }.intersection(imported_modules)


def test_embedding_identity_defaults_are_explicit_and_stable() -> None:
    settings = OllamaEmbeddingSettings()

    assert settings.url == "http://localhost:11434"
    assert settings.model == "embeddinggemma"
    assert settings.timeout_seconds == 30.0
    assert settings.request_batch_size == 32
    assert VECTOR_EMBEDDING_SERIALIZATION_VERSION == "2"
    assert (
        vector_embedding_serializer_fingerprint(ProjectionEmbeddingMode.PROJECTION_V3)
        == ProjectionV3EmbeddingSerializer().serializer_fingerprint
    )


def test_ollama_embedding_batches_large_requests_without_reordering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_batches: list[list[str]] = []

    class Response:
        def __init__(self, raw: bytes) -> None:
            self.raw = raw

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def read(self) -> bytes:
            return self.raw

    def fake_urlopen(request: Request, *, timeout: float) -> Response:
        assert timeout == 30.0
        raw_data = request.data
        assert isinstance(raw_data, bytes)
        payload = json.loads(raw_data.decode("utf-8"))
        inputs = payload["input"]
        assert isinstance(inputs, list)
        batch = [str(item) for item in inputs]
        observed_batches.append(batch)
        embeddings = [[float(item.removeprefix("item-"))] for item in batch]
        return Response(json.dumps({"embeddings": embeddings}).encode("utf-8"))

    monkeypatch.setattr(embedding_module, "urlopen", fake_urlopen)
    provider = OllamaEmbeddingProvider(model="embeddinggemma")
    texts = tuple(f"item-{index}" for index in range(65))

    vectors = provider.embed(texts)

    assert [len(batch) for batch in observed_batches] == [32, 32, 1]
    assert tuple(vector[0] for vector in vectors) == tuple(float(index) for index in range(65))


def test_ollama_embedding_rejects_non_positive_request_batch_size() -> None:
    with pytest.raises(Exception) as captured:
        OllamaEmbeddingProvider(request_batch_size=0)

    error = captured.value
    assert getattr(error, "category", None) == "schema_mismatch"


def test_ollama_embedding_http_error_preserves_bounded_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise HTTPError(
            "http://localhost:11434/api/embed",
            400,
            "Bad Request",
            hdrs=None,
            fp=BytesIO(
                b'{"error":"input exceeds maximum context length and cannot be truncated further"}'
            ),
        )

    monkeypatch.setattr(embedding_module, "urlopen", fail)
    provider = OllamaEmbeddingProvider(model="embeddinggemma")
    texts = ("a" * 123, "b" * 456)

    with pytest.raises(Exception) as captured:
        provider.embed(texts)

    error = captured.value
    assert getattr(error, "category", None) == "embedding_failed"
    metadata = getattr(error, "metadata", {})
    assert metadata["http_status"] == 400
    assert metadata["embedding_model"] == "embeddinggemma"
    assert metadata["input_count"] == 2
    assert metadata["batch_offset"] == 0
    assert metadata["total_input_count"] == 2
    assert metadata["max_input_chars"] == 456
    assert metadata["total_input_chars"] == 579
    assert (
        metadata["provider_error"]
        == "input exceeds maximum context length and cannot be truncated further"
    )
