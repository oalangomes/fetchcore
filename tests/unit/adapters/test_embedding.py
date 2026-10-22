from __future__ import annotations

import ast
import socket
from io import BytesIO
import json
from pathlib import Path
from typing import Self
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

import fetchcore.embedding as embedding_module
import fetchcore.backends.ollama as ollama_module
from fetchcore.embedding import (
    VECTOR_EMBEDDING_SERIALIZATION_VERSION,
    OllamaEmbeddingProvider,
    OllamaEmbeddingSettings,
    ProjectionEmbeddingMode,
    ProjectionV3EmbeddingSerializer,
    vector_embedding_serializer_fingerprint,
)
from fetchcore.retrieval import RetrievalConfig, RetrievalConfigurationError
from fetchcore.retrieval.contracts import KnowledgeSearchError


def _retrieval_with_transport_controls() -> RetrievalConfig:
    return RetrievalConfig.from_mapping(
        {
            "dense": {
                "enabled": True,
                "embedding": {
                    "provider": "ollama",
                    "model": "embeddinggemma",
                    "dimensions": 768,
                    "request_batch_size": 7,
                    "timeout_seconds": 45,
                },
            }
        }
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

    monkeypatch.setattr(ollama_module, "urlopen", fake_urlopen)
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


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_ollama_embedding_rejects_invalid_timeout(timeout: float) -> None:
    with pytest.raises(KnowledgeSearchError) as captured:
        OllamaEmbeddingProvider(timeout_seconds=timeout)

    assert captured.value.category == "schema_mismatch"


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

    monkeypatch.setattr(ollama_module, "urlopen", fail)
    provider = OllamaEmbeddingProvider(model="embeddinggemma")
    texts = ("a" * 123, "b" * 456)

    with pytest.raises(Exception) as captured:
        provider.embed(texts)

    error = captured.value
    assert getattr(error, "category", None) == "embedding_failed"
    metadata = getattr(error, "metadata", {})
    assert metadata["http_status"] == 400
    assert metadata["provider_error_type"] == "context_length_exceeded"
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


@pytest.mark.parametrize("message,reason", [
    ("input exceeds maximum context length: private source", "context_length_exceeded"),
    ("out of memory: private path", "provider_resource_exhausted"),
    ("do embedding request: EOF private endpoint", "transient_provider_failure"),
    ("private source response", None),
])
def test_ollama_failure_reason_never_copies_provider_text(message, reason):
    assert ollama_module._ollama_embedding_failure_reason(message) == reason


def test_embedding_transport_controls_round_trip() -> None:
    config = _retrieval_with_transport_controls()
    embedding = config.dense.embedding

    assert embedding.provider == "ollama"
    assert embedding.model == "embeddinggemma"
    assert embedding.request_batch_size == 7
    assert embedding.timeout_seconds == 45.0
    assert embedding.dimensions == 768
    assert embedding.to_mapping() == {
        "provider": "ollama",
        "model": "embeddinggemma",
        "dimensions": 768,
        "timeout_seconds": 45.0,
        "request_batch_size": 7,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_batch_size", 0),
        ("dimensions", 0),
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

    monkeypatch.setattr(ollama_module, "urlopen", flaky)
    monkeypatch.setattr(ollama_module, "sleep", sleeps.append)

    assert OllamaEmbeddingProvider(model="embeddinggemma").embed(("hello",)) == (
        (1.0, 2.0),
    )
    assert calls == 2
    assert sleeps == [0.25]


@pytest.mark.parametrize(
    ("status", "provider_error"),
    [(400, "invalid embedding request"), (404, "model not found")],
)
def test_ollama_embedding_does_not_retry_permanent_http_error(
    monkeypatch: pytest.MonkeyPatch, status: int, provider_error: str,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def fail(*args: object, **kwargs: object) -> object:
        nonlocal calls
        del args, kwargs
        calls += 1
        raise HTTPError(
            "http://localhost:11434/api/embed",
            status,
            "Request Failed",
            hdrs=None,
            fp=BytesIO(json.dumps({"error": provider_error}).encode("utf-8")),
        )

    monkeypatch.setattr(ollama_module, "urlopen", fail)
    monkeypatch.setattr(ollama_module, "sleep", sleeps.append)

    with pytest.raises(Exception) as captured:
        OllamaEmbeddingProvider(model="embeddinggemma").embed(("hello",))

    assert getattr(captured.value, "category", None) == "embedding_failed"
    assert captured.value.metadata["http_status"] == status
    assert captured.value.metadata["attempt"] == 1
    assert calls == 1
    assert sleeps == []


def test_ollama_embedding_does_not_retry_invalid_response(
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
            return b'{"embeddings":[]}'

    def invalid(*args: object, **kwargs: object) -> Response:
        nonlocal calls
        del args, kwargs
        calls += 1
        return Response()

    monkeypatch.setattr(ollama_module, "urlopen", invalid)
    monkeypatch.setattr(ollama_module, "sleep", sleeps.append)

    with pytest.raises(KnowledgeSearchError) as captured:
        OllamaEmbeddingProvider().embed(("hello",))

    assert captured.value.category == "response_parsing_failure"
    assert calls == 1
    assert sleeps == []


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("timed out"),
        socket.timeout("timed out"),  # noqa: UP041 - verify the socket alias explicitly
        URLError(TimeoutError("timed out")),
        URLError(ConnectionResetError("reset")),
        URLError(ConnectionRefusedError("refused")),
        URLError(socket.gaierror(socket.EAI_AGAIN, "temporary DNS failure")),
    ],
)
def test_ollama_embedding_retries_transient_transport_then_preserves_vector_order(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    calls: list[list[str]] = []
    sleeps: list[float] = []

    class Response:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def read(self) -> bytes:
            return b'{"embeddings":[[1.0],[2.0]]}'

    def flaky(request: Request, *, timeout: float) -> Response:
        assert timeout == 30.0
        assert isinstance(request.data, bytes)
        calls.append(json.loads(request.data)["input"])
        if len(calls) == 1:
            raise failure
        return Response()

    monkeypatch.setattr(ollama_module, "urlopen", flaky)
    monkeypatch.setattr(ollama_module, "sleep", sleeps.append)

    assert OllamaEmbeddingProvider().embed(("first", "second")) == ((1.0,), (2.0,))
    assert calls == [["first", "second"], ["first", "second"]]
    assert sleeps == [0.25]


def test_ollama_embedding_exhausts_transient_transport_retries_with_safe_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def fail(*args: object, **kwargs: object) -> object:
        nonlocal calls
        del args, kwargs
        calls += 1
        raise URLError(TimeoutError("private request content timed out"))

    monkeypatch.setattr(ollama_module, "urlopen", fail)
    monkeypatch.setattr(ollama_module, "sleep", sleeps.append)

    with pytest.raises(KnowledgeSearchError) as captured:
        OllamaEmbeddingProvider().embed(("secret query",))

    error = captured.value
    assert error.category == "timeout"
    assert error.metadata == {
        "embedding_model": "embeddinggemma",
        "input_count": 1,
        "batch_offset": 0,
        "total_input_count": 1,
        "max_input_chars": 12,
        "total_input_chars": 12,
        "attempt": 3,
        "max_attempts": 3,
        "cause_type": "TimeoutError",
        "cause_errno": None,
        "retries_exhausted": True,
    }
    assert "private" not in str(error)
    assert "secret" not in str(error)
    assert calls == 3
    assert sleeps == [0.25, 1.0]


def test_ollama_embedding_does_not_retry_permanent_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def fail(*args: object, **kwargs: object) -> object:
        nonlocal calls
        del args, kwargs
        calls += 1
        raise URLError(socket.gaierror(socket.EAI_NONAME, "unknown host"))

    monkeypatch.setattr(ollama_module, "urlopen", fail)
    monkeypatch.setattr(ollama_module, "sleep", sleeps.append)

    with pytest.raises(KnowledgeSearchError) as captured:
        OllamaEmbeddingProvider().embed(("hello",))

    assert captured.value.category == "unavailable"
    assert captured.value.metadata["attempt"] == 1
    assert captured.value.metadata["cause_type"] == "gaierror"
    assert captured.value.metadata["retries_exhausted"] is False
    assert calls == 1
    assert sleeps == []
