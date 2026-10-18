from __future__ import annotations

import copy
from pathlib import Path
from typing import cast

import pytest

from fetchcore.search import KnowledgeSearchError
from fetchcore.repository.incremental import GitDeltaPlan
from fetchcore.repository.incremental_persistence import (
    TargetedGitAwareSolrSynchronizer,
    targeted_incremental_sync,
)
from fetchcore.repository.index_contract import (
    IndexContractSolrSnapshot,
    bind_indexing_contract_store,
    indexing_contract_fingerprint,
)
from fetchcore.repository.indexing import (
    RepositoryIndexConfig,
    RepositoryIndexer,
    RepositoryIndexResult,
)
from fetchcore.backends.solr import (
    SOLR_PROJECTION_VERSION,
    SolrDocumentMapper,
    SolrIndexSnapshot,
    SolrIndexStore,
    SolrIndexSyncResult,
    SolrSettings,
    corpus_checksum,
)
from memory_solr_transport import MemoryTransport


def _indexer(revision: str = "rev-a") -> RepositoryIndexer:
    return RepositoryIndexer(
        RepositoryIndexConfig(repository="example/repo", branch="main", revision=revision)
    )


def _fingerprint() -> str:
    return indexing_contract_fingerprint(_indexer())


def _store(transport: MemoryTransport) -> SolrIndexStore:
    base = SolrIndexStore(
        transport,
        SolrSettings(url="http://solr.test/solr", collection="knowledge"),
        clock=lambda: "2026-08-19T17:00:00+00:00",
        timer=lambda: 1.0,
    )
    return bind_indexing_contract_store(base, _indexer())


def _index_pair(tmp_path: Path) -> tuple[RepositoryIndexResult, RepositoryIndexResult]:
    (tmp_path / "a.py").write_text("def first():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def second():\n    return 2\n", encoding="utf-8")
    first = _indexer("rev-a").index(tmp_path)
    (tmp_path / "a.py").write_text("def first():\n    return 10\n", encoding="utf-8")
    (tmp_path / "b.py").unlink()
    (tmp_path / "c.py").write_text("def third():\n    return 3\n", encoding="utf-8")
    second = _indexer("rev-b").index(tmp_path, previous_documents=first.documents)
    return first, second


def _seed(
    transport: MemoryTransport,
    first: RepositoryIndexResult,
) -> SolrIndexSnapshot:
    mapper = SolrDocumentMapper()
    for document in first.documents:
        transport.documents[document.id] = mapper.to_solr(document)
    snapshot = IndexContractSolrSnapshot(
        repository=first.repository,
        branch=first.branch,
        revision=first.revision,
        corpus_checksum=corpus_checksum(first.documents),
        document_count=len(first.documents),
        projection_version=SOLR_PROJECTION_VERSION,
        indexed_at="2026-08-19T16:00:00+00:00",
        indexing_fingerprint=_fingerprint(),
    )
    transport.documents[snapshot.id] = snapshot.to_solr()
    return snapshot


def test_noop_uses_snapshot_count_without_loading_corpus(tmp_path: Path) -> None:
    snapshot = IndexContractSolrSnapshot(
        repository="example/repo",
        branch="main",
        revision="rev-a",
        corpus_checksum="checksum",
        document_count=2422,
        projection_version=SOLR_PROJECTION_VERSION,
        indexed_at="2026-08-19T16:00:00+00:00",
        indexing_fingerprint=_fingerprint(),
    )

    class SnapshotOnlyStore:
        def load_snapshot(self, repository: str) -> SolrIndexSnapshot:
            assert repository == "example/repo"
            return snapshot

        def embedding_snapshot_matches(self, value: SolrIndexSnapshot) -> bool:
            assert value == snapshot
            return True

        def load_documents(self, repository: str) -> object:
            pytest.fail("no-op must not load the repository corpus")

    class NoopDetector:
        def plan(
            self,
            workspace: Path,
            *,
            previous_revision: str | None,
            current_revision: str | None,
        ) -> GitDeltaPlan:
            return GitDeltaPlan(
                mode="git-delta",
                base_revision=previous_revision,
                current_revision=current_revision,
            )

    ticks = iter((0.0, 0.2))
    synchronizer = TargetedGitAwareSolrSynchronizer(
        cast(SolrIndexStore, SnapshotOnlyStore()),
        _indexer(),
        detector=NoopDetector(),  # type: ignore[arg-type]
        timer=lambda: next(ticks),
    )

    outcome = synchronizer.sync(tmp_path)

    assert outcome.noop is True
    assert outcome.storage.unchanged == 2422
    assert outcome.storage.duration_ms == 0
    assert outcome.stage_timings_ms == {"snapshot_load_ms": 200}


