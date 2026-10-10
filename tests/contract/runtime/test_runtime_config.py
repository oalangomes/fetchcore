"""Operator runtime configuration and implementation registry contracts."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from fetchcore.adapters.embedding.ollama.provider import OllamaEmbeddingProvider
from fetchcore.adapters.retrieval.local.backend import LocalSemanticBackend
from fetchcore.adapters.retrieval.qdrant.backend import QdrantVectorBackend, QdrantVectorSettings
from fetchcore.adapters.retrieval.solr.backend import SolrBackend
from fetchcore.application.composition import (
    BUILTIN_BACKEND_IDS,
    BUILTIN_EMBEDDING_PROVIDER_IDS,
    BackendRegistry,
    EmbeddingProviderRegistry,
    builtin_backend_factories,
    builtin_embedding_provider_factories,
)
from fetchcore.application.runtime_config import (
    FetchCoreRuntimeConfig,
    load_runtime_config,
    service_from_runtime_config,
)
from fetchcore.serving.mcp import service_from_environment
from fetchcore.retrieval.config import EmbeddingConfig, RetrievalConfig
from fetchcore.retrieval.contracts import KnowledgeSearchError


def test_defaults_and_explicit_sdk_bootstrap(tmp_path: Path) -> None:
    default = load_runtime_config(environ={})
    assert default.backend == "local-semantic"
    assert default.ollama.url == "http://localhost:11434"
    assert "backends.local-semantic" not in default.sources
    config = FetchCoreRuntimeConfig(workspace=tmp_path, repository="owner/repo")
    service = service_from_runtime_config(config)
    assert service.repositories == ("owner/repo",)


def test_yaml_env_and_explicit_precedence(tmp_path: Path) -> None:
    path = tmp_path / "operator.yaml"
    path.write_text(
        "version: 1\nruntime:\n  backend: solr\n"
        "backends:\n  solr:\n    url: http://file:8983/solr\n"
        "    collection: from-file\n"
        "embedding:\n  ollama:\n    url: http://file:11434\n",
        encoding="utf-8",
    )
    config = load_runtime_config(
        path=path,
        environ={"FETCHCORE_SOLR_URL": "http://env:8983/solr", "FETCHCORE_OLLAMA_URL": "http://env:11434"},
        overrides={"backends": {"solr": {"url": "http://caller:8983/solr"}}},
    )
    assert config.backend_settings == {
        "url": "http://caller:8983/solr", "collection": "from-file"
    }
    assert config.ollama.url == "http://env:11434"
    assert config.sources["runtime.backend"] == "config_file"
    assert config.sources["backends.solr.url"] == "explicit"
    assert config.sources["backends.solr.collection"] == "config_file"
    assert config.sources["embedding.ollama.url"] == "environment"
    assert "runtime.workspace" not in config.sources
    assert load_runtime_config(path=path, environ={}).backend_settings["url"] == "http://file:8983/solr"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("version: 2\n", "version"),
        ("- version: 1\n", "mapping"),
        ("version: 1\nunknown: 1\n", "unsupported fields"),
        ("version: 1\nbackends: []\n", "mapping"),
        ("version: 1\nembedding:\n  ollama: []\n", "mapping"),
        ("version: 1\nembedding:\n  ollama:\n    url: 123\n", "url"),
        ("version: 1\nretrieval:\n  dense:\n    embedding:\n      request_batch_size: 0\n", "request_batch_size"),
        ("version: 1\nretrieval:\n  dense:\n    embedding:\n      timeout_seconds: -1\n", "timeout_seconds"),
        ("version: 1\nembedding:\n  ollama:\n    timeout_seconds: 30\n", "unsupported fields"),
        ("version: 1\nembedding:\n  ollama:\n    extra: true\n", "unsupported fields"),
        ("version: 1\nbackends:\n  solr:\n    url: ftp://bad\n", "url"),
        ("version: 1\nbackends:\n  solr:\n    url: http://host:bad/solr\n", "url"),
        ("version: 1\nbackends:\n  solr:\n    surprise: true\n", "unsupported fields"),
        ("version: 1\nruntime:\n  backend: absent\n", "unsupported"),
        ("version: 1\nbackends:\n  123: {}\n", "string keys"),
    ],
)
def test_invalid_file_fails_closed(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "operator.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_runtime_config(path=path, environ={})


def test_invalid_higher_precedence_value_does_not_fall_back(tmp_path: Path) -> None:
    path = tmp_path / "operator.yaml"
    path.write_text(
        "version: 1\nruntime:\n  backend: solr\nbackends:\n  solr:\n    url: http://valid:8983/solr\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="url"):
        load_runtime_config(path=path, environ={"FETCHCORE_SOLR_URL": "not-a-url"})
    with pytest.raises(ValueError, match="url"):
        load_runtime_config(
            path=path, environ={}, overrides={"backends": {"solr": {"url": 123}}}
        )


def test_legacy_mcp_and_qdrant_embedding_inputs(tmp_path: Path) -> None:
    config = load_runtime_config(
        environ={
            "FETCHCORE_MCP_BACKEND": "qdrant-vector",
            "FETCHCORE_MCP_BACKEND_SETTINGS": (
                '{"url":"http://qdrant:6333","embedding_url":"http://legacy:11434",'
                '"embedding_model":"old-model"}'
            ),
            "FETCHCORE_MCP_RETRIEVAL": (
                '{"dense":{"enabled":true,"embedding":{"model":"new-model"}}}'
            ),
            "FETCHCORE_MCP_REPOSITORY": "owner/repo",
            "FETCHCORE_MCP_WORKSPACE": str(tmp_path),
        }
    )
    assert config.ollama.url == "http://legacy:11434"
    assert config.backend_settings["embedding_model"] == "new-model"
    assert config.sources["embedding.ollama.url"] == "environment"
    assert config.sources["backends.qdrant-vector.embedding_url"] == "environment"
    assert service_from_runtime_config(config).repositories == ("owner/repo",)


def test_canonical_env_wins_over_legacy() -> None:
    config = load_runtime_config(environ={
        "FETCHCORE_BACKEND": "solr",
        "FETCHCORE_MCP_BACKEND": "qdrant-vector",
        "FETCHCORE_SOLR_URL": "http://solr:8983/solr",
        "FETCHCORE_OLLAMA_URL": "http://canonical:11434",
        "OLLAMA_URL": "http://legacy:11434",
    })
    assert config.backend == "solr"
    assert config.ollama.url == "http://canonical:11434"


def test_mcp_bootstrap_uses_application_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "operator.yaml"
    path.write_text(
        "version: 1\nruntime:\n  backend: local-semantic\n  repository: owner/repo\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FETCHCORE_CONFIG", str(path))
    monkeypatch.delenv("FETCHCORE_BACKEND", raising=False)
    monkeypatch.delenv("FETCHCORE_MCP_BACKEND", raising=False)
    assert service_from_environment().repositories == ("owner/repo",)


def test_canonical_empty_retrieval_fails() -> None:
    with pytest.raises(ValueError, match="FETCHCORE_RETRIEVAL"):
        load_runtime_config(environ={"FETCHCORE_RETRIEVAL": ""})


def test_runtime_provider_settings_reach_builtin_adapter() -> None:
    providers = EmbeddingProviderRegistry(settings={"ollama": {"url": "http://operator:11434"}})
    backend = builtin_backend_factories(providers)["solr"]()
    assert isinstance(backend, SolrBackend)
    provider = backend.embedding_factory(RetrievalConfig())
    assert isinstance(provider, OllamaEmbeddingProvider)
    assert provider.url == "http://operator:11434"
    assert provider.timeout_seconds == 30.0
    assert provider.request_batch_size == 32


def test_qdrant_transport_timeout_does_not_override_embedding_envelope() -> None:
    config = load_runtime_config(environ={}, overrides={
        "runtime": {"backend": "qdrant-vector"},
        "backends": {"qdrant-vector": {"timeout_seconds": 5}},
        "retrieval": {"dense": {"enabled": True, "embedding": {
            "provider": "ollama", "model": "embeddinggemma",
            "timeout_seconds": 60, "request_batch_size": 7,
        }}},
        "embedding": {"ollama": {"url": "http://operator:11434"}},
    })
    assert config.retrieval is not None
    settings = QdrantVectorSettings.from_mapping(config.backend_settings)
    providers = EmbeddingProviderRegistry(settings={"ollama": config.ollama.to_mapping()})
    backend = BackendRegistry(providers=providers).create("qdrant-vector")
    assert isinstance(backend, QdrantVectorBackend)
    provider = backend.embedding_factory(settings, config.retrieval.dense.embedding)
    assert isinstance(provider, OllamaEmbeddingProvider)
    assert settings.timeout_seconds == 5
    assert provider.timeout_seconds == 60
    assert provider.request_batch_size == 7
    assert provider.url == "http://operator:11434"


def test_execution_env_overrides_retrieval_envelope(tmp_path: Path) -> None:
    path = tmp_path / "operator.yaml"
    path.write_text(
        "version: 1\nretrieval:\n  dense:\n    embedding:\n"
        "      timeout_seconds: 30\n      request_batch_size: 2\n",
        encoding="utf-8",
    )
    env = {
        "FETCHCORE_OLLAMA_TIMEOUT_SECONDS": "60",
        "FETCHCORE_OLLAMA_REQUEST_BATCH_SIZE": "7",
    }
    config = load_runtime_config(path=path, environ=env)
    assert config.retrieval is not None
    assert config.retrieval.dense.embedding.timeout_seconds == 60
    assert config.retrieval.dense.embedding.request_batch_size == 7
    assert config.ollama.to_mapping() == {"url": "http://localhost:11434"}
    explicit = load_runtime_config(path=path, environ=env, overrides={
        "retrieval": {"dense": {"embedding": {
            "timeout_seconds": 90, "request_batch_size": 9,
        }}}
    })
    assert explicit.retrieval is not None
    assert explicit.retrieval.dense.embedding.timeout_seconds == 90
    assert explicit.retrieval.dense.embedding.request_batch_size == 9
    with pytest.raises(ValueError, match="timeout_seconds"):
        load_runtime_config(
            path=path, environ={"FETCHCORE_OLLAMA_TIMEOUT_SECONDS": "-1"}
        )


def test_qdrant_env_only_execution_preserves_legacy_model() -> None:
    config = load_runtime_config(environ={
        "FETCHCORE_MCP_BACKEND": "qdrant-vector",
        "FETCHCORE_MCP_BACKEND_SETTINGS": '{"embedding_model":"legacy-model"}',
        "FETCHCORE_OLLAMA_TIMEOUT_SECONDS": "60",
    })
    assert config.retrieval is not None
    assert config.retrieval.dense.enabled
    assert config.retrieval.dense.embedding.model == "legacy-model"
    assert config.retrieval.dense.embedding.timeout_seconds == 60


def test_builtin_catalogs_match_runtime_accepted_ids() -> None:
    assert set(builtin_backend_factories()) == BUILTIN_BACKEND_IDS
    assert set(builtin_embedding_provider_factories()) == BUILTIN_EMBEDDING_PROVIDER_IDS
    for backend_id in BUILTIN_BACKEND_IDS:
        assert load_runtime_config(
            environ={}, overrides={"runtime": {"backend": backend_id}}
        ).backend == backend_id
    for provider_id in BUILTIN_EMBEDDING_PROVIDER_IDS:
        load_runtime_config(environ={}, overrides={"embedding": {provider_id: {}}})


def test_runtime_string_lists_strip_and_reject_blank_or_non_string(tmp_path: Path) -> None:
    path = tmp_path / "operator.yaml"
    path.write_text(
        'version: 1\nruntime:\n  knowledge_types: [" code "]\n'
        '  repository_paths: [" src/foo "]\n',
        encoding="utf-8",
    )
    config = load_runtime_config(path=path, environ={})
    assert config.knowledge_types == ("code",)
    assert config.repository_paths == ("src/foo",)
    for invalid in ('["   "]', '[123]'):
        path.write_text(
            f"version: 1\nruntime:\n  knowledge_types: {invalid}\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="knowledge_types"):
            load_runtime_config(path=path, environ={})


def test_provider_registry_resolves_and_rejects_unknown_or_mismatch() -> None:
    registry = EmbeddingProviderRegistry()
    assert isinstance(
        registry.create(EmbeddingConfig(), backend_id="solr", backend_name="Solr"),
        OllamaEmbeddingProvider,
    )
    with pytest.raises(KnowledgeSearchError, match="not supported"):
        registry.create(EmbeddingConfig(provider="missing"), backend_id="solr", backend_name="Solr")
    fixture = EmbeddingProviderRegistry(
        factories={"fixture": lambda embedding, settings: OllamaEmbeddingProvider()}
    )
    with pytest.raises(KnowledgeSearchError) as captured:
        fixture.create(EmbeddingConfig(provider="fixture"), backend_id="solr", backend_name="Solr")
    assert captured.value.category == "schema_mismatch"


def test_injected_factories_are_used() -> None:
    class FixtureProvider:
        provider_id = "fixture"

        def __init__(self, model: str) -> None:
            self.model_id = model

        def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            return tuple((1.0,) for _ in texts)

    providers = EmbeddingProviderRegistry(
        factories={"fixture": lambda embedding, settings: FixtureProvider(embedding.model)}
    )
    assert providers.create(
        EmbeddingConfig(provider="fixture"), backend_id="solr", backend_name="Solr"
    ).provider_id == "fixture"


def test_provider_registry_rejects_model_identity_mismatch() -> None:
    class WrongModelProvider:
        provider_id = "fixture"
        model_id = "different-model"

        def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            return tuple((1.0,) for _ in texts)

    providers = EmbeddingProviderRegistry(
        factories={"fixture": lambda embedding, settings: WrongModelProvider()}
    )
    with pytest.raises(KnowledgeSearchError) as captured:
        providers.create(
            EmbeddingConfig(provider="fixture", model="expected-model"),
            backend_id="solr",
            backend_name="Solr",
        )

    assert captured.value.category == "schema_mismatch"
    assert captured.value.metadata == {
        "requested_model": "expected-model",
        "actual_model": "different-model",
    }


def test_backend_registry_custom_factory_and_unknown() -> None:
    registry = BackendRegistry({"local-semantic": LocalSemanticBackend})
    assert isinstance(registry.create("local-semantic"), LocalSemanticBackend)
    with pytest.raises(KnowledgeSearchError) as captured:
        registry.create("missing")
    assert captured.value.category == "unsupported_backend"


def test_repository_policy_is_not_runtime_config(tmp_path: Path) -> None:
    policy = tmp_path / ".fetchcore" / "runtime.yaml"
    policy.parent.mkdir()
    policy.write_text("version: 1\nembedding:\n  ollama:\n    url: http://attacker:11434\n")
    config = load_runtime_config(environ={}, overrides={"runtime": {"workspace": str(tmp_path)}})
    assert config.ollama.url == "http://localhost:11434"


def test_adapters_do_not_discover_environment() -> None:
    root = Path(__file__).resolve().parents[3] / "src" / "fetchcore" / "adapters"
    for module in (*root.joinpath("retrieval").rglob("*.py"), *root.joinpath("embedding").rglob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert not (node.value.id == "os" and node.attr in {"environ", "getenv"}), module
