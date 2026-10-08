from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from fetchcore import cli
from fetchcore.adapters.embedding.ollama.provider import OllamaEmbeddingProvider
from fetchcore.adapters.retrieval.qdrant.backend import QdrantVectorBackend, QdrantVectorSettings
from fetchcore.application.composition import EmbeddingProviderRegistry
from fetchcore.application import runtime_inspection
from fetchcore.retrieval.config import EmbeddingConfig
from fetchcore.retrieval.contracts import KnowledgeSearchError
from fetchcore.retrieval.embedding import EmbeddingProvider


def _config_file(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "operator.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def _qdrant_config_file(tmp_path: Path) -> Path:
    return _config_file(
        tmp_path,
        "version: 1\n"
        "runtime:\n  backend: qdrant-vector\n"
        "backends:\n  qdrant-vector:\n    url: http://qdrant:6333\n"
        "    collection: fetchcore-vector\n"
        "embedding:\n  ollama:\n    url: http://ollama:11434\n"
        "retrieval:\n  dense:\n    enabled: true\n"
        "    embedding:\n      provider: ollama\n"
        "      model: test-embedding-model\n",
    )


def _isolated_qdrant_env() -> dict[str, str | None]:
    return {
        "FETCHCORE_QDRANT_API_KEY": "qdrant-api-secret",
        "FETCHCORE_BACKEND": None,
        "FETCHCORE_CONFIG": None,
        "FETCHCORE_MCP_BACKEND": None,
        "FETCHCORE_MCP_BACKEND_SETTINGS": None,
        "FETCHCORE_MCP_RETRIEVAL": None,
        "FETCHCORE_OLLAMA_URL": None,
        "FETCHCORE_QDRANT_COLLECTION": None,
        "FETCHCORE_QDRANT_URL": None,
        "FETCHCORE_RETRIEVAL": None,
        "OLLAMA_URL": None,
    }


def _patch_read_only_qdrant_transport(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[tuple[str, str]],
    settings_seen: list[QdrantVectorSettings],
) -> None:
    class ReadOnlyTransport:
        def __init__(self, settings: QdrantVectorSettings) -> None:
            settings_seen.append(settings)

        def request(
            self,
            method: str,
            path: str,
            payload: object | None = None,
        ) -> dict[str, object]:
            calls.append((method, path))
            if method != "GET":
                raise AssertionError("doctor must not mutate Qdrant")
            return {"result": {"collections": []}}

    monkeypatch.setattr(runtime_inspection, "UrllibQdrantTransport", ReadOnlyTransport)


def _track_embedding_provider_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> list[EmbeddingConfig]:
    creations: list[EmbeddingConfig] = []
    original_create = EmbeddingProviderRegistry.create

    def track_create(
        registry: EmbeddingProviderRegistry,
        embedding: EmbeddingConfig,
        *,
        backend_id: str,
        backend_name: str,
    ) -> EmbeddingProvider:
        provider = original_create(
            registry,
            embedding,
            backend_id=backend_id,
            backend_name=backend_name,
        )
        creations.append(embedding)
        return provider

    monkeypatch.setattr(EmbeddingProviderRegistry, "create", track_create)
    return creations


def _invoke_json(args: list[str], *, env: dict[str, str | None] | None = None):
    result = CliRunner().invoke(cli.main, [*args, "--json"], env=env)
    assert result.output
    return result, json.loads(result.output)


def test_config_show_outputs_resolved_sources_and_redacts_secrets(
    tmp_path: Path,
) -> None:
    path = _config_file(
        tmp_path,
        "version: 1\nruntime:\n  backend: qdrant-vector\n"
        "backends:\n  qdrant-vector:\n    url: http://qdrant:6333\n"
        "    collection: fetchcore-vector\n",
    )
    for args in (
        ["config", "show", "--config", str(path), "--json"],
        ["config", "show", "--config", str(path)],
    ):
        result = CliRunner().invoke(
            cli.main,
            args,
            env={"FETCHCORE_QDRANT_API_KEY": "secret-api-key"},
        )
        assert result.exit_code == 0, result.output
        assert "secret-api-key" not in result.output
        assert "********" in result.output
        if "--json" in args:
            payload = json.loads(result.output)
            assert payload["result"]["config"]["runtime"]["backend"] == "qdrant-vector"
            assert payload["result"]["config"]["backends"]["qdrant-vector"]["api_key"] == (
                "********"
            )
            assert payload["result"]["sources"]["runtime.backend"] == "config_file"
            assert payload["result"]["sources"]["backends.qdrant-vector.api_key"] == (
                "environment"
            )
            assert payload["result"]["config"]["backends"]["qdrant-vector"]["timeout_seconds"] == 30.0
            assert payload["result"]["sources"]["backends.qdrant-vector.timeout_seconds"] == (
                "default"
            )
        else:
            assert "runtime.backend=\"qdrant-vector\" source=config_file" in result.output
            assert "backends.qdrant-vector.api_key=\"********\"" in result.output


def test_config_show_materializes_typed_backend_defaults(tmp_path: Path) -> None:
    path = _config_file(
        tmp_path,
        "version: 1\nruntime:\n  backend: solr\n",
    )
    result, payload = _invoke_json(["config", "show", "--config", str(path)])

    assert result.exit_code == 0, result.output
    backend = payload["result"]["config"]["backends"]["solr"]
    assert backend["url"] == "http://localhost:8983/solr"
    assert backend["collection"] == "fetchcore-knowledge"
    assert payload["result"]["sources"]["backends.solr.url"] == "default"
    assert payload["result"]["sources"]["backends.solr.collection"] == "default"


def test_config_show_does_not_attribute_generated_empty_settings_to_environment(
    tmp_path: Path,
) -> None:
    path = _config_file(
        tmp_path,
        "version: 1\nruntime:\n  backend: local-semantic\n",
    )
    result, payload = _invoke_json(["config", "show", "--config", str(path)])

    assert result.exit_code == 0, result.output
    assert payload["result"]["sources"]["backends.local-semantic"] == "default"


def test_config_show_preserves_source_for_opensearch_collection_alias(
    tmp_path: Path,
) -> None:
    path = _config_file(
        tmp_path,
        "version: 1\nruntime:\n  backend: opensearch\n"
        "backends:\n  opensearch:\n    collection: fetchcore-index\n",
    )
    result, payload = _invoke_json(["config", "show", "--config", str(path)])

    assert result.exit_code == 0, result.output
    backend = payload["result"]["config"]["backends"]["opensearch"]
    assert backend["index"] == "fetchcore-index"
    assert payload["result"]["sources"]["backends.opensearch.index"] == "config_file"


def test_capabilities_come_from_backend_and_indexing_declarations(tmp_path: Path) -> None:
    path = _config_file(
        tmp_path,
        "version: 1\nruntime:\n  backend: qdrant-vector\n",
    )
    result, payload = _invoke_json(["capabilities", "--config", str(path)])

    assert result.exit_code == 0, result.output
    capabilities = payload["result"]
    declared = QdrantVectorBackend.retrieval_capabilities.to_mapping()
    assert capabilities["backend"] == "qdrant-vector"
    assert capabilities["retrieval"]["dense"] == declared["dense"] is True
    assert capabilities["retrieval"]["hybrid"] == declared["hybrid"] is True
    assert capabilities["intent_translation"]["structured_anchors"] is False
    assert capabilities["indexing"] == {"incremental": False, "rebuild": False}
    human = CliRunner().invoke(cli.main, ["capabilities", "--config", str(path)])
    assert human.exit_code == 0, human.output
    assert "retrieval.dense=true" in human.output
    assert "indexing.incremental=false" in human.output


def test_capabilities_unknown_backend_is_typed() -> None:
    result, payload = _invoke_json(
        ["capabilities"],
        env={"FETCHCORE_BACKEND": "unregistered"},
    )

    assert result.exit_code == 1
    assert payload["error"]["category"] == "unsupported_backend"


def test_capabilities_resolve_configured_dense_provider(tmp_path: Path) -> None:
    path = _config_file(
        tmp_path,
        f"version: 1\nruntime:\n  backend: local-semantic\n  workspace: {tmp_path}\n",
    )
    result, payload = _invoke_json(
        ["capabilities", "--config", str(path)],
        env={
            "FETCHCORE_RETRIEVAL": (
                '{"dense":{"enabled":true,"embedding":{"provider":"unknown-provider"}}}'
            ),
        },
    )

    assert result.exit_code == 1
    assert payload["error"]["category"] == "unsupported_capability"


def test_doctor_unknown_backend_is_typed() -> None:
    result, payload = _invoke_json(
        ["doctor"],
        env={"FETCHCORE_BACKEND": "unregistered"},
    )

    assert result.exit_code == 1
    assert payload["result"]["checks"] == [{
        "name": "runtime_config",
        "status": "failed",
        "category": "unsupported_backend",
    }]


def test_doctor_healthy_local_runtime_and_human_parity(tmp_path: Path) -> None:
    path = _config_file(
        tmp_path,
        f"version: 1\nruntime:\n  backend: local-semantic\n  workspace: {tmp_path}\n",
    )
    result, payload = _invoke_json(["doctor", "--config", str(path)])
    human = CliRunner().invoke(cli.main, ["doctor", "--config", str(path)])

    assert result.exit_code == 0, result.output
    assert human.exit_code == 0, human.output
    report = payload["result"]
    assert report["status"] == "healthy"
    checks = {check["name"]: check["status"] for check in report["checks"]}
    assert checks == {
        "backend_connectivity": "ok",
        "backend_resolution": "ok",
        "embedding_provider": "not_required",
        "runtime_config": "ok",
        "schema_compatibility": "not_required",
    }
    for name, status in checks.items():
        assert f"{name}={status}" in human.output


def test_doctor_invalid_config_fails_without_fallback(tmp_path: Path) -> None:
    path = _config_file(
        tmp_path,
        "version: 1\nruntime:\n  backend: solr\n"
        "backends:\n  solr:\n    url: http://valid:8983/solr\n",
    )
    result, payload = _invoke_json(
        ["doctor", "--config", str(path)],
        env={"FETCHCORE_SOLR_URL": "not-a-url"},
    )

    assert result.exit_code == 1
    report = payload["result"]
    assert report["status"] == "unhealthy"
    assert report["checks"] == [{
        "name": "runtime_config",
        "status": "failed",
        "category": "invalid_runtime_configuration",
    }]
    assert "valid:8983" not in result.output


def test_doctor_does_not_require_an_embedding_provider_for_lexical_runtime(
    tmp_path: Path,
) -> None:
    path = _config_file(
        tmp_path,
        f"version: 1\nruntime:\n  backend: local-semantic\n  workspace: {tmp_path}\n",
    )
    result, payload = _invoke_json(
        ["doctor", "--config", str(path)],
        env={
            "FETCHCORE_RETRIEVAL": (
                '{"dense":{"enabled":false,"embedding":{"provider":"unknown-provider"}}}'
            ),
        },
    )

    assert result.exit_code == 0, result.output
    checks = {check["name"]: check for check in payload["result"]["checks"]}
    assert checks["embedding_provider"]["status"] == "not_required"


def test_doctor_unknown_embedding_provider_is_typed(tmp_path: Path) -> None:
    path = _config_file(
        tmp_path,
        f"version: 1\nruntime:\n  backend: local-semantic\n  workspace: {tmp_path}\n",
    )
    result, payload = _invoke_json(
        ["doctor", "--config", str(path)],
        env={
            "FETCHCORE_RETRIEVAL": (
                '{"dense":{"enabled":true,"embedding":{"provider":"unknown-provider"}}}'
            ),
        },
    )

    assert result.exit_code == 1, result.output
    checks = {check["name"]: check for check in payload["result"]["checks"]}
    assert checks["embedding_provider"]["status"] == "failed"
    assert checks["embedding_provider"]["category"] == "unsupported_capability"


def test_doctor_unreachable_backend_redacts_transport_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _config_file(
        tmp_path,
        "version: 1\nruntime:\n  backend: solr\n"
        "backends:\n  solr:\n    url: http://solr:8983/solr\n",
    )

    class UnavailableTransport:
        def __init__(self, settings: object) -> None:
            pass

        def get(self, path: str, params: object = None) -> dict[str, object]:
            raise KnowledgeSearchError(
                "https://user:secret@host/private", category="unavailable",
            )

        def post(self, *args: object, **kwargs: object) -> dict[str, object]:
            raise AssertionError("doctor must not write")

    monkeypatch.setattr(runtime_inspection, "UrllibSolrTransport", UnavailableTransport)
    result, payload = _invoke_json(["doctor", "--config", str(path)])

    assert result.exit_code == 1
    checks = {check["name"]: check for check in payload["result"]["checks"]}
    assert checks["backend_connectivity"]["category"] == "connection_failure"
    assert "secret" not in result.output
    assert "private" not in result.output


def test_doctor_schema_mismatch_and_missing_collection_are_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _config_file(
        tmp_path,
        "version: 1\nruntime:\n  backend: solr\n"
        "backends:\n  solr:\n    url: http://solr:8983/solr\n",
    )
    calls: list[str] = []

    class SchemaMismatchTransport:
        def __init__(self, settings: object) -> None:
            pass

        def get(self, path: str, params: object = None) -> dict[str, object]:
            calls.append(f"GET {path}")
            if path == "schema":
                raise KnowledgeSearchError("internal schema response", category="schema_mismatch")
            return {}

        def post(self, *args: object, **kwargs: object) -> dict[str, object]:
            calls.append("POST")
            raise AssertionError("doctor must not write")

    monkeypatch.setattr(runtime_inspection, "UrllibSolrTransport", SchemaMismatchTransport)
    mismatch, payload = _invoke_json(["doctor", "--config", str(path)])
    assert mismatch.exit_code == 1
    checks = {check["name"]: check for check in payload["result"]["checks"]}
    assert checks["schema_compatibility"]["category"] == "schema_mismatch"
    assert calls == ["GET admin/ping", "GET schema"]
    assert "internal schema response" not in mismatch.output

    calls.clear()

    class MissingCollectionTransport(SchemaMismatchTransport):
        def get(self, path: str, params: object = None) -> dict[str, object]:
            calls.append(f"GET {path}")
            raise KnowledgeSearchError("collection absent", category="collection_missing")

    monkeypatch.setattr(runtime_inspection, "UrllibSolrTransport", MissingCollectionTransport)
    missing, missing_payload = _invoke_json(["doctor", "--config", str(path)])
    assert missing.exit_code == 1
    missing_checks = {check["name"]: check for check in missing_payload["result"]["checks"]}
    assert missing_checks["backend_connectivity"]["status"] == "ok"
    assert missing_checks["schema_compatibility"]["category"] == "collection_missing"
    assert calls == ["GET admin/ping"]


def test_qdrant_doctor_fails_when_embedding_execution_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _qdrant_config_file(tmp_path)
    transport_calls: list[tuple[str, str]] = []
    transport_settings: list[QdrantVectorSettings] = []
    embedding_calls: list[tuple[str, str, tuple[str, ...]]] = []
    _patch_read_only_qdrant_transport(monkeypatch, transport_calls, transport_settings)
    provider_resolutions = _track_embedding_provider_resolution(monkeypatch)

    def fail_embedding(
        provider: OllamaEmbeddingProvider,
        texts: tuple[str, ...],
    ) -> tuple[tuple[float, ...], ...]:
        embedding_calls.append((provider.url, provider.model_id, texts))
        raise KnowledgeSearchError(
            "https://user:secret@host/private",
            category="unavailable",
        )

    monkeypatch.setattr(OllamaEmbeddingProvider, "embed", fail_embedding)
    result, payload = _invoke_json(
        ["doctor", "--config", str(path)],
        env=_isolated_qdrant_env(),
    )

    assert result.exit_code == 1, result.output
    assert payload["result"]["status"] == "unhealthy"
    checks = {check["name"]: check for check in payload["result"]["checks"]}
    assert checks["backend_resolution"]["status"] == "ok"
    assert checks["embedding_provider"]["status"] == "ok"
    assert checks["backend_connectivity"]["status"] == "failed"
    assert checks["backend_connectivity"]["category"] == "connection_failure"
    assert checks["schema_compatibility"]["status"] == "not_checked"
    assert transport_calls == [("GET", "collections")]
    assert provider_resolutions == [EmbeddingConfig(model="test-embedding-model")]
    assert len(transport_settings) == 1
    assert transport_settings[0].api_key == "qdrant-api-secret"
    assert embedding_calls == [(
        "http://ollama:11434",
        "test-embedding-model",
        ("fetchcore embedding probe",),
    )]
    assert "secret" not in result.output
    assert "private" not in result.output
    assert "qdrant-api-secret" not in result.output


def test_qdrant_doctor_is_healthy_when_embedding_health_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _qdrant_config_file(tmp_path)
    transport_calls: list[tuple[str, str]] = []
    settings_seen: list[QdrantVectorSettings] = []
    embedding_calls: list[tuple[str, str, tuple[str, ...]]] = []
    _patch_read_only_qdrant_transport(monkeypatch, transport_calls, settings_seen)
    provider_resolutions = _track_embedding_provider_resolution(monkeypatch)

    def embed(
        provider: OllamaEmbeddingProvider,
        texts: tuple[str, ...],
    ) -> tuple[tuple[float, ...], ...]:
        embedding_calls.append((provider.url, provider.model_id, texts))
        return (tuple(0.25 for _ in range(8)),)

    monkeypatch.setattr(OllamaEmbeddingProvider, "embed", embed)
    result, payload = _invoke_json(
        ["doctor", "--config", str(path)],
        env=_isolated_qdrant_env(),
    )

    assert result.exit_code == 0, result.output
    assert payload["result"]["status"] == "healthy"
    checks = {check["name"]: check["status"] for check in payload["result"]["checks"]}
    assert checks["backend_resolution"] == "ok"
    assert checks["embedding_provider"] == "ok"
    assert checks["backend_connectivity"] == "ok"
    assert checks["schema_compatibility"] == "not_checked"
    assert checks["retrieval_compatibility"] == "ok"
    assert transport_calls == [("GET", "collections")]
    assert provider_resolutions == [EmbeddingConfig(model="test-embedding-model")]
    assert len(settings_seen) == 1
    assert embedding_calls == [(
        "http://ollama:11434",
        "test-embedding-model",
        ("fetchcore embedding probe",),
    )]


def test_qdrant_doctor_rejects_unusable_embedding_dimensions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _qdrant_config_file(tmp_path)
    transport_calls: list[tuple[str, str]] = []
    settings_seen: list[QdrantVectorSettings] = []
    _patch_read_only_qdrant_transport(monkeypatch, transport_calls, settings_seen)
    provider_resolutions = _track_embedding_provider_resolution(monkeypatch)

    def empty_embedding(
        provider: OllamaEmbeddingProvider,
        texts: tuple[str, ...],
    ) -> tuple[tuple[float, ...], ...]:
        return ((),)

    monkeypatch.setattr(OllamaEmbeddingProvider, "embed", empty_embedding)
    result, payload = _invoke_json(
        ["doctor", "--config", str(path)],
        env=_isolated_qdrant_env(),
    )

    assert result.exit_code == 1, result.output
    assert payload["result"]["status"] == "unhealthy"
    checks = {check["name"]: check for check in payload["result"]["checks"]}
    assert checks["embedding_provider"]["status"] == "ok"
    assert checks["backend_connectivity"]["category"] == "embedding_failed"
    assert checks["schema_compatibility"]["status"] == "not_checked"
    assert transport_calls == [("GET", "collections")]
    assert provider_resolutions == [EmbeddingConfig(model="test-embedding-model")]
    assert "embedding provider returned no usable vectors" not in result.output
