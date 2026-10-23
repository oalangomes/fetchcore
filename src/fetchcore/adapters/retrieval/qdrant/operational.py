"""Operational repository lifecycle for the Qdrant projection adapter."""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections.abc import Callable
import time

from fetchcore.adapters.retrieval.qdrant.backend import (
    QdrantVectorIndexStore,
    QdrantVectorIndexSyncResult,
    QdrantVectorSnapshot,
)
from fetchcore.application.index_store import RepositoryIndexRuntimeView
from fetchcore.projection import corpus_checksum
from fetchcore.repository.incremental import (
    GitDeltaPlan,
    GitRepositoryChangeDetector,
    index_repository_delta,
)
from fetchcore.repository.indexing import KnowledgeDocument, RepositoryIndexResult
from fetchcore.retrieval.contracts import KnowledgeSearchError
from fetchcore.telemetry import IndexStageTelemetry, IndexSyncTelemetry, elapsed_ms


@dataclass(frozen=True)
class QdrantObservation:
    mode: str
    currentness: str
    reason: str
    snapshot: QdrantVectorSnapshot | None = None
    schema_compatible: bool = True
    projection_compatible: bool | None = None
    snapshot_load_ms: int | None = None
    snapshot_projection_fingerprint: str | None = None


class QdrantOperationalIndex:
    """Run canonical plan/status/sync/rebuild through the Qdrant store port."""

    def __init__(
        self,
        store: QdrantVectorIndexStore,
        runtime: RepositoryIndexRuntimeView,
        *,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timer = timer
        self.store = store
        self.runtime = runtime
        self.store.repository = runtime.repository
        self.store.indexing_fingerprint = runtime.fingerprint

    def observe(self) -> QdrantObservation:
        try:
            self.store.validate_schema(require_vectors=True)
        except KnowledgeSearchError as exc:
            if exc.category == "index_missing":
                return QdrantObservation("initial", "absent", "index_missing")
            if exc.category == "schema_mismatch":
                return QdrantObservation(
                    "rebuild_required",
                    "incompatible",
                    "schema_mismatch",
                    schema_compatible=False,
                )
            raise

        started = self.timer()
        try:
            snapshot = self.store.load_snapshot(self.runtime.repository)
            snapshot_load_ms = elapsed_ms(started, self.timer())
        except KnowledgeSearchError as exc:
            if exc.category == "snapshot_missing":
                return QdrantObservation("initial", "absent", "snapshot_missing")
            if exc.category in {
                "schema_mismatch",
                "index_stale",
                "response_parsing_failure",
            }:
                actual = exc.metadata.get("actual")
                actual_fingerprint = (
                    actual.get("projection_fingerprint") if isinstance(actual, dict) else None
                )
                return QdrantObservation(
                    "rebuild_required",
                    "incompatible",
                    exc.category,
                    projection_compatible=False if exc.category == "schema_mismatch" else None,
                    snapshot_projection_fingerprint=(
                        actual_fingerprint if isinstance(actual_fingerprint, str) else None
                    ),
                )
            raise

        if snapshot.indexing_fingerprint != self.runtime.fingerprint:
            return QdrantObservation(
                "rebuild_required",
                "incompatible",
                "indexing_contract_changed",
                snapshot,
                projection_compatible=True,
            )
        if not self.store.embedding_snapshot_matches(snapshot):
            return QdrantObservation(
                "rebuild_required",
                "incompatible",
                "embedding_identity_changed",
                snapshot,
                projection_compatible=True,
            )

        plan = self._detector().plan(
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
        return QdrantObservation(
            "noop" if clean else "incremental",
            "current"
            if clean
            else "unknown"
            if self.runtime.revision is None or self.runtime.revision == snapshot.revision
            else "stale",
            "up_to_date" if clean else plan.reason,
            snapshot,
            projection_compatible=True,
            snapshot_load_ms=snapshot_load_ms,
        )

    def run(self, operation: str) -> dict[str, object]:
        if operation not in {"status", "plan", "sync", "rebuild"}:
            raise ValueError("Unsupported indexing operation")
        observation = self.observe()
        snapshot = observation.snapshot
        stage_timings_ms: dict[str, int] = {}
        if observation.snapshot_load_ms is not None:
            stage_timings_ms["snapshot_load_ms"] = observation.snapshot_load_ms
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
            "snapshot_indexing_fingerprint": (
                None if snapshot is None else snapshot.indexing_fingerprint
            ),
            "snapshot_projection_fingerprint": (
                observation.snapshot_projection_fingerprint
                if snapshot is None else snapshot.projection_fingerprint
            ),
            "snapshot": None if snapshot is None else _snapshot_identity(snapshot),
            "fingerprints": _fingerprints(snapshot, self.runtime.fingerprint),
        }
        if operation == "status":
            return result
        if operation == "plan":
            if observation.mode == "rebuild_required":
                return result
            started = self.timer()
            previous = self._load_previous(snapshot)
            if snapshot is not None:
                stage_timings_ms["previous_documents_load_ms"] = elapsed_ms(
                    started, self.timer(),
                )
            started = self.timer()
            indexed, discovery = self._index_changed(previous, snapshot)
            phase = "canonical_full_index_ms" if snapshot is None else "canonical_delta_index_ms"
            stage_timings_ms[phase] = elapsed_ms(started, self.timer())
            changes = indexed.changes
            same = (
                not (changes.added or changes.updated or changes.removed)
                and (
                    snapshot is None
                    or (
                        snapshot.revision == indexed.revision
                        and snapshot.branch == indexed.branch
                        and snapshot.corpus_checksum == corpus_checksum(indexed.documents)
                    )
                )
            )
            if snapshot is not None and same:
                result.update(mode="noop", currentness="current", reason="canonical_noop")
            elif snapshot is not None:
                result.update(mode="incremental", currentness="stale")
            result["discovery"] = _discovery(discovery, include_timings=True)
            result["stage_timings_ms"] = IndexStageTelemetry.from_mapping(
                stage_timings_ms,
            ).to_mapping()
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
                    "Qdrant index absent; call explicit rebuild first",
                    category="snapshot_missing",
                )
            if observation.mode == "rebuild_required":
                raise KnowledgeSearchError(
                    "Explicit Qdrant rebuild required: " + observation.reason,
                    category="rebuild_required",
                )
            assert snapshot is not None
            discovery = self._detector().plan(
                self.runtime.workspace,
                previous_revision=snapshot.revision,
                current_revision=self.runtime.revision,
            )
            if observation.mode == "noop":
                self._require_same_snapshot(snapshot)
                storage = QdrantVectorIndexSyncResult(
                    "noop",
                    snapshot,
                    0,
                    0,
                    telemetry=IndexSyncTelemetry(0, 0, 0),
                    unchanged=snapshot.document_count,
                )
                noop = True
            else:
                started = self.timer()
                previous = self._load_previous(snapshot)
                stage_timings_ms["previous_documents_load_ms"] = elapsed_ms(
                    started, self.timer(),
                )
                started = self.timer()
                indexed, discovery = self._index_changed(previous, snapshot, discovery)
                stage_timings_ms["canonical_delta_index_ms"] = elapsed_ms(
                    started, self.timer(),
                )
                if indexed.repository != snapshot.repository:
                    raise KnowledgeSearchError(
                        "Qdrant repository binding changed",
                        category="snapshot_revision_mismatch",
                    )
                started = self.timer()
                storage = self.store.incremental_sync(
                    indexed,
                    expected_snapshot=snapshot,
                )
                stage_timings_ms["persistence_call_ms"] = elapsed_ms(started, self.timer())
                noop = storage.mode == "noop"
                result["indexing_telemetry"] = indexed.telemetry.to_mapping()
            execution = _execution_result(storage)
            execution["noop"] = noop
            execution["stage_timings_ms"] = IndexStageTelemetry.from_mapping(
                stage_timings_ms,
            ).to_mapping()
            execution["discovery"] = _discovery(discovery, include_timings=True)
            result["execution"] = execution
            result["discovery"] = _discovery(discovery, include_timings=True)
            result["mode"] = "noop" if noop else "incremental"
            result["previous_indexed_revision"] = result.pop("indexed_revision")
            result["previous_currentness"] = result.pop("currentness")
            return result

        if not observation.schema_compatible:
            raise KnowledgeSearchError(
                "Correct and validate the schema before explicit rebuild",
                category="schema_mismatch",
            )
        started = self.timer()
        indexed = self.runtime.indexer.index(self.runtime.workspace)
        stage_timings_ms["canonical_full_index_ms"] = elapsed_ms(started, self.timer())
        started = self.timer()
        storage = self.store.full_rebuild(indexed)
        stage_timings_ms["persistence_call_ms"] = elapsed_ms(started, self.timer())
        execution = _execution_result(storage)
        execution["stage_timings_ms"] = IndexStageTelemetry.from_mapping(
            stage_timings_ms,
        ).to_mapping()
        result["execution"] = execution
        result["indexing_telemetry"] = indexed.telemetry.to_mapping()
        result["mode"] = "rebuild"
        result["previous_indexed_revision"] = result.pop("indexed_revision")
        result["previous_currentness"] = result.pop("currentness")
        result["reason"] = "explicit_rebuild"
        return result

    def _load_previous(
        self,
        snapshot: QdrantVectorSnapshot | None,
    ) -> tuple[KnowledgeDocument, ...]:
        if snapshot is None:
            return ()
        if snapshot.active_collection is None:
            raise KnowledgeSearchError(
                "Qdrant snapshot has no pinned collection identity",
                category="index_stale",
            )
        documents = self.store.load_documents(
            self.runtime.repository,
            collection=snapshot.active_collection,
        )
        if (
            len(documents) != snapshot.document_count
            or corpus_checksum(documents) != snapshot.corpus_checksum
        ):
            raise KnowledgeSearchError(
                "Qdrant active corpus differs from its snapshot",
                category="index_stale",
                metadata={"rebuild_required": True},
            )
        return documents

    def _require_same_snapshot(self, snapshot: QdrantVectorSnapshot) -> None:
        observed = self.store.load_snapshot(snapshot.repository)
        if observed != snapshot or observed.active_collection != snapshot.active_collection:
            raise KnowledgeSearchError(
                "Qdrant snapshot changed during no-op",
                category="concurrent_write",
            )

    def _index_changed(
        self,
        previous: tuple[KnowledgeDocument, ...],
        snapshot: QdrantVectorSnapshot | None,
        discovery: GitDeltaPlan | None = None,
    ) -> tuple[RepositoryIndexResult, GitDeltaPlan | None]:
        if snapshot is None:
            return self.runtime.indexer.index(self.runtime.workspace), None
        plan = discovery or self._detector().plan(
            self.runtime.workspace,
            previous_revision=snapshot.revision,
            current_revision=self.runtime.revision,
        )
        if plan.mode == "git-delta":
            delta = index_repository_delta(
                self.runtime.indexer,
                self.runtime.workspace,
                previous,
                plan,
            )
            if delta is not None:
                prior_by_id = {document.id: document for document in previous}
                affected_paths = set(plan.changed_paths) | set(plan.removed_paths)
                current_documents: list[KnowledgeDocument] = []
                for document in delta.documents:
                    if document.revision == delta.revision:
                        if document.provenance.revision != delta.revision:
                            raise KnowledgeSearchError(
                                "Git delta contains inconsistent source provenance",
                                category="unsafe_snapshot",
                            )
                        current_documents.append(document)
                        continue
                    prior = prior_by_id.get(document.id)
                    if (
                        prior != document
                        or document.path in affected_paths
                        or document.provenance.revision != document.revision
                    ):
                        raise KnowledgeSearchError(
                            "Git delta cannot prove retained source provenance",
                            category="unsafe_snapshot",
                        )
                    current_documents.append(replace(
                        document,
                        branch=delta.branch,
                        revision=delta.revision,
                        provenance=replace(document.provenance, revision=delta.revision),
                    ))
                current_by_id = {document.id: document for document in current_documents}
                return replace(
                    delta,
                    documents=tuple(current_documents),
                    changes=replace(
                        delta.changes,
                        unchanged=tuple(
                            current_by_id[document.id]
                            for document in delta.changes.unchanged
                        ),
                    ),
                ), plan
            plan = replace(plan, mode="full-scan", reason="delta_scan_unsafe")
        return (
            self.runtime.indexer.index(
                self.runtime.workspace,
                previous_documents=previous,
            ),
            plan,
        )

    @staticmethod
    def _detector() -> GitRepositoryChangeDetector:
        return GitRepositoryChangeDetector()


