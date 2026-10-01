"""Solr snapshot persistence for the canonical indexing fingerprint."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from fetchcore.retrieval.contracts import KnowledgeSearchError
from fetchcore.repository.index_fingerprint import indexing_contract_fingerprint
from fetchcore.repository.indexing import RepositoryIndexer, RepositoryIndexResult
from fetchcore.backends.solr import (
    SolrIndexSnapshot, SolrIndexStore, corpus_checksum,
)
from fetchcore.backends.solr_query import _term_filter


@dataclass(frozen=True)
class IndexContractSolrSnapshot(SolrIndexSnapshot):
    """Snapshot carrying the canonical indexing contract in stored payload."""

    indexing_fingerprint: str | None = None

    def to_solr(self) -> dict[str, object]:
        """Serialize the snapshot with persisted indexing contract metadata."""
        payload = super().to_solr()
        payload["canonical_payload"] = json.dumps(
            {"indexing_fingerprint": self.indexing_fingerprint},
            sort_keys=True,
            separators=(",", ":"),
        )
        return payload

    def to_mapping(self) -> dict[str, object]:
        """Return a stable mapping representation of the index-contract snapshot."""
        payload = super().to_solr()
        payload["indexing_fingerprint"] = self.indexing_fingerprint
        return payload

    @classmethod
    def from_solr(cls, value: Mapping[str, object]) -> IndexContractSolrSnapshot:
        """Rebuild an index-contract snapshot from persisted Solr state."""
        base = SolrIndexSnapshot.from_solr(value)
        fingerprint: str | None = None
        raw_payload = value.get("canonical_payload")
        if raw_payload is not None:
            if not isinstance(raw_payload, str):
                raise KnowledgeSearchError(
                    "Solr snapshot canonical payload has an invalid shape",
                    category="response_parsing_failure",
                )
            try:
                decoded = json.loads(raw_payload)
            except json.JSONDecodeError as exc:
                raise KnowledgeSearchError(
                    "Solr snapshot canonical payload is invalid",
                    category="response_parsing_failure",
                ) from exc
            if not isinstance(decoded, dict):
                raise KnowledgeSearchError(
                    "Solr snapshot canonical payload must be an object",
                    category="response_parsing_failure",
                )
            raw_fingerprint = decoded.get("indexing_fingerprint")
            if raw_fingerprint is not None:
                fingerprint = str(raw_fingerprint)
        return cls(
            repository=base.repository,
            branch=base.branch,
            revision=base.revision,
            corpus_checksum=base.corpus_checksum,
            document_count=base.document_count,
            projection_version=base.projection_version,
            indexed_at=base.indexed_at,
            status=base.status,
            embedding_provider=base.embedding_provider,
            embedding_model=base.embedding_model,
            vector_dimensions=base.vector_dimensions,
            embedding_serializer_mode=base.embedding_serializer_mode,
            embedding_serializer_version=base.embedding_serializer_version,
            embedding_serializer_fingerprint=base.embedding_serializer_fingerprint,
            projection_fingerprint=base.projection_fingerprint,
            indexing_fingerprint=fingerprint,
        )


class IndexContractSolrIndexStore(SolrIndexStore):
    """Solr store variant that reads/writes the indexing fingerprint atomically."""

    indexing_fingerprint: str | None = None

    def load_snapshot(self, repository: str) -> IndexContractSolrSnapshot:
        """Load and validate the active repository snapshot and indexing contract."""
        if not repository.strip():
            raise KnowledgeSearchError(
                "Solr snapshot lookup requires a repository identity",
                category="schema_mismatch",
            )
        filters = ["record_kind:snapshot", _term_filter("repository", repository)]
        response = self._select(
            {"q": "*:*", "fq": filters, "rows": 2, "fl": "*", "sort": "id asc"}
        )
        docs = response["docs"]
        if not docs:
            raise KnowledgeSearchError(
                "Solr has no active index snapshot",
                category="snapshot_missing",
                metadata={"repository": repository},
            )
        if len(docs) != 1:
            raise KnowledgeSearchError(
                "Solr repository scope does not select exactly one snapshot",
                category="snapshot_missing",
                metadata={"repository": repository, "snapshot_count": len(docs)},
            )
        snapshot = IndexContractSolrSnapshot.from_solr(docs[0])
        self._ensure_snapshot_compatible(snapshot)
        if snapshot.status != "ready":
            raise KnowledgeSearchError(
                "Solr snapshot is not ready",
                category="index_stale",
                metadata={"snapshot_status": snapshot.status},
            )
        return snapshot

    def _snapshot(self, result: RepositoryIndexResult) -> IndexContractSolrSnapshot:
        identity = self._embedding_snapshot_identity()
        return IndexContractSolrSnapshot(
            repository=result.repository,
            branch=result.branch,
            revision=result.revision,
            corpus_checksum=corpus_checksum(result.documents),
            document_count=len(result.documents),
            projection_version=self.settings.projection_version,
            indexed_at=self.clock(),
            status="ready",
            embedding_provider=None if identity is None else identity.provider,
            embedding_model=None if identity is None else identity.model,
            vector_dimensions=None if identity is None else identity.dimensions,
            embedding_serializer_mode=None if identity is None else identity.serializer_mode,
            embedding_serializer_version=(
                None if identity is None else identity.serializer_version
            ),
            embedding_serializer_fingerprint=(
                None if identity is None else identity.serializer_fingerprint
            ),
            projection_fingerprint=self.projection_fingerprint,
            indexing_fingerprint=self.indexing_fingerprint,
        )


def bind_indexing_contract_store(
    store: SolrIndexStore,
    indexer: RepositoryIndexer,
) -> SolrIndexStore:
    """Bind the current canonical indexing contract to a Solr store."""

    fingerprint = indexing_contract_fingerprint(indexer)
    if isinstance(store, IndexContractSolrIndexStore):
        store.indexing_fingerprint = fingerprint
        return store
    if not isinstance(store, SolrIndexStore):
        return store
    bound = IndexContractSolrIndexStore(
        store.transport,
        store.settings,
        embedding_provider=store.embedding_provider,
        clock=store.clock,
        timer=store.timer,
    )
    bound.indexing_fingerprint = fingerprint
    return bound


def snapshot_indexing_fingerprint(snapshot: SolrIndexSnapshot) -> str | None:
    """Return the persisted indexing fingerprint when the snapshot provides one."""
    return cast(str | None, getattr(snapshot, "indexing_fingerprint", None))


def snapshot_mapping(snapshot: SolrIndexSnapshot) -> dict[str, object]:
    """Return a stable mapping for any supported Solr snapshot contract."""
    if isinstance(snapshot, IndexContractSolrSnapshot):
        return snapshot.to_mapping()
    return snapshot.to_solr()

# Historical public class paths are retained for serialized Python consumers.
IndexContractSolrSnapshot.__module__ = "fetchcore.repository.index_contract"
IndexContractSolrIndexStore.__module__ = "fetchcore.repository.index_contract"
