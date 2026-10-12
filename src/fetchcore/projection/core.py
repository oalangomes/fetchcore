"""Backend-neutral deterministic Projection v3 for Repository Knowledge."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import cast

from fetchcore.knowledge.evidence import KnowledgeEvidence
from fetchcore.repository.indexing import KnowledgeDocument, KnowledgeProvenance
from fetchcore.repository.lexical import supports_lexical_analysis


class ProjectionError(ValueError):
    """Base failure while validating or hydrating a projected representation."""

    def __init__(
        self,
        message: str,
        *,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.metadata = dict(metadata or {})


class ProjectionCompatibilityError(ProjectionError):
    """Projection version or contract fingerprint is incompatible."""


class ProjectionParsingError(ProjectionError):
    """Projected or canonical data cannot be reconstructed safely."""


PROJECTION_VERSION = "3"
_PROJECTION_CONTRACT_REVISION = "3"
_PROJECTED_KNOWLEDGE_FIELDS = (
    "branch",
    "canonical_payload",
    "checksum",
    "chunk_ref",
    "content",
    "content_identifiers_exact",
    "dependency_targets",
    "document_revision",
    "documentation_text",
    "filename",
    "filename_exact",
    "id",
    "identifiers_exact",
    "import_targets",
    "knowledge_type",
    "language",
    "line_end",
    "line_start",
    "path",
    "path_segments",
    "path_text",
    "projection_fingerprint",
    "projection_version",
    "record_kind",
    "relation_targets",
    "relation_tokens",
    "relation_types",
    "repository",
    "route_mechanisms",
    "route_methods",
    "route_paths",
    "source_checksum",
    "source_ref",
    "source_type",
    "structural_tokens",
    "symbol_tokens",
    "symbol_types",
    "symbols",
    "technical_role",
)
_IDENTIFIER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_$])[A-Za-z_$][A-Za-z0-9_$]*(?![A-Za-z0-9_$])"
)
_KNOWN_EVIDENCE_FIELDS = frozenset(
    {
        "symbol",
        "structure",
        "section",
        "behavior",
        "relations",
        "manifest",
        "documentation",
        "lexical",
        "semantic",
    }
)


def projection_contract_fingerprint(
    projection_version: str = PROJECTION_VERSION,
) -> str:
    """Identify the complete stored projection contract, not only its major version.

    Field-layout changes are included automatically. Semantic changes that preserve
    the layout must bump ``_PROJECTION_CONTRACT_REVISION``.
    """

    payload = {
        "contract_revision": _PROJECTION_CONTRACT_REVISION,
        "fields": _PROJECTED_KNOWLEDGE_FIELDS,
        "projection_version": projection_version,
    }
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def corpus_checksum(documents: Iterable[KnowledgeDocument]) -> str:
    """Hash canonical document identities and checksums for backend snapshots."""

    digest = hashlib.sha256()
    for document in sorted(documents, key=lambda item: item.id):
        digest.update(document.id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(document.checksum.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def extract_identifiers(document: KnowledgeDocument) -> tuple[str, ...]:
    """Extract whole programming identifiers without domain-specific vocabulary."""

    content_identifiers = extract_content_identifiers(document)
    return _aggregate_identifiers(document, content_identifiers)


def _aggregate_identifiers(
    document: KnowledgeDocument,
    content_identifiers: tuple[str, ...],
) -> tuple[str, ...]:
    candidates: list[str] = [*document.symbols, document.filename]
    candidates.extend(Path(document.path).parts)
    structural_identifiers = _identifiers_from_values(candidates)
    return tuple(dict.fromkeys((*structural_identifiers, *content_identifiers)))


def _identifiers_from_values(values: Iterable[str]) -> tuple[str, ...]:
    result: dict[str, str] = {}
    for candidate in values:
        for match in _IDENTIFIER_PATTERN.finditer(candidate):
            identifier = match.group(0)
            result.setdefault(identifier.casefold(), identifier.casefold())
    return tuple(result.values())


def extract_content_identifiers(document: KnowledgeDocument) -> tuple[str, ...]:
    """Extract identifiers whose provenance is the chunk content itself."""

    if supports_lexical_analysis(document.language):
        return _casefold_identifiers(document.evidence.lexical.exact_identifiers)
    return _identifiers_from_values((document.content,))


def _casefold_identifiers(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.casefold() for value in values if value))


def _symbol_tokens(document: KnowledgeDocument) -> tuple[str, ...]:
    return _casefold_identifiers(
        f"{kind}:{name}"
        for name, kind in zip(document.symbols, document.symbol_types, strict=False)
        if name and kind
    )


def _path_segments(document: KnowledgeDocument) -> tuple[str, ...]:
    return _casefold_identifiers(PurePosixPath(document.path).parts)


def _route_channel_values(
    document: KnowledgeDocument,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    routes = document.evidence.behavior.routes
    return (
        _casefold_identifiers(
            route.http_method for route in routes if route.http_method is not None
        ),
        _casefold_identifiers(route.path for route in routes),
        _casefold_identifiers(
            route.mechanism for route in routes if route.mechanism is not None
        ),
    )


def _dependency_targets(document: KnowledgeDocument) -> tuple[str, ...]:
    manifest = document.evidence.manifest
    if manifest is None:
        return ()
    return _casefold_identifiers(
        dependency.identity
        for dependency in (*manifest.dependencies, *manifest.managed_dependencies)
    )


@dataclass(frozen=True)
class _ProjectedRelation:
    """One rebuildable relation view compiled from typed canonical evidence."""

    relation_type: str
    target: str

    @property
    def token(self) -> str:
        return f"{self.relation_type}:{self.target}"


def _canonical_relations(document: KnowledgeDocument) -> tuple[_ProjectedRelation, ...]:
    """Normalize only explicit canonical relation targets, preserving each pair."""

    candidates: list[tuple[str, str]] = []
    candidates.extend(
        ("calls", call.target) for call in document.evidence.relations.calls
    )
    candidates.extend(
        ("references", reference.target)
        for reference in document.evidence.relations.references
    )
    candidates.extend(
        ("imports", imported.source)
        for imported in document.evidence.structure.imports
    )
    candidates.extend(
        ("depends_on", dependency)
        for dependency in document.evidence.relations.dependencies
    )
    candidates.extend(
        ("tests", test_target.target_source)
        for test_target in document.evidence.relations.test_targets
    )
    candidates.extend(
        (relation.kind, relation.target)
        for relation in document.evidence.relations.semantic
    )

    manifest = document.evidence.manifest
    if manifest is not None:
        candidates.extend(
            ("depends_on", dependency.identity)
            for dependency in (*manifest.dependencies, *manifest.managed_dependencies)
        )

    relations: dict[tuple[str, str], _ProjectedRelation] = {}
    for raw_type, raw_target in candidates:
        relation_type = raw_type.strip().casefold()
        target = raw_target.strip().casefold()
        if not relation_type or not target:
            continue
        relation = _ProjectedRelation(relation_type=relation_type, target=target)
        relations.setdefault((relation.relation_type, relation.target), relation)
    return tuple(relations.values())


def _documentation_text(document: KnowledgeDocument) -> tuple[str, ...]:
    values: list[str] = []
    for documentation in document.evidence.documentation:
        if documentation.summary:
            values.append(documentation.summary)
        for parameter in documentation.parameters:
            values.append(parameter.name)
            if parameter.description:
                values.append(parameter.description)
        if documentation.returns:
            values.append(documentation.returns)
        for thrown in documentation.throws:
            values.append(thrown.name)
            if thrown.description:
                values.append(thrown.description)
        if documentation.deprecated:
            values.append("deprecated")
    return tuple(dict.fromkeys(value for value in values if value))


def _structural_tokens(document: KnowledgeDocument) -> tuple[str, ...]:
    values = [
        f"source_type:{document.source_type}",
        f"knowledge_type:{document.knowledge_type}",
    ]
    if document.language:
        values.append(f"language:{document.language}")
    values.extend(f"symbol_kind:{kind}" for kind in document.symbol_types if kind)

    section = document.evidence.section
    if section is not None:
        values.extend(
            (
                f"section_strategy:{section.strategy}",
                f"section_kind:{section.kind}",
            )
        )
    values.extend(
        f"config_key:{key}" for key in document.evidence.behavior.config_keys if key
    )

    manifest = document.evidence.manifest
    if manifest is not None:
        values.append(f"manifest_ecosystem:{manifest.ecosystem}")
        for kind, value in (
            ("manifest_packaging", manifest.packaging),
            ("manifest_module_type", manifest.module_type),
            ("manifest_package_manager", manifest.package_manager),
        ):
            if value:
                values.append(f"{kind}:{value}")

    values.extend(
        f"documentation_kind:{documentation.kind}"
        for documentation in document.evidence.documentation
        if documentation.kind
    )
    values.extend(
        f"semantic_kind:{item.namespace}:{item.kind}" for item in document.evidence.semantic
    )
    return _casefold_identifiers(values)


@dataclass(frozen=True)
class ProjectedKnowledgeRecord:
    """Immutable deterministic values for one rebuildable backend record."""

    fields: Mapping[str, object]

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ProjectedKnowledgeRecord:
        """Hydrate one immutable projected record from a mapping."""

        frozen = MappingProxyType(
            {
                key: tuple(item) if isinstance(item, list) else item
                for key, item in sorted(value.items())
            }
        )
        return cls(fields=frozen)

    def to_mapping(self) -> dict[str, object]:
        """Serialize this projected record into backend-neutral primitives."""

        return {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in self.fields.items()
        }

    def to_solr(self) -> dict[str, object]:
        """Compatibility serializer for the historical Solr projection API."""

        return self.to_mapping()


class ProjectionV3Compiler:
    """Compile canonical knowledge into a deterministic, derived backend record."""

    def __init__(self, projection_version: str = PROJECTION_VERSION) -> None:
        """Initialize the compiler for one explicit Projection v3 contract."""

        if not projection_version.strip():
            raise ValueError("projection_version must not be empty")
        self.projection_version = projection_version
        self.projection_fingerprint = projection_contract_fingerprint(projection_version)

    def compile(
        self,
        document: KnowledgeDocument,
    ) -> ProjectedKnowledgeRecord:
        """Compile one canonical knowledge document into Projection v3."""

        content_identifiers = extract_content_identifiers(document)
        identifiers = _aggregate_identifiers(document, content_identifiers)
        route_methods, route_paths, route_mechanisms = _route_channel_values(document)
        relations = _canonical_relations(document)
        canonical_payload = json.dumps(
            document.to_mapping(),
            sort_keys=True,
            separators=(",", ":"),
        )
        projected = {
            "id": document.id,
            "record_kind": "knowledge",
            "source_type": document.source_type,
            "source_ref": document.source_ref,
            "chunk_ref": document.chunk_ref,
            "repository": document.repository,
            "branch": document.branch,
            "document_revision": document.revision,
            "path": document.path,
            "filename": document.filename,
            "language": document.language,
            "knowledge_type": document.knowledge_type,
            "technical_role": document.technical_role,
            "symbols": list(document.symbols),
            "symbol_types": list(document.symbol_types),
            "symbol_tokens": list(_symbol_tokens(document)),
            "content": document.content,
            "line_start": document.line_start,
            "line_end": document.line_end,
            "checksum": document.checksum,
            "source_checksum": document.provenance.source_checksum,
            "identifiers_exact": list(identifiers),
            "content_identifiers_exact": list(content_identifiers),
            "filename_exact": document.filename.casefold(),
            "path_text": document.path,
            "path_segments": list(_path_segments(document)),
            "route_methods": list(route_methods),
            "route_paths": list(route_paths),
            "route_mechanisms": list(route_mechanisms),
            "import_targets": list(
                _casefold_identifiers(document.evidence.structure.import_sources)
            ),
            "dependency_targets": list(_dependency_targets(document)),
            "relation_types": list(
                _casefold_identifiers(relation.relation_type for relation in relations)
            ),
            "relation_targets": list(
                _casefold_identifiers(relation.target for relation in relations)
            ),
            "relation_tokens": [relation.token for relation in relations],
            "documentation_text": list(_documentation_text(document)),
            "structural_tokens": list(_structural_tokens(document)),
            "canonical_payload": canonical_payload,
            "projection_fingerprint": self.projection_fingerprint,
            "projection_version": self.projection_version,
        }
        if tuple(sorted(projected)) != _PROJECTED_KNOWLEDGE_FIELDS:
            raise RuntimeError("Projection v3 contract fields are out of sync")
        return ProjectedKnowledgeRecord.from_mapping(projected)


class ProjectionV3CanonicalDocumentReader:
    """Hydrate current canonical documents without granting projection authority."""

    def __init__(self, projection_version: str = PROJECTION_VERSION) -> None:
        """Initialize a reader for one explicit Projection v3 contract."""

        self.projection_version = projection_version
        self.projection_fingerprint = projection_contract_fingerprint(projection_version)

    def read(self, value: Mapping[str, object]) -> KnowledgeDocument:
        """Hydrate one canonical document from a compatible projected record."""

        if value.get("record_kind") != "knowledge":
            raise ProjectionParsingError(
                "Projection control record cannot be reconstructed as knowledge",
            )
        actual_projection_version = value.get("projection_version")
        if actual_projection_version != self.projection_version:
            raise ProjectionCompatibilityError(
                "Knowledge projection version is incompatible",
                metadata={
                    "expected_projection_version": self.projection_version,
                    "actual_projection_version": actual_projection_version,
                },
            )
        actual_projection_fingerprint = value.get("projection_fingerprint")
        if actual_projection_fingerprint != self.projection_fingerprint:
            raise ProjectionCompatibilityError(
                "Knowledge projection contract is incompatible",
                metadata={
                    "expected_projection_fingerprint": self.projection_fingerprint,
                    "actual_projection_fingerprint": actual_projection_fingerprint,
                    "rebuild_required": True,
                },
            )
        payload = value.get("canonical_payload")
        if not isinstance(payload, str):
            raise ProjectionParsingError(
                "Knowledge projection record has no canonical payload",
            )
        try:
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise TypeError("canonical payload must be an object")
            provenance_data = data["provenance"]
            if not isinstance(provenance_data, Mapping):
                raise TypeError("provenance must be an object")
            provenance = KnowledgeProvenance(
                source_ref=str(provenance_data["source_ref"]),
                chunk_ref=str(provenance_data["chunk_ref"]),
                repository=str(provenance_data["repository"]),
                revision=str(provenance_data["revision"]),
                path=str(provenance_data["path"]),
                line_start=int(provenance_data["line_start"]),
                line_end=int(provenance_data["line_end"]),
                source_checksum=str(provenance_data["source_checksum"]),
            )
            metadata = data.get("metadata", {})
            if not isinstance(metadata, dict):
                raise TypeError("metadata must be an object")

            raw_evidence: object | None
            if "evidence" in data:
                raw_evidence = data["evidence"]
            else:
                raw_evidence = metadata.get("evidence")

            if raw_evidence is not None and not isinstance(raw_evidence, Mapping):
                raise TypeError("evidence must be an object")
            if isinstance(raw_evidence, Mapping):
                unknown_fields = sorted(
                    str(key) for key in raw_evidence if key not in _KNOWN_EVIDENCE_FIELDS
                )
                if unknown_fields:
                    raise ProjectionParsingError(
                        "Canonical payload contains unsupported evidence fields",
                        metadata={"unknown_evidence_fields": unknown_fields},
                    )

            evidence = (
                KnowledgeEvidence.from_mapping(raw_evidence)
                if isinstance(raw_evidence, Mapping)
                else KnowledgeEvidence()
            )

            return KnowledgeDocument(
                id=str(data["id"]),
                source_type=str(data["source_type"]),
                source_ref=str(data["source_ref"]),
                chunk_ref=str(data["chunk_ref"]),
                repository=str(data["repository"]),
                branch=None if data.get("branch") is None else str(data["branch"]),
                revision=str(data["revision"]),
                path=str(data["path"]),
                filename=str(data["filename"]),
                language=None if data.get("language") is None else str(data["language"]),
                knowledge_type=str(data["knowledge_type"]),
                technical_role=(
                    None if data.get("technical_role") is None else str(data["technical_role"])
                ),
                symbols=tuple(str(item) for item in data.get("symbols", [])),
                symbol_types=tuple(str(item) for item in data.get("symbol_types", [])),
                content=str(data["content"]),
                line_start=int(data["line_start"]),
                line_end=int(data["line_end"]),
                checksum=str(data["checksum"]),
                provenance=provenance,
                metadata=cast(dict[str, object], metadata),
                indexed_at=str(data["indexed_at"]),
                evidence=evidence,
                corpus_purpose=str(data.get("corpus_purpose", "unknown")),
                source_origin=str(data.get("source_origin", "unknown")),
            )
        except ProjectionParsingError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProjectionParsingError(
                "Canonical payload is invalid",
            ) from exc
