"""Application boundary for exact navigation over canonical knowledge."""

from __future__ import annotations

from typing import get_args

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
