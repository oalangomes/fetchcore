"""Targeted Solr persistence for proven Git repository deltas."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from fetchcore.retrieval.contracts import KnowledgeSearchError
from fetchcore.backends.solr_document_incremental import (
    DocumentAwareGitSolrSynchronizer,
)
from fetchcore.backends.solr_incremental import RepositorySyncOutcome
from fetchcore.backends.solr_index_contract import snapshot_indexing_fingerprint
from fetchcore.repository.indexing import KnowledgeDocument, RepositoryIndexResult
from fetchcore.backends.solr import (
    SolrIndexSnapshot,
    SolrIndexStore,
    SolrIndexSyncResult,
)
from fetchcore.backends.solr_query import _term_filter


class TargetedGitAwareSolrSynchronizer(DocumentAwareGitSolrSynchronizer):
    """Use targeted persistence only after Git proves a safe repository delta.

    Full-scan and recovery paths continue to use ``SolrIndexStore``'s complete
    projection validation. The optimized path relies on the existing invariant
    that one writer owns a repository projection at a time. It validates every
    mutated/deleted document, repository document count, and active snapshot.
    """

    def sync(self, workspace: Path) -> RepositorySyncOutcome:
        """Synchronize repository state while preserving persistent index contracts."""
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
            return super().sync(workspace)

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

        plan = self.detector.plan(
            workspace,
            previous_revision=snapshot.revision,
            current_revision=self.indexer.config.revision,
        )
        current_branch = self.indexer.config.branch
        if (
            plan.mode == "git-delta"
            and not plan.changed_paths
            and not plan.removed_paths
            and snapshot.revision == plan.current_revision
            and (current_branch is None or snapshot.branch == current_branch)
        ):
            return _noop_outcome(snapshot, plan, indexing_duration_ms=0)

        previous = self.store.load_documents(snapshot.repository)
        indexing_started = self.timer()
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
        indexing_duration_ms = max(0, round((self.timer() - indexing_started) * 1_000))

        if (
            plan.mode == "git-delta"
            and not _has_corpus_changes(result)
            and snapshot.revision == result.revision
            and (result.branch is None or snapshot.branch == result.branch)
        ):
            return _noop_outcome(
                snapshot,
                plan,
                indexing_duration_ms=indexing_duration_ms,
            )

        if plan.mode == "git-delta" and self.store.embedding_provider is None:
            storage = targeted_incremental_sync(
                self.store,
                result,
                previous_documents=previous,
                previous_snapshot=snapshot,
            )
        else:
            # Vector updates use the standard transactional path so rollback can
            # restore persisted raw vectors without depending on the embedding
            # provider that may have caused the failed mutation.
            storage = self.store.incremental_sync(result)
        return RepositorySyncOutcome(
            storage=storage,
            discovery=plan,
            indexing_duration_ms=indexing_duration_ms,
        )


def _noop_outcome(
    snapshot: SolrIndexSnapshot,
    plan: object,
    *,
    indexing_duration_ms: int,
) -> RepositorySyncOutcome:
    from fetchcore.repository.incremental import GitDeltaPlan

    discovery = plan
    if not isinstance(discovery, GitDeltaPlan):
        raise TypeError("repository no-op requires a GitDeltaPlan")
    return RepositorySyncOutcome(
        storage=SolrIndexSyncResult(
            mode="incremental",
            snapshot=snapshot,
            added=0,
            updated=0,
            deleted=0,
            unchanged=snapshot.document_count,
            duration_ms=0,
        ),
        discovery=discovery,
        indexing_duration_ms=indexing_duration_ms,
        noop=True,
    )


def _has_corpus_changes(result: RepositoryIndexResult) -> bool:
    changes = result.changes
    return bool(changes.added or changes.updated or changes.removed)


def targeted_incremental_sync(
    store: SolrIndexStore,
    result: RepositoryIndexResult,
    *,
    previous_documents: tuple[KnowledgeDocument, ...],
    previous_snapshot: SolrIndexSnapshot,
) -> SolrIndexSyncResult:
    """Persist a proven delta without re-reading the complete Solr projection."""

    store._ensure_complete(result)
    store._ensure_snapshot_compatible(previous_snapshot)
    if store.embedding_provider is not None:
        return store.incremental_sync(result)
    if not store.embedding_snapshot_matches(previous_snapshot):
        raise KnowledgeSearchError(
            "Solr embedding identity changed; targeted incremental sync requires rebuild",
            category="index_stale",
            metadata={
                "repository": result.repository,
                "rebuild_required": True,
                "reason": "embedding_identity_changed",
            },
        )
    started = store.timer()
    changes = result.changes
    upserts = (*changes.added, *(item.document for item in changes.updated))
    expected_snapshot = store._snapshot(result)

    previous_by_id = {item.id: item for item in previous_documents}
    restore_ids = {
        *(item.previous_document_id for item in changes.updated),
        *(item.id for item in changes.removed),
    }
    restore_documents = tuple(
        previous_by_id[document_id]
        for document_id in sorted(restore_ids)
        if document_id in previous_by_id
    )
    added_ids = tuple(sorted(item.id for item in changes.added))

    try:
        store._upsert_documents(upserts)
        _delete_ids(store, (item.id for item in changes.removed))
        store._stage_snapshot(expected_snapshot)
        store._commit()
        _validate_targeted_state(
            store,
            expected_documents=upserts,
            removed_ids=tuple(item.id for item in changes.removed),
            expected_snapshot=expected_snapshot,
        )
    except Exception as exc:  # noqa: BLE001 - compensation must cover every mutation failure
        _restore_targeted_delta(
            store,
            added_ids=added_ids,
            restore_documents=restore_documents,
            previous_snapshot=previous_snapshot,
            cause=exc,
        )

    return SolrIndexSyncResult(
        mode="incremental",
        snapshot=expected_snapshot,
        added=len(changes.added),
        updated=len(changes.updated),
        deleted=len(changes.removed),
        unchanged=len(changes.unchanged),
        duration_ms=max(0, round((store.timer() - started) * 1_000)),
    )


def _validate_targeted_state(
    store: SolrIndexStore,
    *,
    expected_documents: tuple[KnowledgeDocument, ...],
    removed_ids: tuple[str, ...],
    expected_snapshot: SolrIndexSnapshot,
) -> None:
    actual_snapshot = store.load_snapshot(expected_snapshot.repository)
    if actual_snapshot != expected_snapshot:
        raise KnowledgeSearchError(
            "Solr active snapshot does not match the targeted incremental update",
            category="index_update_failure",
            metadata={
                "expected_snapshot_revision": expected_snapshot.revision,
                "actual_snapshot_revision": actual_snapshot.revision,
            },
        )

    actual_count = _repository_document_count(store, expected_snapshot.repository)
    if actual_count != expected_snapshot.document_count:
        raise KnowledgeSearchError(
            "Solr repository document count does not match the targeted incremental update",
            category="index_update_failure",
            metadata={"expected": expected_snapshot.document_count, "actual": actual_count},
        )

    expected_by_id = {item.id: item for item in expected_documents}
    persisted = _load_documents_by_ids(store, tuple(expected_by_id))
    actual_by_id = {item.id: item for item in persisted}
    if actual_by_id != expected_by_id:
        raise KnowledgeSearchError(
            "Solr targeted documents do not match the canonical delta",
            category="index_update_failure",
            metadata={
                "expected_ids": sorted(expected_by_id),
                "actual_ids": sorted(actual_by_id),
            },
        )

    leaked = _load_documents_by_ids(store, removed_ids)
    if leaked:
        raise KnowledgeSearchError(
            "Solr targeted deletion left stale knowledge documents",
            category="index_update_failure",
            metadata={"stale_ids": sorted(item.id for item in leaked)},
        )


def _restore_targeted_delta(
    store: SolrIndexStore,
    *,
    added_ids: tuple[str, ...],
    restore_documents: tuple[KnowledgeDocument, ...],
    previous_snapshot: SolrIndexSnapshot,
    cause: Exception,
) -> None:
    try:
        _delete_ids(store, added_ids)
        store._upsert_documents(restore_documents)
        store._stage_snapshot(previous_snapshot)
        store._commit()
        _validate_restored_target(
            store,
            expected_documents=restore_documents,
            absent_ids=added_ids,
            previous_snapshot=previous_snapshot,
        )
    except Exception as rollback_error:
        raise KnowledgeSearchError(
            "Solr targeted index update failed and rollback could not restore the previous projection",
            category="index_update_failure",
            metadata={
                "operation": "targeted_incremental_sync",
                "rollback": "failed",
                "cause_category": _error_category(cause),
                "rollback_category": _error_category(rollback_error),
            },
        ) from rollback_error
    raise KnowledgeSearchError(
        "Solr targeted index update failed; the previous projection was restored",
        category="index_update_failure",
        metadata={
            "operation": "targeted_incremental_sync",
            "rollback": "succeeded",
            "cause_category": _error_category(cause),
        },
    ) from cause


def _validate_restored_target(
    store: SolrIndexStore,
    *,
    expected_documents: tuple[KnowledgeDocument, ...],
    absent_ids: tuple[str, ...],
    previous_snapshot: SolrIndexSnapshot,
) -> None:
    actual_snapshot = store.load_snapshot(previous_snapshot.repository)
    if actual_snapshot != previous_snapshot:
        raise KnowledgeSearchError(
            "targeted rollback did not restore the previous snapshot",
            category="index_update_failure",
        )
    if _repository_document_count(store, previous_snapshot.repository) != previous_snapshot.document_count:
        raise KnowledgeSearchError(
            "targeted rollback did not restore the previous document count",
            category="index_update_failure",
        )
    expected_by_id = {item.id: item for item in expected_documents}
    actual_by_id = {
        item.id: item for item in _load_documents_by_ids(store, tuple(expected_by_id))
    }
    if actual_by_id != expected_by_id:
        raise KnowledgeSearchError(
            "targeted rollback did not restore the previous documents",
            category="index_update_failure",
        )
    if _load_documents_by_ids(store, absent_ids):
        raise KnowledgeSearchError(
            "targeted rollback left newly-added documents behind",
            category="index_update_failure",
        )


def _repository_document_count(store: SolrIndexStore, repository: str) -> int:
    response = store._select(
        {
            "q": "*:*",
            "fq": ["record_kind:knowledge", _term_filter("repository", repository)],
            "rows": 0,
            "fl": "id",
        }
    )
    return response["num_found"]


def _load_documents_by_ids(
    store: SolrIndexStore,
    ids: tuple[str, ...],
) -> tuple[KnowledgeDocument, ...]:
    if not ids:
        return ()
    documents: list[KnowledgeDocument] = []
    batch_size = min(50, max(1, store.settings.batch_size))
    for offset in range(0, len(ids), batch_size):
        batch = ids[offset : offset + batch_size]
        response = store._select(
            {
                "q": "*:*",
                "fq": [
                    "record_kind:knowledge",
                    "{!terms f=id}" + ",".join(batch),
                ],
                "rows": len(batch),
                "fl": (
                    "record_kind,projection_version,projection_fingerprint,"
                    "canonical_payload"
                ),
                "sort": "id asc",
            }
        )
        documents.extend(store.mapper.from_solr(item) for item in response["docs"])
    return tuple(sorted(documents, key=lambda item: item.id))


def _delete_ids(store: SolrIndexStore, ids: Iterable[str]) -> None:
    unique = tuple(sorted(set(ids)))
    if not unique:
        return
    store.transport.post(
        "update",
        {"delete": [{"id": document_id} for document_id in unique]},
        {"commit": "false", "wt": "json"},
    )


def _error_category(error: Exception) -> str:
    if isinstance(error, KnowledgeSearchError):
        return error.category
    return type(error).__name__
