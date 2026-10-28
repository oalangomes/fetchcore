"""Exact navigation derived from canonical declarations and relation evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from typing import Literal, get_args

from fetchcore.knowledge.evidence import SymbolEvidence, identify_declarations
from fetchcore.knowledge.structure import (
    ExtractionCapabilities,
    ResolutionStatus,
    optional_text,
    required_text,
)
from fetchcore.repository.indexing import KnowledgeDocument


NavigationDirection = Literal["outgoing"]
_MAX_NAVIGATION_RESULTS = 100


class StructuralNavigationError(ValueError):
    """Explicit missing, unsupported or incompatible structural knowledge."""

    def __init__(self, message: str, *, category: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class SymbolDefinition:
    """One exact declaration with its logical identity and current source trace."""

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
    parent_identity: str | None = None
    extraction: ExtractionCapabilities | None = None

    def __post_init__(self) -> None:
        if self.line_start < 1 or self.line_end < self.line_start:
            raise ValueError("symbol definition span is invalid")
        if self.parent_identity == self.identity:
            raise ValueError("symbol definition cannot be its own parent")

    def to_mapping(self) -> dict[str, object]:
        return {
            **asdict(self),
            "extraction": self.extraction.to_mapping() if self.extraction else None,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SymbolDefinition:
        return cls(
            identity=required_text(value, "identity"),
            repository=required_text(value, "repository"),
            revision=required_text(value, "revision"),
            path=required_text(value, "path"),
            source_ref=required_text(value, "source_ref"),
            checksum=required_text(value, "checksum"),
            language=optional_text(value, "language"),
            name=required_text(value, "name"),
            qualified_name=required_text(value, "qualified_name"),
            kind=required_text(value, "kind"),
            signature=optional_text(value, "signature"),
            line_start=_positive_integer(value, "line_start"),
            line_end=_positive_integer(value, "line_end"),
            document_id=required_text(value, "document_id"),
            parent_identity=optional_text(value, "parent_identity"),
            extraction=_read_extraction(value),
        )


@dataclass(frozen=True)
class RelatedEvidence:
    """A directed observation, separate from any proven target location."""

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
    status: ResolutionStatus = "observed"
    subtype: str | None = None
    repository: str | None = None
    path: str | None = None
    line_end: int | None = None
    extraction: ExtractionCapabilities | None = None
    direction: NavigationDirection = "outgoing"
    target_revision: str | None = None
    target_checksum: str | None = None

    def __post_init__(self) -> None:
        if self.status not in get_args(ResolutionStatus):
            raise ValueError("invalid relation resolution status")
        if self.direction != "outgoing":
            raise ValueError("only outgoing relation evidence is supported")

    def to_mapping(self) -> dict[str, object]:
        return {
            **asdict(self),
            "extraction": self.extraction.to_mapping() if self.extraction else None,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> RelatedEvidence:
        status = required_text(value, "status")
        if status not in get_args(ResolutionStatus):
            raise ValueError("invalid relation resolution status")
        if value.get("direction") != "outgoing":
            raise ValueError("only outgoing relation evidence is supported")
        return cls(
            source_identity=required_text(value, "source_identity"),
            kind=required_text(value, "kind"),
            target=required_text(value, "target"),
            resolution=required_text(value, "resolution"),
            evidence_basis=required_text(value, "evidence_basis"),
            source_ref=required_text(value, "source_ref"),
            revision=required_text(value, "revision"),
            checksum=required_text(value, "checksum"),
            line=_optional_integer(value, "line"),
            target_document_id=optional_text(value, "target_document_id"),
            target_source_ref=optional_text(value, "target_source_ref"),
            target_path=optional_text(value, "target_path"),
            status=_resolution_status(status),
            subtype=optional_text(value, "subtype"),
            repository=optional_text(value, "repository"),
            path=optional_text(value, "path"),
            line_end=_optional_integer(value, "line_end"),
            extraction=_read_extraction(value),
            target_revision=optional_text(value, "target_revision"),
            target_checksum=optional_text(value, "target_checksum"),
        )


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
    """Return exact homonyms, never resolve ambiguity by choosing a candidate."""

    if not name.strip():
        raise ValueError("symbol name must not be empty")
    _validate_limit(limit)
    scoped = tuple(
        document for document in documents
        if (repository is None or document.repository == repository)
        and (revision is None or document.revision == revision)
        and (language is None or document.language == language)
        and (path is None or document.path == path)
    )
    definitions: dict[tuple[str, str], SymbolDefinition] = {}
    for document in sorted(scoped, key=lambda item: (item.line_start, item.line_end, item.id)):
        for observed in _document_symbols(document):
            if name.strip() not in {
                observed.name, observed.qualified_name or observed.name,
                observed.identity.key if observed.identity else "",
            }:
                continue
            if kind is not None and observed.kind != kind:
                continue
            if observed.identity is None:
                raise StructuralNavigationError(
                    "declaration has no logical identity", category="schema_mismatch",
                )
            definition = SymbolDefinition(
                identity=observed.identity.key,
                repository=document.repository,
                revision=document.revision,
                path=document.path,
                source_ref=document.source_ref,
                checksum=document.provenance.source_checksum,
                language=document.language,
                name=observed.name,
                qualified_name=observed.qualified_name or observed.name,
                kind=observed.kind,
                signature=observed.signature,
                line_start=observed.line_start,
                line_end=observed.line_end,
                document_id=document.id,
                parent_identity=observed.parent_identity,
                extraction=document.evidence.extraction,
            )
            key = (definition.identity, definition.revision)
            existing = definitions.setdefault(key, definition)
            if replace(existing, document_id=definition.document_id) != definition:
                raise StructuralNavigationError(
                    "declaration replicas disagree within one revision", category="schema_mismatch",
                )
    if scoped and not definitions and not any(
        doc.evidence.extraction is not None
        and "declarations" in doc.evidence.extraction.capabilities
        for doc in scoped
    ):
        raise StructuralNavigationError(
            "declaration extraction is unavailable in this scope",
            category="unsupported_capability",
        )
    return tuple(sorted(definitions.values(), key=lambda item: (
        item.repository, item.path, item.qualified_name, item.kind,
        item.signature or "", item.revision, item.line_start, item.identity,
    ))[:limit])


def get_related(
    documents: tuple[KnowledgeDocument, ...],
    symbol: SymbolDefinition | str,
    *,
    relation_types: tuple[str, ...] = (),
    direction: NavigationDirection = "outgoing",
    limit: int = 20,
    require_resolution: bool = False,
) -> tuple[RelatedEvidence, ...]:
    """Read symbol-owned calls/references or file-owned imports and semantic relations."""

    if direction != "outgoing":
        raise StructuralNavigationError(
            "reverse traversal is not supported", category="unsupported_capability",
        )
    _validate_limit(limit)
    scoped = tuple(document for document in documents if (
        document.source_ref == symbol if isinstance(symbol, str) else
        document.repository == symbol.repository
        and document.revision == symbol.revision
        and document.path == symbol.path
        and document.provenance.source_checksum == symbol.checksum
        and any(
            item.identity is not None and item.identity.key == symbol.identity
            for item in _document_symbols(document)
        )
    ))
    if not scoped:
        raise StructuralNavigationError(
            "source or declaration is absent from the selected snapshot",
            category="source_missing",
        )
    if len({(doc.repository, doc.revision) for doc in scoped}) != 1:
        raise StructuralNavigationError(
            "source reference matches multiple revisions", category="ambiguous_source",
        )
    if require_resolution and not any(
        doc.evidence.extraction is not None
        and "cross_file_resolution" in doc.evidence.extraction.capabilities
        for doc in scoped
    ):
        raise StructuralNavigationError(
            "precise cross-file resolution is unavailable",
            category="unsupported_capability",
        )
    allowed = set(relation_types)
    values: list[RelatedEvidence] = []
    for document in scoped:
        values.extend(_relations(document, symbol))
    selected = [item for item in values if not allowed or item.kind in allowed]
    required = {
        "calls": "calls_observed", "references": "references_observed",
        "tests": "tests_observed", "imports": "imports", "dependencies": "imports",
        "extends": "inheritance", "implements": "inheritance",
    }
    available = {
        cap for doc in scoped if doc.evidence.extraction is not None
        for cap in doc.evidence.extraction.capabilities
    }
    needed = {required[kind] for kind in allowed if kind in required}
    if not selected and (
        (needed and not needed <= available)
        or (
            not needed
            and not available & (
                {"imports"} if isinstance(symbol, str)
                else {"calls_observed", "references_observed", "tests_observed"}
            )
        )
    ):
        raise StructuralNavigationError(
            "requested relation extraction is unavailable", category="unsupported_capability",
        )
    # Full observation keys preserve resolution, target location and producer paths.
    targets = {
        doc.id: doc for doc in documents
        if doc.repository == scoped[0].repository and doc.revision == scoped[0].revision
    }
    located: list[RelatedEvidence] = []
    for relation in selected:
        target = targets.get(relation.target_document_id or "")
        if target is not None:
            if (
                relation.target_source_ref is not None
                and relation.target_source_ref != target.source_ref
            ) or (
                relation.target_path is not None and relation.target_path != target.path
            ):
                raise StructuralNavigationError(
                    "resolved target provenance conflicts with canonical document",
                    category="schema_mismatch",
                )
            relation = replace(
                relation, target_revision=target.revision,
                target_checksum=target.provenance.source_checksum,
            )
        located.append(relation)
    return tuple(sorted(set(located), key=lambda item: (
        item.kind, item.target, item.source_ref, item.line or 0,
        json.dumps(item.to_mapping(), sort_keys=True, separators=(",", ":")),
    ))[:limit])


def _relations(
    document: KnowledgeDocument, source: SymbolDefinition | str,
) -> tuple[RelatedEvidence, ...]:
    result: list[RelatedEvidence] = []

    def add(
        kind: str, target: str, resolution: str, line: int | None = None,
        *, subtype: str | None = None, evidence_basis: str = "source",
        target_document_id: str | None = None, target_source_ref: str | None = None,
        target_path: str | None = None,
    ) -> None:
        result.append(RelatedEvidence(
            source_identity=source if isinstance(source, str) else source.identity,
            kind=kind, target=target, resolution=resolution,
            status=_resolution_status(resolution), subtype=subtype,
            evidence_basis=evidence_basis,
            source_ref=document.source_ref, revision=document.revision,
            checksum=document.provenance.source_checksum, line=line,
            repository=document.repository, path=document.path, line_end=line,
            extraction=document.evidence.extraction,
            target_document_id=target_document_id, target_source_ref=target_source_ref,
            target_path=target_path,
        ))

    relations = document.evidence.relations
    if isinstance(source, str):
        for imported in document.evidence.structure.imports:
            add("imports", imported.source, "observed", imported.line, subtype=imported.kind)
        for dependency in relations.dependencies:
            add("dependencies", dependency, "observed")
        for semantic in relations.semantic:
            add(
                semantic.kind, semantic.target, semantic.resolution,
                evidence_basis=semantic.evidence_basis,
                target_document_id=semantic.target_document_id,
                target_source_ref=semantic.target_source_ref, target_path=semantic.target_path,
            )
    else:
        for call in relations.calls:
            if _owns(source, call.source_symbol, call.line, document):
                add("calls", call.target, call.resolution, call.line, subtype=call.kind)
        for reference in relations.references:
            if _owns(source, reference.source_symbol, reference.line, document):
                add(
                    "references", reference.target, reference.resolution, reference.line,
                    subtype=reference.kind,
                )
        for test in relations.test_targets:
            if test.call_line is not None and source.line_start <= test.call_line <= source.line_end:
                add("tests", test.target_source, test.resolution, test.call_line,
                    subtype=test.target_symbol)
    return tuple(result)


def _owns(
    source: SymbolDefinition, name: str | None, line: int | None, document: KnowledgeDocument,
) -> bool:
    if name not in {source.qualified_name, source.name}:
        return False
    candidates = [
        item for item in _document_symbols(document)
        if name in {item.name, item.qualified_name}
        and (line is None or item.line_start <= line <= item.line_end)
    ]
    return (
        len(candidates) == 1 and candidates[0].identity is not None
        and candidates[0].identity.key == source.identity
    )


def _document_symbols(document: KnowledgeDocument) -> tuple[SymbolEvidence, ...]:
    if document.evidence.extraction is not None:
        for symbol in document.evidence.declarations:
            identity = symbol.identity
            if identity is None or (
                identity.repository != document.repository or identity.path != document.path
                or identity.language != document.language
            ):
                raise StructuralNavigationError(
                    "declaration identity conflicts with document scope", category="schema_mismatch",
                )
        return document.evidence.declarations
    # Legacy documents have partial coverage, not an invented producer capability.
    return identify_declarations(
        tuple(dict.fromkeys((
            *((document.evidence.symbol,) if document.evidence.symbol else ()),
            *document.evidence.behavior.methods,
        ))),
        repository=document.repository, path=document.path, language=document.language,
    )


def _resolution_status(value: str) -> ResolutionStatus:
    if value in {"resolved", "exact-unique"}:
        return "resolved"
    if value == "ambiguous":
        return "ambiguous"
    if value == "unsupported":
        return "unsupported"
    if value in {"syntax", "unresolved", "unknown", "legacy", "import_binding",
                 "dynamic_import_binding"}:
        return "unresolved"
    return "observed"


def _positive_integer(value: Mapping[str, object], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool) or item < 1:
        raise ValueError(f"{key} must be a positive integer")
    return item


def _optional_integer(value: Mapping[str, object], key: str) -> int | None:
    return _positive_integer(value, key) if value.get(key) is not None else None


def _read_extraction(value: Mapping[str, object]) -> ExtractionCapabilities | None:
    raw = value.get("extraction")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("extraction must be an object")
    return ExtractionCapabilities.from_mapping(raw)


def _validate_limit(limit: int) -> None:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _MAX_NAVIGATION_RESULTS:
        raise ValueError(f"limit must be between 1 and {_MAX_NAVIGATION_RESULTS}")
