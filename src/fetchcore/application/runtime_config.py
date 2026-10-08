"""Operator-owned runtime configuration and deterministic source precedence."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

import yaml

from fetchcore.adapters.embedding.ollama.provider import OllamaEmbeddingSettings
from fetchcore.retrieval.config import EmbeddingConfig, RetrievalConfig
from fetchcore.retrieval.contracts import KnowledgeSearchError
from .composition import BUILTIN_BACKEND_IDS, BUILTIN_EMBEDDING_PROVIDER_IDS

if TYPE_CHECKING:
    from .composition import BackendRegistry, EmbeddingProviderRegistry
    from .service import RetrievalApplicationService

RUNTIME_CONFIG_VERSION = 1
RuntimeConfigValueSource = Literal["default", "config_file", "environment", "explicit"]
_BACKEND_FIELDS = {
    "local-semantic": frozenset({
        "file_patterns", "exclude_dirs", "max_files_scanned", "max_file_bytes",
        "max_total_bytes", "max_matches", "context_lines", "merge_adjacent_lines",
        "max_expanded_query_terms", "query_aliases", "categorization_rules",
        "technical_roles", "product_segments", "knowledge_type_rules",
        "default_knowledge_type",
    }),
    "solr": frozenset({
        "url", "collection", "timeout_seconds", "projection_version", "field_boosts",
        "signal_type_boosts", "batch_size", "diagnostic_candidate_window",
    }),
    "opensearch": frozenset({
        "url", "index", "collection", "timeout_seconds", "projection_version",
        "field_boosts", "signal_type_boosts", "batch_size", "diagnostic_candidate_window",
    }),
    "qdrant-vector": frozenset({
        "url", "collection", "timeout_seconds", "projection_version",
        "embedding_serializer_mode", "embedding_url", "embedding_model", "batch_size",
        "diagnostic_candidate_window", "api_key",
    }),
}


def _mapping(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{path} must be a mapping with string keys")
    return dict(value)


def _fields(data: Mapping[str, object], allowed: set[str] | frozenset[str], path: str) -> None:
    unknown = sorted(data.keys() - allowed)
    if unknown:
        raise ValueError(f"{path} contains unsupported fields: {', '.join(unknown)}")


def _string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value.strip()


def _url(value: object, path: str) -> str:
    url = _string(value, path).rstrip("/")
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{path} must be an absolute HTTP(S) URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (port is not None and port < 1)
        or any(char.isspace() for char in url)
    ):
        raise ValueError(f"{path} must be an absolute HTTP(S) URL")
    return url


def _positive_float(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be a positive number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{path} must be a positive finite number")
    return result


def _positive_int(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{path} must be a positive integer")
    return value


@dataclass(frozen=True)
class OllamaRuntimeSettings:
    """Provider-specific deployment endpoint for the built-in Ollama adapter."""

    url: str = OllamaEmbeddingSettings.url

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> OllamaRuntimeSettings:
        data = _mapping(value, "embedding.ollama")
        _fields(data, {"url"}, "embedding.ollama")
        return cls(url=_url(data.get("url", cls.url), "embedding.ollama.url"))

    def to_mapping(self) -> dict[str, object]:
        return {"url": self.url}


def ollama_settings_from_environment(
    environ: Mapping[str, str] | None = None,
) -> OllamaRuntimeSettings:
    """Compatibility source for historical direct embedding factory helpers."""
    env = os.environ if environ is None else environ
    url = env.get("FETCHCORE_OLLAMA_URL", env.get("OLLAMA_URL"))
    return OllamaRuntimeSettings.from_mapping({} if url is None else {"url": url})


@dataclass(frozen=True)
class FetchCoreRuntimeConfig:
    """Resolved application bootstrap, independent of its input source."""

    backend: str = "local-semantic"
    backend_settings: Mapping[str, object] = field(default_factory=dict)
    retrieval: RetrievalConfig | None = None
    ollama: OllamaRuntimeSettings = OllamaRuntimeSettings()
    workspace: Path = field(default_factory=Path.cwd)
    repository: str | None = None
    revision: str | None = None
    knowledge_types: tuple[str, ...] = ()
    repository_paths: tuple[str, ...] = ()
    sources: Mapping[str, RuntimeConfigValueSource] = field(
        default_factory=dict, compare=False, repr=False,
    )

    def __post_init__(self) -> None:
        if self.backend not in BUILTIN_BACKEND_IDS:
            raise KnowledgeSearchError(
                "unsupported FetchCore backend",
                category="unsupported_backend",
            )
        validate_backend_settings(self.backend, self.backend_settings)
        OllamaRuntimeSettings.from_mapping(self.ollama.to_mapping())
        if self.repository is not None:
            _string(self.repository, "runtime.repository")


def validate_backend_settings(backend: str, settings: Mapping[str, object]) -> None:
    """Reject unknown or malformed deployment values before adapter execution."""
    data = _mapping(settings, f"backends.{backend}")
    _fields(data, _BACKEND_FIELDS[backend], f"backends.{backend}")
    if "url" in data:
        _url(data["url"], f"backends.{backend}.url")
    if "embedding_url" in data:
        _url(data["embedding_url"], "backends.qdrant-vector.embedding_url")
    for key in ("timeout_seconds",):
        if key in data:
            _positive_float(data[key], f"backends.{backend}.{key}")
    for key in ("batch_size", "max_files_scanned", "max_file_bytes", "max_total_bytes"):
        if key in data:
            _positive_int(data[key], f"backends.{backend}.{key}")
    for key in ("collection", "index", "embedding_model", "api_key"):
        if key in data:
            _string(data[key], f"backends.{backend}.{key}")
    if backend == "solr":
        from fetchcore.adapters.retrieval.solr.backend import SolrSettings

        SolrSettings.from_mapping(data)
    elif backend == "opensearch":
        from fetchcore.adapters.retrieval.opensearch.backend import OpenSearchSettings

        OpenSearchSettings.from_mapping(data)
    elif backend == "qdrant-vector":
        from fetchcore.adapters.retrieval.qdrant.backend import QdrantVectorSettings

        QdrantVectorSettings.from_mapping(data)


def _merge(base: dict[str, object], override: Mapping[str, object]) -> None:
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _merge(base[key], value)  # type: ignore[arg-type]
        else:
            base[key] = value


def _record_sources(
    value: object,
    *,
    source: RuntimeConfigValueSource,
    sources: dict[str, RuntimeConfigValueSource],
    prefix: str = "",
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            _record_sources(item, source=source, sources=sources, prefix=path)
        return
    if prefix:
        sources[prefix] = source


def _json_mapping(raw: str, name: str) -> dict[str, object]:
    try:
        return _mapping(json.loads(raw), name)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must contain a JSON object") from exc


def _env_overlay(env: Mapping[str, str], default_backend: str) -> dict[str, object]:
    overlay: dict[str, object] = {"runtime": {}, "backends": {}, "embedding": {}}
    runtime = overlay["runtime"]
    backends = overlay["backends"]
    embedding = overlay["embedding"]
    assert isinstance(runtime, dict) and isinstance(backends, dict) and isinstance(embedding, dict)

    def pick(canonical: str, legacy: str | None = None) -> str | None:
        if canonical in env:
            return env[canonical]
        return env.get(legacy) if legacy else None

    for field_name, canonical, legacy in (
        ("backend", "FETCHCORE_BACKEND", "FETCHCORE_MCP_BACKEND"),
        ("workspace", "FETCHCORE_WORKSPACE", "FETCHCORE_MCP_WORKSPACE"),
        ("repository", "FETCHCORE_REPOSITORY", "FETCHCORE_MCP_REPOSITORY"),
        ("revision", "FETCHCORE_REVISION", "FETCHCORE_MCP_REVISION"),
    ):
        value = pick(canonical, legacy)
        if value is not None and value.strip():
            runtime[field_name] = value
        elif value is not None and field_name in {"backend", "workspace", "repository"}:
            raise ValueError(f"{canonical} must not be empty")

    selected = runtime.get("backend", default_backend)
    if not isinstance(selected, str) or selected not in BUILTIN_BACKEND_IDS:
        raise KnowledgeSearchError(
            "unsupported FetchCore backend",
            category="unsupported_backend",
        )
    legacy_settings = env.get("FETCHCORE_MCP_BACKEND_SETTINGS")
    if legacy_settings is not None and legacy_settings.strip():
        backends[selected] = _json_mapping(legacy_settings, "FETCHCORE_MCP_BACKEND_SETTINGS")
    backend_settings = backends.setdefault(selected, {})
    assert isinstance(backend_settings, dict)
    prefix = {"solr": "SOLR", "opensearch": "OPENSEARCH", "qdrant-vector": "QDRANT"}.get(selected)
    if prefix:
        for field_name in ("URL", "COLLECTION", "INDEX", "API_KEY"):
            name = f"FETCHCORE_{prefix}_{field_name}"
            if name in env:
                backend_settings[field_name.lower()] = env[name]

    retrieval = pick("FETCHCORE_RETRIEVAL", "FETCHCORE_MCP_RETRIEVAL")
    if "FETCHCORE_RETRIEVAL" in env and not env["FETCHCORE_RETRIEVAL"].strip():
        raise ValueError("FETCHCORE_RETRIEVAL must contain a JSON object")
    if retrieval is not None and retrieval.strip():
        overlay["retrieval"] = _json_mapping(retrieval, "FETCHCORE_RETRIEVAL")
    ollama_url = pick("FETCHCORE_OLLAMA_URL", "OLLAMA_URL")
    if ollama_url is not None:
        embedding["ollama"] = {"url": ollama_url}
    for field_name in ("TIMEOUT_SECONDS", "REQUEST_BATCH_SIZE"):
        name = f"FETCHCORE_OLLAMA_{field_name}"
        if name in env:
            retrieval_overlay = overlay.setdefault("retrieval", {})
            assert isinstance(retrieval_overlay, dict)
            dense = _mapping(retrieval_overlay.get("dense", {}), "retrieval.dense")
            execution = _mapping(dense.get("embedding", {}), "retrieval.dense.embedding")
            try:
                execution[field_name.lower()] = (
                    float(env[name]) if field_name == "TIMEOUT_SECONDS" else int(env[name])
                )
            except ValueError as exc:
                raise ValueError(f"{name} must be numeric") from exc
            dense["embedding"] = execution
            retrieval_overlay["dense"] = dense
    for field_name in ("KNOWLEDGE_TYPES", "REPOSITORY_PATHS"):
        value = pick(f"FETCHCORE_{field_name}", f"FETCHCORE_MCP_{field_name}")
        if value is not None:
            runtime[field_name.lower()] = [item.strip() for item in value.split(",") if item.strip()]
    return overlay


def load_runtime_config(
    *,
    path: Path | None = None,
    environ: Mapping[str, str] | None = None,
    overrides: Mapping[str, object] | None = None,
) -> FetchCoreRuntimeConfig:
    """Load defaults < explicit operator YAML < ENV < explicit caller values.

    No path is discovered in the repository workspace. FETCHCORE_CONFIG is an
    explicit operator path when `path` is absent.
    """
    env = os.environ if environ is None else environ
    config_path = path if path is not None else (
        Path(env["FETCHCORE_CONFIG"]).expanduser() if "FETCHCORE_CONFIG" in env else None
    )
    data: dict[str, object] = {}
    sources: dict[str, RuntimeConfigValueSource] = {}
    if config_path is not None:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        data = _mapping(loaded, "runtime config")
        _record_sources(data, source="config_file", sources=sources)
        _fields(data, {"version", "runtime", "retrieval", "backends", "embedding"}, "runtime config")
        if type(data.get("version")) is not int or data["version"] != RUNTIME_CONFIG_VERSION:
            raise ValueError("unsupported runtime config version")
        for section in ("runtime", "backends", "embedding", "retrieval"):
            if section in data:
                _mapping(data[section], section)
    explicit_retrieval = (
        "retrieval" in data
        or any(env.get(name, "").strip() for name in ("FETCHCORE_RETRIEVAL", "FETCHCORE_MCP_RETRIEVAL"))
        or (overrides is not None and "retrieval" in overrides)
    )
    envelope_env = any(
        name in env
        for name in ("FETCHCORE_OLLAMA_TIMEOUT_SECONDS", "FETCHCORE_OLLAMA_REQUEST_BATCH_SIZE")
    )
    file_runtime = _mapping(data.get("runtime", {}), "runtime")
    file_backend = file_runtime.get("backend", "local-semantic")
    if not isinstance(file_backend, str):
        raise ValueError("runtime.backend must be a string")
    environment = _env_overlay(env, file_backend)
    _merge(data, environment)
    _record_sources(environment, source="environment", sources=sources)
    if overrides is not None:
        explicit = _mapping(overrides, "runtime overrides")
        _merge(data, explicit)
        _record_sources(explicit, source="explicit", sources=sources)
    _fields(data, {"version", "runtime", "retrieval", "backends", "embedding"}, "runtime config")
    if "version" in data and (
        type(data["version"]) is not int or data["version"] != RUNTIME_CONFIG_VERSION
    ):
        raise ValueError("unsupported runtime config version")
    runtime = _mapping(data.get("runtime", {}), "runtime")
    _fields(runtime, {
        "backend", "workspace", "repository", "revision", "knowledge_types", "repository_paths"
    }, "runtime")
    backend = _string(runtime.get("backend", "local-semantic"), "runtime.backend")
    if backend not in BUILTIN_BACKEND_IDS:
        raise KnowledgeSearchError(
            "unsupported FetchCore backend",
            category="unsupported_backend",
        )
    backends = _mapping(data.get("backends", {}), "backends")
    _fields(backends, BUILTIN_BACKEND_IDS, "backends")
    for name, settings in backends.items():
        validate_backend_settings(name, _mapping(settings, f"backends.{name}"))
    selected_settings = _mapping(backends.get(backend, {}), f"backends.{backend}")
    embedding = _mapping(data.get("embedding", {}), "embedding")
    _fields(embedding, BUILTIN_EMBEDDING_PROVIDER_IDS, "embedding")
    ollama = OllamaRuntimeSettings.from_mapping(
        _mapping(embedding.get("ollama", {}), "embedding.ollama")
    )
    if backend == "qdrant-vector" and envelope_env and not explicit_retrieval:
        # Qdrant is dense by construction. Preserve its legacy model when ENV
        # supplies only the neutral embedding execution envelope.
        raw_retrieval = _mapping(data.get("retrieval", {}), "retrieval")
        raw_dense = _mapping(raw_retrieval.get("dense", {}), "retrieval.dense")
        raw_embedding = _mapping(raw_dense.get("embedding", {}), "retrieval.dense.embedding")
        raw_dense["enabled"] = True
        raw_embedding["model"] = selected_settings.get("embedding_model", EmbeddingConfig.model)
        raw_dense["embedding"] = raw_embedding
        raw_retrieval["dense"] = raw_dense
        data["retrieval"] = raw_retrieval
    retrieval_raw = data.get("retrieval")
    retrieval = None if retrieval_raw is None else RetrievalConfig.from_mapping(
        _mapping(retrieval_raw, "retrieval")
    )
    workspace = Path(_string(runtime.get("workspace", os.getcwd()), "runtime.workspace")).expanduser()
    repository_raw = runtime.get("repository")
    repository = None if repository_raw is None else _string(repository_raw, "runtime.repository")
    revision_raw = runtime.get("revision")
    revision = None if revision_raw is None else _string(revision_raw, "runtime.revision")

    def string_tuple(field_name: str) -> tuple[str, ...]:
        raw = runtime.get(field_name, [])
        if not isinstance(raw, list) or any(
            not isinstance(item, str) or not item.strip() for item in raw
        ):
            raise ValueError(f"runtime.{field_name} must be a list of non-empty strings")
        return tuple(item.strip() for item in raw)

    if backend == "qdrant-vector":
        # Translate compatibility fields at bootstrap; the adapter's historical
        # settings shape remains available to existing direct callers.
        if "embedding_url" in selected_settings and "ollama" not in embedding:
            ollama = OllamaRuntimeSettings.from_mapping(
                {"url": selected_settings["embedding_url"]}
            )
            sources["embedding.ollama.url"] = sources.get(
                f"backends.{backend}.embedding_url", "default",
            )
        selected_settings["embedding_url"] = ollama.url
        if retrieval is not None:
            selected_settings["embedding_model"] = retrieval.dense.embedding.model
            sources[f"backends.{backend}.embedding_model"] = sources.get(
                "retrieval.dense.embedding.model", "default",
            )
        sources[f"backends.{backend}.embedding_url"] = sources.get(
            "embedding.ollama.url",
            sources.get(f"backends.{backend}.embedding_url", "default"),
        )
    return FetchCoreRuntimeConfig(
        backend=backend,
        backend_settings=selected_settings,
        retrieval=retrieval,
        ollama=ollama,
        workspace=workspace,
        repository=repository,
        revision=revision,
        knowledge_types=string_tuple("knowledge_types"),
        repository_paths=string_tuple("repository_paths"),
        sources=sources,
    )


def service_from_runtime_config(
    config: FetchCoreRuntimeConfig,
    *,
    backends: BackendRegistry | None = None,
    providers: EmbeddingProviderRegistry | None = None,
) -> RetrievalApplicationService:
    """Compose a serving service from an already resolved in-memory config."""
    from .service import RepositoryBinding, RetrievalApplicationService

    resolved_backends, _ = _runtime_registries_for_config(
        config, backends=backends, providers=providers,
    )
    repository = config.repository or config.workspace.name
    binding = RepositoryBinding(
        repository=repository,
        workspace=config.workspace,
        backend=config.backend,
        revision=config.revision,
        settings=dict(config.backend_settings),
        retrieval=config.retrieval,
        knowledge_types=config.knowledge_types,
        repository_paths=config.repository_paths,
    )
    return RetrievalApplicationService(
        {repository: binding},
        backends=resolved_backends,
    )


def _runtime_registries_for_config(
    config: FetchCoreRuntimeConfig,
    *,
    backends: BackendRegistry | None = None,
    providers: EmbeddingProviderRegistry | None = None,
) -> tuple[BackendRegistry, EmbeddingProviderRegistry]:
    from .composition import BackendRegistry, EmbeddingProviderRegistry

    resolved_providers = providers or EmbeddingProviderRegistry(
        settings={"ollama": config.ollama.to_mapping()}
    )
    resolved_backends = backends or BackendRegistry(providers=resolved_providers)
    return resolved_backends, resolved_providers
