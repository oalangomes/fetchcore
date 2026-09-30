"""Backend-neutral, deterministic repository indexing contracts."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import quote, urlparse

from fetchcore.repository.corpus_policy import (
    CorpusPurposeRule,
    SourceOriginRule,
    resolve_corpus_purpose,
    resolve_source_origin,
    validate_corpus_policy,
)
from fetchcore.repository.evidence import (
    KnowledgeEvidence,
    _StructureObservation,
    build_knowledge_evidence,
    extract_source_structure,
)
from fetchcore.repository.knowledge_chunking import chunks_for_knowledge
from fetchcore.repository.lexical import (
    LexicalChunkProjection,
    analyze_lexical_source,
)
from fetchcore.repository.safe_scan import (
    SafeScanResult,
    SafeSource,
    scan_repository_text,
    source_kind,
)
from fetchcore.repository.source_coverage import (
    RepositorySourceCoverage,
    SourceLanguageCoverage,
)


_INTENTIONAL_INELIGIBILITY_REASONS = frozenset(
    {"excluded", "not_included", "max_file_bytes"}
)
_INCOMPLETE_OBSERVATION_REASONS = frozenset(
    {"invalid_path", "io_error", "changed_or_oversized", "unsafe_path_or_symlink"}
)
_INCOMPLETE_TRAVERSAL_REASONS = frozenset({"max_files", "max_total_bytes"})


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class KnowledgeProvenance:
    """Canonical trace from an indexed chunk back to repository source."""

    source_ref: str
    chunk_ref: str
    repository: str
    revision: str
    path: str
    line_start: int
    line_end: int
    source_checksum: str

    def to_mapping(self) -> dict[str, object]:
        """Serialize canonical source provenance."""

        return asdict(self)


@dataclass(frozen=True)
class KnowledgeDocument:
    """A canonical searchable unit with backend-neutral observable evidence."""

    id: str
    source_type: str
    source_ref: str
    chunk_ref: str
    repository: str
    branch: str | None
    revision: str
    path: str
    filename: str
    language: str | None
    knowledge_type: str
    technical_role: str | None
    symbols: tuple[str, ...]
    symbol_types: tuple[str, ...]
    content: str
    line_start: int
    line_end: int
    checksum: str
    provenance: KnowledgeProvenance
    metadata: dict[str, object]
    indexed_at: str
    evidence: KnowledgeEvidence = field(default_factory=KnowledgeEvidence)
    corpus_purpose: str = "unknown"
    source_origin: str = "unknown"

    def __post_init__(self) -> None:
        """Hydrate legacy evidence and normalize metadata to the current contract."""

        validate_corpus_policy(
            default_purpose=self.corpus_purpose,
            default_origin=self.source_origin,
            purpose_rules=(),
            origin_rules=(),
        )
        raw_evidence = self.metadata.get("evidence")
        if self.evidence == KnowledgeEvidence() and isinstance(raw_evidence, Mapping):
            object.__setattr__(
                self,
                "evidence",
                KnowledgeEvidence.from_mapping(raw_evidence),
            )
        if "evidence" in self.metadata:
            metadata = dict(self.metadata)
            metadata.pop("evidence", None)
            object.__setattr__(self, "metadata", metadata)

    def to_mapping(self) -> dict[str, object]:
        """Serialize this canonical knowledge document."""

        return {
            "id": self.id,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "chunk_ref": self.chunk_ref,
            "repository": self.repository,
            "branch": self.branch,
            "revision": self.revision,
            "path": self.path,
            "filename": self.filename,
            "language": self.language,
            "knowledge_type": self.knowledge_type,
            "corpus_purpose": self.corpus_purpose,
            "source_origin": self.source_origin,
            "technical_role": self.technical_role,
            "symbols": list(self.symbols),
            "symbol_types": list(self.symbol_types),
            "content": self.content,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "checksum": self.checksum,
            "provenance": self.provenance.to_mapping(),
            "evidence": self.evidence.to_mapping(),
            "metadata": dict(self.metadata),
            "indexed_at": self.indexed_at,
        }


@dataclass(frozen=True)
class SymbolSpan:
    """One deterministic source-symbol span used for chunking."""

    name: str
    symbol_type: str
    line_start: int
    line_end: int


class SymbolExtractor(Protocol):
    """Language-specific symbol extraction extension point."""

    def extract(self, content: str) -> tuple[SymbolSpan, ...]:
        """Extract symbol spans from source content."""

        ...


class PythonSymbolExtractor:
    """Extract top-level Python functions and classes with the standard-library AST."""

    def extract(self, content: str) -> tuple[SymbolSpan, ...]:
        """Extract top-level Python function and class spans."""

        try:
            tree = ast.parse(content)
        except (SyntaxError, ValueError):
            return ()
        spans: list[SymbolSpan] = []
        for node in tree.body:
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)):
                continue
            decorators = getattr(node, "decorator_list", [])
            starts = [node.lineno, *(item.lineno for item in decorators)]
            symbol_type = "class" if isinstance(node, ast.ClassDef) else "function"
            spans.append(
                SymbolSpan(
                    name=node.name,
                    symbol_type=symbol_type,
                    line_start=min(starts),
                    line_end=int(node.end_lineno or node.lineno),
                )
            )
        return tuple(sorted(spans, key=lambda item: (item.line_start, item.line_end, item.name)))


@dataclass(frozen=True)
class ClassificationRule:
    """Map one repository path pattern to a knowledge type."""

    pattern: str
    knowledge_type: str


@dataclass(frozen=True)
class TechnicalRoleRule:
    """Map one deterministic source token to a technical role."""

    token: str
    role: str


DEFAULT_INCLUDE_PATTERNS = (
    "*.py",
    "*.pyi",
    "*.js",
    "*.jsx",
    "*.mjs",
    "*.cjs",
    "*.ts",
    "*.tsx",
    "*.mts",
    "*.cts",
    "*.java",
    "*.kt",
    "*.kts",
    "*.go",
    "*.rs",
    "*.cs",
    "*.rb",
    "*.php",
    "*.swift",
    "*.scala",
    "*.groovy",
    "*.dart",
    "*.sh",
    "*.html",
    "*.htm",
    "*.md",
    "*.rst",
    "*.txt",
    "*.json",
    "*.yaml",
    "*.yml",
    "*.toml",
    "*.xml",
    "*.properties",
    "*.ini",
    "*.cfg",
    ".*ignore",
    ".editorconfig",
    "Dockerfile",
    "Makefile",
)

DEFAULT_EXCLUDE_PATTERNS = (
    ".git",
    ".fetchcore",
    ".agentsorch",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "build",
    "dist",
    "target",
    "coverage",
    "generated",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
)

DEFAULT_CLASSIFICATION_RULES = (
    ClassificationRule("tests/**", "tests"),
    ClassificationRule("test/**", "tests"),
    ClassificationRule("**/tests/**", "tests"),
    ClassificationRule("**/test/**", "tests"),
    ClassificationRule("__tests__/**", "tests"),
    ClassificationRule("**/__tests__/**", "tests"),
    ClassificationRule("test_*.*", "tests"),
    ClassificationRule("*_test.*", "tests"),
    ClassificationRule("*.test.*", "tests"),
    ClassificationRule("*.spec.*", "tests"),
    ClassificationRule("*Test.java", "tests"),
    ClassificationRule("*Tests.cs", "tests"),
    ClassificationRule("docs/**", "documentation"),
    ClassificationRule("doc/**", "documentation"),
    ClassificationRule("README*", "documentation"),
    ClassificationRule("CHANGELOG*", "documentation"),
    ClassificationRule("requirements*.txt", "configuration"),
    ClassificationRule("constraints*.txt", "configuration"),
    ClassificationRule(".*ignore", "configuration"),
    ClassificationRule(".editorconfig", "configuration"),
    ClassificationRule("*.md", "documentation"),
    ClassificationRule("*.rst", "documentation"),
    ClassificationRule("*.txt", "documentation"),
    ClassificationRule("Dockerfile", "configuration"),
    ClassificationRule("Makefile", "configuration"),
    ClassificationRule("*.json", "configuration"),
    ClassificationRule("*.yaml", "configuration"),
    ClassificationRule("*.yml", "configuration"),
    ClassificationRule("*.toml", "configuration"),
    ClassificationRule("*.xml", "configuration"),
    ClassificationRule("*.properties", "configuration"),
    ClassificationRule("*.ini", "configuration"),
    ClassificationRule("*.cfg", "configuration"),
)

DEFAULT_TECHNICAL_ROLE_RULES = (
    TechnicalRoleRule("Controller", "controller"),
    TechnicalRoleRule("Service", "service"),
    TechnicalRoleRule("Facade", "facade"),
    TechnicalRoleRule("Repository", "repository"),
    TechnicalRoleRule("Dao", "dao"),
    TechnicalRoleRule("Client", "client"),
    TechnicalRoleRule("Gateway", "gateway"),
    TechnicalRoleRule("Adapter", "adapter"),
    TechnicalRoleRule("Mapper", "mapper"),
    TechnicalRoleRule("Converter", "converter"),
    TechnicalRoleRule("Populator", "populator"),
    TechnicalRoleRule("Configuration", "configuration"),
    TechnicalRoleRule("Config", "configuration"),
    TechnicalRoleRule("Model", "model"),
    TechnicalRoleRule("Entity", "entity"),
    TechnicalRoleRule("Dto", "dto"),
    TechnicalRoleRule("Event", "event"),
    TechnicalRoleRule("Listener", "listener"),
    TechnicalRoleRule("Handler", "handler"),
    TechnicalRoleRule("Command", "command"),
    TechnicalRoleRule("Job", "job"),
    TechnicalRoleRule("Cron", "cron"),
    TechnicalRoleRule("Process", "process"),
    TechnicalRoleRule("Workflow", "workflow"),
    TechnicalRoleRule("Interceptor", "interceptor"),
    TechnicalRoleRule("Validator", "validator"),
    TechnicalRoleRule("Strategy", "strategy"),
    TechnicalRoleRule("Factory", "factory"),
    TechnicalRoleRule("Provider", "provider"),
    TechnicalRoleRule("Fixture", "fixture"),
    TechnicalRoleRule("Migration", "migration"),
    TechnicalRoleRule("Script", "script"),
)

_TECHNICAL_ROLE_PATH_SEGMENTS = {
    "controllers": "controller",
    "services": "service",
    "facades": "facade",
    "repositories": "repository",
    "daos": "dao",
    "clients": "client",
    "gateways": "gateway",
    "adapters": "adapter",
    "mappers": "mapper",
    "converters": "converter",
    "populators": "populator",
    "config": "configuration",
    "configuration": "configuration",
    "models": "model",
    "entities": "entity",
    "dtos": "dto",
    "events": "event",
    "listeners": "listener",
    "handlers": "handler",
    "commands": "command",
    "jobs": "job",
    "cron": "cron",
    "processes": "process",
    "workflows": "workflow",
    "interceptors": "interceptor",
    "validators": "validator",
    "strategies": "strategy",
    "factories": "factory",
    "providers": "provider",
    "fixtures": "fixture",
    "migrations": "migration",
    "scripts": "script",
}


@dataclass(frozen=True)
class RepositoryIndexConfig:
    """Declarative repository selection, classification, and chunk limits."""

    include_patterns: tuple[str, ...] = DEFAULT_INCLUDE_PATTERNS
    exclude_patterns: tuple[str, ...] = DEFAULT_EXCLUDE_PATTERNS
    classification_rules: tuple[ClassificationRule, ...] = DEFAULT_CLASSIFICATION_RULES
    technical_role_rules: tuple[TechnicalRoleRule, ...] = DEFAULT_TECHNICAL_ROLE_RULES
    default_corpus_purpose: str = "unknown"
    default_source_origin: str = "unknown"
    corpus_purpose_rules: tuple[CorpusPurposeRule, ...] = ()
    source_origin_rules: tuple[SourceOriginRule, ...] = ()
    max_files: int = 1_000
    max_file_bytes: int = 1_000_000
    max_total_bytes: int = 32_000_000
    max_chunk_lines: int = 160
    chunk_overlap_lines: int = 20
    repository: str | None = None
    branch: str | None = None
    revision: str | None = None

    def __post_init__(self) -> None:
        limits = (self.max_files, self.max_file_bytes, self.max_total_bytes, self.max_chunk_lines)
        if any(value < 1 for value in limits):
            raise ValueError("repository indexing limits must be positive")
        if self.chunk_overlap_lines < 0 or self.chunk_overlap_lines >= self.max_chunk_lines:
            raise ValueError("chunk_overlap_lines must be non-negative and smaller than max_chunk_lines")
        if not self.include_patterns:
            raise ValueError("include_patterns must not be empty")
        validate_corpus_policy(
            default_purpose=self.default_corpus_purpose,
            default_origin=self.default_source_origin,
            purpose_rules=self.corpus_purpose_rules,
            origin_rules=self.source_origin_rules,
        )


@dataclass(frozen=True)
class DocumentUpdate:
    """Represent one canonical document replacement during incremental indexing."""

    previous_document_id: str
    document: KnowledgeDocument

    def to_mapping(self) -> dict[str, object]:
        """Serialize one incremental document update."""

        return {
            "previous_document_id": self.previous_document_id,
            "document": self.document.to_mapping(),
        }


@dataclass(frozen=True)
class RemovedKnowledgeDocument:
    """Retain canonical identity for a document removed from the repository."""

    id: str
    source_ref: str
    chunk_ref: str
    repository: str
    revision: str
    path: str

    @classmethod
    def from_document(cls, document: KnowledgeDocument) -> RemovedKnowledgeDocument:
        """Create removal evidence from an existing canonical document."""

        return cls(
            id=document.id,
            source_ref=document.source_ref,
            chunk_ref=document.chunk_ref,
            repository=document.repository,
            revision=document.revision,
            path=document.path,
        )

    def to_mapping(self) -> dict[str, object]:
        """Serialize removed-document identity."""

        return asdict(self)


@dataclass(frozen=True)
class RepositoryIndexChanges:
    """Summarize deterministic additions, updates, removals and unchanged documents."""

    added: tuple[KnowledgeDocument, ...] = ()
    updated: tuple[DocumentUpdate, ...] = ()
    removed: tuple[RemovedKnowledgeDocument, ...] = ()
    unchanged: tuple[KnowledgeDocument, ...] = ()
    removal_detection_complete: bool = True

    def to_mapping(self) -> dict[str, object]:
        """Serialize the incremental repository-index change set."""

        return {
            "added": [item.to_mapping() for item in self.added],
            "updated": [item.to_mapping() for item in self.updated],
            "removed": [item.to_mapping() for item in self.removed],
            "unchanged": [item.to_mapping() for item in self.unchanged],
            "removal_detection_complete": self.removal_detection_complete,
        }


@dataclass(frozen=True)
class RepositoryIndexTelemetry:
    """Record bounded deterministic telemetry for one repository indexing run."""

    repository: str
    revision: str
    files_discovered: int
    files_evaluated: int
    files_eligible: int
    files_read: int
    files_indexed: int
    files_changed: int
    files_skipped: int
    files_unvisited_due_to_truncation: int
    skip_reasons: dict[str, int]
    documents_produced: int
    documents_by_knowledge_type: dict[str, int]
    documents_by_technical_role: dict[str, int]
    documents_per_source: dict[str, int]
    source_coverage: RepositorySourceCoverage
    truncated_files: int
    scan_truncated: bool
    scan_traversal_complete: bool
    truncation_reasons: tuple[str, ...]
    added: int
    updated: int
    removed: int
    unchanged: int
    removal_detection_complete: bool
    removal_detection_blockers: tuple[str, ...]
    duration_ms: int

    def to_mapping(self) -> dict[str, object]:
        """Serialize repository indexing telemetry."""

        payload = asdict(self)
        payload["source_coverage"] = self.source_coverage.to_mapping()
        payload["files_scanned"] = self.files_evaluated
        payload["chunks_total"] = self.documents_produced
        payload["chunks_changed"] = self.added + self.updated + self.removed
        payload["indexing_throughput_chunks_per_second"] = (
            None
            if self.duration_ms <= 0
            else round(self.documents_produced / (self.duration_ms / 1_000), 3)
        )
        return payload


@dataclass(frozen=True)
class RepositoryIndexResult:
    """Return canonical indexed documents, changes and telemetry for one repository."""

    repository: str
    branch: str | None
    revision: str
    documents: tuple[KnowledgeDocument, ...]
    changes: RepositoryIndexChanges
    telemetry: RepositoryIndexTelemetry

    def to_mapping(self) -> dict[str, object]:
        """Serialize the complete repository indexing result."""

        return {
            "repository": self.repository,
            "branch": self.branch,
            "revision": self.revision,
            "documents": [item.to_mapping() for item in self.documents],
            "changes": self.changes.to_mapping(),
            "telemetry": self.telemetry.to_mapping(),
        }


@dataclass(frozen=True)
class _Chunk:
    discriminator: str
    content: str
    line_start: int
    line_end: int
    symbols: tuple[SymbolSpan, ...]
    strategy: str = "source-lines"


class RepositoryIndexer:
    """Build a complete canonical corpus and an optional incremental diff."""

    def __init__(
        self,
        config: RepositoryIndexConfig | None = None,
        *,
        symbol_extractors: Mapping[str, SymbolExtractor] | None = None,
        clock: Callable[[], str] = _utc_now,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize repository indexing with deterministic extractors and clocks."""

        self.config = config or RepositoryIndexConfig()
        self.symbol_extractors: dict[str, SymbolExtractor] = {
            "python": PythonSymbolExtractor(),
            **dict(symbol_extractors or {}),
        }
        self.clock = clock
        self.timer = timer

    def resolve_repository_identity(self, workspace: Path) -> str:
        """Resolve the canonical repository before any persisted state is loaded."""

        root = workspace.resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"repository workspace is not a directory: {workspace}")
        return self.config.repository or _repository_identity(root)

    def index(
        self,
        workspace: Path,
        *,
        previous_documents: Iterable[KnowledgeDocument] = (),
    ) -> RepositoryIndexResult:
        """Index one repository workspace into canonical knowledge documents."""

        started = self.timer()
        root = workspace.resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"repository workspace is not a directory: {workspace}")
        scan = scan_repository_text(
            root,
            include_patterns=self.config.include_patterns,
            exclude_patterns=self.config.exclude_patterns,
            max_files=self.config.max_files,
            max_file_bytes=self.config.max_file_bytes,
            max_total_bytes=self.config.max_total_bytes,
        )
        repository = self.resolve_repository_identity(root)
        branch = self.config.branch if self.config.branch is not None else _git_value(
            root, ("branch", "--show-current")
        )
        revision = self.config.revision or _git_value(root, ("rev-parse", "HEAD"))
        revision = revision or _working_tree_revision(scan.sources)
        indexed_at = self.clock()

        indexed_sources = tuple(
            self._documents_for_source(
                source,
                repository=repository,
                branch=branch or None,
                revision=revision,
                indexed_at=indexed_at,
            )
            for source in scan.sources
        )
        documents = tuple(document for indexed in indexed_sources for document in indexed)
        documents = tuple(sorted(documents, key=_document_order))
        _validate_document_uniqueness(documents, repository=repository, corpus="current")
        previous = tuple(previous_documents)
        foreign = sorted({item.repository for item in previous if item.repository != repository})
        if foreign:
            raise ValueError(f"previous documents belong to another repository: {foreign}")
        _validate_document_uniqueness(previous, repository=repository, corpus="previous")
        removal_detection_blockers = _removal_detection_blockers(scan)
        removal_detection_complete = not removal_detection_blockers
        changes = _diff_documents(
            documents,
            previous,
            removal_detection_complete=removal_detection_complete,
        )
        telemetry = _telemetry(
            repository=repository,
            revision=revision,
            scan=scan,
            documents=documents,
            changes=changes,
            config=self.config,
            symbol_extractors=self.symbol_extractors,
            removal_detection_blockers=removal_detection_blockers,
            duration_ms=max(0, round((self.timer() - started) * 1_000)),
        )
        return RepositoryIndexResult(
            repository=repository,
            branch=branch or None,
            revision=revision,
            documents=documents,
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
        language = _detect_language(source.relative)
        knowledge_type = _classify(source.relative, self.config.classification_rules)
        extractor = self.symbol_extractors.get(language or "")
        structure = extract_source_structure(
            extractor,
            source.content,
            path=source.relative,
        )
        if structure is not None:
            symbols = cast(tuple[SymbolSpan, ...], structure.chunk_symbols)
        else:
            symbols = extractor.extract(source.content) if extractor is not None else ()
        if knowledge_type in {"code", "tests"}:
            chunks = _chunks(
                source.content,
                symbols=symbols,
                max_lines=self.config.max_chunk_lines,
                overlap=self.config.chunk_overlap_lines,
            )
        else:
            chunks = tuple(
                _Chunk(
                    discriminator=item.discriminator,
                    content=item.content,
                    line_start=item.line_start,
                    line_end=item.line_end,
                    symbols=(),
                    strategy=item.strategy,
                )
                for item in chunks_for_knowledge(
                    source.content,
                    language=language,
                    knowledge_type=knowledge_type,
                    max_lines=self.config.max_chunk_lines,
                    overlap=self.config.chunk_overlap_lines,
                )
            )
        lexical_source = analyze_lexical_source(language, source.content)
        source_ref = _source_ref(repository, source.relative)
        documents: list[KnowledgeDocument] = []
        for chunk in chunks:
            lexical = (
                lexical_source.project(
                    line_start=chunk.line_start,
                    line_end=chunk.line_end,
                    content=chunk.content,
                )
                if lexical_source is not None
                else None
            )
            document = self._document(
                source,
                chunk,
                repository=repository,
                branch=branch,
                revision=revision,
                source_ref=source_ref,
                language=language,
                knowledge_type=knowledge_type,
                indexed_at=indexed_at,
                extractor=extractor,
                structure=structure,
                lexical=lexical,
            )
            documents.append(document)
        return tuple(documents)

    def _document(
        self,
        source: SafeSource,
        chunk: _Chunk,
        *,
        repository: str,
        branch: str | None,
        revision: str,
        source_ref: str,
        language: str | None,
        knowledge_type: str,
        indexed_at: str,
        extractor: SymbolExtractor | None,
        structure: _StructureObservation | None,
        lexical: LexicalChunkProjection | None,
    ) -> KnowledgeDocument:
        chunk_ref = f"{source_ref}#{quote(chunk.discriminator, safe=':=,-')}"
        symbols = tuple(item.name for item in chunk.symbols)
        symbol_types = tuple(item.symbol_type for item in chunk.symbols)
        technical_role = _technical_role(
            symbols,
            source.relative,
            knowledge_type,
            self.config,
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
        checksum = _checksum(chunk.content.encode("utf-8"))
        provenance = KnowledgeProvenance(
            source_ref=source_ref,
            chunk_ref=chunk_ref,
            repository=repository,
            revision=revision,
            path=source.relative,
            line_start=chunk.line_start,
            line_end=chunk.line_end,
            source_checksum=source.checksum,
        )
        evidence = build_knowledge_evidence(
            structure,
            primary_symbol=chunk.symbols[0] if chunk.symbols else None,
            discriminator=chunk.discriminator,
            strategy=chunk.strategy,
            chunk_content=chunk.content,
            chunk_line_start=chunk.line_start,
            lexical_content=(lexical.masked_content if lexical is not None else None),
            lexical_identifiers=(lexical.identifiers if lexical is not None else ()),
            knowledge_type=knowledge_type,
        )
        metadata: dict[str, object] = {
            "chunk_discriminator": chunk.discriminator,
            "chunk_strategy": chunk.strategy,
            "source_checksum": source.checksum,
            "source_size_bytes": source.size_bytes,
            "symbol_extractor": type(extractor).__name__ if extractor is not None else None,
        }
        return KnowledgeDocument(
            id=_stable_id("kdoc", chunk_ref),
            source_type="repository",
            source_ref=source_ref,
            chunk_ref=chunk_ref,
            repository=repository,
            branch=branch,
            revision=revision,
            path=source.relative,
            filename=Path(source.relative).name,
            language=language,
            knowledge_type=knowledge_type,
            technical_role=technical_role,
            symbols=symbols,
            symbol_types=symbol_types,
            content=chunk.content,
            line_start=chunk.line_start,
            line_end=chunk.line_end,
            checksum=checksum,
            provenance=provenance,
            metadata=metadata,
            indexed_at=indexed_at,
            evidence=evidence,
            corpus_purpose=corpus_purpose,
            source_origin=source_origin,
        )


def _chunks(
    content: str,
    *,
    symbols: tuple[SymbolSpan, ...],
    max_lines: int,
    overlap: int,
) -> tuple[_Chunk, ...]:
    lines = content.splitlines(keepends=True)
    if not lines or not content.strip():
        return ()
    valid_symbols = tuple(
        sorted(
            (
                item
                for item in symbols
                if 1 <= item.line_start <= item.line_end <= len(lines)
            ),
            # Stable sorting preserves source order for simple declarators that
            # intentionally share one source span.
            key=lambda item: (item.line_start, item.line_end),
        )
    )
    chunks: list[_Chunk] = []
    cursor = 1
    non_symbol_region = 0
    symbol_occurrences: Counter[tuple[str, str]] = Counter()
    for symbol_group in _co_located_symbols(valid_symbols):
        primary = symbol_group[0]
        if primary.line_start < cursor:
            raise ValueError(
                "partially overlapping symbol spans are not safe for structural "
                f"chunking: symbol={primary.name!r}, line_start={primary.line_start}, "
                f"cursor={cursor}"
            )
        if cursor < primary.line_start:
            chunks.extend(
                _line_chunks(
                    lines,
                    cursor,
                    primary.line_start - 1,
                    max_lines=max_lines,
                    overlap=overlap,
                    discriminator_prefix=f"lines:region:{non_symbol_region}",
                )
            )
            non_symbol_region += 1
        symbol_key = (primary.symbol_type, primary.name)
        occurrence = symbol_occurrences[symbol_key]
        symbol_occurrences[symbol_key] += 1
        chunks.extend(
            _line_chunks(
                lines,
                primary.line_start,
                primary.line_end,
                max_lines=max_lines,
                overlap=overlap,
                symbols=symbol_group,
                discriminator_prefix=(
                    f"symbol:{primary.symbol_type}:{primary.name}:occurrence:{occurrence}"
                ),
            )
        )
        cursor = primary.line_end + 1
    if cursor <= len(lines):
        chunks.extend(
            _line_chunks(
                lines,
                cursor,
                len(lines),
                max_lines=max_lines,
                overlap=overlap,
                discriminator_prefix=f"lines:region:{non_symbol_region}",
            )
        )
    return tuple(sorted(chunks, key=lambda item: (item.line_start, item.line_end, item.discriminator)))


def _co_located_symbols(
    symbols: tuple[SymbolSpan, ...],
) -> tuple[tuple[SymbolSpan, ...], ...]:
    groups: list[tuple[SymbolSpan, ...]] = []
    current: list[SymbolSpan] = []
    current_span: tuple[int, int] | None = None
    for symbol in symbols:
        span = (symbol.line_start, symbol.line_end)
        if current_span is not None and span != current_span:
            groups.append(tuple(current))
            current = []
        current.append(symbol)
        current_span = span
    if current:
        groups.append(tuple(current))
    return tuple(groups)


def _line_chunks(
    lines: list[str],
    first: int,
    last: int,
    *,
    max_lines: int,
    overlap: int,
    symbols: tuple[SymbolSpan, ...] = (),
    discriminator_prefix: str | None = None,
) -> tuple[_Chunk, ...]:
    chunks: list[_Chunk] = []
    start = first
    segment = 0
    while start <= last:
        end = min(last, start + max_lines - 1)
        rendered = "".join(lines[start - 1 : end])
        if rendered.strip():
            discriminator = discriminator_prefix or "lines"
            discriminator = f"{discriminator}:segment:{segment}"
            chunks.append(
                _Chunk(
                    discriminator=discriminator,
                    content=rendered,
                    line_start=start,
                    line_end=end,
                    symbols=symbols,
                    strategy="source-symbol" if symbols else "source-lines",
                )
            )
        if end >= last:
            break
        start = end - overlap + 1
        segment += 1
    return tuple(chunks)


def _classify(relative: str, rules: tuple[ClassificationRule, ...]) -> str:
    for rule in rules:
        if _matches(relative, rule.pattern):
            return rule.knowledge_type
    return "code"


def _matches(relative: str, pattern: str) -> bool:
    return fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(Path(relative).name, pattern)


def _detect_language(relative: str) -> str | None:
    name = Path(relative).name
    if name == "Dockerfile":
        return "dockerfile"
    if name == "Makefile":
        return "makefile"
    return {
        ".py": "python",
        ".pyi": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".mts": "typescript",
        ".cts": "typescript",
        ".java": "java",
        ".kt": "kotlin",
        ".kts": "kotlin",
        ".go": "go",
        ".rs": "rust",
        ".cs": "csharp",
        ".rb": "ruby",
        ".php": "php",
        ".swift": "swift",
        ".scala": "scala",
        ".groovy": "groovy",
        ".dart": "dart",
        ".sh": "shell",
        ".html": "html",
        ".htm": "html",
        ".md": "markdown",
        ".rst": "rst",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".xml": "xml",
        ".properties": "properties",
        ".ini": "ini",
        ".cfg": "ini",
    }.get(Path(relative).suffix.lower())


def _technical_role(
    symbols: tuple[str, ...],
    relative: str,
    knowledge_type: str,
    config: RepositoryIndexConfig,
) -> str | None:
    if knowledge_type == "tests":
        return "test"
    if knowledge_type == "configuration":
        return "configuration"
    candidates = (*symbols, Path(relative).stem)
    for rule in config.technical_role_rules:
        token = rule.token.casefold()
        if any(candidate.casefold().endswith(token) for candidate in candidates):
            return rule.role
    for segment in reversed(Path(relative).parts[:-1]):
        role = _TECHNICAL_ROLE_PATH_SEGMENTS.get(segment.casefold())
        if role is not None:
            return role
    return None


def _source_ref(repository: str, relative: str) -> str:
    encoded_repository = quote(repository, safe="/.:@")
    return f"repository://{encoded_repository}/{quote(relative, safe='/')}"


def _document_order(document: KnowledgeDocument) -> tuple[str, int, int, str]:
    return (document.path, document.line_start, document.line_end, document.id)


def _validate_document_uniqueness(
    documents: tuple[KnowledgeDocument, ...],
    *,
    repository: str,
    corpus: str,
) -> None:
    ids: dict[str, KnowledgeDocument] = {}
    chunk_refs: dict[str, KnowledgeDocument] = {}
    logical_keys: dict[tuple[str, str, str], KnowledgeDocument] = {}
    for document in documents:
        discriminator = str(
            document.metadata.get("chunk_discriminator", document.chunk_ref)
        )
        prior_chunk_ref = chunk_refs.get(document.chunk_ref)
        if prior_chunk_ref is not None:
            raise ValueError(
                "duplicate chunk_ref detected during repository indexing: "
                f"repository={repository!r}, corpus={corpus!r}, path={document.path!r}, "
                f"source_ref={document.source_ref!r}, chunk_ref={document.chunk_ref!r}, "
                f"discriminator={discriminator!r}, first_path={prior_chunk_ref.path!r}, "
                "first_discriminator="
                f"{prior_chunk_ref.metadata.get('chunk_discriminator')!r}"
            )
        chunk_refs[document.chunk_ref] = document

        prior_id = ids.get(document.id)
        if prior_id is not None:
            raise ValueError(
                "duplicate KnowledgeDocument id detected during repository indexing: "
                f"repository={repository!r}, corpus={corpus!r}, path={document.path!r}, "
                f"source_ref={document.source_ref!r}, id={document.id!r}, "
                f"discriminator={discriminator!r}, first_path={prior_id.path!r}, "
                f"first_chunk_ref={prior_id.chunk_ref!r}"
            )
        ids[document.id] = document

        logical_key = _logical_chunk_key(document)
        prior_logical_key = logical_keys.get(logical_key)
        if prior_logical_key is not None:
            raise ValueError(
                "duplicate logical chunk key detected during repository indexing: "
                f"repository={repository!r}, corpus={corpus!r}, path={document.path!r}, "
                f"source_ref={document.source_ref!r}, chunk_ref={document.chunk_ref!r}, "
                f"discriminator={discriminator!r}, first_path={prior_logical_key.path!r}"
            )
        logical_keys[logical_key] = document


def _diff_documents(
    current: tuple[KnowledgeDocument, ...],
    previous: tuple[KnowledgeDocument, ...],
    *,
    removal_detection_complete: bool,
) -> RepositoryIndexChanges:
    previous_by_key = {_logical_chunk_key(item): item for item in previous}
    added: list[KnowledgeDocument] = []
    updated: list[DocumentUpdate] = []
    unchanged: list[KnowledgeDocument] = []
    matched_previous: set[str] = set()
    for document in current:
        prior = previous_by_key.get(_logical_chunk_key(document))
        if prior is None:
            added.append(document)
            continue
        matched_previous.add(prior.id)
        if _canonical_payload(prior) == _canonical_payload(document):
            unchanged.append(document)
        else:
            updated.append(DocumentUpdate(previous_document_id=prior.id, document=document))
    removed: tuple[RemovedKnowledgeDocument, ...] = ()
    if removal_detection_complete:
        removed = tuple(
            RemovedKnowledgeDocument.from_document(item)
            for item in sorted(previous, key=_document_order)
            if item.id not in matched_previous
        )
    return RepositoryIndexChanges(
        added=tuple(added),
        updated=tuple(updated),
        removed=removed,
        unchanged=tuple(unchanged),
        removal_detection_complete=removal_detection_complete,
    )


def _logical_chunk_key(document: KnowledgeDocument) -> tuple[str, str, str]:
    discriminator = str(document.metadata.get("chunk_discriminator", document.chunk_ref))
    return (document.repository, document.path, discriminator)


def _canonical_payload(document: KnowledgeDocument) -> str:
    payload = document.to_mapping()
    for observation_field in ("branch", "revision", "indexed_at"):
        payload.pop(observation_field, None)
    provenance = payload.get("provenance")
    if isinstance(provenance, dict):
        provenance.pop("revision", None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _removal_detection_blockers(scan: SafeScanResult) -> tuple[str, ...]:
    observed_reasons = {reason for reason, count in scan.skipped.items() if count > 0}
    known_reasons = (
        _INTENTIONAL_INELIGIBILITY_REASONS
        | _INCOMPLETE_OBSERVATION_REASONS
        | _INCOMPLETE_TRAVERSAL_REASONS
    )
    unknown_reasons = observed_reasons - known_reasons
    blockers = (
        (observed_reasons & _INCOMPLETE_OBSERVATION_REASONS)
        | (observed_reasons & _INCOMPLETE_TRAVERSAL_REASONS)
        | unknown_reasons
    )
    return tuple(sorted(blockers))


def _telemetry(
    *,
    repository: str,
    revision: str,
    scan: SafeScanResult,
    documents: tuple[KnowledgeDocument, ...],
    changes: RepositoryIndexChanges,
    config: RepositoryIndexConfig,
    symbol_extractors: Mapping[str, SymbolExtractor],
    removal_detection_blockers: tuple[str, ...],
    duration_ms: int,
) -> RepositoryIndexTelemetry:
    by_type = Counter(item.knowledge_type for item in documents)
    by_role = Counter(item.technical_role or "unknown" for item in documents)
    per_source = Counter(item.source_ref for item in documents)
    truncated_files = sum(
        scan.skipped.get(reason, 0)
        for reason in ("max_file_bytes", "changed_or_oversized")
    )
    source_coverage = _source_coverage(
        scan=scan,
        documents=documents,
        config=config,
        symbol_extractors=symbol_extractors,
    )
    return RepositoryIndexTelemetry(
        repository=repository,
        revision=revision,
        files_discovered=scan.discovered_files,
        files_evaluated=scan.evaluated_files,
        files_eligible=scan.eligible_files,
        files_read=len(scan.sources),
        files_indexed=len({item.path for item in documents}),
        files_changed=len(
            {
                *(item.path for item in changes.added),
                *(item.document.path for item in changes.updated),
                *(item.path for item in changes.removed),
            }
        ),
        files_skipped=max(0, scan.evaluated_files - len(scan.sources)),
        files_unvisited_due_to_truncation=scan.unvisited_files,
        skip_reasons=dict(sorted(scan.skipped.items())),
        documents_produced=len(documents),
        documents_by_knowledge_type=dict(sorted(by_type.items())),
        documents_by_technical_role=dict(sorted(by_role.items())),
        documents_per_source=dict(sorted(per_source.items())),
        source_coverage=source_coverage,
        truncated_files=truncated_files,
        scan_truncated=scan.truncated,
        scan_traversal_complete=scan.unvisited_files == 0,
        truncation_reasons=scan.truncation_reasons,
        added=len(changes.added),
        updated=len(changes.updated),
        removed=len(changes.removed),
        unchanged=len(changes.unchanged),
        removal_detection_complete=changes.removal_detection_complete,
        removal_detection_blockers=removal_detection_blockers,
        duration_ms=duration_ms,
    )


def _source_coverage(
    *,
    scan: SafeScanResult,
    documents: tuple[KnowledgeDocument, ...],
    config: RepositoryIndexConfig,
    symbol_extractors: Mapping[str, SymbolExtractor],
) -> RepositorySourceCoverage:
    indexed_paths = {item.path for item in documents}
    symbol_paths = {item.path for item in documents if item.symbols}
    documents_by_path = Counter(item.path for item in documents)
    language_counts: dict[str, Counter[str]] = {}
    without_documents_by_source_kind: Counter[str] = Counter()
    implementation_files_read = 0
    implementation_files_indexed = 0
    structural_capability = 0
    textual_only = 0
    implementation_with_symbols = 0
    knowledge_files_read = 0

    for source in scan.sources:
        language = _detect_language(source.relative) or "unknown"
        knowledge_type = _classify(source.relative, config.classification_rules)
        implementation = knowledge_type not in {"documentation", "configuration"}
        indexed = source.relative in indexed_paths
        structural = implementation and language in symbol_extractors
        has_symbols = implementation and source.relative in symbol_paths
        counts = language_counts.setdefault(language, Counter())
        counts["files_read"] += 1
        counts["documents"] += documents_by_path.get(source.relative, 0)
        if indexed:
            counts["files_indexed"] += 1
        else:
            without_documents_by_source_kind[source_kind(source.relative)] += 1
        if implementation:
            implementation_files_read += 1
            counts["implementation_files"] += 1
            if indexed:
                implementation_files_indexed += 1
            if structural:
                structural_capability += 1
                counts["structural_capability_files"] += 1
            else:
                textual_only += 1
                counts["textual_only_implementation_files"] += 1
            if has_symbols:
                implementation_with_symbols += 1
                counts["files_with_symbol_chunks"] += 1
        else:
            knowledge_files_read += 1

    by_language = {
        language: SourceLanguageCoverage(
            files_read=counts["files_read"],
            files_indexed=counts["files_indexed"],
            implementation_files=counts["implementation_files"],
            structural_capability_files=counts["structural_capability_files"],
            textual_only_implementation_files=counts["textual_only_implementation_files"],
            files_with_symbol_chunks=counts["files_with_symbol_chunks"],
            documents=counts["documents"],
        )
        for language, counts in language_counts.items()
    }
    skipped = scan.coverage.skipped_by_reason_and_source_kind
    return RepositorySourceCoverage(
        files_read=len(scan.sources),
        files_indexed=len(indexed_paths),
        files_without_documents=max(0, len(scan.sources) - len(indexed_paths)),
        implementation_files_read=implementation_files_read,
        implementation_files_indexed=implementation_files_indexed,
        implementation_files_structural_capability=structural_capability,
        implementation_files_textual_only=textual_only,
        implementation_files_with_symbol_chunks=implementation_with_symbols,
        knowledge_files_read=knowledge_files_read,
        by_language=by_language,
        discovered_by_source_kind=scan.coverage.discovered_by_source_kind,
        eligible_by_source_kind=scan.coverage.eligible_by_source_kind,
        read_by_source_kind=scan.coverage.read_by_source_kind,
        files_without_documents_by_source_kind=dict(without_documents_by_source_kind),
        not_included_by_source_kind=skipped.get("not_included", {}),
        excluded_by_source_kind=skipped.get("excluded", {}),
        unvisited_by_source_kind=scan.coverage.unvisited_by_source_kind,
    )


def _repository_identity(workspace: Path) -> str:
    remote = _git_value(workspace, ("remote", "get-url", "origin"))
    if not remote:
        return workspace.name
    value = remote.removesuffix(".git")
    if "://" in value:
        parsed = urlparse(value)
        path = parsed.path.strip("/")
        return path or workspace.name
    if "@" in value and ":" in value:
        return value.split(":", 1)[1].strip("/") or workspace.name
    return value.strip("/") or workspace.name


def _git_value(workspace: Path, args: Sequence[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _working_tree_revision(sources: tuple[SafeSource, ...]) -> str:
    payload = [(item.relative, item.checksum) for item in sources]
    rendered = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return "tree:" + hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _checksum(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()}"