def test_indexing_contract_mismatch_forces_rebuild_before_loading_corpus(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.py").write_text("def stable():\n    return True\n", encoding="utf-8")
    indexer = _indexer()
    stale = IndexContractSolrSnapshot(
        repository="example/repo",
        branch="main",
        revision="rev-a",
        corpus_checksum="old",
        document_count=10,
        projection_version=SOLR_PROJECTION_VERSION,
        indexed_at="2026-08-19T16:00:00+00:00",
        indexing_fingerprint="sha256:stale",
    )

    class StaleContractStore:
        def load_snapshot(self, repository: str) -> SolrIndexSnapshot:
            assert repository == "example/repo"
            return stale

        def load_documents(self, repository: str) -> object:
            pytest.fail("stale indexing contract must rebuild before loading old corpus")

        def full_rebuild(self, result: RepositoryIndexResult) -> SolrIndexSyncResult:
            snapshot = IndexContractSolrSnapshot(
                repository=result.repository,
                branch=result.branch,
                revision=result.revision,
                corpus_checksum=corpus_checksum(result.documents),
                document_count=len(result.documents),
                projection_version=SOLR_PROJECTION_VERSION,
                indexed_at="2026-08-19T17:00:00+00:00",
                indexing_fingerprint=indexing_contract_fingerprint(indexer),
            )
            return SolrIndexSyncResult(
                mode="full_rebuild",
                snapshot=snapshot,
                added=len(result.documents),
                updated=0,
                deleted=0,
                unchanged=0,
                duration_ms=1,
            )

    outcome = TargetedGitAwareSolrSynchronizer(
        cast(SolrIndexStore, StaleContractStore()),
        indexer,
    ).sync(tmp_path)

    assert outcome.noop is False
    assert outcome.discovery.mode == "full-scan"
    assert outcome.discovery.reason == "indexing_contract_changed"
    assert outcome.discovery.base_revision == "rev-a"
    assert outcome.storage.mode == "full_rebuild"
    assert outcome.storage.snapshot.document_count == 1
    assert (
        getattr(outcome.storage.snapshot, "indexing_fingerprint", None)
        == indexing_contract_fingerprint(indexer)
    )


def test_embedding_identity_mismatch_forces_rebuild_before_loading_corpus(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.py").write_text("def stable():\n    return True\n", encoding="utf-8")
    indexer = _indexer()
    stale = IndexContractSolrSnapshot(
        repository="example/repo",
        branch="main",
        revision="rev-a",
        corpus_checksum="old",
        document_count=10,
        projection_version=SOLR_PROJECTION_VERSION,
        indexed_at="2026-08-19T16:00:00+00:00",
        indexing_fingerprint=indexing_contract_fingerprint(indexer),
    )

    class StaleEmbeddingStore:
        def load_snapshot(self, repository: str) -> SolrIndexSnapshot:
            assert repository == "example/repo"
            return stale

        def embedding_snapshot_matches(self, value: SolrIndexSnapshot) -> bool:
            assert value == stale
            return False

        def load_documents(self, repository: str) -> object:
            pytest.fail("embedding identity drift must rebuild before loading old corpus")

        def full_rebuild(self, result: RepositoryIndexResult) -> SolrIndexSyncResult:
            snapshot = IndexContractSolrSnapshot(
                repository=result.repository,
                branch=result.branch,
                revision=result.revision,
                corpus_checksum=corpus_checksum(result.documents),
                document_count=len(result.documents),
                projection_version=SOLR_PROJECTION_VERSION,
                indexed_at="2026-08-19T17:00:00+00:00",
                indexing_fingerprint=indexing_contract_fingerprint(indexer),
            )
            return SolrIndexSyncResult(
                mode="full_rebuild",
                snapshot=snapshot,
                added=len(result.documents),
                updated=0,
                deleted=0,
                unchanged=0,
                duration_ms=1,
            )

    outcome = TargetedGitAwareSolrSynchronizer(
        cast(SolrIndexStore, StaleEmbeddingStore()),
        indexer,
    ).sync(tmp_path)

    assert outcome.noop is False
    assert outcome.discovery.mode == "full-scan"
    assert outcome.discovery.reason == "embedding_identity_changed"
    assert outcome.storage.mode == "full_rebuild"


def test_dirty_path_without_corpus_changes_skips_solr_mutation(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def stable():\n    return True\n", encoding="utf-8")
    first = _indexer().index(tmp_path)
    transport = MemoryTransport()
    snapshot = _seed(transport, first)
    transport.post_calls.clear()
    (tmp_path / "notes.bin").write_bytes(b"not indexable")

    class NonCorpusDetector:
        def plan(
            self,
            workspace: Path,
            *,
            previous_revision: str | None,
            current_revision: str | None,
        ) -> GitDeltaPlan:
            return GitDeltaPlan(
                mode="git-delta",
                base_revision=previous_revision,
                current_revision=current_revision,
                changed_paths=("notes.bin",),
            )

    indexer = _indexer()
    ticks = iter((0.0, 0.1, 0.2, 0.3, 0.4, 0.6))
    outcome = TargetedGitAwareSolrSynchronizer(
        _store(transport),
        indexer,
        detector=NonCorpusDetector(),  # type: ignore[arg-type]
        timer=lambda: next(ticks),
    ).sync(tmp_path)

    assert outcome.noop is True
    assert outcome.storage.snapshot == snapshot
    assert outcome.storage.duration_ms == 0
    assert outcome.storage.unchanged == len(first.documents)
    assert outcome.stage_timings_ms == {
        "snapshot_load_ms": 100,
        "previous_documents_load_ms": 100,
        "canonical_delta_index_ms": 200,
    }
    assert transport.post_calls == []


def test_targeted_delta_validates_only_mutated_ids_count_and_snapshot(tmp_path: Path) -> None:
    first, second = _index_pair(tmp_path)
    transport = MemoryTransport()
    snapshot = _seed(transport, first)
    store = _store(transport)
    transport.get_calls.clear()

    result = targeted_incremental_sync(
        store,
        second,
        previous_documents=first.documents,
        previous_snapshot=snapshot,
    )

    assert result.added == len(second.changes.added)
    assert result.updated == len(second.changes.updated)
    assert result.deleted == len(second.changes.removed)
    assert store.load_snapshot("example/repo").revision == "rev-b"
    assert store.load_documents("example/repo") == second.documents

    validation_calls = [params for path, params in transport.get_calls if path == "select"]
    assert any(params.get("rows") == 0 for params in validation_calls)
    assert any(
        any(str(item).startswith("{!terms f=id}") for item in cast(list[object], params.get("fq", [])))
        for params in validation_calls
    )


def test_targeted_delta_failure_restores_previous_projection(tmp_path: Path) -> None:
    first, second = _index_pair(tmp_path)
    transport = MemoryTransport()
    snapshot = _seed(transport, first)
    store = _store(transport)
    before = copy.deepcopy(transport.documents)
    transport.fail_next_commit = True

    with pytest.raises(KnowledgeSearchError) as failed:
        targeted_incremental_sync(
            store,
            second,
            previous_documents=first.documents,
            previous_snapshot=snapshot,
        )

    assert failed.value.category == "index_update_failure"
    assert failed.value.metadata["rollback"] == "succeeded"
    assert transport.documents == before