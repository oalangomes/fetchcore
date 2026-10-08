"""Solr persistence orchestration over canonical Git delta discovery."""

from __future__ import annotations

import glob
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from fetchcore.retrieval.contracts import KnowledgeSearchError
from fetchcore.repository.indexing import (
    KnowledgeDocument, RepositoryIndexResult, RepositoryIndexTelemetry, RepositoryIndexer,
)
from fetchcore.repository.incremental import (
    GitDeltaPlan, GitRepositoryChangeDetector, _delta_telemetry, _diff_documents,
    _eligible_path, _full_scan,
)
from fetchcore.repository.index_fingerprint import indexing_contract_fingerprint
from fetchcore.adapters.retrieval.solr.index_contract import (
    bind_indexing_contract_store, snapshot_indexing_fingerprint,
)
from fetchcore.adapters.retrieval.solr.backend import SolrIndexStore, SolrIndexSyncResult


@dataclass(frozen=True)
class RepositorySyncOutcome:
    """Storage result plus observable repository-discovery behavior."""

    storage: SolrIndexSyncResult
    discovery: GitDeltaPlan
    indexing_duration_ms: int
    noop: bool = False

    def to_mapping(self) -> dict[str, object]:
        """Return the combined discovery and persistence synchronization evidence."""
        payload = self.storage.to_mapping()
        payload["discovery"] = self.discovery.to_mapping()
        payload["indexing_duration_ms"] = self.indexing_duration_ms
        payload["noop"] = self.noop
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
        affected_paths = set(plan.changed_paths) | set(plan.removed_paths)
        eligible_changed = tuple(
            path
            for path in plan.changed_paths
            if _eligible_path(path, self.indexer.config.include_patterns, self.indexer.config.exclude_patterns)
        )
        if len(eligible_changed) > self.indexer.config.max_files:
            return None

        delta_documents: tuple[KnowledgeDocument, ...] = ()
        delta_telemetry: RepositoryIndexTelemetry | None = None
        if eligible_changed:
            delta_config = replace(
                self.indexer.config,
                include_patterns=tuple(glob.escape(path) for path in eligible_changed),
            )
            delta_indexer = RepositoryIndexer(
                delta_config,
                symbol_extractors=self.indexer.symbol_extractors,
                clock=self.indexer.clock,
                timer=self.indexer.timer,
            )
            delta = delta_indexer.index(workspace)
            if (
                not delta.changes.removal_detection_complete
                or not delta.telemetry.scan_traversal_complete
            ):
                return None
            delta_documents = delta.documents
            delta_telemetry = delta.telemetry

        retained = tuple(item for item in previous if item.path not in affected_paths)
        documents = tuple(
            sorted(
                (*retained, *delta_documents),
                key=lambda item: (item.path, item.line_start, item.line_end, item.chunk_ref),
            )
        )
        changes = _diff_documents(documents, previous)
        telemetry = _delta_telemetry(
            repository=self.indexer.config.repository or self.indexer.resolve_repository_identity(workspace),
            revision=plan.current_revision or self.indexer.config.revision or "unknown",
            documents=documents,
            changes=changes,
            delta=delta_telemetry,
        )
        return RepositoryIndexResult(
            repository=self.indexer.config.repository or self.indexer.resolve_repository_identity(workspace),
            branch=self.indexer.config.branch,
            revision=plan.current_revision or self.indexer.config.revision or "unknown",
            documents=documents,
            changes=changes,
            telemetry=telemetry,
        )



# Stable historical import identities for callers that serialize Python classes.
RepositorySyncOutcome.__module__ = "fetchcore.repository.incremental"
GitAwareSolrSynchronizer.__module__ = "fetchcore.repository.incremental"
