"""Backend-neutral retrieval capability configuration and validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Literal


RetrievalMode = Literal["lexical", "dense", "hybrid"]
FusionStrategy = Literal["rrf"]
RerankingParameterValue = str | int | float | bool


class RetrievalConfigurationError(ValueError):
    """Stable configuration error raised before backend execution."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "schema_mismatch",
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Initialize RetrievalConfigurationError with its explicit dependencies."""
        super().__init__(message)
        self.category = category
        self.metadata = dict(metadata or {})


@dataclass(frozen=True)
class EmbeddingConfig:
    """Backend-neutral embedding identity and optional transport execution envelope."""

    provider: str = "ollama"
    model: str = "embeddinggemma"
    timeout_seconds: float | None = None
    request_batch_size: int | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> EmbeddingConfig:
        """Build the value from a mapping while validating its public contract."""
        data = _configuration_mapping(value, "retrieval.dense.embedding")
        provider = _string_value(
            data.get("provider", cls.provider), "retrieval.dense.embedding.provider"
        ).strip()
        model = _string_value(
            data.get("model", cls.model), "retrieval.dense.embedding.model"
        ).strip()
        timeout_seconds = _optional_positive_float(
            data.get("timeout_seconds"),
            "retrieval.dense.embedding.timeout_seconds",
        )
        request_batch_size = _optional_positive_int(
            data.get("request_batch_size"),
            "retrieval.dense.embedding.request_batch_size",
        )
        if not provider:
            raise RetrievalConfigurationError(
                "retrieval.dense.embedding.provider must not be empty"
            )
        if not model:
            raise RetrievalConfigurationError(
                "retrieval.dense.embedding.model must not be empty"
            )
        return cls(
            provider=provider,
            model=model,
            timeout_seconds=timeout_seconds,
            request_batch_size=request_batch_size,
        )

    def to_mapping(self) -> dict[str, object]:
        """Return a stable mapping representation of this value."""
        result: dict[str, object] = {
            "provider": self.provider,
            "model": self.model,
        }
        if self.timeout_seconds is not None:
            result["timeout_seconds"] = self.timeout_seconds
        if self.request_batch_size is not None:
            result["request_batch_size"] = self.request_batch_size
        return result


@dataclass(frozen=True)
class DenseRetrievalConfig:
    """Configuration for opt-in dense retrieval."""

    enabled: bool = False
    embedding: EmbeddingConfig = EmbeddingConfig()

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> DenseRetrievalConfig:
        """Build the value from a mapping while validating its public contract."""
        data = _configuration_mapping(value, "retrieval.dense")
        enabled = _bool_value(data.get("enabled", False), "retrieval.dense.enabled")
        embedding = _mapping_value(
            data.get("embedding"),
            "retrieval.dense.embedding",
            allow_none=True,
        )
        return cls(
            enabled=enabled,
            embedding=EmbeddingConfig.from_mapping(embedding),
        )

    def to_mapping(self) -> dict[str, object]:
        """Return a stable mapping representation of this value."""
        return {
            "enabled": self.enabled,
            "embedding": self.embedding.to_mapping(),
        }


@dataclass(frozen=True)
class HybridRetrievalConfig:
    """Configuration for opt-in hybrid retrieval and fusion."""

    enabled: bool = False
    strategy: FusionStrategy = "rrf"

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> HybridRetrievalConfig:
        """Build the value from a mapping while validating its public contract."""
        data = _configuration_mapping(value, "retrieval.hybrid")
        enabled = _bool_value(data.get("enabled", False), "retrieval.hybrid.enabled")
        strategy = _string_value(
            data.get("strategy", "rrf"), "retrieval.hybrid.strategy"
        ).strip().casefold()
        if strategy != "rrf":
            raise RetrievalConfigurationError(
                "retrieval.hybrid.strategy currently supports only 'rrf'",
                metadata={"strategy": strategy},
            )
        return cls(enabled=enabled, strategy="rrf")

    def to_mapping(self) -> dict[str, object]:
        """Return a stable mapping representation of this value."""
        return {"enabled": self.enabled, "strategy": self.strategy}


@dataclass(frozen=True)
class RetrievalToggleConfig:
    """Boolean configuration for retrieval capabilities that remain opt-in."""

    enabled: bool = False

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, object] | None,
        *,
        path: str,
    ) -> RetrievalToggleConfig:
        """Build the value from a mapping while validating its public contract."""
        data = _configuration_mapping(value, path)
        return cls(enabled=_bool_value(data.get("enabled", False), f"{path}.enabled"))

    def to_mapping(self) -> dict[str, object]:
        """Return a stable mapping representation of this value."""
        return {"enabled": self.enabled}


@dataclass(frozen=True)
class RerankingConfig:
    """Backend-neutral post-acquisition reranking intent and scorer identity."""

    enabled: bool = False
    scorer_id: str | None = None
    scorer_version: str | None = None
    window_size: int | None = None
    parameters: tuple[tuple[str, RerankingParameterValue], ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> RerankingConfig:
        """Build a fail-closed reranking configuration from its public mapping."""
        data = _configuration_mapping(value, "retrieval.reranking")
        allowed = {"enabled", "scorer", "window_size", "parameters"}
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise RetrievalConfigurationError(
                "retrieval.reranking contains unsupported fields",
                metadata={"fields": unknown},
            )

        enabled = _bool_value(data.get("enabled", False), "retrieval.reranking.enabled")
        scorer = _mapping_value(
            data.get("scorer"),
            "retrieval.reranking.scorer",
            allow_none=True,
        )
        scorer_id: str | None = None
        scorer_version: str | None = None
        if scorer is not None:
            unknown_scorer = sorted(set(scorer) - {"id", "version"})
            if unknown_scorer:
                raise RetrievalConfigurationError(
                    "retrieval.reranking.scorer contains unsupported fields",
                    metadata={"fields": unknown_scorer},
                )
            scorer_id = _optional_non_empty_string(
                scorer.get("id"), "retrieval.reranking.scorer.id"
            )
            scorer_version = _optional_non_empty_string(
                scorer.get("version"), "retrieval.reranking.scorer.version"
            )

        window_size = _optional_positive_int(
            data.get("window_size"), "retrieval.reranking.window_size"
        )
        raw_parameters = _mapping_value(
            data.get("parameters"),
            "retrieval.reranking.parameters",
            allow_none=True,
        )
        parameters = tuple(
            (name, _reranking_parameter_value(parameter, name))
            for name, parameter in sorted(
                (raw_parameters or {}).items(), key=lambda item: item[0]
            )
        )
        if any(not name for name, _value in parameters):
            raise RetrievalConfigurationError(
                "retrieval.reranking parameter names must not be empty"
            )

        if enabled:
            missing = tuple(
                field
                for field, present in (
                    ("scorer.id", scorer_id is not None),
                    ("scorer.version", scorer_version is not None),
                    ("window_size", window_size is not None),
                )
                if not present
            )
            if missing:
                raise RetrievalConfigurationError(
                    "enabled retrieval.reranking requires explicit scorer identity and window",
                    category="unsupported_capability",
                    metadata={
                        "capability": "reranking",
                        "owner": "post_acquisition",
                        "missing": list(missing),
                    },
                )

        return cls(
            enabled=enabled,
            scorer_id=scorer_id,
            scorer_version=scorer_version,
            window_size=window_size,
            parameters=parameters,
        )

    def to_mapping(self) -> dict[str, object]:
        """Return a stable mapping without inventing scorer defaults."""
        result: dict[str, object] = {"enabled": self.enabled}
        if self.scorer_id is not None or self.scorer_version is not None:
            result["scorer"] = {
                **({"id": self.scorer_id} if self.scorer_id is not None else {}),
                **(
                    {"version": self.scorer_version}
                    if self.scorer_version is not None
                    else {}
                ),
            }
        if self.window_size is not None:
            result["window_size"] = self.window_size
        if self.parameters:
            result["parameters"] = dict(self.parameters)
        return result


@dataclass(frozen=True)
class RetrievalConfig:
    """Product-level retrieval intent, independent from any engine."""

    dense: DenseRetrievalConfig = DenseRetrievalConfig()
    hybrid: HybridRetrievalConfig = HybridRetrievalConfig()
    reranking: RerankingConfig = RerankingConfig()
    ltr: RetrievalToggleConfig = RetrievalToggleConfig()

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> RetrievalConfig:
        """Build the value from a mapping while validating its public contract."""
        data = _configuration_mapping(value, "retrieval")
        allowed = {"dense", "hybrid", "reranking", "ltr", "mode"}
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise RetrievalConfigurationError(
                "retrieval contains unsupported fields",
                metadata={"fields": unknown},
            )

        dense = DenseRetrievalConfig.from_mapping(
            _mapping_value(data.get("dense"), "retrieval.dense", allow_none=True)
        )
        hybrid = HybridRetrievalConfig.from_mapping(
            _mapping_value(data.get("hybrid"), "retrieval.hybrid", allow_none=True)
        )
        reranking = RerankingConfig.from_mapping(
            _mapping_value(data.get("reranking"), "retrieval.reranking", allow_none=True)
        )
        ltr = RetrievalToggleConfig.from_mapping(
            _mapping_value(data.get("ltr"), "retrieval.ltr", allow_none=True),
            path="retrieval.ltr",
        )
        config = cls(dense=dense, hybrid=hybrid, reranking=reranking, ltr=ltr)
        config.validate_semantics()
        if "mode" not in data:
            return config
        serialized_mode = data["mode"]
        if not isinstance(serialized_mode, str):
            raise RetrievalConfigurationError("retrieval.mode must be a string")
        if serialized_mode != config.mode:
            raise RetrievalConfigurationError(
                "retrieval.mode does not match effective retrieval configuration",
                metadata={
                    "serialized_mode": serialized_mode,
                    "effective_mode": config.mode,
                },
            )
        return config

    @property
    def mode(self) -> RetrievalMode:
        """Return the effective retrieval mode selected by configuration."""
        if self.hybrid.enabled:
            return "hybrid"
        if self.dense.enabled:
            return "dense"
        return "lexical"

    def validate_semantics(self) -> None:
        """Validate cross-capability retrieval configuration semantics."""
        if self.hybrid.enabled and not self.dense.enabled:
            raise RetrievalConfigurationError(
                "retrieval.hybrid.enabled requires retrieval.dense.enabled",
                metadata={"capability": "hybrid"},
            )

    def to_mapping(self) -> dict[str, object]:
        """Return a stable mapping representation of this value."""
        return {
            "dense": self.dense.to_mapping(),
            "hybrid": self.hybrid.to_mapping(),
            "reranking": self.reranking.to_mapping(),
            "ltr": self.ltr.to_mapping(),
            "mode": self.mode,
        }


@dataclass(frozen=True)
class RetrievalBackendCapabilities:
    """Capabilities explicitly advertised by a candidate-acquisition backend.

    ``reranking`` remains serialized for compatibility with the initial #115
    schema, but it is not an acquisition capability gate. Backend-neutral
    reranking is post-acquisition and is owned by #120.
    """

    lexical: bool = False
    dense: bool = False
    hybrid: bool = False
    reranking: bool = False
    ltr: bool = False
    canonical_query: bool = False
    structured_anchors: bool = False
    boolean_expressions: bool = False

    def to_mapping(self) -> dict[str, bool]:
        """Return a stable mapping representation of this value."""
        return {
            "lexical": self.lexical,
            "dense": self.dense,
            "hybrid": self.hybrid,
            "reranking": self.reranking,
            "ltr": self.ltr,
            "canonical_query": self.canonical_query,
            "structured_anchors": self.structured_anchors,
            "boolean_expressions": self.boolean_expressions,
        }


def validate_backend_retrieval(
    config: RetrievalConfig,
    *,
    backend_id: str,
    capabilities: RetrievalBackendCapabilities,
) -> None:
    """Fail closed when requested candidate-acquisition intent is unsupported."""
    config.validate_semantics()

    required: list[str] = []
    if config.mode == "lexical":
        required.append("lexical")
    elif config.mode == "dense":
        required.append("dense")
    else:
        # Hybrid is its own acquisition capability. Its non-dense channel may be
        # lexical/structured (Solr/OpenSearch) or native sparse lexical (Qdrant)
        # without implying that standalone lexical mode is supported.
        required.extend(("hybrid", "dense"))

    # Reranking is intentionally absent: it is resolved and executed by the
    # post-acquisition #120 boundary, never by backend capability declaration.
    if config.ltr.enabled:
        required.append("ltr")

    available = capabilities.to_mapping()
    for capability in required:
        if not available[capability]:
            raise RetrievalConfigurationError(
                f"backend {backend_id!r} does not support retrieval capability {capability!r}",
                category="unsupported_capability",
                metadata={"backend": backend_id, "capability": capability},
            )


def _mapping_value(
    value: object,
    path: str,
    *,
    allow_none: bool,
) -> Mapping[str, object] | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, Mapping):
        raise RetrievalConfigurationError(f"{path} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise RetrievalConfigurationError(f"{path} field names must be strings")
    return dict(value)


def _configuration_mapping(
    value: Mapping[str, object] | None,
    path: str,
) -> dict[str, object]:
    if value is None:
        return {}
    mapping = _mapping_value(value, path, allow_none=False)
    assert mapping is not None
    return dict(mapping)


def _string_value(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise RetrievalConfigurationError(f"{path} must be a string")
    return value


def _bool_value(value: object, path: str) -> bool:
    if isinstance(value, bool):
        return value
    raise RetrievalConfigurationError(f"{path} must be boolean")


def _optional_non_empty_string(value: object, path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RetrievalConfigurationError(f"{path} must be a non-empty string")
    return value.strip()


def _optional_positive_float(value: object, path: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RetrievalConfigurationError(f"{path} must be a positive finite number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise RetrievalConfigurationError(
            f"{path} must be a positive finite number"
        ) from exc
    if result <= 0 or not isfinite(result):
        raise RetrievalConfigurationError(f"{path} must be a positive finite number")
    return result


def _optional_positive_int(value: object, path: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RetrievalConfigurationError(f"{path} must be a positive integer")
    return value


def _reranking_parameter_value(
    value: object,
    name: str,
) -> RerankingParameterValue:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and isfinite(value):
        return value
    raise RetrievalConfigurationError(
        "retrieval.reranking parameter values must be finite JSON scalars",
        metadata={"parameter": name},
    )
