"""Exact navigation over canonical repository knowledge documents."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from fetchcore.knowledge.evidence import SymbolEvidence
from fetchcore.repository.indexing import KnowledgeDocument


NavigationDirection = Literal["outgoing"]
_MAX_NAVIGATION_RESULTS = 100


@dataclass(frozen=True)
class SymbolDefinition:
    """One exact symbol observation with its canonical source provenance."""

    identity: str
    repository: str
    revision: str
    path: str
    source_ref: str
    checksum: str
    language: str | None
    name: str
    qualified_name: str
    kind: str
    signature: str | None
    line_start: int
    line_end: int
    document_id: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "identity": self.identity,
            "repository": self.repository,
            "revision": self.revision,
            "path": self.path,
            "source_ref": self.source_ref,
            "checksum": self.checksum,
            "language": self.language,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "kind": self.kind,
            "signature": self.signature,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "document_id": self.document_id,
        }


@dataclass(frozen=True)
class RelatedEvidence:
    """A relation observation; targets remain unresolved unless evidence says so."""

    source_identity: str
    kind: str
    target: str
    resolution: str
    evidence_basis: str
    source_ref: str
    revision: str
    checksum: str
    line: int | None
    target_document_id: str | None = None
    target_source_ref: str | None = None
    target_path: str | None = None

    def to_mapping(self) -> dict[str, object]:
        return {
            "source_identity": self.source_identity,
            "kind": self.kind,
            "target": self.target,
            "resolution": self.resolution,
            "evidence_basis": self.evidence_basis,
            "source_ref": self.source_ref,
            "revision": self.revision,
            "checksum": self.checksum,
            "line": self.line,
            "target_document_id": self.target_document_id,
            "target_source_ref": self.target_source_ref,
            "target_path": self.target_path,
        }


def find_symbol(
    documents: tuple[KnowledgeDocument, ...],
    name: str,
    *,
    repository: str | None = None,
    revision: str | None = None,
    language: str | None = None,
    kind: str | None = None,
    path: str | None = None,
    limit: int = 20,
) -> tuple[SymbolDefinition, ...]:
    """Find exact names or qualified names; return homonyms as separate evidence."""

    query = name.strip()
    if not query:
        raise ValueError("symbol name must not be empty")
    _validate_limit(limit)
    definitions: dict[str, SymbolDefinition] = {}
    for document in documents:
        if repository is not None and document.repository != repository:
            continue
        if revision is not None and document.revision != revision:
            continue
        if language is not None and document.language != language:
            continue
        if path is not None and document.path != path:
            continue
        for observed in _document_symbols(document):
            qualified_name = observed.qualified_name or observed.name
            if query not in {observed.name, qualified_name}:
                continue
            if kind is not None and observed.kind != kind:
                continue
            identity = _symbol_identity(document, observed)
            definitions[identity] = SymbolDefinition(
                identity=identity,
                repository=document.repository,
                revision=document.revision,
                path=document.path,
                source_ref=document.source_ref,
                checksum=document.provenance.source_checksum,
                language=document.language,
                name=observed.name,
                qualified_name=qualified_name,
                kind=observed.kind,
                signature=observed.signature,
                line_start=observed.line_start,
                line_end=observed.line_end,
                document_id=document.id,
            )
    return tuple(
        sorted(
            definitions.values(),
            key=lambda item: (
                item.repository,
                item.path,
                item.qualified_name,
                item.kind,
                item.signature or "",
                item.line_start,
                item.identity,
            ),
        )[:limit]
    )


def get_related(
    documents: tuple[KnowledgeDocument, ...],
    symbol: SymbolDefinition,
    *,
    relation_types: tuple[str, ...] = (),
    direction: NavigationDirection = "outgoing",
    limit: int = 20,
) -> tuple[RelatedEvidence, ...]:
    """Return only relations explicitly attached to the selected source symbol."""

    if direction != "outgoing":
        raise ValueError("only outgoing relation evidence is currently supported")
    _validate_limit(limit)
    allowed = {value.strip().casefold() for value in relation_types if value.strip()}
    result: dict[tuple[str, str, str, int | None], RelatedEvidence] = {}
    for document in documents:
        if (
            document.repository != symbol.repository
            or document.revision != symbol.revision
            or document.path != symbol.path
        ):
            continue
        if not any(
            _symbol_identity(document, observed) == symbol.identity
            for observed in _document_symbols(document)
        ):
            continue
        relations = document.evidence.relations
        for call in relations.calls:
            if call.source_symbol not in {symbol.qualified_name, symbol.name}:
                continue
            _add_relation(
                result,
                symbol,
                document,
                kind=call.kind or "call",
                target=call.target,
                resolution=call.resolution,
                evidence_basis="source",
                line=call.line,
                relation_types=allowed,
            )
        for reference in relations.references:
            if reference.source_symbol not in {symbol.qualified_name, symbol.name}:
                continue
            _add_relation(
                result,
                symbol,
                document,
                kind=reference.kind or "reference",
                target=reference.target,
                resolution=reference.resolution,
                evidence_basis="source",
                line=reference.line,
                relation_types=allowed,
            )
        primary = document.evidence.symbol
        if (
            primary is not None
            and (primary.qualified_name or primary.name) == symbol.qualified_name
        ):
            for semantic in relations.semantic:
                _add_relation(
                    result,
                    symbol,
                    document,
                    kind=semantic.kind,
                    target=semantic.target,
                    resolution=semantic.resolution,
                    evidence_basis=semantic.evidence_basis,
                    line=None,
                    relation_types=allowed,
                    target_document_id=semantic.target_document_id,
                    target_source_ref=semantic.target_source_ref,
                    target_path=semantic.target_path,
                )
    return tuple(
        sorted(
            result.values(),
            key=lambda item: (
                item.kind,
                item.target,
                item.source_ref,
                item.line or 0,
            ),
        )[:limit]
    )


def _document_symbols(document: KnowledgeDocument) -> tuple[SymbolEvidence, ...]:
    values: list[SymbolEvidence] = []
    if document.evidence.symbol is not None:
        values.append(document.evidence.symbol)
    values.extend(document.evidence.behavior.methods)
    return tuple(values)


def _symbol_identity(document: KnowledgeDocument, observed: SymbolEvidence) -> str:
    qualified_name = observed.qualified_name or observed.name
    signature = observed.signature
    identity = {
        "repository": document.repository,
        "path": document.path,
        "language": document.language,
        "qualified_name": qualified_name,
        "kind": observed.kind,
        "signature": signature,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "symbol:sha256:" + hashlib.sha256(encoded).hexdigest()


def _add_relation(
    result: dict[tuple[str, str, str, int | None], RelatedEvidence],
    symbol: SymbolDefinition,
    document: KnowledgeDocument,
    *,
    kind: str,
    target: str,
    resolution: str,
    evidence_basis: str,
    line: int | None,
    relation_types: set[str],
    target_document_id: str | None = None,
    target_source_ref: str | None = None,
    target_path: str | None = None,
) -> None:
    normalized_kind = kind.strip().casefold()
    if not target.strip() or (relation_types and normalized_kind not in relation_types):
        return
    relation = RelatedEvidence(
        source_identity=symbol.identity,
        kind=normalized_kind,
        target=target,
        resolution=resolution,
        evidence_basis=evidence_basis,
        source_ref=document.source_ref,
        revision=document.revision,
        checksum=document.provenance.source_checksum,
        line=line,
        target_document_id=target_document_id,
        target_source_ref=target_source_ref,
        target_path=target_path,
    )
    result[(relation.kind, relation.target, relation.source_ref, relation.line)] = relation


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not 1 <= limit <= _MAX_NAVIGATION_RESULTS:
        raise ValueError(f"limit must be between 1 and {_MAX_NAVIGATION_RESULTS}")
