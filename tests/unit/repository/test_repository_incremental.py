from __future__ import annotations

import subprocess
from pathlib import Path

from fetchcore.repository.incremental import (
    GitAwareSolrSynchronizer,
    GitRepositoryChangeDetector,
)
from fetchcore.repository.index_contract import (
    IndexContractSolrSnapshot,
    indexing_contract_fingerprint,
)
from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
from fetchcore.backends.solr import SolrIndexSnapshot, SolrIndexSyncResult


def _git(workspace: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _repository(workspace: Path) -> None:
    _git(workspace, "init")
    _git(workspace, "config", "user.email", "agentsorch@example.test")
    _git(workspace, "config", "user.name", "AgentsOrch Tests")


def _commit(workspace: Path, message: str) -> str:
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-m", message)
    return _git(workspace, "rev-parse", "HEAD")


def test_git_detector_combines_committed_and_working_tree_changes(tmp_path: Path) -> None:
    _repository(tmp_path)
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "removed.py").write_text("old = True\n", encoding="utf-8")
    base = _commit(tmp_path, "base")

    (tmp_path / "a.py").write_text("a = 2\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("b = 1\n", encoding="utf-8")
    (tmp_path / "removed.py").unlink()
    head = _commit(tmp_path, "committed delta")

    (tmp_path / "a.py").write_text("a = 3\n", encoding="utf-8")
    (tmp_path / "untracked.py").write_text("u = 1\n", encoding="utf-8")

    plan = GitRepositoryChangeDetector().plan(
        tmp_path,
        previous_revision=base,
        current_revision=head,
    )

    assert plan.mode == "git-delta"
    assert plan.changed_paths == ("a.py", "b.py", "untracked.py")
    assert plan.removed_paths == ("removed.py",)
    assert plan.reason == "safe_git_delta"


def test_git_detector_tracks_dirty_deletion_at_same_head(tmp_path: Path) -> None:
    _repository(tmp_path)
    (tmp_path / "keep.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "delete.py").write_text("value = 2\n", encoding="utf-8")
    head = _commit(tmp_path, "base")

    (tmp_path / "keep.py").write_text("value = 3\n", encoding="utf-8")
    (tmp_path / "delete.py").unlink()
    (tmp_path / "new.py").write_text("value = 4\n", encoding="utf-8")

    plan = GitRepositoryChangeDetector().plan(
        tmp_path,
        previous_revision=head,
        current_revision=head,
    )

    assert plan.mode == "git-delta"
    assert plan.changed_paths == ("keep.py", "new.py")
    assert plan.removed_paths == ("delete.py",)


def test_git_detector_falls_back_when_previous_revision_is_unavailable(tmp_path: Path) -> None:
    _repository(tmp_path)
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    head = _commit(tmp_path, "base")

    plan = GitRepositoryChangeDetector().plan(
        tmp_path,
        previous_revision="0" * 40,
        current_revision=head,
    )

    assert plan.mode == "full-scan"
    assert plan.reason == "previous_revision_unavailable"


class FixtureStore:
    def __init__(self, snapshot: SolrIndexSnapshot, documents: tuple[object, ...]) -> None:
        self.snapshot = snapshot
        self.documents = documents
        self.incremental_results: list[object] = []

    def load_snapshot(self, repository: str) -> SolrIndexSnapshot:
        assert repository == self.snapshot.repository
        return self.snapshot

    def load_documents(self, repository: str) -> tuple[object, ...]:
        assert repository == self.snapshot.repository
        return self.documents

    def embedding_snapshot_matches(self, snapshot: SolrIndexSnapshot) -> bool:
        assert snapshot == self.snapshot
        return True

    def incremental_sync(self, result: object) -> SolrIndexSyncResult:
        self.incremental_results.append(result)
        changes = result.changes
        snapshot = SolrIndexSnapshot(
            repository=result.repository,
            branch=result.branch,
            revision=result.revision,
            corpus_checksum="updated",
            document_count=len(result.documents),
            projection_version="2",
            indexed_at="now",
        )
        return SolrIndexSyncResult(
            mode="incremental",
            snapshot=snapshot,
            added=len(changes.added),
            updated=len(changes.updated),
            deleted=len(changes.removed),
            unchanged=len(changes.unchanged),
            duration_ms=1,
        )


def test_git_aware_sync_is_true_noop_for_clean_same_revision(tmp_path: Path) -> None:
    _repository(tmp_path)
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    head = _commit(tmp_path, "base")
    config = RepositoryIndexConfig(
        repository="acme/widgets",
        branch="main",
        revision=head,
    )
    indexer = RepositoryIndexer(config)
    previous = indexer.index(tmp_path).documents
    snapshot = IndexContractSolrSnapshot(
        repository="acme/widgets",
        branch="main",
        revision=head,
        corpus_checksum="same",
        document_count=len(previous),
        projection_version="2",
        indexed_at="before",
        indexing_fingerprint=indexing_contract_fingerprint(indexer),
    )
    store = FixtureStore(snapshot, previous)

    outcome = GitAwareSolrSynchronizer(store, indexer).sync(tmp_path)  # type: ignore[arg-type]

    assert outcome.noop is True
    assert outcome.discovery.mode == "git-delta"
    assert outcome.discovery.targeted_paths == 0
    assert outcome.storage.unchanged == len(previous)
    assert store.incremental_results == []


def test_git_aware_sync_reindexes_only_changed_paths_and_deletes_removed(
    tmp_path: Path,
) -> None:
    _repository(tmp_path)
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("b = 1\n", encoding="utf-8")
    base = _commit(tmp_path, "base")
    previous_indexer = RepositoryIndexer(
        RepositoryIndexConfig(repository="acme/widgets", branch="main", revision=base)
    )
    previous = previous_indexer.index(tmp_path).documents

    (tmp_path / "a.py").write_text("a = 2\n", encoding="utf-8")
    (tmp_path / "b.py").unlink()
    head = _commit(tmp_path, "change one and remove one")
    current_indexer = RepositoryIndexer(
        RepositoryIndexConfig(repository="acme/widgets", branch="main", revision=head)
    )
    snapshot = IndexContractSolrSnapshot(
        repository="acme/widgets",
        branch="main",
        revision=base,
        corpus_checksum="before",
        document_count=len(previous),
        projection_version="2",
        indexed_at="before",
        indexing_fingerprint=indexing_contract_fingerprint(current_indexer),
    )
    store = FixtureStore(snapshot, previous)

    outcome = GitAwareSolrSynchronizer(store, current_indexer).sync(tmp_path)  # type: ignore[arg-type]

    assert outcome.noop is False
    assert outcome.discovery.mode == "git-delta"
    assert outcome.discovery.changed_paths == ("a.py",)
    assert outcome.discovery.removed_paths == ("b.py",)
    assert len(store.incremental_results) == 1
    indexed = store.incremental_results[0]
    assert [document.path for document in indexed.documents] == ["a.py"]
    assert len(indexed.changes.updated) == 1
    assert len(indexed.changes.removed) == 1
    assert len(indexed.changes.added) == 0
