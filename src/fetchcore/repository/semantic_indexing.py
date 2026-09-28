"""Composable source interpretation and semantic enrichment for repository knowledge."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from fetchcore.repository.document_extractors import DocumentSourceExtractor
from fetchcore.repository.document_indexing import DocumentRepositoryIndexer
from fetchcore.repository.document_policy import RepositoryDocumentIndexPolicy
from fetchcore.repository.indexing import (
    KnowledgeDocument,
    RepositoryIndexConfig,
    RepositoryIndexResult,
    SymbolExtractor,
    _Chunk,
    _diff_documents,
    _source_ref,
    _utc_now,
)
from fetchcore.repository.safe_scan import SafeSource


@dataclass(frozen=True)
class KnowledgeSourceUnit:
    """One sanitized canonical unit emitted by a source interpreter.

    The unit owns only source-local representation. Canonical document identity,
    provenance, policy classification fields, and backend projection remain the
    responsibility of the repository indexing runtime.
    """

    discriminator: str
    content: str
    line_start: int
    line_end: int
    language: str | None = None
    knowledge_type: str = "configuration"
    strategy: str = "interpreted-source"

    def __post_init__(self) -> None:
        """Validate the bounded source-local unit contract."""

        if not self.discriminator.strip():
            raise ValueError("knowledge source unit discriminator must not be empty")
        if not self.content.strip():
            raise ValueError("knowledge source unit content must not be empty")
        if self.line_start < 1 or self.line_end < self.line_start:
            raise ValueError("knowledge source unit line provenance is invalid")
        if not self.knowledge_type.strip():
            raise ValueError("knowledge source unit knowledge_type must not be empty")
        if not self.strategy.strip():
            raise ValueError("knowledge source unit strategy must not be empty")


class KnowledgeSourceInterpreter(Protocol):
    """Claim a text source and replace raw chunking with sanitized source units.

    ``None`` means the interpreter does not own the source. An empty tuple means
    the source was intentionally claimed but yielded no safe canonical knowledge;
    callers must not fall back to raw-text indexing in that case.
    """

    indexing_contract_id: str
    indexing_contract_version: str

    def interpret(self, source: SafeSource) -> tuple[KnowledgeSourceUnit, ...] | None:
        """Return sanitized units, ``()`` for safe empty output, or ``None`` if unclaimed."""
        ...


class KnowledgeDocumentEnricher(Protocol):
    """Semantic-only extension point applied after canonical source indexing."""

    indexing_contract_id: str
    indexing_contract_version: str

    def enrich(
        self,
        source: SafeSource,
        document: KnowledgeDocument,
    ) -> KnowledgeDocument:
        """Return the document with additional canonical evidence only."""
        ...


class KnowledgeCorpusResolver(Protocol):
    """Resolve corpus-wide relationships without changing document authority.

    Resolvers run only after source interpretation and semantic enrichment have
    produced the complete current corpus. They may derive canonical evidence from
    exact corpus facts, but may not change document identity, provenance, content,
    classification, ordering, or membership.
    """

    indexing_contract_id: str
    indexing_contract_version: str

    def resolve(
        self,
        documents: tuple[KnowledgeDocument, ...],
    ) -> tuple[KnowledgeDocument, ...]:
        """Return the same corpus with evidence-only deterministic resolution."""
        ...


class SemanticDocumentRepositoryIndexer(DocumentRepositoryIndexer):
    """Canonical indexer with governed, fingerprinted repository extensions."""

    def __init__(
        self,
        config: RepositoryIndexConfig | None = None,
        *,
        symbol_extractors: Mapping[str, SymbolExtractor] | None = None,
        document_extractors: Mapping[str, DocumentSourceExtractor] | None = None,
        document_policy: RepositoryDocumentIndexPolicy | None = None,
        source_interpreters: Iterable[KnowledgeSourceInterpreter] = (),
        semantic_enrichers: Iterable[KnowledgeDocumentEnricher] = (),
        corpus_resolvers: Iterable[KnowledgeCorpusResolver] = (),
        clock: Callable[[], str] = _utc_now,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize ordered repository extensions and validate their identities."""

        super().__init__(
            config,
            symbol_extractors=symbol_extractors,
            document_extractors=document_extractors,
            document_policy=document_policy,
            clock=clock,
            timer=timer,
        )
        interpreters = tuple(source_interpreters)
        enrichers = tuple(semantic_enrichers)
        resolvers = tuple(corpus_resolvers)
        _validate_unique_component_ids(interpreters, "source interpreter")
        _validate_unique_component_ids(enrichers, "semantic enricher")
        _validate_unique_component_ids(resolvers, "corpus resolver")
        self.source_interpreters = interpreters
        self.semantic_enrichers = enrichers
        self.corpus_resolvers = resolvers

    @property
    def semantic_indexing_contract(self) -> dict[str, object]:
        """Return deterministic repository-extension identities for diagnostics."""

        return {
            "source_interpreters": [
                _component_contract(item) for item in self.source_interpreters
            ],
            "enrichers": [_component_contract(item) for item in self.semantic_enrichers],
            "corpus_resolvers": [
                _component_contract(item) for item in self.corpus_resolvers
            ],
        }

    @property
    def document_indexing_contract(self) -> dict[str, object]:
        """Extend the existing fingerprint only when repository extensions are active."""

        payload = dict(super().document_indexing_contract)
        if self.source_interpreters:
            payload["source_interpreters"] = [
                _component_contract(item) for item in self.source_interpreters
            ]
        if self.semantic_enrichers:
            payload["semantic_enrichers"] = [
                _component_contract(item) for item in self.semantic_enrichers
            ]
        if self.corpus_resolvers:
            payload["corpus_resolvers"] = [
                _component_contract(item) for item in self.corpus_resolvers
            ]
        return payload

    def requires_full_scan_for_paths(
        self,
        changed_paths: Sequence[str],
        removed_paths: Sequence[str] = (),
    ) -> bool:
        """Use a full scan until deltas can safely replay every active extension."""

        if (
            self.source_interpreters
            or self.semantic_enrichers
            or self.corpus_resolvers
        ) and (changed_paths or removed_paths):
            return True
        return super().requires_full_scan_for_paths(changed_paths, removed_paths)

    def index(
        self,
        workspace: Path,
        *,
        previous_documents: Iterable[KnowledgeDocument] = (),
    ) -> RepositoryIndexResult:
        """Index source-local facts, resolve the full corpus, then compute the final diff."""

        started = self.timer()
        previous = tuple(previous_documents)
        base = super().index(workspace, previous_documents=previous)
        if not self.corpus_resolvers:
            return base

        current = base.documents
        for resolver in self.corpus_resolvers:
            candidate = resolver.resolve(current)
            current = _validate_corpus_resolution(
                current,
                candidate,
                resolver=resolver,
            )

        changes = _diff_documents(
            current,
            previous,
            removal_detection_complete=base.changes.removal_detection_complete,
        )
        telemetry = replace(
            base.telemetry,
            documents_produced=len(current),
            added=len(changes.added),
            updated=len(changes.updated),
            removed=len(changes.removed),
            unchanged=len(changes.unchanged),
            duration_ms=max(0, round((self.timer() - started) * 1_000)),
        )
        return replace(
            base,
            documents=current,
            changes=changes,
            telemetry=telemetry,
        )

    def _documents_for_source(
        self,
        source: SafeSource,
        *,
        repository: str,
        branch: str | None,
        revision: str,
        indexed_at: str,
    ) -> tuple[KnowledgeDocument, ...]:
        interpreted = self._interpret_source(
            source,
            repository=repository,
            branch=branch,
            revision=revision,
            indexed_at=indexed_at,
        )
        documents = (
            super()._documents_for_source(
                source,
                repository=repository,
                branch=branch,
                revision=revision,
                indexed_at=indexed_at,
            )
            if interpreted is None
            else interpreted
        )
        return self._enrich_documents(source, documents)

    def _interpret_source(
        self,
        source: SafeSource,
        *,
        repository: str,
        branch: str | None,
        revision: str,
        indexed_at: str,
    ) -> tuple[KnowledgeDocument, ...] | None:
        claimed_by: KnowledgeSourceInterpreter | None = None
        units: tuple[KnowledgeSourceUnit, ...] | None = None
        for interpreter in self.source_interpreters:
            candidate = interpreter.interpret(source)
            if candidate is None:
                continue
            validated = _validate_interpreted_units(
                candidate,
                interpreter=interpreter,
                path=source.relative,
            )
            if claimed_by is not None:
                raise ValueError(
                    "repository source was claimed by multiple interpreters: "
                    f"{claimed_by.indexing_contract_id!r}, "
                    f"{interpreter.indexing_contract_id!r}; path={source.relative!r}"
                )
            claimed_by = interpreter
            units = validated
        if claimed_by is None or units is None:
            return None
        if not units:
            return ()

        max_line = max(1, len(source.content.splitlines()))
        source_ref = _source_ref(repository, source.relative)
        documents: list[KnowledgeDocument] = []
        for unit in units:
            if unit.line_end > max_line:
                raise ValueError(
                    "knowledge source interpreter emitted provenance outside source bounds: "
                    f"{claimed_by.indexing_contract_id!r}; path={source.relative!r}; "
                    f"line_end={unit.line_end}; source_lines={max_line}"
                )
            chunk = _Chunk(
                discriminator=unit.discriminator,
                content=unit.content,
                line_start=unit.line_start,
                line_end=unit.line_end,
                symbols=(),
                strategy=unit.strategy,
            )
            documents.append(
                self._document(
                    source,
                    chunk,
                    repository=repository,
                    branch=branch,
                    revision=revision,
                    source_ref=source_ref,
                    language=unit.language,
                    knowledge_type=unit.knowledge_type,
                    indexed_at=indexed_at,
                    extractor=None,
                    structure=None,
                    lexical=None,
                )
            )
        return tuple(documents)

    def _enrich_documents(
        self,
        source: SafeSource,
        documents: tuple[KnowledgeDocument, ...],
    ) -> tuple[KnowledgeDocument, ...]:
        enriched: list[KnowledgeDocument] = []
        for document in documents:
            current = document
            for enricher in self.semantic_enrichers:
                candidate = enricher.enrich(source, current)
                _validate_semantic_only_change(current, candidate, enricher)
                current = candidate
            enriched.append(current)
        return tuple(enriched)


