"""Application boundary for exact navigation over canonical knowledge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import get_args, Literal

from fetchcore.knowledge.structure import (
    ExtractionCapabilities, StructuralCapability, StructuralCoverage,
)
from fetchcore.repository.indexing import KnowledgeDocument
from fetchcore.repository.navigation import (
    NavigationDirection,
    RelatedEvidence,
    SymbolDefinition,
    find_symbol,
    get_related,
    StructuralNavigationError,
)

RELATION_CAPABILITIES: dict[str, StructuralCapability] = {
    "calls": "calls_observed", "references": "references_observed",
    "tests": "tests_observed", "imports": "imports", "dependencies": "imports",
    "extends": "inheritance", "implements": "inheritance",
}


@dataclass(frozen=True)
class FindSymbolRequest:
    name: str
    repository: str | None = None
    revision: str | None = None
    language: str | None = None
    kind: str | None = None
    path: str | None = None
    limit: int = 20


@dataclass(frozen=True)
class GetRelatedRequest:
    source: str
    repository: str | None = None
    revision: str | None = None
    relation_types: tuple[str, ...] = ()
    direction: str = "outgoing"
    limit: int = 20
    depth: int = 1
    require_resolution: bool = False


@dataclass(frozen=True)
class NavigationResult:
    repository: str
    revision: str
    results: tuple[SymbolDefinition, ...] | tuple[RelatedEvidence, ...]
    coverage: tuple[StructuralCoverage, ...]
    status: Literal["success", "not_found", "partial", "unsupported", "unknown"]
    limit_reached: bool
    scan_complete: bool = True
    scan_diagnostics: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, object]:
        return {
            "repository": self.repository, "revision": self.revision,
            "results": [item.to_mapping() for item in self.results],
            "coverage": [item.to_mapping() for item in self.coverage],
            "status": self.status, "limit_reached": self.limit_reached,
            "scan_complete": self.scan_complete,
            "scan_diagnostics": list(self.scan_diagnostics),
        }


def validate_navigation_request(request: FindSymbolRequest | GetRelatedRequest) -> None:
    value = request.name if isinstance(request, FindSymbolRequest) else request.source
    if not value.strip():
        raise StructuralNavigationError("navigation identifier must not be empty",
                                        category="schema_mismatch")
    if type(request.limit) is not int or not 1 <= request.limit <= 100:
        raise StructuralNavigationError("navigation limit must be between 1 and 100",
                                        category="schema_mismatch")
    if isinstance(request, GetRelatedRequest):
        if request.direction != "outgoing" or type(request.depth) is not int or request.depth != 1:
            raise StructuralNavigationError("only outgoing depth-one navigation is supported",
                                            category="unsupported_capability")
        if set(request.relation_types) - RELATION_CAPABILITIES.keys():
            raise StructuralNavigationError("unsupported relation type",
                                            category="unsupported_capability")


def navigation_result(
    repository: str, revision: str,
    results: tuple[SymbolDefinition, ...] | tuple[RelatedEvidence, ...],
    coverage: tuple[StructuralCoverage, ...], limit: int,
    *, scan_diagnostics: tuple[str, ...] = (),
) -> NavigationResult:
    statuses = {item.status for item in coverage}
    status: Literal["success", "not_found", "partial", "unsupported", "unknown"]
    if scan_diagnostics or "partial" in statuses or len(statuses) > 1:
        status = "partial"
    elif statuses == {"unsupported"}:
        status = "unsupported"
    elif statuses == {"unknown"}:
        status = "unknown"
    else:
        status = "success" if results else "not_found"
    return NavigationResult(
        repository, revision, results, coverage, status, len(results) >= limit,
        not scan_diagnostics, scan_diagnostics,
    )


class RepositoryKnowledgeNavigationService:
    """Provide deterministic symbol and relation lookups over canonical documents."""

    def __init__(self, documents: tuple[KnowledgeDocument, ...]) -> None:
        repositories = {item.repository for item in documents}
        revisions = {item.revision for item in documents}
        if len(repositories) > 1:
            raise ValueError("navigation documents must belong to one repository")
        if len(revisions) > 1:
            raise ValueError("navigation documents must belong to one revision")
        sources: dict[str, tuple[str, str]] = {}
        for document in documents:
            trace = (document.source_ref, document.provenance.source_checksum)
            if sources.setdefault(document.path, trace) != trace:
                raise ValueError("navigation source has conflicting provenance in one revision")
        self._documents = documents

    def find_symbol(
        self,
        name: str,
        *,
        language: str | None = None,
        kind: str | None = None,
        path: str | None = None,
        limit: int = 20,
    ) -> tuple[SymbolDefinition, ...]:
        """Return exact symbol observations without choosing among homonyms."""

        return find_symbol(
            self._documents,
            name,
            language=language,
            kind=kind,
            path=path,
            limit=limit,
        )

    def capabilities(
        self, *, language: str | None = None, path: str | None = None,
    ) -> tuple[ExtractionCapabilities, ...]:
        """Return persisted coverage, never infer capabilities from a language name."""

        return tuple(sorted({
            doc.evidence.extraction for doc in self._documents
            if doc.evidence.extraction is not None
            and (language is None or doc.language == language)
            and (path is None or doc.path == path)
        }, key=lambda item: (item.language or "", item.producer, item.version, item.capabilities)))

    def coverage(
        self, capability: StructuralCapability = "declarations", *,
        language: str | None = None, path: str | None = None,
    ) -> StructuralCoverage:
        """Distinguish verified misses from partial, unsupported or legacy coverage."""

        if capability not in get_args(StructuralCapability):
            raise ValueError("unknown structural capability")
        sources = {
            doc.source_ref: doc.evidence.extraction for doc in self._documents
            if (language is None or doc.language == language)
            and (path is None or doc.path == path)
        }
        return StructuralCoverage(
            capability=capability, sources=len(sources),
            supported_sources=sum(
                item is not None and capability in item.capabilities for item in sources.values()
            ),
            unknown_sources=sum(item is None for item in sources.values()),
            producers=self.capabilities(language=language, path=path),
        )

    def get_related(
        self,
        symbol: SymbolDefinition | str,
        *,
        relation_types: tuple[str, ...] = (),
        direction: NavigationDirection = "outgoing",
        limit: int = 20,
        require_resolution: bool = False,
    ) -> tuple[RelatedEvidence, ...]:
        """Return observed relations for a selected symbol with provenance."""

        return get_related(
            self._documents,
            symbol,
            relation_types=relation_types,
            direction=direction,
            limit=limit,
            require_resolution=require_resolution,
        )
