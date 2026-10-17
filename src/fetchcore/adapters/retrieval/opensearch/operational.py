"""Operational repository lifecycle for the OpenSearch projection adapter."""

from __future__ import annotations

from dataclasses import dataclass, replace

from fetchcore.application.index_store import RepositoryIndexRuntimeView
from fetchcore.repository.indexing import KnowledgeDocument, RepositoryIndexResult
from fetchcore.adapters.retrieval.opensearch.backend import (
    OpenSearchIndexSnapshot,
    OpenSearchIndexStore,
    OpenSearchIndexSyncResult,
)
from fetchcore.repository.incremental import (
    GitDeltaPlan,
    GitRepositoryChangeDetector,
    index_repository_delta,
)
from fetchcore.projection import corpus_checksum
from fetchcore.retrieval.contracts import KnowledgeSearchError
from fetchcore.telemetry import IndexSyncTelemetry


@dataclass(frozen=True)
class OpenSearchObservation:
    mode: str
    currentness: str
    reason: str
    snapshot: OpenSearchIndexSnapshot | None = None
    schema_compatible: bool = True
    projection_compatible: bool | None = None


class OpenSearchOperationalIndex:
    """Use canonical indexing and Git currentness with OpenSearch persistence."""

    def __init__(self, store: OpenSearchIndexStore, runtime: RepositoryIndexRuntimeView) -> None:
        self.store = store
        self.runtime = runtime
        self.store.indexing_fingerprint = runtime.fingerprint
        self.store.repository = runtime.repository

    def observe(self) -> OpenSearchObservation:
        try:
            self.store.validate_schema(require_vectors=False)
        except KnowledgeSearchError as exc:
            if exc.category == "index_missing":
                return OpenSearchObservation("initial", "absent", "index_missing")
            if exc.category == "schema_mismatch":
                return OpenSearchObservation(
                    "rebuild_required", "incompatible", "schema_mismatch", schema_compatible=False
                )
            raise
        try:
            snapshot = self.store.load_snapshot(self.runtime.repository)
        except KnowledgeSearchError as exc:
            if exc.category == "snapshot_missing" and exc.metadata.get("snapshot_count") in (
                None,
                0,
            ):
                return OpenSearchObservation("initial", "absent", "snapshot_missing")
            if exc.category in {
                "snapshot_missing",
                "schema_mismatch",
                "index_stale",
                "response_parsing_failure",
            }:
                return OpenSearchObservation(
                    "rebuild_required",
                    "incompatible",
                    exc.category,
                    projection_compatible=False if exc.category == "schema_mismatch" else None,
                )
            raise
        if snapshot.indexing_fingerprint != self.runtime.fingerprint:
            return OpenSearchObservation(
                "rebuild_required",
                "incompatible",
                "indexing_contract_changed",
                snapshot,
                projection_compatible=True,
            )
        if not self.store.embedding_snapshot_matches(snapshot):
            return OpenSearchObservation(
                "rebuild_required",
                "incompatible",
                "embedding_identity_changed",
                snapshot,
                projection_compatible=True,
            )
        plan = GitRepositoryChangeDetector().plan(
            self.runtime.workspace,
            previous_revision=snapshot.revision,
            current_revision=self.runtime.revision,
        )
        clean = (
            plan.mode == "git-delta"
            and not plan.changed_paths
            and not plan.removed_paths
            and snapshot.revision == plan.current_revision
            and (self.runtime.branch is None or snapshot.branch == self.runtime.branch)
        )
        return OpenSearchObservation(
            "noop" if clean else "incremental",
            "current"
            if clean
            else "unknown"
            if self.runtime.revision is None or self.runtime.revision == snapshot.revision
            else "stale",
            "up_to_date" if clean else plan.reason,
            snapshot,
            projection_compatible=True,
        )

    def run(self, operation: str) -> dict[str, object]:
        observation = self.observe()
        snapshot = observation.snapshot
        result: dict[str, object] = {
            "indexed_revision": None if snapshot is None else snapshot.revision,
            "mode": observation.mode,
            "currentness": observation.currentness,
            "reason": observation.reason,
            "schema_compatible": observation.schema_compatible,
            "projection_compatible": observation.projection_compatible,
            "incremental_possible": observation.mode in {"incremental", "noop"},
            "indexing_fingerprint": self.runtime.fingerprint,
            "projection_fingerprint": self.store.projection_fingerprint,
        }
        if operation == "status":
            return result
        if operation == "plan":
            if observation.mode == "rebuild_required":
                return result
            previous = (
                () if snapshot is None else self.store.load_documents(self.runtime.repository)
            )
            if snapshot is not None:
                self._validate_previous(snapshot, previous)
            indexed, discovery = self._index_changed(previous, snapshot)
            result["discovery"] = _discovery(discovery)
            changes = indexed.changes
            same = not (changes.added or changes.updated or changes.removed)
            if snapshot is not None and same and snapshot.revision == indexed.revision:
                result.update(mode="noop", currentness="current", reason="canonical_noop")
            elif snapshot is not None:
                result.update(mode="incremental", currentness="stale")
            result["planned_mutations"] = {
                "added": len(changes.added),
                "updated": len(changes.updated),
                "deleted": None if snapshot is None else len(changes.removed),
                "unchanged": len(changes.unchanged),
                "upsert_documents": len(changes.added) + len(changes.updated),
                "resulting_document_count": len(indexed.documents),
                "previous_document_count": None if snapshot is None else snapshot.document_count,
                "replace_repository": snapshot is None,
            }
            return result
        if operation == "sync":
            if observation.mode == "initial":
                raise KnowledgeSearchError(
                    "Index absent; call index rebuild explicitly", category="snapshot_missing"
                )
            if observation.mode == "rebuild_required":
                raise KnowledgeSearchError(
                    "Explicit rebuild required: " + observation.reason, category="rebuild_required"
                )
            assert snapshot is not None
            discovery = GitRepositoryChangeDetector().plan(
                self.runtime.workspace,
                previous_revision=snapshot.revision,
                current_revision=self.runtime.revision,
            )
            if observation.mode == "noop":
                self._require_same_snapshot(snapshot)
                storage = OpenSearchIndexSyncResult(
                    "incremental",
                    snapshot,
                    0,
                    0,
                    0,
                    snapshot.document_count,
                    0,
                    IndexSyncTelemetry(0, 0, 0),
                )
                noop = True
            else:
                previous = self.store.load_documents(self.runtime.repository)
                self._validate_previous(snapshot, previous)
                indexed, discovery = self._index_changed(previous, snapshot, discovery)
                if indexed.repository != snapshot.repository:
                    raise KnowledgeSearchError(
                        "OpenSearch repository binding changed",
                        category="snapshot_revision_mismatch",
                    )
                if (
                    not (
                        indexed.changes.added or indexed.changes.updated or indexed.changes.removed
                    )
                    and snapshot.revision == indexed.revision
                    and (indexed.branch is None or snapshot.branch == indexed.branch)
                ):
                    self._require_same_snapshot(snapshot)
                    storage = OpenSearchIndexSyncResult(
                        "incremental",
                        snapshot,
                        0,
                        0,
                        0,
                        snapshot.document_count,
                        0,
                        IndexSyncTelemetry(0, 0, 0),
                    )
                    noop = True
                else:
                    storage = self.store.incremental_sync(indexed, expected_snapshot=snapshot)
                    noop = False
            execution = _execution_result(storage)
            execution["noop"] = noop
            result["execution"] = execution
            result["discovery"] = _discovery(discovery)
            result["mode"] = "noop" if noop else "incremental"
            result["previous_indexed_revision"] = result.pop("indexed_revision")
            result["previous_currentness"] = result.pop("currentness")
            return result
        if operation == "rebuild":
            # Explicitly requested generation creation permits a mapping change.
            indexed = self.runtime.indexer.index(self.runtime.workspace)
            storage = self.store.full_rebuild(indexed)
            result["execution"] = _execution_result(storage)
            result["mode"] = "rebuild"
            result["previous_indexed_revision"] = result.pop("indexed_revision")
            result["previous_currentness"] = result.pop("currentness")
            result["reason"] = "explicit_rebuild"
            return result
        raise ValueError("Unsupported indexing operation")

    @staticmethod
    def _validate_previous(
        snapshot: OpenSearchIndexSnapshot, documents: tuple[KnowledgeDocument, ...]
    ) -> None:
        if (
            len(documents) != snapshot.document_count
            or corpus_checksum(documents) != snapshot.corpus_checksum
        ):
            raise KnowledgeSearchError(
                "OpenSearch active corpus differs from its snapshot",
                category="index_stale",
                metadata={"rebuild_required": True},
            )

    def _require_same_snapshot(self, snapshot: OpenSearchIndexSnapshot) -> None:
        if self.store.load_snapshot(snapshot.repository) != snapshot:
            raise KnowledgeSearchError(
                "OpenSearch snapshot changed during no-op", category="concurrent_write"
            )

    def _index_changed(
        self,
        previous: tuple[KnowledgeDocument, ...],
        snapshot: OpenSearchIndexSnapshot | None,
        discovery: GitDeltaPlan | None = None,
    ) -> tuple[RepositoryIndexResult, GitDeltaPlan | None]:
        if snapshot is None:
            return self.runtime.indexer.index(self.runtime.workspace), None
        plan = discovery or GitRepositoryChangeDetector().plan(
            self.runtime.workspace,
            previous_revision=snapshot.revision,
            current_revision=self.runtime.revision,
        )
        if plan.mode == "git-delta":
            delta = index_repository_delta(
                self.runtime.indexer, self.runtime.workspace, previous, plan
            )
            if delta is not None:
                return delta, plan
            plan = replace(plan, mode="full-scan", reason="delta_scan_unsafe")
        return self.runtime.indexer.index(
            self.runtime.workspace,
            previous_documents=previous,
        ), plan