def _component_contract(component: object) -> dict[str, str]:
    identity = str(getattr(component, "indexing_contract_id", "")).strip()
    version = str(getattr(component, "indexing_contract_version", "")).strip()
    if not identity:
        raise ValueError("repository indexing extension id must not be empty")
    if not version:
        raise ValueError("repository indexing extension version must not be empty")
    return {
        "id": identity,
        "version": version,
        "type": f"{type(component).__module__}.{type(component).__qualname__}",
    }


def _validate_unique_component_ids(components: tuple[object, ...], subject: str) -> None:
    identities = tuple(_component_contract(item)["id"] for item in components)
    if len(identities) != len(set(identities)):
        raise ValueError(f"{subject} indexing_contract_id values must be unique")


def _validate_interpreted_units(
    value: object,
    *,
    interpreter: KnowledgeSourceInterpreter,
    path: str,
) -> tuple[KnowledgeSourceUnit, ...]:
    if not isinstance(value, tuple):
        raise TypeError(
            "knowledge source interpreter must return a tuple, None, or an empty tuple: "
            f"{interpreter.indexing_contract_id!r}; path={path!r}"
        )
    units: list[KnowledgeSourceUnit] = []
    discriminators: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, KnowledgeSourceUnit):
            raise TypeError(
                "knowledge source interpreter returned an invalid unit: "
                f"{interpreter.indexing_contract_id!r}; path={path!r}; index={index}"
            )
        if item.discriminator in discriminators:
            raise ValueError(
                "knowledge source interpreter emitted duplicate unit discriminator: "
                f"{interpreter.indexing_contract_id!r}; path={path!r}; "
                f"discriminator={item.discriminator!r}"
            )
        discriminators.add(item.discriminator)
        units.append(item)
    return tuple(units)


