"""Backend-neutral FetchCore search contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from fetchcore.retrieval.evidence import EvidenceObservation, QueryMatchEvidence
from fetchcore.retrieval import RetrievalBackendCapabilities, RetrievalConfig
from fetchcore.retrieval.signals import QuerySignal
from fetchcore.telemetry import RetrievalTelemetry


SearchMode = Literal["repository_source", "discovery", "targeted"]
SearchCapabilitySource = Literal[
    "provider_declared",
    "provider_classification",
    "legacy_metadata",
    "inferred_compatibility",
]


class KnowledgeSearchError(ValueError):
    """Backend/search failure with a stable caller-facing category."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "failed",
        metadata: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.metadata = dict(metadata or {})


@dataclass(frozen=True)
class SearchIntent:
    """Backend-neutral acquisition intent."""

    exact_terms: tuple[str, ...] = ()
    semantic_terms: tuple[str, ...] = ()
    hints: tuple[str, ...] = ()
    signals: tuple[QuerySignal, ...] = ()

    @property
    def terms(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.exact_terms, *self.semantic_terms)))

    def to_mapping(self) -> dict[str, object]:
        return {
            "exact_terms": list(self.exact_terms),
            "semantic_terms": list(self.semantic_terms),
            "hints": list(self.hints),
            "signals": [item.to_mapping() for item in self.signals],
        }


@dataclass(frozen=True)
class SearchRequirement:
    """Bounded retrieval allocation hint; never a satisfaction decision."""

    id: str
    knowledge_type: str
    capabilities: tuple[str, ...] = ()
    required: bool = True
    minimum_relevant: int = 1
    target: int = 1
    priority: int = 0

    def to_mapping(self) -> dict[str, object]:
        data = asdict(self)
        data["capabilities"] = list(self.capabilities)
        return data


@dataclass(frozen=True)
class SearchScope:
    """Authorized corpus scope plus retrieval candidate budget."""

    workspace: Path
    revision: str | None
    repository: str | None
    knowledge_types: tuple[str, ...]
    max_results: int
    mode: SearchMode = "discovery"
    repository_paths: tuple[str, ...] = ()
    result_namespace: str = "fetchcore-search"
    settings: dict[str, object] = field(default_factory=dict)
    retrieval: RetrievalConfig | None = None

    def to_mapping(self) -> dict[str, object]:
        return {
            "workspace": str(self.workspace),
            "revision": self.revision,
            "repository": self.repository,
            "knowledge_types": list(self.knowledge_types),
            "max_results": self.max_results,
            "mode": self.mode,
            "repository_paths": list(self.repository_paths),
            "result_namespace": self.result_namespace,
            **(
                {"retrieval": self.retrieval.to_mapping()}
                if self.retrieval is not None
                else {}
            ),
        }


@dataclass(frozen=True)
class SearchRelationship:
    """Normalized source-derived relationship attached to a retrieval hit."""

    type: str
    target: str
    confidence: float = 1.0
    discovered_from: str | None = None


@dataclass(frozen=True)
class SearchResult:
    """FetchCore retrieval hit before any consumer-specific normalization."""

    id: str
    source: str
    source_ref: str
    knowledge_type: str
    content: str
    score: float
    query_alignment_score: float | None = None
    capabilities: tuple[str, ...] = ()
    capability_sources: dict[str, SearchCapabilitySource] = field(default_factory=dict)
    matched_terms: tuple[str, ...] = ()
    matched_signals: tuple[QuerySignal, ...] = ()
    reasons: tuple[str, ...] = ()
    line_start: int | None = None
    line_end: int | None = None
    revision: str | None = None
    checksum: str | None = None
    trust_level: str = "reviewed"
    source_type: str = "repository_file"
    authority_hints: tuple[str, ...] = ()
    observations: tuple[EvidenceObservation, ...] = ()
    relationships: tuple[SearchRelationship, ...] = ()
    query_matches: tuple[QueryMatchEvidence, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            self.query_alignment_score is not None
            and not 0.0 <= self.query_alignment_score <= 1.0
        ):
            raise ValueError("query_alignment_score must be between 0 and 1")
        if set(self.capability_sources) != set(self.capabilities):
            raise ValueError(
                "capability_sources must define exactly one source per capability"
            )

    @property
    def source_identity(self) -> str:
        return self.source_ref or self.source

    def to_mapping(self) -> dict[str, object]:
        data = asdict(self)
        data["capabilities"] = list(self.capabilities)
        data["matched_terms"] = list(self.matched_terms)
        data["matched_signals"] = [
            item.to_mapping() for item in self.matched_signals
        ]
        data["observations"] = [item.to_mapping() for item in self.observations]
        data["query_matches"] = [item.to_mapping() for item in self.query_matches]
        data["reasons"] = list(self.reasons)
        return {
            key: value for key, value in data.items() if value not in (None, (), {}, [])
        }


@dataclass(frozen=True)
class KnowledgeSearchResponse:
    """Backend-neutral response from a FetchCore search backend."""

    backend: str
    status: str
    results: tuple[SearchResult, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)
    duration_ms: int = 0
    telemetry: RetrievalTelemetry | None = None

    def to_mapping(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "status": self.status,
            "results": [item.to_mapping() for item in self.results],
            "metadata": self.metadata,
            "duration_ms": self.duration_ms,
            **(
                {"telemetry": self.telemetry.to_mapping()}
                if self.telemetry is not None
                else {}
            ),
        }


class KnowledgeSearchBackend(Protocol):
    """Protocol implemented by backend-neutral retrieval adapters."""

    backend_id: str
    retrieval_capabilities: RetrievalBackendCapabilities

    def search(
        self,
        intent: SearchIntent,
        scope: SearchScope,
        requirements: tuple[SearchRequirement, ...],
    ) -> KnowledgeSearchResponse:
        ...