def _discovery(
    plan: GitDeltaPlan | None,
    *,
    include_timings: bool,
) -> dict[str, object]:
    if plan is None:
        return {"mode": "full-scan", "reason": "snapshot_missing"}
    payload = plan.to_mapping()
    if plan.mode == "git-delta":
        payload["changed_path_values"] = list(plan.changed_paths)
        payload["removed_path_values"] = list(plan.removed_paths)
    else:
        for name in ("changed_paths", "removed_paths", "targeted_paths"):
            payload.pop(name, None)
    if not include_timings:
        for name in ("discovery_duration_ms", "git_diff_duration_ms"):
            payload.pop(name, None)
    return payload


def _execution_result(storage: QdrantVectorIndexSyncResult) -> dict[str, object]:
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
        "serializer_mode": snapshot.serializer_mode.value,
        "serializer_version": snapshot.serializer_version,
        "serializer_fingerprint": snapshot.serializer_fingerprint,
        "sparse_vector_name": snapshot.sparse_vector_name,
        "sparse_analyzer_version": snapshot.sparse_analyzer_version,
        "sparse_analyzer_fingerprint": snapshot.sparse_analyzer_fingerprint,
        "sparse_modifier": snapshot.sparse_modifier,
    }
    return result


def _snapshot_identity(snapshot: QdrantVectorSnapshot) -> dict[str, object]:
    return {
        "repository": snapshot.repository,
        "branch": snapshot.branch,
        "revision": snapshot.revision,
        "document_count": snapshot.document_count,
        "corpus_checksum": snapshot.corpus_checksum,
        "indexed_at": snapshot.indexed_at,
        "status": snapshot.status,
        "projection_version": snapshot.projection_version,
        "projection_fingerprint": snapshot.projection_fingerprint,
        "indexing_fingerprint": snapshot.indexing_fingerprint,
        "embedding_provider": snapshot.embedding_provider,
        "embedding_model": snapshot.embedding_model,
        "vector_dimensions": snapshot.vector_dimensions,
        "serializer_mode": snapshot.serializer_mode.value,
        "serializer_version": snapshot.serializer_version,
        "serializer_fingerprint": snapshot.serializer_fingerprint,
        "sparse_vector_name": snapshot.sparse_vector_name,
        "sparse_analyzer_version": snapshot.sparse_analyzer_version,
        "sparse_analyzer_fingerprint": snapshot.sparse_analyzer_fingerprint,
        "sparse_modifier": snapshot.sparse_modifier,
    }


def _fingerprints(
    snapshot: QdrantVectorSnapshot | None,
    indexing_fingerprint: str,
) -> dict[str, str | None]:
    return {
        "indexing": indexing_fingerprint,
        "projection": None if snapshot is None else snapshot.projection_fingerprint,
        "embedding_serializer": (
            None if snapshot is None else snapshot.serializer_fingerprint
        ),
        "sparse_analyzer": (
            None if snapshot is None else snapshot.sparse_analyzer_fingerprint
        ),
    }
