"""Operator-owned runtime configuration and deterministic source precedence."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import yaml

from fetchcore.adapters.embedding.ollama.provider import OllamaEmbeddingSettings
from fetchcore.retrieval.config import RetrievalConfig

if TYPE_CHECKING:
    from .composition import BackendRegistry, EmbeddingProviderRegistry
    from .service import RetrievalApplicationService

RUNTIME_CONFIG_VERSION = 1
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
_BACKENDS = frozenset(_BACKEND_FIELDS)


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
    """Deployment values for the built-in Ollama implementation."""

    url: str = OllamaEmbeddingSettings.url
    timeout_seconds: float = OllamaEmbeddingSettings.timeout_seconds
    request_batch_size: int = OllamaEmbeddingSettings.request_batch_size

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> OllamaRuntimeSettings:
        data = _mapping(value, "embedding.ollama")
        _fields(data, {"url", "timeout_seconds", "request_batch_size"}, "embedding.ollama")
        return cls(
            url=_url(data.get("url", cls.url), "embedding.ollama.url"),
            timeout_seconds=_positive_float(
                data.get("timeout_seconds", cls.timeout_seconds), "embedding.ollama.timeout_seconds"
            ),
            request_batch_size=_positive_int(
                data.get("request_batch_size", cls.request_batch_size),
                "embedding.ollama.request_batch_size",
            ),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "url": self.url,
            "timeout_seconds": self.timeout_seconds,
            "request_batch_size": self.request_batch_size,
        }


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

    def __post_init__(self) -> None:
        if self.backend not in _BACKENDS:
            raise ValueError(f"unsupported FetchCore backend {self.backend!r}")
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
    if not isinstance(selected, str) or selected not in _BACKENDS:
        raise ValueError(f"unsupported FetchCore backend {selected!r}")
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
            ollama = embedding.setdefault("ollama", {})
            assert isinstance(ollama, dict)
            try:
                ollama[field_name.lower()] = (
                    float(env[name]) if field_name == "TIMEOUT_SECONDS" else int(env[name])
                )
            except ValueError as exc:
                raise ValueError(f"{name} must be numeric") from exc
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
    if config_path is not None:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        data = _mapping(loaded, "runtime config")
        _fields(data, {"version", "runtime", "retrieval", "backends", "embedding"}, "runtime config")
        if type(data.get("version")) is not int or data["version"] != RUNTIME_CONFIG_VERSION:
            raise ValueError("unsupported runtime config version")
        for section in ("runtime", "backends", "embedding", "retrieval"):
            if section in data:
                _mapping(data[section], section)
    file_runtime = _mapping(data.get("runtime", {}), "runtime")
    file_backend = file_runtime.get("backend", "local-semantic")
    if not isinstance(file_backend, str):
        raise ValueError("runtime.backend must be a string")
    _merge(data, _env_overlay(env, file_backend))
    if overrides is not None:
        _merge(data, _mapping(overrides, "runtime overrides"))
    _fields(data, {"version", "runtime", "retrieval", "backends", "embedding"}, "runtime config")
    if "version" in data and (type(data["version"]) is not int or data["version"] != 1):
        raise ValueError("unsupported runtime config version")
    runtime = _mapping(data.get("runtime", {}), "runtime")
    _fields(runtime, {
        "backend", "workspace", "repository", "revision", "knowledge_types", "repository_paths"
    }, "runtime")
    backend = _string(runtime.get("backend", "local-semantic"), "runtime.backend")
    if backend not in _BACKENDS:
        raise ValueError(f"unsupported FetchCore backend {backend!r}")
    backends = _mapping(data.get("backends", {}), "backends")
    _fields(backends, _BACKENDS, "backends")
    for name, settings in backends.items():
        validate_backend_settings(name, _mapping(settings, f"backends.{name}"))
    embedding = _mapping(data.get("embedding", {}), "embedding")
    _fields(embedding, {"ollama"}, "embedding")
    ollama = OllamaRuntimeSettings.from_mapping(
        _mapping(embedding.get("ollama", {}), "embedding.ollama")
    )
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
        if not isinstance(raw, list) or any(not isinstance(item, str) or not item for item in raw):
            raise ValueError(f"runtime.{field_name} must be a list of non-empty strings")
        return tuple(raw)

    selected_settings = _mapping(backends.get(backend, {}), f"backends.{backend}")
    if backend == "qdrant-vector":
        # Translate compatibility fields at bootstrap; the adapter's historical
        # settings shape remains available to existing direct callers.
        if "embedding_url" in selected_settings and "ollama" not in embedding:
            ollama = OllamaRuntimeSettings.from_mapping(
                {"url": selected_settings["embedding_url"]}
            )
        selected_settings["embedding_url"] = ollama.url
        if retrieval is not None:
            selected_settings["embedding_model"] = retrieval.dense.embedding.model
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
    )


def service_from_runtime_config(
    config: FetchCoreRuntimeConfig,
    *,
    backends: BackendRegistry | None = None,
    providers: EmbeddingProviderRegistry | None = None,
) -> RetrievalApplicationService:
    """Compose a serving service from an already resolved in-memory config."""
    from .composition import BackendRegistry, EmbeddingProviderRegistry
    from .service import RepositoryBinding, RetrievalApplicationService

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
    resolved_providers = providers or EmbeddingProviderRegistry(
        settings={"ollama": config.ollama.to_mapping()}
    )
    return RetrievalApplicationService(
        {repository: binding},
        backends=backends or BackendRegistry(providers=resolved_providers),
    )
