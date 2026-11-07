"""Small backend-neutral persistence boundary for operational indexing.

The application owns repository binding and canonical indexing. An adapter owns
schema validation, snapshots and projection writes. Retrieval semantics remain
outside this port.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from dataclasses import dataclass
from typing import Literal, Protocol

from fetchcore.repository.indexing import (
    KnowledgeDocument,
    RepositoryIndexer,
    RepositoryIndexResult,
)


class RepositoryIndexRuntimeView(Protocol):
    """Resolved canonical inputs an operational adapter may observe."""

    @property
    def workspace(self) -> Path: ...

    @property
    def repository(self) -> str: ...

    @property
    def branch(self) -> str | None: ...

    @property
    def revision(self) -> str | None: ...

    @property
    def indexer(self) -> RepositoryIndexer: ...

    @property
    def fingerprint(self) -> str: ...


class IndexSnapshot(Protocol):
    @property
    def repository(self) -> str: ...

    @property
    def branch(self) -> str | None: ...

    @property
    def revision(self) -> str: ...

    @property
    def corpus_checksum(self) -> str: ...

    @property
    def document_count(self) -> int: ...

    @property
    def projection_version(self) -> str: ...

    @property
    def projection_fingerprint(self) -> str | None: ...

    @property
    def status(self) -> str: ...


class IndexSyncResult(Protocol):
    def to_mapping(self) -> dict[str, object]: ...


StructuralSelector = Literal["name", "qualified_name", "identity", "source_ref"]
STRUCTURAL_SELECTOR_FIELDS: dict[StructuralSelector, str] = {
    "name": "structural_names",
    "qualified_name": "structural_qualified_names",
    "identity": "structural_identities",
    "source_ref": "source_ref",
}


@dataclass(frozen=True)
class StructuralDocumentPage:
    """Bounded canonical candidates from an exact derived lookup field."""

    documents: tuple[KnowledgeDocument, ...]
    has_more: bool


def structural_candidate_matches(
    raw: Mapping[str, object], document: KnowledgeDocument,
    selector: StructuralSelector, value: str,
) -> bool:
    """Check stored exact keys against hydrated canonical evidence."""

    from fetchcore.repository.structure_lookup import structural_lookup_keys

    expected = structural_lookup_keys(document).to_mapping()
    for key, values in expected.items():
        observed = raw.get(key, [])
        if not isinstance(observed, list) or any(not isinstance(item, str) for item in observed):
            return False
        if not isinstance(values, list) or set(observed) != set(values):
            return False
    field = STRUCTURAL_SELECTOR_FIELDS[selector]
    if field == "source_ref":
        return document.source_ref == value
    selected = expected[field]
    return isinstance(selected, list) and value in selected


class OperationalIndexStore(Protocol):
    """Required storage operations; optional features stay in adapter capabilities."""

    projection_fingerprint: str

    def validate_schema(self, *, require_vectors: bool = False) -> None: ...

    def load_snapshot(self, repository: str) -> IndexSnapshot: ...

    def load_documents(self, repository: str) -> tuple[KnowledgeDocument, ...]: ...

    def lookup_structural_documents(
        self, repository: str, revision: str, *,
        selector: StructuralSelector, value: str, limit: int,
    ) -> StructuralDocumentPage: ...

    def load_structural_targets(
        self, repository: str, revision: str, identifiers: tuple[str, ...],
    ) -> tuple[KnowledgeDocument, ...]: ...

    def full_rebuild(self, result: RepositoryIndexResult) -> IndexSyncResult: ...

    def incremental_sync(self, result: RepositoryIndexResult) -> IndexSyncResult: ...


class OperationalIndexDriver(Protocol):
    """Adapter lifecycle after application source binding and materialization."""

    def run(self, operation: str) -> dict[str, object]: ...
