"""Solr persistence orchestration over canonical Git delta discovery."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from fetchcore.retrieval.contracts import KnowledgeSearchError
from fetchcore.repository.indexing import (
    KnowledgeDocument, RepositoryIndexResult, RepositoryIndexer,
)
from fetchcore.repository.incremental import (
    GitDeltaPlan, GitRepositoryChangeDetector, _full_scan, index_repository_delta,
)
from fetchcore.repository.index_fingerprint import indexing_contract_fingerprint
from fetchcore.adapters.retrieval.solr.index_contract import (
    bind_indexing_contract_store, snapshot_indexing_fingerprint,
)
from fetchcore.adapters.retrieval.solr.backend import SolrIndexStore, SolrIndexSyncResult
from fetchcore.telemetry import IndexStageTelemetry


@dataclass(frozen=True)
class RepositorySyncOutcome:
    """Storage result plus observable repository-discovery behavior."""

    storage: SolrIndexSyncResult
    discovery: GitDeltaPlan
    indexing_duration_ms: int
    noop: bool = False
    stage_timings_ms: dict[str, int] | None = None

    def to_mapping(self) -> dict[str, object]:
        """Return the combined discovery and persistence synchronization evidence."""
        payload = self.storage.to_mapping()
        payload["discovery"] = self.discovery.to_mapping()
        payload["indexing_duration_ms"] = self.indexing_duration_ms
        payload["noop"] = self.noop
        if self.stage_timings_ms is not None:
            payload["stage_timings_ms"] = IndexStageTelemetry.from_mapping(
                self.stage_timings_ms,
            ).to_mapping()
        return payload


class GitAwareSolrSynchronizer:
    """Coordinate Git delta discovery, canonical indexing and Solr persistence."""

    def __init__(
        self,
        store: SolrIndexStore,
        indexer: RepositoryIndexer,
        *,
        detector: GitRepositoryChangeDetector | None = None,
        timer: Callable[[], float] = time.monotonic,
        allow_rebuild: bool = True,
    ) -> None:
        """Initialize the Git-aware Solr synchronizer with explicit collaborators."""
        self.allow_rebuild = allow_rebuild
        self.indexer = indexer
        self.indexing_fingerprint = indexing_contract_fingerprint(indexer)
        self.store = bind_indexing_contract_store(store, indexer)
        self.detector = detector or GitRepositoryChangeDetector()
        self.timer = timer

    def sync(self, workspace: Path) -> RepositorySyncOutcome:
        """Synchronize repository state using the safest provable Git delta."""
        repository = self.indexer.resolve_repository_identity(workspace)
        try:
            snapshot = self.store.load_snapshot(repository)
        except KnowledgeSearchError as exc:
            if exc.category not in {
                "snapshot_missing",
                "schema_mismatch",
                "response_parsing_failure",
            }:
                raise
            return self._full_rebuild_outcome(
                workspace,
                previous_revision=None,
                reason=f"snapshot_{exc.category}",
            )

        if snapshot_indexing_fingerprint(snapshot) != self.indexing_fingerprint:
            return self._full_rebuild_outcome(
                workspace,
                previous_revision=snapshot.revision,
                reason="indexing_contract_changed",
            )
        if not self.store.embedding_snapshot_matches(snapshot):
            return self._full_rebuild_outcome(
                workspace,
                previous_revision=snapshot.revision,
                reason="embedding_identity_changed",
            )

        try:
            previous = self.store.load_documents(snapshot.repository)
        except KnowledgeSearchError as exc:
            if exc.category not in {"schema_mismatch", "response_parsing_failure"}:
                raise
            return self._full_rebuild_outcome(
                workspace,
                previous_revision=snapshot.revision,
                reason=f"documents_{exc.category}",
            )

        current_revision = self.indexer.config.revision
        plan = self.detector.plan(
            workspace,
            previous_revision=snapshot.revision,
            current_revision=current_revision,
        )
        current_branch = self.indexer.config.branch
        if (
            plan.mode == "git-delta"
            and not plan.changed_paths
            and not plan.removed_paths
            and snapshot.revision == plan.current_revision
            and (current_branch is None or snapshot.branch == current_branch)
        ):
            return RepositorySyncOutcome(
                storage=SolrIndexSyncResult(
                    mode="incremental",
                    snapshot=snapshot,
                    added=0,
                    updated=0,
                    deleted=0,
                    unchanged=len(previous),
                    duration_ms=0,
                ),
                discovery=plan,
                indexing_duration_ms=0,
                noop=True,
            )

        started = self.timer()
        if plan.mode == "git-delta":
            delta_result = self._index_delta(workspace, previous, plan)
            if delta_result is None:
                plan = replace(plan, mode="full-scan", reason="delta_scan_unsafe")
                result = self.indexer.index(workspace, previous_documents=previous)
            else:
                result = delta_result
        else:
            result = self.indexer.index(workspace, previous_documents=previous)

        if result.repository != snapshot.repository:
            raise KnowledgeSearchError(
                "repository identity differs from the active Solr snapshot",
                category="snapshot_revision_mismatch",
            )
        indexing_duration_ms = max(0, round((self.timer() - started) * 1_000))
        storage = self.store.incremental_sync(result)
        return RepositorySyncOutcome(
            storage=storage,
            discovery=plan,
            indexing_duration_ms=indexing_duration_ms,
        )

    def _full_rebuild_outcome(
        self,
        workspace: Path,
        *,
        previous_revision: str | None,
        reason: str,
    ) -> RepositorySyncOutcome:
        if not self.allow_rebuild:
            raise KnowledgeSearchError(
                "Explicit rebuild required: " + reason,
                category="rebuild_required",
                metadata={"reason": reason},
            )
        started = self.timer()
        result = self.indexer.index(workspace)
        storage = self.store.full_rebuild(result)
        return RepositorySyncOutcome(
            storage=storage,
            discovery=_full_scan(previous_revision, result.revision, reason),
            indexing_duration_ms=max(0, round((self.timer() - started) * 1_000)),
        )

    def _index_delta(
        self,
        workspace: Path,
        previous: tuple[KnowledgeDocument, ...],
        plan: GitDeltaPlan,
    ) -> RepositoryIndexResult | None:
        return index_repository_delta(self.indexer, workspace, previous, plan)



# Stable historical import identities for callers that serialize Python classes.
RepositorySyncOutcome.__module__ = "fetchcore.repository.incremental"
GitAwareSolrSynchronizer.__module__ = "fetchcore.repository.incremental"