def _discovery(plan: GitDeltaPlan | None) -> dict[str, object]:
    if plan is None:
        return {"mode": "full-scan", "reason": "snapshot_missing"}
    payload = plan.to_mapping()
    if plan.mode == "git-delta":
        payload["changed_path_values"] = list(plan.changed_paths)
        payload["removed_path_values"] = list(plan.removed_paths)
    else:
        for name in ("changed_paths", "removed_paths", "targeted_paths"):
            payload.pop(name, None)
    return payload


def _execution_result(storage: OpenSearchIndexSyncResult) -> dict[str, object]:
    snapshot = storage.snapshot
    result = storage.to_mapping()
    result["snapshot"] = {
        "repository": snapshot.repository,
        "branch": snapshot.branch,
        "revision": snapshot.revision,
        "document_count": snapshot.document_count,
        "corpus_checksum": snapshot.corpus_checksum,
        "projection_version": snapshot.projection_version,
        "indexed_at": snapshot.indexed_at,
        "status": snapshot.status,
        "projection_fingerprint": snapshot.projection_fingerprint,
        "indexing_fingerprint": snapshot.indexing_fingerprint,
        "embedding_provider": snapshot.embedding_provider,
        "embedding_model": snapshot.embedding_model,
        "vector_dimensions": snapshot.vector_dimensions,
        "serializer_mode": snapshot.embedding_serializer_mode,
        "serializer_version": snapshot.embedding_serializer_version,
        "serializer_fingerprint": snapshot.embedding_serializer_fingerprint,
    }
    return result
