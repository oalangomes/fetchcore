"""LiteLLM/Bifrost contracts: batch ordering, transport failures and runtime composition."""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Self
from urllib.error import HTTPError

import pytest

import fetchcore.adapters.embedding.openai_compatible.provider as gateway_module
from fetchcore.adapters.embedding.openai_compatible.provider import (
    GatewayEmbeddingProvider,
    GatewayEmbeddingSettings,
)
from fetchcore.application.composition import BackendRegistry, EmbeddingProviderRegistry
from fetchcore.application.runtime_config import (
    embedding_provider_settings, load_runtime_config,
)
from fetchcore.retrieval.config import EmbeddingConfig, RetrievalConfig
from fetchcore.retrieval.contracts import KnowledgeSearchError


class Response:
    def __init__(self, value: dict[str, object]) -> None:
        self.value = value

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.value).encode()


@pytest.mark.parametrize("gateway,port", [("litellm", 4000), ("bifrost", 8080)])
def test_gateway_embedding_batches_and_reorders(
    monkeypatch: pytest.MonkeyPatch, gateway: str, port: int,
) -> None:
    requests: list[tuple[str, dict[str, str], dict[str, object]]] = []

    def fake_urlopen(request: object, timeout: float) -> Response:
        assert timeout == 12.0
        url = request.full_url
        headers = dict(request.header_items())
        payload = json.loads(request.data)
        requests.append((url, headers, payload))
        return Response({"data": [
            {"index": i, "embedding": [float(len(text))] * 3}
            for i, text in reversed(list(enumerate(payload["input"])))
        ]})

    monkeypatch.setattr(gateway_module, "urlopen", fake_urlopen)
    provider = GatewayEmbeddingProvider(GatewayEmbeddingSettings(
        provider_id=gateway,
        base_url=f"http://localhost:{port}/v1",
        model="stable-embedding-v1",
        api_key="secret",
        allow_source_upload=True,
        timeout_seconds=12,
        request_batch_size=2,
        dimensions=3,
    ))
    assert provider.embed(("a", "bb", "ccc")) == ((1.0, 1.0, 1.0), (2.0, 2.0, 2.0), (3.0, 3.0, 3.0))
    assert provider.embed(()) == ()
    assert [len(r[2]["input"]) for r in requests] == [2, 1]
    assert all(r[0] == f"http://localhost:{port}/v1/embeddings" for r in requests)
    assert all(r[1]["Authorization"] == "Bearer secret" for r in requests)
    assert all(r[2]["model"] == "stable-embedding-v1" for r in requests)