def _validate_semantic_only_change(
    original: KnowledgeDocument,
    enriched: KnowledgeDocument,
    enricher: KnowledgeDocumentEnricher,
) -> None:
    if not isinstance(enriched, KnowledgeDocument):
        raise TypeError(
            f"semantic enricher {enricher.indexing_contract_id!r} returned an invalid document"
        )
    if replace(enriched, evidence=original.evidence) != original:
        raise ValueError(
            f"semantic enricher {enricher.indexing_contract_id!r} may only change evidence"
        )


def _validate_corpus_resolution(
    original: tuple[KnowledgeDocument, ...],
    resolved: object,
    *,
    resolver: KnowledgeCorpusResolver,
) -> tuple[KnowledgeDocument, ...]:
    if not isinstance(resolved, tuple):
        raise TypeError(
            f"corpus resolver {resolver.indexing_contract_id!r} must return a tuple"
        )
    if len(resolved) != len(original):
        raise ValueError(
            f"corpus resolver {resolver.indexing_contract_id!r} may not change corpus membership"
        )

    validated: list[KnowledgeDocument] = []
    for index, (before, candidate) in enumerate(zip(original, resolved, strict=True)):
        if not isinstance(candidate, KnowledgeDocument):
            raise TypeError(
                f"corpus resolver {resolver.indexing_contract_id!r} returned an invalid "
                f"document at index {index}"
            )
        if candidate.id != before.id:
            raise ValueError(
                f"corpus resolver {resolver.indexing_contract_id!r} may not reorder documents"
            )
        if replace(candidate, evidence=before.evidence) != before:
            raise ValueError(
                f"corpus resolver {resolver.indexing_contract_id!r} may only change evidence"
            )
        validated.append(candidate)
    return tuple(validated)
