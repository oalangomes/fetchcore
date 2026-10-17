"""Small backend-neutral persistence boundary for operational indexing.

The application owns repository binding and canonical indexing. An adapter owns
schema validation, snapshots and projection writes. Retrieval semantics remain
outside this port.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

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


class OperationalIndexStore(Protocol):
    """Required storage operations; optional features stay in adapter capabilities."""

    projection_fingerprint: str

    def validate_schema(self, *, require_vectors: bool = False) -> None: ...

    def load_snapshot(self, repository: str) -> IndexSnapshot: ...

    def load_documents(self, repository: str) -> tuple[KnowledgeDocument, ...]: ...

    def full_rebuild(self, result: RepositoryIndexResult) -> IndexSyncResult: ...

    def incremental_sync(self, result: RepositoryIndexResult) -> IndexSyncResult: ...


class OperationalIndexDriver(Protocol):
    """Adapter lifecycle after application source binding and materialization."""

    def run(self, operation: str) -> dict[str, object]: ...
