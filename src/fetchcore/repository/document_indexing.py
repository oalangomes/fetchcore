"""Composite repository indexing for text source plus binary/document knowledge.

The existing :class:`RepositoryIndexer` remains the canonical text-source path.
This module adds a separate bounded document acquisition/extraction path and
converges both paths only at ``KnowledgeDocument``. PDF/XLSX bytes are never
fed through ``scan_repository_text``.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import cast
from urllib.parse import quote

from fetchcore.repository.corpus_policy import (
    resolve_corpus_purpose,
    resolve_source_origin,
)
from fetchcore.repository.document_extractors import (
    DocumentExtractionError,
    DocumentSourceExtractor,
    DocumentUnit,
    default_document_extractors,
)
from fetchcore.repository.document_policy import RepositoryDocumentIndexPolicy
from fetchcore.repository.document_scan import (
    SafeDocumentScanResult,
    SafeDocumentSource,
    scan_repository_documents,
)
from fetchcore.repository.evidence import KnowledgeEvidence, SectionEvidence
from fetchcore.repository.html import HtmlRepositoryIndexer
from fetchcore.repository.indexing import (
    KnowledgeDocument,
    KnowledgeProvenance,
    RepositoryIndexChanges,
    RepositoryIndexConfig,
    RepositoryIndexResult,
    RepositoryIndexTelemetry,
    SymbolExtractor,
    _classify,
    _diff_documents,
    _document_order,
    _utc_now,
    _validate_document_uniqueness,
)


_DOCUMENT_SOURCE_TYPE = "repository_document"
_DOCUMENT_TRAVERSAL_BLOCKERS = frozenset(
    {"max_files", "max_total_bytes", "changed_or_oversized"}
)


@dataclass(frozen=True)
class RepositoryDocumentSourceTelemetry:
    """Diagnostics for the document-source path, separate from text-source coverage."""

    files_discovered: int
    files_evaluated: int
    files_eligible: int
    files_read: int
    files_extracted: int
    files_with_documents: int
    documents_produced: int
    bytes_read: int
    skipped: dict[str, int]
    extraction_failures: dict[str, int]
    documents_by_format: dict[str, int]
    truncated: bool
    truncation_reasons: tuple[str, ...]
    complete: bool

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class DocumentRepositoryIndexTelemetry(RepositoryIndexTelemetry):
    """Repository telemetry extended with an explicit document-source channel."""

    document_sources: RepositoryDocumentSourceTelemetry

    def to_mapping(self) -> dict[str, object]:
        payload = super().to_mapping()
        payload["document_sources"] = self.document_sources.to_mapping()
        return payload


class DocumentRepositoryIndexer(HtmlRepositoryIndexer):
    """Canonical repository indexer that composes text and document source paths."""

    def __init__(
        self,
        config: RepositoryIndexConfig | None = None,
        *,
        symbol_extractors: Mapping[str, SymbolExtractor] | None = None,
        document_extractors: Mapping[str, DocumentSourceExtractor] | None = None,
        document_policy: RepositoryDocumentIndexPolicy | None = None,
        clock: Callable[[], str] = _utc_now,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(
            config,
            symbol_extractors=symbol_extractors,
            clock=clock,
            timer=timer,
        )
        self.document_policy = document_policy or RepositoryDocumentIndexPolicy()
        available = (
            dict(document_extractors or default_document_extractors())
            if self.document_policy.enabled
            else {}
        )
        self.document_extractors: dict[str, DocumentSourceExtractor] = {
            suffix: extractor
            for suffix, extractor in available.items()
            if extractor.format_id in self.document_policy.formats
        }

    @property
    def document_indexing_contract(self) -> dict[str, object]:
        """Fingerprintable document-source policy and extractor semantics."""

        policy = self.document_policy.to_mapping()
        policy.pop("source", None)
        return {
            "policy": policy,
            "extractors": {
                suffix: {
                    "type": f"{type(extractor).__module__}.{type(extractor).__qualname__}",
                    "version": str(
                        getattr(extractor, "indexing_contract_version", "1")
                    ),
                }
                for suffix, extractor in sorted(self.document_extractors.items())
            },
        }

    def requires_full_scan_for_paths(
        self,
        changed_paths: Sequence[str],
        removed_paths: Sequence[str] = (),
    ) -> bool:
        """Document deltas currently fall back to the proven full-scan lifecycle."""

        if not self.document_policy.enabled:
            return False
        return any(
            self._document_path_eligible(path)
            for path in (*changed_paths, *removed_paths)
        )

    def index(
        self,
        workspace: Path,
        *,
        previous_documents: Iterable[KnowledgeDocument] = (),
    ) -> RepositoryIndexResult:
        started = self.timer()
        previous = tuple(previous_documents)
        previous_text = tuple(
            item for item in previous if item.source_type != _DOCUMENT_SOURCE_TYPE
        )
        previous_document_sources = tuple(
            item for item in previous if item.source_type == _DOCUMENT_SOURCE_TYPE
        )

        base = super().index(workspace, previous_documents=previous_text)
        scan = scan_repository_documents(
            workspace,
            include_patterns=(
                self.document_policy.include_patterns
                if self.document_policy.enabled
                else (".agentsorch-document-sources-disabled",)
            ),
            exclude_patterns=self.config.exclude_patterns,
            max_files=self.document_policy.max_files,
            max_file_bytes=self.document_policy.max_file_bytes,
            max_total_bytes=self.document_policy.max_total_bytes,
        )
        indexed_at = base.documents[0].indexed_at if base.documents else self.clock()
        document_values, failures, extracted_files = self._index_document_sources(
            scan,
            repository=base.repository,
            branch=base.branch,
            revision=base.revision,
            indexed_at=indexed_at,
        )

        revision = _combined_tree_revision(base.revision, scan)
        text_documents = _with_revision(base.documents, revision)
        document_values = _with_revision(document_values, revision)
        documents = tuple(sorted((*text_documents, *document_values), key=_document_order))
        _validate_document_uniqueness(documents, repository=base.repository, corpus="current")
        _validate_document_uniqueness(previous, repository=base.repository, corpus="previous")

        document_blockers = _document_removal_blockers(scan, failures)
        document_changes = _diff_documents(
            document_values,
            previous_document_sources,
            removal_detection_complete=not document_blockers,
        )
        overall_complete = base.changes.removal_detection_complete and not document_blockers
        removed = (
            (*base.changes.removed, *document_changes.removed)
            if overall_complete
            else ()
        )
        changes = RepositoryIndexChanges(
            added=(*base.changes.added, *document_changes.added),
            updated=(*base.changes.updated, *document_changes.updated),
            removed=removed,
            unchanged=(*base.changes.unchanged, *document_changes.unchanged),
            removal_detection_complete=overall_complete,
        )
        document_telemetry = _document_telemetry(
            scan,
            document_values,
            failures=failures,
            extracted_files=extracted_files,
            complete=not document_blockers,
        )
        blockers = tuple(
            sorted(
                {
                    *base.telemetry.removal_detection_blockers,
                    *(f"document:{value}" for value in document_blockers),
                }
            )
        )
        telemetry = _combined_telemetry(
            base.telemetry,
            repository=base.repository,
            revision=revision,
            documents=documents,
            changes=changes,
            document_telemetry=document_telemetry,
            blockers=blockers,
            duration_ms=max(0, round((self.timer() - started) * 1_000)),
        )
        return RepositoryIndexResult(
            repository=base.repository,
            branch=base.branch,
            revision=revision,
            documents=documents,
            changes=changes,
            telemetry=telemetry,
        )

    def _index_document_sources(
        self,
        scan: SafeDocumentScanResult,
        *,
        repository: str,
        branch: str | None,
        revision: str,
        indexed_at: str,
    ) -> tuple[tuple[KnowledgeDocument, ...], Counter[str], int]:
        documents: list[KnowledgeDocument] = []
        failures: Counter[str] = Counter()
        extracted_files = 0
        for source in scan.sources:
            extractor = self._document_extractor_for_path(source.relative)
            if extractor is None:
                failures["extractor_missing"] += 1
                continue
            try:
                units = extractor.extract(source.payload, path=source.relative)
            except DocumentExtractionError as exc:
                failures[exc.category] += 1
                continue
            extracted_files += 1
            for unit in units:
                documents.append(
                    self._document_from_unit(
                        source,
                        unit,
                        extractor=extractor,
                        repository=repository,
                        branch=branch,
                        revision=revision,
                        indexed_at=indexed_at,
                    )
                )
        return tuple(documents), failures, extracted_files

    def _document_from_unit(
        self,
        source: SafeDocumentSource,
        unit: DocumentUnit,
        *,
        extractor: DocumentSourceExtractor,
        repository: str,
        branch: str | None,
        revision: str,
        indexed_at: str,
    ) -> KnowledgeDocument:
        source_ref = _document_source_ref(repository, source.relative)
        chunk_ref = f"{source_ref}#{quote(unit.discriminator, safe=':=,-!')}"
        line_end = max(1, len(unit.content.splitlines()))
        knowledge_type = _classify(source.relative, self.config.classification_rules)
        if knowledge_type == "code":
            knowledge_type = "documentation"
        checksum = "sha256:" + hashlib.sha256(unit.content.encode("utf-8")).hexdigest()
        provenance = KnowledgeProvenance(
            source_ref=source_ref,
            chunk_ref=chunk_ref,
            repository=repository,
            revision=revision,
            path=source.relative,
            line_start=1,
            line_end=line_end,
            source_checksum=source.checksum,
        )
        evidence = KnowledgeEvidence(
            section=SectionEvidence(
                strategy=unit.section_kind,
                kind=unit.section_kind,
                name=unit.section_name,
            )
        )
        corpus_purpose = resolve_corpus_purpose(
            source.relative,
            default=self.config.default_corpus_purpose,
            rules=self.config.corpus_purpose_rules,
        )
        source_origin = resolve_source_origin(
            source.relative,
            default=self.config.default_source_origin,
            rules=self.config.source_origin_rules,
        )
        return KnowledgeDocument(
            id=_stable_document_id(chunk_ref),
            source_type=_DOCUMENT_SOURCE_TYPE,
            source_ref=source_ref,
            chunk_ref=chunk_ref,
            repository=repository,
            branch=branch,
            revision=revision,
            path=source.relative,
            filename=Path(source.relative).name,
            language=None,
            knowledge_type=knowledge_type,
            technical_role=None,
            symbols=(),
            symbol_types=(),
            content=unit.content,
            line_start=1,
            line_end=line_end,
            checksum=checksum,
            provenance=provenance,
            metadata={
                "chunk_discriminator": unit.discriminator,
                "chunk_strategy": unit.section_kind,
                "source_checksum": source.checksum,
                "source_size_bytes": source.size_bytes,
                "document_format": extractor.format_id,
                "document_extractor": type(extractor).__name__,
                "document_locator": dict(unit.locator),
                "line_coordinate_system": "normalized_document_text",
            },
            indexed_at=indexed_at,
            evidence=evidence,
            corpus_purpose=corpus_purpose,
            source_origin=source_origin,
        )

    def _document_extractor_for_path(self, path: str) -> DocumentSourceExtractor | None:
        if not self._document_path_eligible(path):
            return None
        return self.document_extractors.get(Path(path).suffix.casefold())

    def _document_path_eligible(self, path: str) -> bool:
        if not self.document_policy.enabled:
            return False
        parts = Path(path).parts
        if any(
            exclude in parts
            or fnmatch.fnmatch(path, exclude)
            or fnmatch.fnmatch(path, f"**/{exclude}/**")
            for exclude in self.config.exclude_patterns
        ):
            return False
        name = Path(path).name
        return any(
            fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(path, pattern)
            for pattern in self.document_policy.include_patterns
        )


def _document_telemetry(
    scan: SafeDocumentScanResult,
    documents: tuple[KnowledgeDocument, ...],
    *,
    failures: Counter[str],
    extracted_files: int,
    complete: bool,
) -> RepositoryDocumentSourceTelemetry:
    by_format = Counter(
        str(item.metadata.get("document_format", "unknown")) for item in documents
    )
    return RepositoryDocumentSourceTelemetry(
        files_discovered=scan.discovered_files,
        files_evaluated=scan.evaluated_files,
        files_eligible=scan.eligible_files,
        files_read=len(scan.sources),
        files_extracted=extracted_files,
        files_with_documents=len({item.path for item in documents}),
        documents_produced=len(documents),
        bytes_read=scan.bytes_read,
        skipped=dict(sorted(scan.skipped.items())),
        extraction_failures=dict(sorted(failures.items())),
        documents_by_format=dict(sorted(by_format.items())),
        truncated=scan.truncated,
        truncation_reasons=scan.truncation_reasons,
        complete=complete,
    )


def _combined_telemetry(
    base: RepositoryIndexTelemetry,
    *,
    repository: str,
    revision: str,
    documents: tuple[KnowledgeDocument, ...],
    changes: RepositoryIndexChanges,
    document_telemetry: RepositoryDocumentSourceTelemetry,
    blockers: tuple[str, ...],
    duration_ms: int,
) -> DocumentRepositoryIndexTelemetry:
    by_type = Counter(item.knowledge_type for item in documents)
    by_role = Counter(item.technical_role or "unknown" for item in documents)
    per_source = Counter(item.source_ref for item in documents)
    values = {
        field.name: getattr(base, field.name)
        for field in fields(RepositoryIndexTelemetry)
    }
    values.update(
        {
            "repository": repository,
            "revision": revision,
            "documents_produced": len(documents),
            "documents_by_knowledge_type": dict(sorted(by_type.items())),
            "documents_by_technical_role": dict(sorted(by_role.items())),
            "documents_per_source": dict(sorted(per_source.items())),
            "added": len(changes.added),
            "updated": len(changes.updated),
            "removed": len(changes.removed),
            "unchanged": len(changes.unchanged),
            "removal_detection_complete": changes.removal_detection_complete,
            "removal_detection_blockers": blockers,
            "duration_ms": duration_ms,
        }
    )
    return DocumentRepositoryIndexTelemetry(
        **cast(dict[str, object], values),  # type: ignore[arg-type]
        document_sources=document_telemetry,
    )


def _document_removal_blockers(
    scan: SafeDocumentScanResult,
    failures: Counter[str],
) -> tuple[str, ...]:
    blockers = set(failures)
    blockers.update(
        reason
        for reason in scan.truncation_reasons
        if reason in _DOCUMENT_TRAVERSAL_BLOCKERS
    )
    for reason in (
        "invalid_path",
        "io_error",
        "changed_or_oversized",
        "unsafe_path_or_symlink",
    ):
        if scan.skipped.get(reason, 0) > 0:
            blockers.add(reason)
    return tuple(sorted(blockers))


def _combined_tree_revision(revision: str, scan: SafeDocumentScanResult) -> str:
    if not revision.startswith("tree:"):
        return revision
    payload = {
        "text_revision": revision,
        "document_sources": [(item.relative, item.checksum) for item in scan.sources],
    }
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "tree:" + hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _with_revision(
    documents: tuple[KnowledgeDocument, ...],
    revision: str,
) -> tuple[KnowledgeDocument, ...]:
    return tuple(
        replace(
            item,
            revision=revision,
            provenance=replace(item.provenance, revision=revision),
        )
        for item in documents
    )


def _document_source_ref(repository: str, relative: str) -> str:
    encoded_repository = quote(repository, safe="/.:@")
    return f"repository://{encoded_repository}/{quote(relative, safe='/')}"


def _stable_document_id(chunk_ref: str) -> str:
    return "kdoc_" + hashlib.sha256(chunk_ref.encode("utf-8")).hexdigest()