@pytest.mark.parametrize("bad_rows", [
    [{"index": 0, "embedding": [1.0]}, {"index": 0, "embedding": [1.0]}],
    [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": [float("nan")]}],
    [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": [2.0, 3.0]}],
    [{"index": "0", "embedding": [1.0]}, {"index": 1, "embedding": [1.0]}],
])
def test_gateway_rejects_corrupt_vectors(monkeypatch: pytest.MonkeyPatch, bad_rows: list[object]) -> None:
    monkeypatch.setattr(gateway_module, "urlopen", lambda *_args, **_kwargs: Response({"data": bad_rows}))
    provider = GatewayEmbeddingProvider(GatewayEmbeddingSettings(
        provider_id="litellm", base_url="http://localhost:4000/v1",
        model="fixed", api_key=None, allow_source_upload=True,
    ))
    with pytest.raises(KnowledgeSearchError) as captured:
        provider.embed(("a", "b"))
    assert captured.value.category in {"response_parsing_failure", "schema_mismatch"}


def test_gateway_http_error_never_exposes_provider_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> object:
        raise HTTPError(
            "http://localhost:4000/v1/embeddings", 401, "Unauthorized",
            hdrs=None, fp=BytesIO(b'{"error":"private code and secret"}'),
        )
    monkeypatch.setattr(gateway_module, "urlopen", fail)
    provider = GatewayEmbeddingProvider(GatewayEmbeddingSettings(
        provider_id="litellm", base_url="http://localhost:4000/v1",
        model="fixed", api_key=None, allow_source_upload=True,
    ))
    with pytest.raises(KnowledgeSearchError) as captured:
        provider.embed(("private code",))
    assert captured.value.category == "embedding_failed"
    assert captured.value.metadata["http_status"] == 401
    assert "private" not in str(captured.value)
    assert "secret" not in str(captured.value)


def test_gateway_retries_transient_errors_only(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    sleeps: list[float] = []

    def flaky(*_args: object, **_kwargs: object) -> Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise HTTPError("http://localhost:4000/v1/embeddings", 429, "Rate limit", None, None)
        return Response({"data": [{"index": 0, "embedding": [1.0]}]})
    monkeypatch.setattr(gateway_module, "urlopen", flaky)
    monkeypatch.setattr(gateway_module, "sleep", sleeps.append)
    provider = GatewayEmbeddingProvider(GatewayEmbeddingSettings(
        provider_id="litellm", base_url="http://localhost:4000/v1",
        model="fixed", api_key=None, allow_source_upload=True,
    ))
    assert provider.embed(("hello",)) == ((1.0,),)
    assert attempts == 2
    assert sleeps == [0.25]


@pytest.mark.parametrize("gateway,port", [("litellm", 4000), ("bifrost", 8080)])
def test_gateway_runtime_bootstrap_and_backend_composition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gateway: str, port: int,
) -> None:
    key_env = f"FETCHCORE_{gateway.upper()}_TOKEN"
    monkeypatch.setenv(key_env, "secret-token")
    path = tmp_path / "operator.yaml"
    path.write_text(
        "version: 1\nruntime:\n  backend: solr\n"
        "retrieval:\n  dense:\n    enabled: true\n"
        f"    embedding:\n      provider: {gateway}\n"
        "      model: pinned-embedding-space-v1\n      dimensions: 768\n"
        f"embedding:\n  {gateway}:\n"
        f"    base_url: http://localhost:{port}/v1\n"
        f"    api_key_env: {key_env}\n    allow_source_upload: true\n"
    )
    runtime = load_runtime_config(path=path, environ={})
    registry = EmbeddingProviderRegistry(settings=embedding_provider_settings(runtime))
    provider = registry.create(
        runtime.retrieval.dense.embedding, backend_id="solr", backend_name="Solr",
    )
    assert provider.provider_id == gateway
    assert provider.model_id == "pinned-embedding-space-v1"
    assert provider.settings.api_key == "secret-token"
    assert provider.url == f"http://localhost:{port}/v1/embeddings"
    backend = BackendRegistry(providers=registry).create("solr")
    assert backend.embedding_factory(runtime.retrieval).provider_id == gateway


@pytest.mark.parametrize("gateway", ["litellm", "bifrost"])
def test_gateway_requires_explicit_source_upload_and_key(
    monkeypatch: pytest.MonkeyPatch, gateway: str,
) -> None:
    config = EmbeddingConfig(provider=gateway, model="pinned")
    registry = EmbeddingProviderRegistry(settings={gateway: {
        "base_url": "http://localhost:4000/v1",
        "allow_source_upload": False,
    }})
    with pytest.raises(KnowledgeSearchError) as captured:
        registry.create(config, backend_id="solr", backend_name="Solr")
    assert captured.value.category == "unsupported_capability"
    assert captured.value.metadata["capability"] == "embedding_source_upload"

    monkeypatch.delenv("FETCHCORE_GATEWAY_MISSING_TOKEN", raising=False)
    registry = EmbeddingProviderRegistry(settings={gateway: {
        "base_url": "http://localhost:4000/v1",
        "api_key_env": "FETCHCORE_GATEWAY_MISSING_TOKEN",
        "allow_source_upload": True,
    }})
    with pytest.raises(KnowledgeSearchError) as captured:
        registry.create(config, backend_id="solr", backend_name="Solr")
    assert captured.value.category == "schema_mismatch"


def test_gateway_config_rejects_unrecognized_settings() -> None:
    with pytest.raises(ValueError, match="unsupported fields"):
        load_runtime_config(environ={}, overrides={
            "embedding": {"litellm": {"base_url": "http://localhost:4000/v1", "secret": "not allowed"}}
        })
    with pytest.raises(ValueError, match="allow_source_upload"):
        load_runtime_config(environ={}, overrides={
            "embedding": {"bifrost": {"allow_source_upload": "true"}}
        })
