"""Backend-neutral contracts for repository knowledge-pack activation."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from fetchcore.repository.indexing import RepositoryIndexConfig
from fetchcore.repository.semantic_indexing import (
    KnowledgeCorpusResolver,
    KnowledgeDocumentEnricher,
    KnowledgeSourceInterpreter,
)


class RepositoryKnowledgePack(Protocol):
    """Workspace-selected bundle of repository indexing capabilities."""

    pack_id: str
    version: str

    def matches(self, workspace: Path) -> bool:
        """Return whether explicit workspace evidence activates this pack."""
        ...

    def configure(
        self,
        config: RepositoryIndexConfig,
        *,
        apply_defaults: bool,
    ) -> RepositoryIndexConfig:
        """Return bounded configuration required by this pack.

        ``apply_defaults`` is false when the repository owns an explicit indexing
        policy. Mandatory safety constraints may still be applied, but pack-owned
        admission/limit defaults must not silently override repository policy.
        """
        ...

    def source_interpreters(self) -> tuple[KnowledgeSourceInterpreter, ...]:
        """Return ordered source interpreters contributed by this pack."""
        ...

    def semantic_enrichers(self) -> tuple[KnowledgeDocumentEnricher, ...]:
        """Return ordered semantic enrichers contributed by this pack."""
        ...

    def corpus_resolvers(self) -> tuple[KnowledgeCorpusResolver, ...]:
        """Return ordered corpus-wide resolvers contributed by this pack."""
        ...


def select_repository_knowledge_packs(
    workspace: Path,
    packs: Iterable[RepositoryKnowledgePack],
) -> tuple[RepositoryKnowledgePack, ...]:
    """Select active packs deterministically without domain-specific branching."""

    active: list[RepositoryKnowledgePack] = []
    identities: set[str] = set()
    for pack in packs:
        identity = pack.pack_id.strip()
        version = pack.version.strip()
        if not identity or not version:
            raise ValueError("repository knowledge packs require non-empty id and version")
        if identity in identities:
            raise ValueError(f"duplicate repository knowledge pack id: {identity!r}")
        identities.add(identity)
        if pack.matches(workspace):
            active.append(pack)
    return tuple(active)


def configure_repository_knowledge_packs(
    config: RepositoryIndexConfig,
    packs: Iterable[RepositoryKnowledgePack],
    *,
    apply_defaults: bool,
) -> RepositoryIndexConfig:
    """Apply active pack configuration in deterministic registration order."""

    resolved = config
    for pack in packs:
        resolved = pack.configure(resolved, apply_defaults=apply_defaults)
    return resolved


def source_interpreters_from_packs(
    packs: Iterable[RepositoryKnowledgePack],
) -> tuple[KnowledgeSourceInterpreter, ...]:
    """Flatten active source interpreters while preserving pack registration order."""

    return tuple(
        interpreter for pack in packs for interpreter in pack.source_interpreters()
    )


def semantic_enrichers_from_packs(
    packs: Iterable[RepositoryKnowledgePack],
) -> tuple[KnowledgeDocumentEnricher, ...]:
    """Flatten active pack enrichers while preserving pack registration order."""

    return tuple(enricher for pack in packs for enricher in pack.semantic_enrichers())


def corpus_resolvers_from_packs(
    packs: Iterable[RepositoryKnowledgePack],
) -> tuple[KnowledgeCorpusResolver, ...]:
    """Flatten active corpus resolvers while preserving pack registration order."""

    return tuple(resolver for pack in packs for resolver in pack.corpus_resolvers())
