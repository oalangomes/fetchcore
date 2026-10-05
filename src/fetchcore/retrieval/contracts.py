"""Backend-neutral FetchCore search contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, Protocol, TypeAlias, cast, get_args

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


AnchorKind = Literal["identifier", "qualified_symbol", "path", "route", "source_ref"]


def _object(value: object, allowed: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("intent value must be an object with string keys")
    if set(value) - allowed:
        raise ValueError("unsupported intent fields: " + str(sorted(set(value) - allowed)))
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("intent text must be a string")
    return value


def _items(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("intent collection must be a list")
    return value


@dataclass(frozen=True)
class Term:
    """A declared lexical phrase, not an inferred natural-language expression."""

    value: str

    def __post_init__(self) -> None:
        if not _text(self.value).strip():
            raise ValueError("term must not be empty")

    def to_mapping(self) -> dict[str, object]:
        return {"type": "term", "value": self.value}


@dataclass(frozen=True)
class Anchor:
    """Required structured fact; origin records who established it."""

    kind: AnchorKind
    value: str
    origin: str = "caller"

    def __post_init__(self) -> None:
        if self.kind not in get_args(AnchorKind):
            raise ValueError("invalid anchor kind")
        if not _text(self.value).strip() or not _text(self.origin).strip():
            raise ValueError("anchor value and origin must not be empty")

    def to_mapping(self) -> dict[str, object]:
        return {"type": "anchor", "kind": self.kind, "value": self.value, "origin": self.origin}


@dataclass(frozen=True)
class AllOf:
    """All declared children are required, within one retrieved record."""

    children: tuple[SearchExpression, ...]

    def __post_init__(self) -> None:
        _validate_children(self.children)

    def to_mapping(self) -> dict[str, object]:
        return {"type": "all_of", "children": [item.to_mapping() for item in self.children]}


@dataclass(frozen=True)
class AnyOf:
    """At least one declared child must match, preserving branch boundaries."""

    children: tuple[SearchExpression, ...]

    def __post_init__(self) -> None:
        _validate_children(self.children)

    def to_mapping(self) -> dict[str, object]:
        return {"type": "any_of", "children": [item.to_mapping() for item in self.children]}


SearchExpression: TypeAlias = Term | Anchor | AllOf | AnyOf


def _validate_children(children: tuple[SearchExpression, ...]) -> None:
    if not isinstance(children, tuple) or not children:
        raise ValueError("expression children must be a non-empty tuple")
    if any(not isinstance(item, (Term, Anchor, AllOf, AnyOf)) for item in children):
        raise ValueError("invalid expression child")


def expression_from_mapping(value: object, *, _depth: int = 0) -> SearchExpression:
    """Read declared structure only; reject unknown constructs and excessive depth."""
    if _depth > 16:
        raise ValueError("intent expression exceeds depth limit 16")
    data = _object(value, {"type", "kind", "value", "origin", "children"})
    kind = data.get("type")
    if kind == "term":
        _object(data, {"type", "value"})
        return Term(_text(data.get("value")))
    if kind == "anchor":
        _object(data, {"type", "kind", "value", "origin"})
        return Anchor(
            cast(AnchorKind, _text(data.get("kind"))),
            _text(data.get("value")),
            _text(data.get("origin", "caller")),
        )
    if kind in ("all_of", "any_of"):
        _object(data, {"type", "children"})
        children = tuple(
            expression_from_mapping(item, _depth=_depth + 1)
            for item in _items(data.get("children"))
        )
        return AllOf(children) if kind == "all_of" else AnyOf(children)
    raise ValueError("unsupported expression type")


@dataclass(frozen=True)
class QueryExpansion:
    """Caller-provided optional recall text with explicit provenance."""

    value: str
    origin: str

    def __post_init__(self) -> None:
        if not _text(self.value).strip() or not _text(self.origin).strip():
            raise ValueError("expansion value and origin must not be empty")

    def to_mapping(self) -> dict[str, object]:
        return {"value": self.value, "origin": self.origin}

    @classmethod
    def from_mapping(cls, value: object) -> QueryExpansion:
        data = _object(value, {"value", "origin"})
        return cls(_text(data.get("value")), _text(data.get("origin")))


@dataclass(frozen=True)
class SearchIntent:
    """Declared retrieval meaning, independent from scope and execution policy.

    The first four fields retain v1 positional construction and semantics. No
    phrase, anchor, or boolean expression is inferred from canonical_query.
    """

    exact_terms: tuple[str, ...] = ()
    semantic_terms: tuple[str, ...] = ()
    hints: tuple[str, ...] = ()
    signals: tuple[QuerySignal, ...] = ()
    canonical_query: str = ""
    anchors: tuple[Anchor, ...] = ()
    expression: SearchExpression | None = None
    expansions: tuple[QueryExpansion, ...] = ()

    def __post_init__(self) -> None:
        _text(self.canonical_query)
        for values, item_type in (
            (self.exact_terms, str),
            (self.semantic_terms, str),
            (self.hints, str),
            (self.signals, QuerySignal),
            (self.anchors, Anchor),
            (self.expansions, QueryExpansion),
        ):
            if not isinstance(values, tuple) or any(not isinstance(x, item_type) for x in values):
                raise ValueError("intent collections must be typed tuples")
        if len(self.anchors) + len(self.expansions) > 256:
            raise ValueError("intent structure exceeds node limit 256")
        pending = [(self.expression, 0)] if self.expression is not None else []
        count = len(self.anchors) + len(self.expansions)
        while pending:
            item, depth = pending.pop()
            count += 1
            if depth > 16 or count > 256:
                raise ValueError("intent structure exceeds depth/node limit")
            if not isinstance(item, (Term, Anchor, AllOf, AnyOf)):
                raise ValueError("invalid intent expression")
            if isinstance(item, (AllOf, AnyOf)):
                pending.extend((child, depth + 1) for child in item.children)

    @property
    def has_v2_semantics(self) -> bool:
        return bool(self.canonical_query or self.anchors or self.expression or self.expansions)

    @property
    def terms(self) -> tuple[str, ...]:
        """Legacy flat view; deliberately excludes v2 declared structure."""
        return tuple(dict.fromkeys((*self.exact_terms, *self.semantic_terms)))

    def to_mapping(self) -> dict[str, object]:
        data: dict[str, object] = {
            "exact_terms": list(self.exact_terms),
            "semantic_terms": list(self.semantic_terms),
            "hints": list(self.hints),
            "signals": [item.to_mapping() for item in self.signals],
        }
        # Preserve persisted v1 benchmark identities exactly for legacy callers.
        if self.has_v2_semantics:
            data.update(
                schema_version=2,
                canonical_query=self.canonical_query,
                anchors=[item.to_mapping() for item in self.anchors],
                expression=self.expression.to_mapping() if self.expression else None,
                expansions=[item.to_mapping() for item in self.expansions],
            )
        return data

    @classmethod
    def from_mapping(cls, value: object) -> SearchIntent:
        data = _object(
            value,
            {
                "schema_version",
                "canonical_query",
                "anchors",
                "expression",
                "expansions",
                "exact_terms",
                "semantic_terms",
                "hints",
                "signals",
            },
        )
        version = data.get("schema_version", 1)
        if type(version) is not int or version not in (1, 2):
            raise ValueError("unsupported SearchIntent schema_version")
        if version == 1 and set(data) & {"canonical_query", "anchors", "expression", "expansions"}:
            raise ValueError("v2 fields require schema_version 2")
        anchors = tuple(expression_from_mapping(item) for item in _items(data.get("anchors", [])))
        if any(not isinstance(item, Anchor) for item in anchors):
            raise ValueError("anchors must contain only anchors")
        return cls(
            exact_terms=tuple(_text(x) for x in _items(data.get("exact_terms", []))),
            semantic_terms=tuple(_text(x) for x in _items(data.get("semantic_terms", []))),
            hints=tuple(_text(x) for x in _items(data.get("hints", []))),
            signals=tuple(QuerySignal.from_mapping(x) for x in _items(data.get("signals", []))),
            canonical_query=_text(data.get("canonical_query", "")),
            anchors=cast(tuple[Anchor, ...], anchors),
            expression=(
                expression_from_mapping(data["expression"])
                if data.get("expression") is not None
                else None
            ),
            expansions=tuple(
                QueryExpansion.from_mapping(x) for x in _items(data.get("expansions", []))
            ),
        )


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
