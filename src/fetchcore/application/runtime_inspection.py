"""Read-only inspection of the composed FetchCore runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

import yaml

from fetchcore.adapters.retrieval.opensearch.backend import (
    OpenSearchIndexStore,
    OpenSearchSettings,
    UrllibOpenSearchTransport,
)
from fetchcore.adapters.retrieval.qdrant.backend import (
    QdrantVectorIndexStore,
    QdrantVectorSettings,
    UrllibQdrantTransport,
)
from fetchcore.adapters.retrieval.solr.backend import (
    SolrIndexStore,
    SolrSettings,
    UrllibSolrTransport,
)
from fetchcore.adapters.retrieval.mongodb.indexing import MongoDBIndexStore
from fetchcore.adapters.retrieval.mongodb.settings import MongoDBSettings
from fetchcore.adapters.retrieval.mongodb.transport import PyMongoTransport
from fetchcore.application.composition import BackendRegistry, EmbeddingProviderRegistry
from fetchcore.application.indexing import indexing_capabilities
from fetchcore.application.runtime_config import (
    FetchCoreRuntimeConfig,
    RUNTIME_CONFIG_VERSION,
    _runtime_registries_for_config,
    load_runtime_config,
)
from fetchcore.retrieval.config import EmbeddingConfig, validate_backend_retrieval
from fetchcore.retrieval.contracts import KnowledgeSearchError
from fetchcore.retrieval.embedding import EmbeddingProvider


CheckStatus = Literal["ok", "failed", "not_required", "not_checked"]


@dataclass(frozen=True)
class RuntimeCheck:
    name: str
    status: CheckStatus
    category: str | None = None

    def to_mapping(self) -> dict[str, str]:
        result = {"name": self.name, "status": self.status}
        if self.category is not None:
            result["category"] = self.category
        return result


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[RuntimeCheck, ...]

    @property
    def healthy(self) -> bool:
        return all(check.status != "failed" for check in self.checks)

    def to_mapping(self) -> dict[str, object]:
        return {
            "status": "healthy" if self.healthy else "unhealthy",
            "checks": [
                check.to_mapping() for check in sorted(self.checks, key=lambda item: item.name)
            ],
        }


def _error_category(error: Exception, *, fallback: str = "invalid_runtime_configuration") -> str:
    category = getattr(error, "category", None)
    return category if isinstance(category, str) and category else fallback


def _connection_category(error: Exception) -> str:
    category = _error_category(error, fallback="connection_failure")
    return "connection_failure" if category in {"unavailable", "timeout"} else category


def _secret_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    if normalized == "uri":
        return True
    return any(
        part in normalized
        for part in ("api_key", "apikey", "password", "secret", "token", "authorization", "credential")
    )


def _sanitize(value: object, *, key: str = "") -> object:
    if _secret_key(key):
        return "********" if value else None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {
            name: _sanitize(item, key=name)
            for name, item in sorted(value.items())
        }
    if isinstance(value, (tuple, list)):
        return [_sanitize(item) for item in value]
    return value


def _leaf_paths(value: object, prefix: str = "") -> list[str]:
    if isinstance(value, Mapping):
        result: list[str] = []
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            result.extend(_leaf_paths(item, path))
        return result or ([prefix] if prefix else [])
    return [prefix] if prefix else []


class RuntimeInspectionService:
    """Resolve config and inspect declarations/probes through application boundaries."""

    def __init__(
        self,
        config: FetchCoreRuntimeConfig,
        *,
        backends: BackendRegistry | None = None,
        providers: EmbeddingProviderRegistry | None = None,
    ) -> None:
        self.config = config
        self.backends, self.providers = _runtime_registries_for_config(
            config, backends=backends, providers=providers,
        )

    @classmethod
    def from_sources(
        cls,
        *,
        path: Path | None = None,
        environ: Mapping[str, str] | None = None,
        overrides: Mapping[str, object] | None = None,
    ) -> RuntimeInspectionService:
        return cls(load_runtime_config(path=path, environ=environ, overrides=overrides))

    @classmethod
    def doctor_from_sources(
        cls,
        *,
        path: Path | None = None,
        environ: Mapping[str, str] | None = None,
        overrides: Mapping[str, object] | None = None,
    ) -> DoctorReport:
        try:
            return cls.from_sources(path=path, environ=environ, overrides=overrides).doctor()
        except (OSError, ValueError, yaml.YAMLError) as exc:
            return DoctorReport((
                RuntimeCheck("runtime_config", "failed", _error_category(exc)),
            ))

    def resolved_config(self) -> dict[str, object]:
        runtime: dict[str, object] = {
            "backend": self.config.backend,
            "workspace": str(self.config.workspace),
            "knowledge_types": list(self.config.knowledge_types),
            "repository_paths": list(self.config.repository_paths),
        }
        if self.config.repository is not None:
            runtime["repository"] = self.config.repository
        if self.config.revision is not None:
            runtime["revision"] = self.config.revision
        values: dict[str, object] = {
            "version": RUNTIME_CONFIG_VERSION,
            "runtime": runtime,
            "backends": {
                self.config.backend: self._resolved_backend_settings(),
            },
            "embedding": {"ollama": self.config.ollama.to_mapping()},
        }
        if self.config.retrieval is not None:
            values["retrieval"] = self.config.retrieval.to_mapping()
        safe_values = _sanitize(values)
        assert isinstance(safe_values, dict)
        sources = {
            path: self.config.sources.get(path, "default")
            for path in sorted(_leaf_paths(safe_values))
        }
        if self.config.backend == "opensearch" and (
            "index" not in self.config.backend_settings
            and "collection" in self.config.backend_settings
        ):
            sources["backends.opensearch.index"] = self.config.sources.get(
                "backends.opensearch.collection", "default",
            )
        if self.config.backend == "qdrant-vector":
            settings_prefix = f"backends.{self.config.backend}"
            sources[f"{settings_prefix}.embedding_url"] = self.config.sources.get(
                "embedding.ollama.url",
                self.config.sources.get(f"{settings_prefix}.embedding_url", "default"),
            )
            if self.config.retrieval is not None:
                sources[f"{settings_prefix}.embedding_model"] = self.config.sources.get(
                    "retrieval.dense.embedding.model", "default",
                )
        return {"config": safe_values, "sources": sources}

    def _resolved_backend_settings(self) -> dict[str, object]:
        settings = dict(self.config.backend_settings)
        if self.config.backend == "mongodb-search":
            return asdict(MongoDBSettings.from_mapping(settings))
        if self.config.backend == "solr":
            return asdict(SolrSettings.from_mapping(settings))
        if self.config.backend == "opensearch":
            return asdict(OpenSearchSettings.from_mapping(settings))
        if self.config.backend == "qdrant-vector":
            return asdict(QdrantVectorSettings.from_mapping(settings))
        return settings

    def capabilities(self) -> dict[str, object]:
        backend = self.backends.create(self.config.backend)
        embedding = self._required_embedding()
        if embedding is not None:
            self.providers.create(
                embedding,
                backend_id=self.config.backend,
                backend_name=self.config.backend,
            )
        declared = backend.retrieval_capabilities
        retrieval = declared.to_mapping()
        intent_translation = {
            name: retrieval.pop(name)
            for name in ("canonical_query", "structured_anchors", "boolean_expressions")
        }
        retrieval = {
            key: value for key, value in retrieval.items()
            if key in {"lexical", "dense", "hybrid", "reranking", "ltr", "targeted_lookup"}
        }
        try:
            indexing = indexing_capabilities(self.config.backend)
            indexing_values = {
                "incremental": indexing.incremental,
                "rebuild": indexing.rebuild,
            }
        except KnowledgeSearchError as exc:
            if exc.category != "unsupported_backend":
                raise
            indexing_values = {"incremental": False, "rebuild": False}
        return {
            "backend": backend.backend_id,
            "retrieval": retrieval,
            "indexing": indexing_values,
            "intent_translation": intent_translation,
        }

    def doctor(self) -> DoctorReport:
        checks = [RuntimeCheck("runtime_config", "ok")]
        backend = None
        embedding_provider: EmbeddingProvider | None = None
        try:
            backend = self.backends.create(self.config.backend)
        except (KnowledgeSearchError, OSError, ValueError) as exc:
            checks.append(RuntimeCheck(
                "backend_resolution", "failed", _error_category(exc),
            ))
        else:
            checks.append(RuntimeCheck("backend_resolution", "ok"))

        embedding = self._required_embedding()
        if self.config.backend == "qdrant-vector" and self.config.retrieval is None:
            model = self.config.backend_settings.get("embedding_model", EmbeddingConfig.model)
            if not isinstance(model, str):
                raise KnowledgeSearchError(
                    "Qdrant embedding model is invalid",
                    category="schema_mismatch",
                )
            embedding = EmbeddingConfig(model=model)
        if embedding is not None:
            try:
                embedding_provider = self.providers.create(
                    embedding,
                    backend_id=self.config.backend,
                    backend_name=self.config.backend,
                )
            except (KnowledgeSearchError, OSError, ValueError) as exc:
                checks.append(RuntimeCheck(
                    "embedding_provider", "failed", _error_category(exc),
                ))
            else:
                checks.append(RuntimeCheck("embedding_provider", "ok"))
        else:
            checks.append(RuntimeCheck("embedding_provider", "not_required"))

        if self.config.retrieval is not None and backend is not None:
            try:
                validate_backend_retrieval(
                    self.config.retrieval,
                    backend_id=self.config.backend,
                    capabilities=backend.retrieval_capabilities,
                )
            except (KnowledgeSearchError, ValueError) as exc:
                checks.append(RuntimeCheck(
                    "retrieval_compatibility", "failed", _error_category(exc),
                ))
            else:
                checks.append(RuntimeCheck("retrieval_compatibility", "ok"))

        if backend is None:
            checks.extend((
                RuntimeCheck("backend_connectivity", "not_checked"),
                RuntimeCheck("schema_compatibility", "not_checked"),
            ))
        else:
            checks.extend(self._probe_backend(embedding_provider=embedding_provider))
        return DoctorReport(tuple(checks))

    def readiness(self) -> dict[str, object]:
        """Inspect operational repository/mode readiness without index writes."""
        from fetchcore.application.readiness import inspect_readiness

        return inspect_readiness(self).to_mapping()

    def _required_embedding(self) -> EmbeddingConfig | None:
        if self.config.retrieval is not None and self.config.retrieval.dense.enabled:
            return self.config.retrieval.dense.embedding
        if self.config.backend == "qdrant-vector" and self.config.retrieval is None:
            return EmbeddingConfig()
        return None

    def _probe_backend(
        self,
        *,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> tuple[RuntimeCheck, ...]:
        backend_id = self.config.backend
        if backend_id == "local-semantic":
            available = self.config.workspace.is_dir()
            status: CheckStatus = "ok" if available else "failed"
            return (
                RuntimeCheck(
                    "backend_connectivity",
                    status,
                    None if available else "invalid_runtime_configuration",
                ),
                RuntimeCheck("schema_compatibility", "not_required"),
            )
        if backend_id == "solr":
            return self._probe_solr()
        if backend_id == "mongodb-search":
            mongo_transport = None
            try:
                mongo_settings = MongoDBSettings.from_mapping(self.config.backend_settings)
                mongo_transport = PyMongoTransport(mongo_settings)
                mongo_transport.health()
                try:
                    MongoDBIndexStore(mongo_transport, mongo_settings).validate_schema(
                        require_vectors=self._required_embedding() is not None,
                    )
                except KnowledgeSearchError as exc:
                    return (
                        RuntimeCheck("backend_connectivity", "ok"),
                        RuntimeCheck("schema_compatibility", "failed", exc.category),
                    )
            except (KnowledgeSearchError, ValueError, OSError) as exc:
                return (
                    RuntimeCheck("backend_connectivity", "failed", _connection_category(exc)),
                    RuntimeCheck("schema_compatibility", "not_checked"),
                )
            finally:
                if mongo_transport is not None:
                    mongo_transport.close()
            return (
                RuntimeCheck("backend_connectivity", "ok"),
                RuntimeCheck("schema_compatibility", "ok"),
            )
        if backend_id == "opensearch":
            settings = OpenSearchSettings.from_mapping(dict(self.config.backend_settings))
            store = OpenSearchIndexStore(
                UrllibOpenSearchTransport(settings), settings, clock=lambda: "",
            )
            return self._probe_health(store.health)
        if backend_id == "qdrant-vector":
            qdrant_settings = QdrantVectorSettings.from_mapping(
                dict(self.config.backend_settings)
            )
            if embedding_provider is None:
                return (
                    RuntimeCheck("backend_connectivity", "not_checked"),
                    RuntimeCheck("schema_compatibility", "not_checked"),
                )
            qdrant_store = QdrantVectorIndexStore(
                UrllibQdrantTransport(qdrant_settings),
                qdrant_settings,
                embedding_provider,
                clock=lambda: "",
            )
            return self._probe_health(qdrant_store.health)
        raise KnowledgeSearchError(
            "Runtime inspection has no safe probe for the selected backend",
            category="unsupported_backend",
        )

    def _probe_health(self, health: Callable[[], object]) -> tuple[RuntimeCheck, ...]:
        try:
            health()
        except (KnowledgeSearchError, OSError, ValueError) as exc:
            return (
                RuntimeCheck("backend_connectivity", "failed", _connection_category(exc)),
                RuntimeCheck("schema_compatibility", "not_checked"),
            )
        return (
            RuntimeCheck("backend_connectivity", "ok"),
            RuntimeCheck("schema_compatibility", "not_checked"),
        )

    def _probe_solr(self) -> tuple[RuntimeCheck, ...]:
        settings = SolrSettings.from_mapping(dict(self.config.backend_settings))
        store = SolrIndexStore(UrllibSolrTransport(settings), settings)
        try:
            store.health()
        except (KnowledgeSearchError, OSError, ValueError) as exc:
            if _error_category(exc) == "collection_missing":
                return (
                    RuntimeCheck("backend_connectivity", "ok"),
                    RuntimeCheck("schema_compatibility", "failed", "collection_missing"),
                )
            return (
                RuntimeCheck("backend_connectivity", "failed", _connection_category(exc)),
                RuntimeCheck("schema_compatibility", "not_checked"),
            )
        try:
            store.validate_schema(
                require_vectors=(
                    self.config.retrieval is not None and self.config.retrieval.dense.enabled
                ),
            )
        except (KnowledgeSearchError, OSError, ValueError) as exc:
            return (
                RuntimeCheck("backend_connectivity", "ok"),
                RuntimeCheck("schema_compatibility", "failed", _error_category(exc)),
            )
        return (
            RuntimeCheck("backend_connectivity", "ok"),
            RuntimeCheck("schema_compatibility", "ok"),
        )
