"""Backend-neutral repository source/corpus coverage diagnostics."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceLanguageCoverage:
    """Observed coverage for sources mapped to one detected language."""

    files_read: int = 0
    files_indexed: int = 0
    implementation_files: int = 0
    structural_capability_files: int = 0
    textual_only_implementation_files: int = 0
    files_with_symbol_chunks: int = 0
    documents: int = 0

    def to_mapping(self) -> dict[str, int]:
        return {
            "files_read": self.files_read,
            "files_indexed": self.files_indexed,
            "implementation_files": self.implementation_files,
            "structural_capability_files": self.structural_capability_files,
            "textual_only_implementation_files": self.textual_only_implementation_files,
            "files_with_symbol_chunks": self.files_with_symbol_chunks,
            "documents": self.documents,
        }


@dataclass(frozen=True)
class RepositorySourceCoverage:
    """Diagnostic view separating source visibility from structural understanding."""

    files_read: int
    files_indexed: int
    files_without_documents: int
    implementation_files_read: int
    implementation_files_indexed: int
    implementation_files_structural_capability: int
    implementation_files_textual_only: int
    implementation_files_with_symbol_chunks: int
    knowledge_files_read: int
    by_language: dict[str, SourceLanguageCoverage]
    discovered_by_source_kind: dict[str, int]
    eligible_by_source_kind: dict[str, int]
    read_by_source_kind: dict[str, int]
    files_without_documents_by_source_kind: dict[str, int]
    not_included_by_source_kind: dict[str, int]
    excluded_by_source_kind: dict[str, int]
    unvisited_by_source_kind: dict[str, int]

    def to_mapping(self) -> dict[str, object]:
        return {
            "files_read": self.files_read,
            "files_indexed": self.files_indexed,
            "files_without_documents": self.files_without_documents,
            "implementation_files_read": self.implementation_files_read,
            "implementation_files_indexed": self.implementation_files_indexed,
            "implementation_files_structural_capability": (
                self.implementation_files_structural_capability
            ),
            "implementation_files_textual_only": self.implementation_files_textual_only,
            "implementation_files_with_symbol_chunks": (
                self.implementation_files_with_symbol_chunks
            ),
            "knowledge_files_read": self.knowledge_files_read,
            "by_language": {
                language: coverage.to_mapping()
                for language, coverage in sorted(self.by_language.items())
            },
            "discovered_by_source_kind": dict(sorted(self.discovered_by_source_kind.items())),
            "eligible_by_source_kind": dict(sorted(self.eligible_by_source_kind.items())),
            "read_by_source_kind": dict(sorted(self.read_by_source_kind.items())),
            "files_without_documents_by_source_kind": dict(
                sorted(self.files_without_documents_by_source_kind.items())
            ),
            "not_included_by_source_kind": dict(sorted(self.not_included_by_source_kind.items())),
            "excluded_by_source_kind": dict(sorted(self.excluded_by_source_kind.items())),
            "unvisited_by_source_kind": dict(sorted(self.unvisited_by_source_kind.items())),
        }
