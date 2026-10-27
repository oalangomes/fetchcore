"""Application boundary for exact navigation over canonical knowledge."""

from __future__ import annotations

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

    def get_related(
        self,
        symbol: SymbolDefinition,
        *,
        relation_types: tuple[str, ...] = (),
        direction: NavigationDirection = "outgoing",
        limit: int = 20,
    ) -> tuple[RelatedEvidence, ...]:
        """Return observed relations for a selected symbol with provenance."""

        return get_related(
            self._documents,
            symbol,
            relation_types=relation_types,
            direction=direction,
            limit=limit,
        )
